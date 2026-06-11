"""
Dashboard API — exposes bot activity data as JSON endpoints
and serves the static HTML dashboard.
"""
import json
import os
import time as time_mod
from pathlib import Path

import yfinance as yf
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

import config

app = FastAPI()

DATA_DIR = Path(__file__).parent / "data"
STATIC_DIR = Path(__file__).parent / "static"

_chart_cache: dict = {}
_CHART_CACHE_TTL = 300  # 5 minutes


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
    defaults = config.DEFAULT_WEIGHTS
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

