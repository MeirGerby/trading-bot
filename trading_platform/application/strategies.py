"""Strategy implementations (the Strategy port).

Ported from the legacy scanner.py checks. Each returns a Signal with a
normalized strength in [0, 1]:
- at exactly the configured threshold → ~0.5
- well beyond it → approaches 1.0

The options-flow strategy is NOT ported yet: it needs option-chain data,
which MarketDataPort does not expose. Tracked in docs/PROJECT_STATUS.md.
"""
from trading_platform.application.indicators import rsi, sma
from trading_platform.application.ports import MarketDataPort
from trading_platform.domain import Instrument, Signal, SignalType


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


class BreakoutStrategy:
    """High volume near the 52-week high."""

    name = "breakout"

    def evaluate(self, instrument: Instrument, market_data: MarketDataPort,
                 params: dict[str, float]) -> Signal | None:
        bars = market_data.get_daily_bars(instrument.symbol, 252)
        if len(bars) < 21:
            return None

        closes = [b.close for b in bars]
        volumes = [b.volume for b in bars]

        current_price = closes[-1]
        high_52w = max(closes)
        avg_volume_20 = sum(volumes[-21:-1]) / 20
        if avg_volume_20 <= 0 or high_52w <= 0:
            return None

        vol_ratio = volumes[-1] / avg_volume_20
        price_vs_high = current_price / high_52w

        vol_thr = params["breakout_volume_ratio"]
        high_thr = params["breakout_pct_from_high"]
        if vol_ratio < vol_thr or price_vs_high < high_thr:
            return None

        return Signal(
            instrument=instrument,
            signal_type=SignalType.BREAKOUT,
            strength=_clamp(vol_ratio / (vol_thr * 2)),
            details={
                "volume_ratio": f"{vol_ratio:.1f}x",
                "52w_high": f"{high_52w:.2f}",
                "pct_of_high": f"{price_vs_high * 100:.1f}%",
            },
        )


class MomentumStrategy:
    """Price above its moving average with elevated RSI."""

    name = "momentum"

    def evaluate(self, instrument: Instrument, market_data: MarketDataPort,
                 params: dict[str, float]) -> Signal | None:
        ma_period = int(params["momentum_price_above_ma"])
        bars = market_data.get_daily_bars(instrument.symbol, max(ma_period + 1, 30))
        closes = [b.close for b in bars]

        current_ma = sma(closes, ma_period)
        current_rsi = rsi(closes)
        if current_ma is None or current_rsi is None:
            return None

        current_price = closes[-1]
        if current_price <= current_ma or current_rsi < params["momentum_rsi_min"]:
            return None

        return Signal(
            instrument=instrument,
            signal_type=SignalType.MOMENTUM,
            strength=_clamp(current_rsi / 100.0),
            details={
                "RSI": f"{current_rsi:.1f}",
                f"MA{ma_period}": f"{current_ma:.2f}",
                "pct_above_ma": f"{(current_price / current_ma - 1) * 100:.1f}%",
            },
        )


ALL_STRATEGIES = (BreakoutStrategy(), MomentumStrategy())
