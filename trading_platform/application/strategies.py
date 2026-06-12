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
