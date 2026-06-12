"""
Stores user feedback on alerts and adjusts signal weights accordingly.
Each /good or /bad command nudges the weights that contributed to that alert.

Legacy facade: public API unchanged, persistence delegated to
trading_platform.infrastructure.memory_store.JsonMemoryStore
(atomic writes + cross-container locking).
"""
import os
from datetime import datetime

from trading_platform.infrastructure.memory_store import JsonMemoryStore

_store = JsonMemoryStore(os.path.join(os.path.dirname(__file__), "data"))

LEARNING_RATE = 0.05  # how much each feedback nudges a weight


def load_weights(defaults: dict) -> dict:
    # Merge: stored values override defaults, but new default keys are included
    merged = defaults.copy()
    merged.update(_store.load("weights", {}))
    return merged


def save_weights(weights: dict) -> None:
    _store.save("weights", weights)


def record_feedback(ticker: str, signal_types: list[str], positive: bool, weights: dict) -> dict:
    """Record feedback and return updated weights."""
    history = _store.load("feedback", {"history": []})
    history["history"].append({
        "ticker": ticker,
        "signals": signal_types,
        "positive": positive,
        "timestamp": datetime.utcnow().isoformat(),
    })
    _store.save("feedback", history)

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
    history = _store.load("feedback", {"history": []})
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
