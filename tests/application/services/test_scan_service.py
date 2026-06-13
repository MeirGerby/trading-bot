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
from trading_platform.application.services.risk_engine import (
    default_exit_engine,
    default_risk_engine,
)
from trading_platform.application.strategies import BreakoutStrategy, MomentumStrategy
from trading_platform.config import Settings
from trading_platform.config.settings import DEFAULT_RISK_PARAMS
from trading_platform.domain import Bar, OrderSide, OrderStatus, Signal, SignalType
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


def build_exit_service(tmp_path, bars_by_symbol, watchlist, risk_params=None):
    """An auto-executing service with the ExitEngine wired in for sell-path tests."""
    risk_params = risk_params or dict(DEFAULT_RISK_PARAMS)
    settings = Settings(watchlist=tuple(watchlist), risk_params=dict(risk_params))
    market_data = FakeMarketData(bars_by_symbol)
    memory = JsonMemoryStore(tmp_path)
    broker = PaperBroker(memory, 100_000.0)
    pe = PortfolioEngine(risk_params)
    service = ScanService(
        settings=settings,
        strategies=(BreakoutStrategy(market_data), MomentumStrategy(market_data)),
        memory=memory,
        market_data=market_data,
        decision_engine=DecisionEngine(memory),
        portfolio_engine=pe,
        risk_engine=default_risk_engine(risk_params, pe),
        broker=PaperBroker(memory, 100_000.0),
        audit=JsonlAuditLog(tmp_path / "audit.jsonl"),
        auto_execute=True,
        exit_engine=default_exit_engine(risk_params),
    )
    # share the same store
    return service, memory, broker


class TestExitExecution:
    """The scalping exit pipeline: stop-loss / take-profit / signal-decay sells."""

    def _seed_position(self, broker, symbol, qty, entry):
        order = broker.submit_market_order(symbol, OrderSide.BUY, qty, entry)
        assert order.status is OrderStatus.FILLED

    def test_take_profit_triggers_sell(self, tmp_path):
        # Held at 100, current 102 → +2% ≥ 1% micro-target
        service, memory, broker = build_exit_service(
            tmp_path, {"WINNER": make_bars([102.0])}, watchlist=("WINNER",))
        self._seed_position(broker, "WINNER", 10, 100.0)
        service.scan()
        pf = broker.get_portfolio()
        assert all(p.instrument.symbol != "WINNER" for p in pf.positions)
        sells = [t for t in memory.load("trade_log", {"trades": []})["trades"]
                 if t["side"] == "sell"]
        assert len(sells) == 1
        assert "take-profit" in sells[0]["reasoning"]
        assert sells[0]["realized_pnl_pct"] == pytest.approx(2.0)

    def test_stop_loss_triggers_sell(self, tmp_path):
        # Held at 100, current 99 → -1% ≤ -0.5% stop
        service, memory, broker = build_exit_service(
            tmp_path, {"LOSER": make_bars([99.0])}, watchlist=("LOSER",))
        self._seed_position(broker, "LOSER", 10, 100.0)
        service.scan()
        pf = broker.get_portfolio()
        assert all(p.instrument.symbol != "LOSER" for p in pf.positions)
        sells = [t for t in memory.load("trade_log", {"trades": []})["trades"]
                 if t["side"] == "sell"]
        assert len(sells) == 1
        assert "stop-loss" in sells[0]["reasoning"]
        assert sells[0]["realized_pnl_pct"] == pytest.approx(-1.0)

    def test_signal_decay_triggers_sell(self, tmp_path):
        # Held flat (no TP/SL), no momentum/trend signal → decay exit
        service, memory, broker = build_exit_service(
            tmp_path, {"STALE": COLD_BARS}, watchlist=("STALE",))
        # COLD_BARS end at 100.0; buy at 100 so P&L ≈ 0
        self._seed_position(broker, "STALE", 10, 100.0)
        service.scan()
        pf = broker.get_portfolio()
        assert all(p.instrument.symbol != "STALE" for p in pf.positions)
        sells = [t for t in memory.load("trade_log", {"trades": []})["trades"]
                 if t["side"] == "sell"]
        assert len(sells) == 1
        assert "signal-decay" in sells[0]["reasoning"]

    def test_exit_runs_before_entry_no_churn(self, tmp_path):
        # HOT both: held at 100 (current 130 → take-profit) AND generates fresh
        # buy signals. Exit must run first and the name must NOT be re-bought.
        service, memory, broker = build_exit_service(
            tmp_path, {"HOT": HOT_BARS}, watchlist=("HOT",))
        self._seed_position(broker, "HOT", 10, 100.0)
        service.scan()
        pf = broker.get_portfolio()
        assert all(p.instrument.symbol != "HOT" for p in pf.positions), \
            "HOT should be sold and not re-bought in the same cycle"
        sells = [t for t in memory.load("trade_log", {"trades": []})["trades"]
                 if t["side"] == "sell"]
        assert len(sells) == 1
        assert "take-profit" in sells[0]["reasoning"]

    def test_no_exit_when_within_thresholds(self, tmp_path):
        # Held at 100, current 100.2 → +0.2% (below 1% TP, above -0.5% SL),
        # and still trend-supported → keep holding.
        service, memory, broker = build_exit_service(
            tmp_path, {"HOT": HOT_BARS}, watchlist=("HOT",))
        # Buy HOT at 129.5 so current 130 is only +0.39% (within thresholds)
        self._seed_position(broker, "HOT", 10, 129.5)
        service.scan()
        pf = broker.get_portfolio()
        assert any(p.instrument.symbol == "HOT" for p in pf.positions)
        sells = [t for t in memory.load("trade_log", {"trades": []})["trades"]
                 if t["side"] == "sell"]
        assert sells == []

    def test_exit_order_audited(self, tmp_path):
        service, _, broker = build_exit_service(
            tmp_path, {"WINNER": make_bars([102.0])}, watchlist=("WINNER",))
        self._seed_position(broker, "WINNER", 10, 100.0)
        service.scan()
        audit = JsonlAuditLog(tmp_path / "audit.jsonl")
        events = [e for e in audit.tail(50) if e["event"] == "exit_order"]
        assert events
        assert events[-1]["payload"]["symbol"] == "WINNER"
