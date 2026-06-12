"""Tests for PerformanceTracker, LearningEngine, MetaDecisionEngine, SelfCritiqueEngine."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from trading_platform.application.services.learning_engine import LearningEngine
from trading_platform.application.services.meta_decision_engine import MetaDecisionEngine
from trading_platform.application.services.performance_tracker import (
    HOLD_PERIOD_HOURS,
    PerformanceTracker,
)
from trading_platform.application.services.self_critique_engine import (
    ScanSummary,
    SelfCritiqueEngine,
)
from trading_platform.domain.enums import AssetClass, Direction, OrderSide, SignalType
from trading_platform.domain.models import (
    Instrument,
    Recommendation,
    Signal,
    StrategyPerformance,
    TradeOutcome,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow():
    return datetime.now(timezone.utc)


def make_memory_store():
    """In-memory dict-backed MemoryStore double."""
    store: dict = {}

    class _Mem:
        def load(self, key, default):
            return dict(store.get(key, default))

        def save(self, key, value):
            store[key] = dict(value)

        def append_feedback(self, event):
            pass

    return _Mem()


def make_market_data(price=105.0):
    md = MagicMock()
    md.get_last_price.return_value = price
    return md


def make_recommendation(symbol="AAPL", confidence=0.7, created_at=None):
    inst = Instrument(symbol=symbol)
    sig = Signal(instrument=inst, signal_type=SignalType.BREAKOUT, strength=0.8)
    return Recommendation(
        instrument=inst,
        direction=Direction.LONG,
        signals=(sig,),
        price=100.0,
        confidence=confidence,
        rationale="test",
        created_at=created_at or _utcnow(),
    )


def make_outcome(symbol="AAPL", return_pct=0.05, confidence=0.7, signal_types=None):
    now = _utcnow()
    return TradeOutcome(
        id=f"{symbol}-{now.isoformat()}",
        symbol=symbol,
        entry_price=100.0,
        exit_price=100.0 * (1 + return_pct),
        entry_time=now - timedelta(hours=25),
        checked_at=now,
        signal_types=tuple(signal_types or ["breakout"]),
        confidence=confidence,
        return_pct=return_pct,
    )


# ---------------------------------------------------------------------------
# PerformanceTracker
# ---------------------------------------------------------------------------

class TestPerformanceTracker:
    def test_register_stores_pending(self):
        memory = make_memory_store()
        tracker = PerformanceTracker(memory, make_market_data())
        rec = make_recommendation()
        tracker.register([rec])
        pending = memory.load("pending_outcomes", {"items": []})
        assert len(pending["items"]) == 1
        assert pending["items"][0]["symbol"] == "AAPL"
        assert pending["items"][0]["entry_price"] == 100.0

    def test_register_deduplicates(self):
        memory = make_memory_store()
        tracker = PerformanceTracker(memory, make_market_data())
        rec = make_recommendation()
        tracker.register([rec])
        tracker.register([rec])  # second call with same rec
        pending = memory.load("pending_outcomes", {"items": []})
        assert len(pending["items"]) == 1

    def test_update_outcomes_skips_immature(self):
        memory = make_memory_store()
        tracker = PerformanceTracker(memory, make_market_data(110.0))
        # register a very recent rec
        rec = make_recommendation(created_at=_utcnow())
        tracker.register([rec])
        outcomes = tracker.update_outcomes()
        assert outcomes == []

    def test_update_outcomes_evaluates_mature(self):
        memory = make_memory_store()
        md = make_market_data(110.0)
        tracker = PerformanceTracker(memory, md)

        old_time = _utcnow() - timedelta(hours=HOLD_PERIOD_HOURS + 1)
        rec = make_recommendation(created_at=old_time)
        tracker.register([rec])

        # Manually set the entry_time in pending store to be old enough
        pending = memory.load("pending_outcomes", {"items": []})
        pending["items"][0]["entry_time"] = old_time.isoformat(timespec="seconds")
        memory.save("pending_outcomes", pending)

        outcomes = tracker.update_outcomes()
        assert len(outcomes) == 1
        assert outcomes[0].symbol == "AAPL"
        assert outcomes[0].return_pct == pytest.approx(0.10, rel=1e-3)
        assert outcomes[0].is_win is True

    def test_strategy_performance_computed_correctly(self):
        memory = make_memory_store()
        memory.save("trade_outcomes", {
            "outcomes": [
                {"symbol": "AAPL", "signal_types": ["breakout"], "return_pct": 0.05,
                 "is_win": True, "entry_price": 100, "exit_price": 105,
                 "entry_time": "2026-01-01T00:00:00", "checked_at": "2026-01-02T00:00:00",
                 "confidence": 0.7, "id": "AAPL-1"},
                {"symbol": "MSFT", "signal_types": ["breakout"], "return_pct": -0.03,
                 "is_win": False, "entry_price": 200, "exit_price": 194,
                 "entry_time": "2026-01-01T00:00:00", "checked_at": "2026-01-02T00:00:00",
                 "confidence": 0.6, "id": "MSFT-1"},
            ]
        })
        tracker = PerformanceTracker(memory, make_market_data())
        perf = tracker.get_strategy_performance()
        assert "breakout" in perf
        assert perf["breakout"].total_evaluated == 2
        assert perf["breakout"].wins == 1
        assert perf["breakout"].win_rate == 0.5


# ---------------------------------------------------------------------------
# LearningEngine
# ---------------------------------------------------------------------------

class TestLearningEngine:
    def test_extract_lessons_basic(self):
        memory = make_memory_store()
        memory.save("trade_outcomes", {"outcomes": []})
        engine = LearningEngine(memory)
        outcomes = [
            make_outcome("AAPL", 0.08, 0.7),
            make_outcome("MSFT", -0.05, 0.65),
        ]
        lessons = engine.extract_lessons(outcomes, {})
        assert len(lessons) >= 2  # at least one lesson per outcome
        texts = [l.lesson_text for l in lessons]
        assert any("WIN" in t for t in texts)
        assert any("LOSS" in t for t in texts)

    def test_extract_lessons_overconfidence(self):
        memory = make_memory_store()
        memory.save("trade_outcomes", {"outcomes": []})
        engine = LearningEngine(memory)
        outcomes = [make_outcome("AAPL", -0.06, confidence=0.85)]
        lessons = engine.extract_lessons(outcomes, {})
        texts = [l.lesson_text for l in lessons]
        assert any("OVERCONFIDENCE" in t for t in texts)

    def test_lessons_persisted(self):
        memory = make_memory_store()
        memory.save("trade_outcomes", {"outcomes": []})
        engine = LearningEngine(memory)
        engine.extract_lessons([make_outcome("AAPL", 0.03)], {})
        stored = memory.load("lessons", {"lessons": []})
        assert len(stored["lessons"]) >= 1

    def test_get_signal_win_rates(self):
        memory = make_memory_store()
        memory.save("trade_outcomes", {
            "outcomes": [
                {"signal_types": ["breakout", "momentum"], "return_pct": 0.04, "is_win": True},
                {"signal_types": ["breakout", "momentum"], "return_pct": -0.02, "is_win": False},
                {"signal_types": ["breakout", "momentum"], "return_pct": 0.06, "is_win": True},
            ]
        })
        engine = LearningEngine(memory)
        rates = engine.get_signal_win_rates()
        assert "breakout+momentum" in rates
        assert rates["breakout+momentum"]["win_rate"] == pytest.approx(2 / 3, abs=0.001)


# ---------------------------------------------------------------------------
# MetaDecisionEngine
# ---------------------------------------------------------------------------

class TestMetaDecisionEngine:
    def _make_tracker_with_perf(self, perf_data: dict):
        """Build a tracker that returns preset strategy performance."""
        tracker = MagicMock()
        perf = {}
        for name, (wins, losses, returns) in perf_data.items():
            perf[name] = StrategyPerformance(
                strategy_name=name,
                total_evaluated=wins + losses,
                wins=wins,
                losses=losses,
                returns=tuple(returns),
                computed_at=_utcnow(),
            )
        tracker.get_strategy_performance.return_value = perf
        return tracker

    def test_neutral_weight_when_insufficient_data(self):
        tracker = self._make_tracker_with_perf({"breakout": (2, 1, [0.04, -0.02, 0.03])})
        engine = MetaDecisionEngine(tracker)
        weights = engine.strategy_weights()
        # 3 samples < MIN_SAMPLES(5) → neutral
        assert weights.get("breakout", 0.5) == 0.5

    def test_high_win_rate_increases_confidence(self):
        # 8 wins, 2 losses → win rate 0.8
        tracker = self._make_tracker_with_perf(
            {"breakout": (8, 2, [0.05] * 8 + [-0.02] * 2)}
        )
        engine = MetaDecisionEngine(tracker)
        rec = make_recommendation(confidence=0.6)
        adjusted = engine.adjust(rec)
        assert adjusted.confidence > rec.confidence

    def test_low_win_rate_decreases_confidence(self):
        # 2 wins, 8 losses → win rate 0.2
        tracker = self._make_tracker_with_perf(
            {"breakout": (2, 8, [0.03] * 2 + [-0.04] * 8)}
        )
        engine = MetaDecisionEngine(tracker)
        rec = make_recommendation(confidence=0.6)
        adjusted = engine.adjust(rec)
        assert adjusted.confidence < rec.confidence

    def test_adjust_does_not_exceed_bounds(self):
        tracker = self._make_tracker_with_perf(
            {"breakout": (10, 0, [0.05] * 10)}
        )
        engine = MetaDecisionEngine(tracker)
        rec = make_recommendation(confidence=0.95)
        adjusted = engine.adjust(rec)
        assert 0.0 <= adjusted.confidence <= 1.0


# ---------------------------------------------------------------------------
# SelfCritiqueEngine
# ---------------------------------------------------------------------------

class TestSelfCritiqueEngine:
    def _make_report(self, symbols=5, recs=3, errors=0):
        return ScanSummary(
            symbols_scanned=symbols,
            recommendations_count=recs,
            errors_count=errors,
            error_symbols=(),
            duration_seconds=10.0,
        )

    def test_critique_no_outcomes(self):
        memory = make_memory_store()
        engine = SelfCritiqueEngine(memory)
        critique = engine.critique(self._make_report(), [], {})
        assert critique.cycle_id != ""
        assert "No losses" in critique.biggest_mistake
        assert "No wins" in critique.biggest_success

    def test_critique_identifies_worst_loss(self):
        memory = make_memory_store()
        engine = SelfCritiqueEngine(memory)
        outcomes = [
            make_outcome("AAPL", -0.12),
            make_outcome("MSFT", 0.05),
        ]
        critique = engine.critique(self._make_report(), outcomes, {})
        assert "AAPL" in critique.biggest_mistake
        assert "MSFT" in critique.biggest_success

    def test_critique_stored_in_memory(self):
        memory = make_memory_store()
        engine = SelfCritiqueEngine(memory)
        engine.critique(self._make_report(), [], {})
        stored = memory.load("critiques", {"critiques": []})
        assert len(stored["critiques"]) == 1

    def test_overconfidence_bias_detected(self):
        memory = make_memory_store()
        engine = SelfCritiqueEngine(memory)
        # Many high-confidence losses
        outcomes = [make_outcome(f"T{i}", -0.04, confidence=0.82) for i in range(5)]
        critique = engine.critique(self._make_report(), outcomes, {})
        assert "OVERCONFIDENCE" in critique.detected_bias
