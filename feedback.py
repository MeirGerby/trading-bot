"""
Stores user feedback on alerts and adjusts signal weights accordingly.
Each /good or /bad command nudges the weights that contributed to that alert.
"""
import json
import os
from datetime import datetime
from typing import Optional

FEEDBACK_FILE = os.path.join(os.path.dirname(__file__), "data", "feedback.json")
WEIGHTS_FILE = os.path.join(os.path.dirname(__file__), "data", "weights.json")

LEARNING_RATE = 0.05  # how much each feedback nudges a weight


def _load(path: str, default: dict) -> dict:
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default.copy()


def _save(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_weights(defaults: dict) -> dict:
    stored = _load(WEIGHTS_FILE, {})
    # Merge: stored values override defaults, but new default keys are included
    merged = defaults.copy()
    merged.update(stored)
    return merged


def save_weights(weights: dict) -> None:
    _save(WEIGHTS_FILE, weights)


def record_feedback(ticker: str, signal_types: list[str], positive: bool, weights: dict) -> dict:
    """Record feedback and return updated weights."""
    history = _load(FEEDBACK_FILE, {"history": []})

    history["history"].append({
        "ticker": ticker,
        "signals": signal_types,
        "positive": positive,
        "timestamp": datetime.utcnow().isoformat(),
    })
    _save(FEEDBACK_FILE, history)

    # Adjust weights based on which signals triggered
    direction = 1 if positive else -1
    adjustments = {
        "breakout": ["breakout_volume_ratio", "breakout_pct_from_high"],
        "momentum": ["momentum_rsi_min", "momentum_price_above_ma"],
        "options": ["options_vol_oi_ratio", "options_iv_percentile_min"],
    }

    for sig in signal_types:
        for key in adjustments.get(sig, []):
            if key in weights:
                # For thresholds: positive feedback → tighten (raise), negative → loosen (lower)
                weights[key] = round(weights[key] * (1 + direction * LEARNING_RATE), 4)

    save_weights(weights)
    return weights


def get_feedback_summary() -> str:
    history = _load(FEEDBACK_FILE, {"history": []})
    entries = history["history"]
    if not entries:
        return "אין פידבק עדיין."

    total = len(entries)
    positive = sum(1 for e in entries if e["positive"])
    lines = [f"סה\"כ פידבק: {total} | חיובי: {positive} | שלילי: {total - positive}"]

    # Last 5
    lines.append("\nאחרון 5 פידבקים:")
    for e in entries[-5:][::-1]:
        icon = "[V]" if e["positive"] else "[X]"
        lines.append(f"  {icon} {e['ticker']} ({', '.join(e['signals'])}) — {e['timestamp'][:10]}")

    return "\n".join(lines)
