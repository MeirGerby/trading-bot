from datetime import datetime, timezone

import pytest

from trading_platform.domain import (
    AssetClass,
    Bar,
    Direction,
    Instrument,
    PortfolioState,
    Position,
    Recommendation,
    RiskCheckResult,
    Signal,
    SignalType,
)


def make_instrument(symbol: str = "AAPL") -> Instrument:
    return Instrument(symbol=symbol)


def make_signal(strength: float = 0.8, sig_type: SignalType = SignalType.BREAKOUT) -> Signal:
    return Signal(instrument=make_instrument(), signal_type=sig_type, strength=strength)


class TestInstrument:
    def test_valid(self):
        inst = Instrument(symbol="NVDA", asset_class=AssetClass.EQUITY)
        assert inst.symbol == "NVDA"

    @pytest.mark.parametrize("bad", ["", "aapl", "Aapl"])
    def test_rejects_invalid_symbol(self, bad):
        with pytest.raises(ValueError):
            Instrument(symbol=bad)


class TestBar:
    def test_valid(self):
        bar = Bar(time=datetime(2026, 6, 12, tzinfo=timezone.utc),
                  open=100, high=105, low=99, close=104, volume=1_000_000)
        assert bar.close == 104

    def test_rejects_close_above_high(self):
        with pytest.raises(ValueError):
            Bar(time=datetime.now(timezone.utc), open=100, high=105, low=99,
                close=106, volume=1)

    def test_rejects_negative_volume(self):
        with pytest.raises(ValueError):
            Bar(time=datetime.now(timezone.utc), open=100, high=105, low=99,
                close=104, volume=-1)


class TestSignal:
    @pytest.mark.parametrize("strength", [-0.1, 1.1])
    def test_rejects_out_of_range_strength(self, strength):
        with pytest.raises(ValueError):
            make_signal(strength=strength)

    def test_boundaries_allowed(self):
        assert make_signal(strength=0.0).strength == 0.0
        assert make_signal(strength=1.0).strength == 1.0


class TestRecommendation:
    def make_rec(self, signals=None, checks=()):
        if signals is None:
            signals = (make_signal(),)
        return Recommendation(
            instrument=make_instrument(),
            direction=Direction.LONG,
            signals=tuple(signals),
            price=150.0,
            confidence=0.7,
            rationale="test",
            risk_checks=tuple(checks),
        )

    def test_requires_signals(self):
        with pytest.raises(ValueError):
            self.make_rec(signals=[])  # type: ignore[arg-type]

    def test_score_counts_distinct_signal_types(self):
        rec = self.make_rec(signals=[
            make_signal(sig_type=SignalType.BREAKOUT),
            make_signal(sig_type=SignalType.BREAKOUT),
            make_signal(sig_type=SignalType.MOMENTUM),
        ])
        assert rec.score == 2

    def test_approved_requires_all_checks_passing(self):
        ok = RiskCheckResult(rule_name="sizing", passed=True)
        bad = RiskCheckResult(rule_name="exposure", passed=False, reason="too big")
        assert self.make_rec(checks=[ok]).approved
        assert not self.make_rec(checks=[ok, bad]).approved

    def test_no_checks_means_approved(self):
        assert self.make_rec().approved


class TestPortfolio:
    def test_exposure_sums_cost_basis(self):
        pf = PortfolioState(cash=10_000, positions=(
            Position(make_instrument("AAPL"), quantity=10, avg_entry_price=150),
            Position(make_instrument("MSFT"), quantity=5, avg_entry_price=400),
        ))
        assert pf.exposure() == 10 * 150 + 5 * 400
