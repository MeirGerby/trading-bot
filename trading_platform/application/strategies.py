"""Strategy implementations (the Strategy port).

Ported from the legacy scanner.py checks. Each returns a Signal with a
normalized strength in [0, 1]:
- at exactly the configured threshold → ~0.5
- well beyond it → approaches 1.0

Data dependencies are constructor-injected (ADR-6).
"""
from trading_platform.application.indicators import rsi, sma
from trading_platform.application.ports import MarketDataPort, OptionsDataPort
from trading_platform.domain import Instrument, Signal, SignalType


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


class BreakoutStrategy:
    """High volume near the 52-week high."""

    name = "breakout"

    def __init__(self, market_data: MarketDataPort):
        self._market_data = market_data

    def evaluate(self, instrument: Instrument, params: dict[str, float]) -> Signal | None:
        bars = self._market_data.get_daily_bars(instrument.symbol, 252)
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

    def __init__(self, market_data: MarketDataPort):
        self._market_data = market_data

    def evaluate(self, instrument: Instrument, params: dict[str, float]) -> Signal | None:
        ma_period = int(params["momentum_price_above_ma"])
        bars = self._market_data.get_daily_bars(instrument.symbol, max(ma_period + 1, 30))
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


class MeanReversionStrategy:
    """Price pulled far below moving average (oversold bounce setup)."""

    name = "mean_reversion"

    def __init__(self, market_data: MarketDataPort):
        self._market_data = market_data

    def evaluate(self, instrument: Instrument, params: dict[str, float]) -> Signal | None:
        ma_period = int(params.get("mean_reversion_ma_period", 20))
        rsi_max = params.get("mean_reversion_rsi_max", 35.0)
        pct_below_ma = params.get("mean_reversion_pct_below_ma", 0.03)

        bars = self._market_data.get_daily_bars(instrument.symbol, max(ma_period + 2, 32))
        closes = [b.close for b in bars]
        if len(closes) < ma_period + 1:
            return None

        current_price = closes[-1]
        current_ma = sma(closes, ma_period)
        current_rsi = rsi(closes)
        if current_ma is None or current_rsi is None or current_ma <= 0:
            return None

        deviation = (current_ma - current_price) / current_ma
        if deviation < pct_below_ma or current_rsi > rsi_max:
            return None

        strength = _clamp(deviation / 0.10)  # full strength at 10% below MA
        return Signal(
            instrument=instrument,
            signal_type=SignalType.MEAN_REVERSION,
            strength=strength,
            details={
                "RSI": f"{current_rsi:.1f}",
                f"MA{ma_period}": f"{current_ma:.2f}",
                "pct_below_ma": f"{deviation * 100:.1f}%",
            },
        )


class TrendFollowingStrategy:
    """Golden cross: faster MA crosses above slower MA with price confirmation."""

    name = "trend_following"

    def __init__(self, market_data: MarketDataPort):
        self._market_data = market_data

    def evaluate(self, instrument: Instrument, params: dict[str, float]) -> Signal | None:
        fast_period = int(params.get("trend_fast_ma", 20))
        slow_period = int(params.get("trend_slow_ma", 50))
        rsi_min = params.get("trend_rsi_min", 50.0)

        bars = self._market_data.get_daily_bars(instrument.symbol, slow_period + 5)
        closes = [b.close for b in bars]
        if len(closes) < slow_period + 2:
            return None

        fast_ma = sma(closes, fast_period)
        slow_ma = sma(closes, slow_period)
        prev_fast = sma(closes[:-1], fast_period)
        prev_slow = sma(closes[:-1], slow_period)
        current_rsi = rsi(closes)

        if any(v is None for v in [fast_ma, slow_ma, prev_fast, prev_slow, current_rsi]):
            return None

        # Require crossover: fast just crossed above slow
        crossed = prev_fast <= prev_slow and fast_ma > slow_ma  # type: ignore[operator]
        if not crossed or current_rsi < rsi_min:
            return None

        spread = (fast_ma - slow_ma) / slow_ma  # type: ignore[operator]
        strength = _clamp(spread / 0.02)  # full strength at 2% spread
        return Signal(
            instrument=instrument,
            signal_type=SignalType.TREND_FOLLOWING,
            strength=strength,
            details={
                f"MA{fast_period}": f"{fast_ma:.2f}",
                f"MA{slow_period}": f"{slow_ma:.2f}",
                "RSI": f"{current_rsi:.1f}",
                "spread": f"{spread * 100:.2f}%",
            },
        )


class OptionsFlowStrategy:
    """Unusual options activity: volume far above open interest."""

    name = "options"

    MIN_OPEN_INTEREST = 100  # ignore illiquid contracts, as the legacy scanner did

    def __init__(self, options_data: OptionsDataPort):
        self._options_data = options_data

    def evaluate(self, instrument: Instrument, params: dict[str, float]) -> Signal | None:
        contracts = self._options_data.get_option_contracts(instrument.symbol, max_expirations=2)
        liquid = [c for c in contracts
                  if c.open_interest > self.MIN_OPEN_INTEREST and c.volume > 0]
        if not liquid:
            return None

        best = max(liquid, key=lambda c: c.vol_oi_ratio)
        thr = params["options_vol_oi_ratio"]
        if best.vol_oi_ratio < thr:
            return None

        return Signal(
            instrument=instrument,
            signal_type=SignalType.OPTIONS_FLOW,
            strength=_clamp(best.vol_oi_ratio / (thr * 2)),
            details={
                "type": best.option_type.value.upper(),
                "strike": f"{best.strike:.2f}",
                "expiration": best.expiration,
                "vol_oi": f"{best.vol_oi_ratio:.1f}x",
                "IV": f"{best.implied_volatility * 100:.0f}%",
            },
        )
