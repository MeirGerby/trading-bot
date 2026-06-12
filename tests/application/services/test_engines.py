"""Unit tests for PortfolioEngine, RiskEngine rules, and DecisionEngine."""
import pytest

from trading_platform.application.services import (
    CashReserveRule,
    DecisionEngine,
    ExitEngine,
    MaxExposureRule,
    MaxPositionSizeRule,
    PortfolioEngine,
    RiskEngine,
)
from trading_platform.config.settings import DEFAULT_RISK_PARAMS
from trading_platform.domain import (
    Direction,
    Instrument,
    PortfolioState,
    Position,
    Recommendation,
    Signal,
    SignalType,
)

AAPL = Instrument(symbol="AAPL")


def make_signal(strength=0.8, sig_type=SignalType.BREAKOUT):
    return Signal(instrument=AAPL, signal_type=sig_type, strength=strength)


def make_rec(price=100.0, quantity=10.0, confidence=0.7, signals=None):
    return Recommendation(
        instrument=AAPL, direction=Direction.LONG,
        signals=signals or (make_signal(),),
        price=price, confidence=confidence, rationale="t",
        proposed_quantity=quantity,
    )


def make_portfolio(cash=100_000.0, positions=()):
    return PortfolioState(cash=cash, positions=tuple(positions))


class TestPortfolioEngine:
    def test_equity_includes_positions(self):
        pf = make_portfolio(cash=50_000, positions=[
            Position(AAPL, quantity=100, avg_entry_price=100)])
        assert PortfolioEngine(DEFAULT_RISK_PARAMS).equity(pf) == 60_000

    def test_propose_quantity_scales_with_confidence(self):
        engine = PortfolioEngine(DEFAULT_RISK_PARAMS)
        pf = make_portfolio(cash=100_000)
        # equity*0.05*1.0 / 100 = 50 shares at full confidence
        assert engine.propose_quantity(pf, price=100.0, confidence=1.0) == 50
        assert engine.propose_quantity(pf, price=100.0, confidence=0.5) == 25

    def test_propose_quantity_floors_to_whole_shares(self):
        engine = PortfolioEngine(DEFAULT_RISK_PARAMS)
        assert engine.propose_quantity(make_portfolio(10_000), price=333.0, confidence=1.0) == 1

    def test_zero_when_price_invalid(self):
        engine = PortfolioEngine(DEFAULT_RISK_PARAMS)
        assert engine.propose_quantity(make_portfolio(), price=0, confidence=1.0) == 0


class TestRiskRules:
    def test_position_size_pass_and_fail(self):
        rule = MaxPositionSizeRule(0.10)  # max 10k on 100k equity
        assert rule.check(make_rec(price=100, quantity=50), make_portfolio()).passed
        result = rule.check(make_rec(price=100, quantity=150), make_portfolio())
        assert not result.passed
        assert "exceeds" in result.reason

    def test_exposure_counts_existing_positions(self):
        rule = MaxExposureRule(0.50)
        pf = make_portfolio(cash=50_000, positions=[
            Position(AAPL, quantity=450, avg_entry_price=100)])  # 45k exposure, 95k equity
        assert not rule.check(make_rec(price=100, quantity=50), pf).passed
        assert rule.check(make_rec(price=100, quantity=1), pf).passed

    def test_cash_reserve(self):
        rule = CashReserveRule(0.10)
        assert rule.check(make_rec(price=100, quantity=800), make_portfolio()).passed
        assert not rule.check(make_rec(price=100, quantity=950), make_portfolio()).passed

    def test_engine_attaches_all_checks(self):
        engine = RiskEngine([MaxPositionSizeRule(0.10), CashReserveRule(0.10)])
        rec = engine.review(make_rec(), make_portfolio())
        assert [c.rule_name for c in rec.risk_checks] == ["max_position_size", "cash_reserve"]
        assert rec.approved

    def test_zero_quantity_recommendation_passes_all(self):
        engine = RiskEngine([MaxPositionSizeRule(0.10), MaxExposureRule(0.8),
                             CashReserveRule(0.10)])
        assert engine.review(make_rec(quantity=0), make_portfolio()).approved


class FakeMemory:
    def __init__(self, feedback_history=None):
        self.history = feedback_history or []

    def load(self, key, default):
        if key == "feedback":
            return {"history": self.history}
        return default

    def save(self, key, value):
        pass

    def append_feedback(self, event):
        pass


class TestDecisionEngine:
    def test_neutral_prior_without_history(self):
        engine = DecisionEngine(FakeMemory())
        rec = engine.build(AAPL, (make_signal(strength=1.0),), price=100.0)
        # 0.6*1.0 + 0.4*0.5 = 0.8
        assert rec.confidence == pytest.approx(0.8)

    def test_positive_history_raises_confidence(self):
        history = [{"signals": ["breakout"], "positive": True}] * 4
        engine = DecisionEngine(FakeMemory(history))
        rec = engine.build(AAPL, (make_signal(strength=1.0),), price=100.0)
        # 0.6*1.0 + 0.4*1.0 = 1.0
        assert rec.confidence == pytest.approx(1.0)

    def test_negative_history_lowers_confidence(self):
        history = [{"signals": ["breakout"], "positive": False}] * 4
        engine = DecisionEngine(FakeMemory(history))
        rec = engine.build(AAPL, (make_signal(strength=1.0),), price=100.0)
        assert rec.confidence == pytest.approx(0.6)

    def test_rationale_mentions_signals_and_history(self):
        history = [{"signals": ["breakout"], "positive": True}]
        engine = DecisionEngine(FakeMemory(history))
        rec = engine.build(AAPL, (make_signal(), make_signal(sig_type=SignalType.MOMENTUM)),
                           price=100.0)
        assert "2 signal types" in rec.rationale
        assert "historical accuracy 100%" in rec.rationale


TASE = Instrument(symbol="TEVA.TA")
_RISK_WITH_FX = dict(DEFAULT_RISK_PARAMS, ils_to_usd=0.27)


class TestTASECurrencyNormalization:
    """TASE prices arrive in agorot (ILA). PortfolioEngine must convert to USD."""

    def test_tase_position_normalized_in_equity(self):
        # 1000 agorot × 100 shares = 100_000 agorot ≈ 100_000 × 0.01 × 0.27 = 270 USD
        pf = PortfolioState(cash=10_000.0, positions=(
            Position(TASE, quantity=100, avg_entry_price=1000.0),
        ))
        pe = PortfolioEngine(_RISK_WITH_FX)
        expected = 10_000.0 + 100 * 1000.0 * 0.01 * 0.27
        assert pe.equity(pf) == pytest.approx(expected)

    def test_us_position_not_scaled(self):
        pf = PortfolioState(cash=50_000.0, positions=(
            Position(AAPL, quantity=100, avg_entry_price=100.0),
        ))
        pe = PortfolioEngine(_RISK_WITH_FX)
        assert pe.equity(pf) == pytest.approx(60_000.0)

    def test_propose_quantity_uses_usd_price_for_tase(self):
        pe = PortfolioEngine(_RISK_WITH_FX)
        pf = PortfolioState(cash=100_000.0, positions=())
        # equity=100k, base_alloc=5%, confidence=1.0 → target=5_000 USD
        # TEVA.TA price=1500 agorot → price_usd=1500*0.01*0.27=4.05 USD
        # qty = floor(5000 / 4.05) = 1234
        qty = pe.propose_quantity(pf, price=1500.0, confidence=1.0, symbol="TEVA.TA")
        assert qty == pytest.approx(1234.0, abs=1)

    def test_position_value_usd_converts_tase(self):
        pe = PortfolioEngine(_RISK_WITH_FX)
        usd = pe.position_value_usd(100, 1500.0, "TEVA.TA")
        assert usd == pytest.approx(100 * 1500.0 * 0.01 * 0.27)

    def test_position_value_usd_leaves_us_unchanged(self):
        pe = PortfolioEngine(_RISK_WITH_FX)
        assert pe.position_value_usd(10, 200.0, "AAPL") == pytest.approx(2000.0)


class TestExitEngine:
    def _portfolio_with(self, symbol, qty, entry):
        return PortfolioState(cash=100_000.0, positions=(
            Position(Instrument(symbol=symbol), quantity=qty, avg_entry_price=entry),
        ))

    def test_stop_loss_triggers(self):
        engine = ExitEngine(stop_loss_pct=0.08, take_profit_pct=0.20)
        pf = self._portfolio_with("NVDA", 10, 100.0)
        exits = engine.check_exits(pf, {"NVDA": 91.0}, set())  # -9% < -8%
        assert len(exits) == 1
        assert exits[0][0] == "NVDA"
        assert "stop-loss" in exits[0][1]

    def test_stop_loss_not_triggered_within_threshold(self):
        engine = ExitEngine(stop_loss_pct=0.08)
        pf = self._portfolio_with("NVDA", 10, 100.0)
        exits = engine.check_exits(pf, {"NVDA": 93.0}, set())  # -7% > -8%
        assert exits == []

    def test_take_profit_triggers(self):
        engine = ExitEngine(stop_loss_pct=0.08, take_profit_pct=0.20)
        pf = self._portfolio_with("TSLA", 5, 100.0)
        exits = engine.check_exits(pf, {"TSLA": 122.0}, set())  # +22% > +20%
        assert len(exits) == 1
        assert exits[0][0] == "TSLA"
        assert "take-profit" in exits[0][1]

    def test_signal_decay_disabled_by_default(self):
        engine = ExitEngine(stop_loss_pct=0.08, take_profit_pct=0.20, signal_decay_enabled=False)
        pf = self._portfolio_with("AAPL", 10, 100.0)
        exits = engine.check_exits(pf, {"AAPL": 100.0}, set())  # no signals this scan
        assert exits == []  # decay disabled → no exit

    def test_signal_decay_triggers_when_enabled(self):
        engine = ExitEngine(stop_loss_pct=0.08, take_profit_pct=0.20, signal_decay_enabled=True)
        pf = self._portfolio_with("AAPL", 10, 100.0)
        exits = engine.check_exits(pf, {"AAPL": 100.0}, set())  # AAPL not in active signals
        assert len(exits) == 1
        assert "signal-decay" in exits[0][1]

    def test_no_exit_when_price_missing(self):
        engine = ExitEngine(stop_loss_pct=0.05, take_profit_pct=0.10)
        pf = self._portfolio_with("MSFT", 5, 200.0)
        assert engine.check_exits(pf, {}, set()) == []

    def test_tase_pnl_percentage_currency_agnostic(self):
        # agorot price — percentage math works without conversion
        engine = ExitEngine(stop_loss_pct=0.08, take_profit_pct=0.20)
        pf = self._portfolio_with("TEVA.TA", 100, 1500.0)
        # current 1350 → -10% → stop-loss
        exits = engine.check_exits(pf, {"TEVA.TA": 1350.0}, set())
        assert len(exits) == 1
        assert exits[0][0] == "TEVA.TA"
