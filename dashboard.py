"""
Dashboard API — exposes bot activity data as JSON endpoints
and serves the static HTML dashboard.
"""
import json
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

import config

app = FastAPI()

DATA_DIR = Path(__file__).parent / "data"
STATIC_DIR = Path(__file__).parent / "static"


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
