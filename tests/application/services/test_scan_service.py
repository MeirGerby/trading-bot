"""Unit + integration tests for ScanService.

Integration test wires real engines, JsonMemoryStore, PaperBroker and
JsonlAuditLog with fake market/options data — the full pipeline minus
network.
"""
from datetime import datetime, timedelta, timezone

import pytest

from trading_platform.application.services import (
    DecisionEngine,
    PortfolioEngine,
    ScanService,
)
from trading_platform.application.services.risk_engine import default_risk_engine
from trading_platform.application.strategies import BreakoutStrategy, MomentumStrategy
from trading_platform.config import Settings
from trading_platform.config.settings import DEFAULT_RISK_PARAMS
from trading_platform.domain import Bar, OrderStatus, Signal, SignalType
from trading_platform.infrastructure.audit import JsonlAuditLog
from trading_platform.infrastructure.memory_store import JsonMemoryStore
from trading_platform.infrastructure.paper_broker import PaperBroker


def make_bars(closes, volumes=None):
    volumes = volumes or [1_000_000.0] * len(closes)
    start = datetime(2025, 6, 1, tzinfo=timezone.utc)
    return [Bar(time=start + timedelta(days=i), open=c, high=c * 1.01, low=c * 0.99,
                close=c, volume=v)
            for i, (c, v) in enumerate(zip(closes, volumes))]


# Bars that trigger BOTH breakout (3x volume at new high) and momentum (rally).
HOT_BARS = make_bars(
    [100.0 + i * 0.5 for i in range(50)] + [130.0],
    [1_000_000.0] * 50 + [3_000_000.0],
)
# Flat, quiet tape — no signals.
COLD_BARS = make_bars([100.0, 99.0] * 25 + [100.0])


class FakeMarketData:
    def __init__(self, bars_by_symbol):
        self.bars_by_symbol = bars_by_symbol

    def get_daily_bars(self, symbol, lookback_days):
        return self.bars_by_symbol.get(symbol, [])[-lookback_days:]

    def get_last_price(self, symbol):
        bars = self.bars_by_symbol.get(symbol, [])
        return bars[-1].close if bars else None


class FailingStrategy:
    name = "failing"

    def evaluate(self, instrument, params):
        raise ConnectionError("vendor down")


def build_service(tmp_path, watchlist=("HOT", "COLD"), extra_strategies=()):
    settings = Settings(watchlist=tuple(watchlist))
    market_data = FakeMarketData({"HOT": HOT_BARS, "COLD": COLD_BARS})
    memory = JsonMemoryStore(tmp_path)
    strategies = (BreakoutStrategy(market_data), MomentumStrategy(market_data),
                  *extra_strategies)
    return ScanService(
        settings=settings,
        strategies=strategies,
        memory=memory,
        market_data=market_data,
        decision_engine=DecisionEngine(memory),
        portfolio_engine=PortfolioEngine(DEFAULT_RISK_PARAMS),
        risk_engine=default_risk_engine(DEFAULT_RISK_PARAMS),
        broker=PaperBroker(memory, 100_000.0),
        audit=JsonlAuditLog(tmp_path / "audit.jsonl"),
    ), memory, JsonlAuditLog(tmp_path / "audit.jsonl")


class TestScanPipeline:
    def test_recommends_only_symbols_above_min_score(self, tmp_path):
        service, _, _ = build_service(tmp_path)
        report = service.scan()
        assert [r.instrument.symbol for r in report.recommendations] == ["HOT"]
        assert report.symbols_scanned == 2

    def test_recommendation_is_sized_and_risk_checked(self, tmp_path):
        service, _, _ = build_service(tmp_path)
        rec = service.scan().recommendations[0]
        assert rec.score == 2
        assert 0 < rec.confidence <= 1
        assert rec.proposed_quantity > 0
        assert len(rec.risk_checks) == 3
        assert rec.approved

    def test_results_persisted_for_restart(self, tmp_path):
        service, memory, _ = build_service(tmp_path)
        service.scan()
        stored = memory.load("scan_results", {})
        assert stored["recommendations"][0]["ticker"] == "HOT"
        assert stored["recommendations"][0]["approved"] is True
        assert service.last_scan_results() == stored

    def test_audit_trail_written(self, tmp_path):
        service, _, audit = build_service(tmp_path)
        service.scan()
        events = [e["event"] for e in audit.tail(20)]
        assert events[0] == "scan_started"
        assert "recommendation" in events
        assert events[-1] == "scan_completed"

    def test_strategy_failure_is_isolated(self, tmp_path):
        service, _, _ = build_service(tmp_path, extra_strategies=(FailingStrategy(),))
        report = service.scan()
        # HOT still recommended despite the failing strategy
        assert [r.instrument.symbol for r in report.recommendations] == ["HOT"]
        assert any("vendor down" in e for e in report.errors)

    def test_learned_weights_override_defaults(self, tmp_path):
        service, memory, _ = build_service(tmp_path)
        memory.save("weights", {"min_score_to_alert": 3})  # require all 3 types
        report = service.scan()
        assert report.recommendations == ()

    def test_feedback_history_affects_confidence(self, tmp_path):
        service, memory, _ = build_service(tmp_path)
        baseline = service.scan().recommendations[0].confidence
        memory.save("feedback", {"history": [
            {"signals": ["breakout", "momentum"], "positive": False}] * 5})
        lowered = service.scan().recommendations[0].confidence
        assert lowered < baseline


class TestExecution:
    def test_execute_approved_fills_paper_order(self, tmp_path):
        service, _, audit = build_service(tmp_path)
        rec = service.scan().recommendations[0]
        order = service.execute(rec)
        assert order.status is OrderStatus.FILLED
        assert any(e["event"] == "order" for e in audit.tail(30))

    def test_execute_updates_paper_portfolio(self, tmp_path):
        service, memory, _ = build_service(tmp_path)
        rec = service.scan().recommendations[0]
        service.execute(rec)
        broker = PaperBroker(memory, 100_000.0)
        pf = broker.get_portfolio()
        assert pf.positions[0].instrument.symbol == "HOT"
        assert pf.cash == pytest.approx(100_000.0 - rec.proposed_quantity * rec.price)

    def test_execute_rejects_unapproved(self, tmp_path):
        from dataclasses import replace

        from trading_platform.domain import RiskCheckResult
        service, _, _ = build_service(tmp_path)
        rec = service.scan().recommendations[0]
        bad = replace(rec, risk_checks=(RiskCheckResult("x", False, "nope"),))
        with pytest.raises(ValueError, match="failed risk checks"):
            service.execute(bad)

    def test_execute_rejects_zero_quantity(self, tmp_path):
        from dataclasses import replace
        service, _, _ = build_service(tmp_path)
        rec = replace(service.scan().recommendations[0], proposed_quantity=0.0)
        with pytest.raises(ValueError, match="zero quantity"):
            service.execute(rec)
