"""Pure technical-indicator functions over plain float sequences.

Stdlib only — strategies stay testable without pandas. The legacy scanner's
RSI was a non-standard approximation; this is proper Wilder smoothing.
"""
from collections.abc import Sequence


def sma(values: Sequence[float], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def rsi(closes: Sequence[float], period: int = 14) -> float | None:
    """Relative Strength Index with Wilder smoothing."""
    if period <= 0 or len(closes) < period + 1:
        return None

    gains: list[float] = []
    losses: list[float] = []
    for prev, cur in zip(closes, closes[1:]):
        delta = cur - prev
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for g, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)
