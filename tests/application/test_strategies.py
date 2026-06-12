from datetime import datetime, timedelta, timezone

from trading_platform.application.ports import Strategy
from trading_platform.application.strategies import (
    BreakoutStrategy,
    MomentumStrategy,
    OptionsFlowStrategy,
)
from trading_platform.config.settings import DEFAULT_STRATEGY_PARAMS
from trading_platform.domain import Bar, Instrument, OptionContract, OptionType

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


class FakeOptionsData:
    def __init__(self, contracts: list[OptionContract]):
        self.contracts = contracts

    def get_option_contracts(self, symbol: str, max_expirations: int = 2) -> list[OptionContract]:
        return self.contracts


def make_contract(volume: float, open_interest: float,
                  opt_type: OptionType = OptionType.CALL) -> OptionContract:
    return OptionContract(underlying="AAPL", option_type=opt_type, strike=150.0,
                          expiration="2026-07-17", volume=volume,
                          open_interest=open_interest, implied_volatility=0.45)


class TestBreakoutStrategy:
    def test_satisfies_port(self):
        assert isinstance(BreakoutStrategy(FakeMarketData([])), Strategy)

    def test_triggers_on_high_volume_near_52w_high(self):
        closes = [100.0] * 50 + [101.0]
        volumes = [1_000_000.0] * 50 + [3_000_000.0]
        strat = BreakoutStrategy(FakeMarketData(make_bars(closes, volumes)))
        sig = strat.evaluate(AAPL, PARAMS)
        assert sig is not None
        assert sig.signal_type.value == "breakout"
        assert 0.5 <= sig.strength <= 1.0

    def test_no_signal_on_normal_volume(self):
        closes = [100.0] * 50 + [101.0]
        strat = BreakoutStrategy(FakeMarketData(make_bars(closes)))
        assert strat.evaluate(AAPL, PARAMS) is None

    def test_no_signal_far_from_high(self):
        closes = [200.0] * 30 + [100.0] * 20 + [101.0]
        volumes = [1_000_000.0] * 50 + [3_000_000.0]
        strat = BreakoutStrategy(FakeMarketData(make_bars(closes, volumes)))
        assert strat.evaluate(AAPL, PARAMS) is None

    def test_insufficient_history_returns_none(self):
        strat = BreakoutStrategy(FakeMarketData(make_bars([100.0] * 5)))
        assert strat.evaluate(AAPL, PARAMS) is None


class TestMomentumStrategy:
    def test_satisfies_port(self):
        assert isinstance(MomentumStrategy(FakeMarketData([])), Strategy)

    def test_triggers_on_uptrend(self):
        closes = [100.0 + i for i in range(40)]
        strat = MomentumStrategy(FakeMarketData(make_bars(closes)))
        sig = strat.evaluate(AAPL, PARAMS)
        assert sig is not None
        assert sig.signal_type.value == "momentum"
        assert sig.strength == 1.0

    def test_no_signal_on_downtrend(self):
        closes = [140.0 - i for i in range(40)]
        strat = MomentumStrategy(FakeMarketData(make_bars(closes)))
        assert strat.evaluate(AAPL, PARAMS) is None

    def test_no_signal_when_price_below_ma(self):
        closes = [100.0 + i for i in range(39)] + [90.0]
        strat = MomentumStrategy(FakeMarketData(make_bars(closes)))
        assert strat.evaluate(AAPL, PARAMS) is None

    def test_insufficient_history_returns_none(self):
        strat = MomentumStrategy(FakeMarketData(make_bars([100.0] * 5)))
        assert strat.evaluate(AAPL, PARAMS) is None


class TestOptionsFlowStrategy:
    def test_satisfies_port(self):
        assert isinstance(OptionsFlowStrategy(FakeOptionsData([])), Strategy)

    def test_triggers_on_unusual_volume(self):
        contracts = [make_contract(volume=5000, open_interest=1000)]  # 5x ratio
        sig = OptionsFlowStrategy(FakeOptionsData(contracts)).evaluate(AAPL, PARAMS)
        assert sig is not None
        assert sig.signal_type.value == "options"
        assert sig.details["type"] == "CALL"
        assert sig.details["vol_oi"] == "5.0x"

    def test_no_signal_below_ratio_threshold(self):
        contracts = [make_contract(volume=1500, open_interest=1000)]  # 1.5x < 2.0
        assert OptionsFlowStrategy(FakeOptionsData(contracts)).evaluate(AAPL, PARAMS) is None

    def test_ignores_illiquid_contracts(self):
        contracts = [make_contract(volume=500, open_interest=50)]  # OI <= 100
        assert OptionsFlowStrategy(FakeOptionsData(contracts)).evaluate(AAPL, PARAMS) is None

    def test_picks_highest_ratio_contract(self):
        contracts = [
            make_contract(volume=3000, open_interest=1000),
            make_contract(volume=8000, open_interest=1000, opt_type=OptionType.PUT),
        ]
        sig = OptionsFlowStrategy(FakeOptionsData(contracts)).evaluate(AAPL, PARAMS)
        assert sig.details["type"] == "PUT"

    def test_empty_chain_returns_none(self):
        assert OptionsFlowStrategy(FakeOptionsData([])).evaluate(AAPL, PARAMS) is None
