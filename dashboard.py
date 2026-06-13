"""
Dashboard API — exposes bot activity data as JSON endpoints
and serves the static HTML dashboard.
"""
import json
import logging
import os
import threading
import time as time_mod
from datetime import datetime
from pathlib import Path

import yfinance as yf
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from trading_platform.application.services import recommendation_to_dict
from trading_platform.config.settings import DEFAULT_STRATEGY_PARAMS
from trading_platform.application.services.learning_engine import LearningEngine
from trading_platform.application.services.meta_decision_engine import MetaDecisionEngine
from trading_platform.application.services.performance_tracker import PerformanceTracker
from trading_platform.application.services.screener_service import technical_snapshot
from trading_platform.application.services.self_critique_engine import SelfCritiqueEngine
from trading_platform.bootstrap import (
    build_idea_engine,
    build_scan_service,
    build_screener_service,
    build_symbol_repository,
    get_market_data,
)

logger = logging.getLogger(__name__)

app = FastAPI()

DATA_DIR = Path(__file__).parent / "data"
STATIC_DIR = Path(__file__).parent / "static"

_chart_cache: dict = {}
_CHART_CACHE_TTL = 300  # 5 minutes

_scan_service = build_scan_service()
_scan_cache: dict = {"signals": [], "ts": 0.0, "running": False, "next_scan": 0.0}
_SCAN_INTERVAL = 300  # 5 minutes

# Intelligence layer helpers (wired from the scan_service internals)
_tracker: PerformanceTracker | None = getattr(_scan_service, "_tracker", None)
_meta: MetaDecisionEngine | None = getattr(_scan_service, "_meta", None)
_learning: LearningEngine | None = getattr(_scan_service, "_learning", None)
_critique: SelfCritiqueEngine | None = getattr(_scan_service, "_critique", None)

_symbol_repo = build_symbol_repository()
_screener_service = build_screener_service()
_screener_cache: dict = {"rows": [], "ts": 0.0, "running": False}
_SCREENER_INTERVAL = 600  # fundamentals are slow + heavily cached; 10 min is plenty

_idea_engine = build_idea_engine()
_idea_cache: dict = {"ideas": [], "ts": 0.0, "running": False}
_IDEA_INTERVAL = 86400  # once per day


def _run_screener() -> None:
    _screener_cache["running"] = True
    try:
        result = _screener_service.build_rows()
        _screener_cache["rows"] = result["rows"]
        _screener_cache["ts"] = time_mod.time()
    except Exception:
        logger.exception("screener refresh failed")
    finally:
        _screener_cache["running"] = False


def _screener_loop() -> None:
    time_mod.sleep(10)
    while True:
        _run_screener()
        time_mod.sleep(_SCREENER_INTERVAL)


threading.Thread(target=_screener_loop, daemon=True).start()


def _run_idea_cycle() -> None:
    _idea_cache["running"] = True
    try:
        from trading_platform.config.settings import DEFAULT_RISK_PARAMS, DEFAULT_STRATEGY_PARAMS
        s_params = dict(DEFAULT_STRATEGY_PARAMS)
        s_params.update(_scan_service.current_params())
        r_params = dict(DEFAULT_RISK_PARAMS)
        ideas = _idea_engine.run_daily_cycle(s_params, r_params)
        _idea_cache["ideas"] = _idea_engine.get_recent_ideas(30)
        _idea_cache["ts"] = time_mod.time()
        logger.info("idea cycle complete: %d ideas applied", len(ideas))
    except Exception:
        logger.exception("idea engine cycle failed")
    finally:
        _idea_cache["running"] = False


def _idea_loop() -> None:
    time_mod.sleep(3600)  # first run 1h after startup
    while True:
        _run_idea_cycle()
        time_mod.sleep(_IDEA_INTERVAL)


threading.Thread(target=_idea_loop, daemon=True).start()


def _load_persisted_results() -> None:
    """Seed the cache from the last persisted scan so restarts aren't empty."""
    stored = _scan_service.last_scan_results()
    if stored.get("recommendations"):
        _scan_cache["signals"] = stored["recommendations"]
        try:
            _scan_cache["ts"] = datetime.fromisoformat(stored["timestamp"]).timestamp()
        except (ValueError, KeyError):
            pass


_load_persisted_results()
_idea_cache["ideas"] = _idea_engine.get_recent_ideas(30)


def _run_scan() -> None:
    _scan_cache["running"] = True
    try:
        report = _scan_service.scan()
        _scan_cache["signals"] = [recommendation_to_dict(r) for r in report.recommendations]
        _scan_cache["ts"] = time_mod.time()
    except Exception:
        logger.exception("scan failed")
    finally:
        _scan_cache["running"] = False
    _scan_cache["next_scan"] = time_mod.time() + _SCAN_INTERVAL


def _scan_loop() -> None:
    time_mod.sleep(3)
    while True:
        _run_scan()
        time_mod.sleep(_SCAN_INTERVAL)


threading.Thread(target=_scan_loop, daemon=True).start()


def _read_json(path: Path, default):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return default


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/alerts")
def get_alerts():
    store = _read_json(DATA_DIR / "alerts_log.json", {"alerts": []})
    alerts = list(reversed(store["alerts"]))[:50]
    return JSONResponse({"alerts": alerts})


@app.get("/api/weights")
def get_weights():
    current = _read_json(DATA_DIR / "weights.json", {})
    defaults = DEFAULT_STRATEGY_PARAMS
    merged = {k: {"current": current.get(k, v), "default": v} for k, v in defaults.items()}
    return JSONResponse({"weights": merged})


@app.get("/api/feedback")
def get_feedback():
    store = _read_json(DATA_DIR / "feedback.json", {"history": []})
    history = store.get("history", [])

    total = len(history)
    positive = sum(1 for e in history if e.get("positive"))
    negative = total - positive

    by_signal: dict[str, dict] = {}
    by_ticker: dict[str, dict] = {}

    for e in history:
        p = e.get("positive", False)
        for sig in e.get("signals", []):
            s = by_signal.setdefault(sig, {"positive": 0, "negative": 0})
            s["positive" if p else "negative"] += 1

        ticker = e.get("ticker", "")
        t = by_ticker.setdefault(ticker, {"positive": 0, "negative": 0})
        t["positive" if p else "negative"] += 1

    top_tickers = sorted(
        [{"ticker": k, **v} for k, v in by_ticker.items()],
        key=lambda x: x["positive"] + x["negative"],
        reverse=True,
    )[:5]

    return JSONResponse({
        "total": total,
        "positive": positive,
        "negative": negative,
        "by_signal": by_signal,
        "top_tickers": top_tickers,
    })


@app.get("/api/chart/{ticker}")
def get_chart(ticker: str, period: str = "1d"):
    ticker = ticker.upper()
    cache_key = f"{ticker}:{period}"
    cached = _chart_cache.get(cache_key)
    if cached and time_mod.time() - cached["ts"] < _CHART_CACHE_TTL:
        return JSONResponse(cached["data"])

    interval_map = {"1d": "5m", "5d": "30m", "1mo": "1d"}
    interval = interval_map.get(period, "5m")
    is_intraday = interval != "1d"

    empty = {"candles": [], "ticker": ticker, "period": period, "is_intraday": is_intraday}
    try:
        df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
    except Exception:
        return JSONResponse(empty)

    if df.empty:
        return JSONResponse(empty)

    # Flatten MultiIndex columns (newer yfinance versions)
    if hasattr(df.columns, "levels"):
        df.columns = df.columns.get_level_values(0)

    candles = []
    for ts, row in df.iterrows():
        o = float(row.get("Open", float("nan")))
        h = float(row.get("High", float("nan")))
        l = float(row.get("Low", float("nan")))
        c = float(row.get("Close", float("nan")))
        if any(v != v for v in [o, h, l, c]):  # skip NaN rows
            continue
        t = int(ts.value // 10**9) if is_intraday else ts.strftime("%Y-%m-%d")
        candles.append({"time": t, "open": round(o, 2), "high": round(h, 2),
                         "low": round(l, 2), "close": round(c, 2)})

    result = {"candles": candles, "ticker": ticker, "period": period, "is_intraday": is_intraday}
    _chart_cache[cache_key] = {"ts": time_mod.time(), "data": result}
    return JSONResponse(result)


@app.get("/api/recommendations")
def get_recommendations():
    return JSONResponse({
        "signals": _scan_cache["signals"],
        "last_scan": _scan_cache["ts"],
        "running": _scan_cache["running"],
        "next_scan": _scan_cache["next_scan"],
    })


@app.post("/api/scan")
def trigger_scan():
    if _scan_cache["running"]:
        return JSONResponse({"status": "already_running"})
    threading.Thread(target=_run_scan, daemon=True).start()
    return JSONResponse({"status": "started"})


@app.get("/api/portfolio")
def get_portfolio():
    broker = getattr(_scan_service, "_broker", None)
    if broker is None:
        return JSONResponse({"error": "broker not available"}, status_code=503)
    try:
        state = broker.get_portfolio()
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)

    risk_params = _scan_service.effective_risk_params()
    tp_pct = float(risk_params.get("take_profit_pct", 0.01))
    sl_pct = float(risk_params.get("stop_loss_pct", 0.005))

    positions = []
    for pos in state.positions:
        current_price = _scan_service._market_data.get_last_price(pos.instrument.symbol)
        market_value = (current_price or pos.avg_entry_price) * pos.quantity
        unrealized_pnl = (
            (current_price - pos.avg_entry_price) * pos.quantity
            if current_price else 0.0
        )
        positions.append({
            "symbol": pos.instrument.symbol,
            "quantity": pos.quantity,
            "avg_entry_price": round(pos.avg_entry_price, 4),
            "current_price": round(current_price, 4) if current_price else None,
            "market_value": round(market_value, 2),
            "cost_basis": round(pos.cost_basis, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "unrealized_pnl_pct": round(
                (current_price / pos.avg_entry_price - 1) * 100
                if current_price and pos.avg_entry_price > 0 else 0.0, 2
            ),
            # Active exit targets (entry × (1 ± pct)), in the same vendor-native
            # units as avg_entry_price so the UI can show them inline.
            "take_profit_price": round(pos.avg_entry_price * (1 + tp_pct), 4),
            "stop_loss_price": round(pos.avg_entry_price * (1 - sl_pct), 4),
            "take_profit_pct": round(tp_pct * 100, 2),
            "stop_loss_pct": round(sl_pct * 100, 2),
        })

    total_market_value = sum(p["market_value"] for p in positions)
    total_cost_basis = sum(p["cost_basis"] for p in positions)
    total_unrealized_pnl = sum(p["unrealized_pnl"] for p in positions)
    total_equity = state.cash + total_market_value

    return JSONResponse({
        "cash": round(state.cash, 2),
        "total_market_value": round(total_market_value, 2),
        "total_equity": round(total_equity, 2),
        "total_cost_basis": round(total_cost_basis, 2),
        "total_unrealized_pnl": round(total_unrealized_pnl, 2),
        "total_unrealized_pnl_pct": round(
            total_unrealized_pnl / total_cost_basis * 100
            if total_cost_basis > 0 else 0.0, 2
        ),
        "positions": sorted(positions, key=lambda p: -abs(p["market_value"])),
    })


@app.get("/api/performance")
def get_performance():
    if _tracker is None:
        return JSONResponse({"error": "performance tracker not available"}, status_code=503)
    try:
        summary = _tracker.get_summary_dict()
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)

    leaderboard = []
    if _meta is not None:
        try:
            leaderboard = _meta.leaderboard()
        except Exception:
            pass

    recent_outcomes = []
    try:
        all_outcomes = _tracker.get_all_outcomes()
        recent_outcomes = list(reversed(all_outcomes[-20:]))
    except Exception:
        pass

    signal_win_rates = {}
    if _learning is not None:
        try:
            signal_win_rates = _learning.get_signal_win_rates()
        except Exception:
            pass

    return JSONResponse({
        **summary,
        "leaderboard": leaderboard,
        "recent_outcomes": recent_outcomes,
        "signal_combinations": signal_win_rates,
    })


@app.get("/api/learning")
def get_learning():
    result: dict = {}

    if _learning is not None:
        try:
            result["recent_lessons"] = _learning.get_recent_lessons(20)
        except Exception:
            result["recent_lessons"] = []
        try:
            result["signal_win_rates"] = _learning.get_signal_win_rates()
        except Exception:
            result["signal_win_rates"] = {}
    else:
        result["recent_lessons"] = []
        result["signal_win_rates"] = {}

    if _critique is not None:
        try:
            result["recent_critiques"] = _critique.get_recent_critiques(5)
        except Exception:
            result["recent_critiques"] = []
    else:
        result["recent_critiques"] = []

    return JSONResponse(result)


@app.get("/api/screener")
def get_screener(limit: int = 0, offset: int = 0):
    rows = _screener_cache["rows"]
    total = len(rows)
    if offset:
        rows = rows[offset:]
    if limit:
        rows = rows[:limit]
    return JSONResponse({
        "rows": rows,
        "total": total,
        "last_refresh": _screener_cache["ts"],
        "running": _screener_cache["running"],
    })


@app.get("/api/stocks/search")
def search_stocks(q: str = "", limit: int = 20, market: str | None = None):
    try:
        results = _symbol_repo.search(q, limit=min(limit, 50), market=market)
    except Exception:
        logger.exception("symbol search failed")
        results = []
    return JSONResponse({"query": q, "results": results})


@app.get("/api/stocks/{symbol}/indicators")
def stock_indicators(symbol: str):
    symbol = symbol.upper()
    record = _symbol_repo.lookup(symbol)
    if record is None:
        return JSONResponse({"error": f"unknown symbol {symbol}"}, status_code=404)
    snapshot = technical_snapshot(symbol, get_market_data())
    if snapshot is None:
        return JSONResponse({"error": f"no price history for {symbol}"}, status_code=404)
    snapshot["name"] = record.get("name", "")
    snapshot["watched"] = symbol in _symbol_repo.get_watchlist()
    return JSONResponse(snapshot)


@app.get("/api/watchlist")
def get_watchlist():
    symbols = _symbol_repo.get_watchlist()
    return JSONResponse({"symbols": list(symbols), "count": len(symbols)})


@app.post("/api/watchlist/{symbol}")
def add_watch(symbol: str):
    if not _symbol_repo.add_to_watchlist(symbol):
        return JSONResponse({"error": f"could not validate symbol {symbol}"}, status_code=400)
    threading.Thread(target=_run_screener, daemon=True).start()  # pick up the new row
    return JSONResponse({"status": "added", "symbols": list(_symbol_repo.get_watchlist())})


@app.delete("/api/watchlist/{symbol}")
def remove_watch(symbol: str):
    if not _symbol_repo.remove_from_watchlist(symbol):
        return JSONResponse({"error": f"{symbol} not in watchlist"}, status_code=404)
    threading.Thread(target=_run_screener, daemon=True).start()
    return JSONResponse({"status": "removed", "symbols": list(_symbol_repo.get_watchlist())})


@app.post("/api/screener/refresh")
def refresh_screener():
    if _screener_cache["running"]:
        return JSONResponse({"status": "already_running"})
    threading.Thread(target=_run_screener, daemon=True).start()
    return JSONResponse({"status": "started"})


@app.get("/api/ideas")
def get_ideas():
    return JSONResponse({
        "ideas": _idea_cache.get("ideas", []),
        "last_run": _idea_cache.get("ts", 0.0),
        "running": _idea_cache.get("running", False),
    })


@app.post("/api/ideas/run")
def run_ideas():
    if _idea_cache.get("running"):
        return JSONResponse({"status": "already_running"})
    threading.Thread(target=_run_idea_cycle, daemon=True).start()
    return JSONResponse({"status": "started"})


@app.get("/api/trades")
def get_trades():
    try:
        trades = _scan_service.recent_trades(50)
    except Exception:
        trades = []
    return JSONResponse({"trades": trades})


@app.get("/api/audit")
def get_audit():
    audit = getattr(_scan_service, "_audit", None)
    if audit is None:
        return JSONResponse({"events": []})
    try:
        events = audit.tail(50)
    except Exception:
        events = []
    return JSONResponse({"events": events})

