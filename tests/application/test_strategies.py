from datetime import datetime, timedelta, timezone

from trading_platform.application.ports import Strategy
from trading_platform.application.strategies import BreakoutStrategy, MomentumStrategy
from trading_platform.config.settings import DEFAULT_STRATEGY_PARAMS
from trading_platform.domain import Bar, Instrument

AAPL = Instrument(symbol="AAPL")
PARAMS = dict(DEFAULT_STRATEGY_PARAMS)


def make_bars(closes: list[float], volumes: list[float] | None = None) -> list[Bar]:
    volumes = volumes or [1_000_000.0] * len(closes)
    start = datetime(2025, 6, 1, tzinfo=timezone.utc)
    return [
        Bar(time=start + timedelta(days=i), open=c, high=c * 1.01, low=c * 0.99,
            close=c, volume=v)
        for i, (c, v) in enumerate(zip(closes, volumes))
    ]


class FakeMarketData:
    def __init__(self, bars: list[Bar]):
        self.bars = bars

    def get_daily_bars(self, symbol: str, lookback_days: int) -> list[Bar]:
        return self.bars[-lookback_days:]

    def get_last_price(self, symbol: str) -> float | None:
        return self.bars[-1].close if self.bars else None


class TestBreakoutStrategy:
    def test_satisfies_port(self):
        assert isinstance(BreakoutStrategy(), Strategy)

    def test_triggers_on_high_volume_near_52w_high(self):
        closes = [100.0] * 50 + [101.0]  # new high on last bar
        volumes = [1_000_000.0] * 50 + [3_000_000.0]  # 3x average volume
        sig = BreakoutStrategy().evaluate(AAPL, FakeMarketData(make_bars(closes, volumes)), PARAMS)
        assert sig is not None
        assert sig.signal_type.value == "breakout"
        assert 0.5 <= sig.strength <= 1.0

    def test_no_signal_on_normal_volume(self):
        closes = [100.0] * 50 + [101.0]
        sig = BreakoutStrategy().evaluate(AAPL, FakeMarketData(make_bars(closes)), PARAMS)
        assert sig is None

    def test_no_signal_far_from_high(self):
        closes = [200.0] * 30 + [100.0] * 20 + [101.0]  # 50% below 52w high
        volumes = [1_000_000.0] * 50 + [3_000_000.0]
        sig = BreakoutStrategy().evaluate(AAPL, FakeMarketData(make_bars(closes, volumes)), PARAMS)
        assert sig is None

    def test_insufficient_history_returns_none(self):
        sig = BreakoutStrategy().evaluate(AAPL, FakeMarketData(make_bars([100.0] * 5)), PARAMS)
        assert sig is None


class TestMomentumStrategy:
    def test_satisfies_port(self):
        assert isinstance(MomentumStrategy(), Strategy)

    def test_triggers_on_uptrend(self):
        closes = [100.0 + i for i in range(40)]  # steady rally: price > MA20, RSI 100
        sig = MomentumStrategy().evaluate(AAPL, FakeMarketData(make_bars(closes)), PARAMS)
        assert sig is not None
        assert sig.signal_type.value == "momentum"
        assert sig.strength == 1.0
        assert sig.details["RSI"] == "100.0"

    def test_no_signal_on_downtrend(self):
        closes = [140.0 - i for i in range(40)]
        sig = MomentumStrategy().evaluate(AAPL, FakeMarketData(make_bars(closes)), PARAMS)
        assert sig is None

    def test_no_signal_when_price_below_ma(self):
        closes = [100.0 + i for i in range(39)] + [90.0]  # rally then drop below MA
        sig = MomentumStrategy().evaluate(AAPL, FakeMarketData(make_bars(closes)), PARAMS)
        assert sig is None

    def test_insufficient_history_returns_none(self):
        sig = MomentumStrategy().evaluate(AAPL, FakeMarketData(make_bars([100.0] * 5)), PARAMS)
        assert sig is None
