"""ScanService — the single orchestration entry point for market scanning.

Pipeline per scan:
  1. merge configured defaults with learned weights (memory)
  2. load portfolio state (broker)
  3. evaluate every enabled strategy per watchlist symbol
  4. aggregate signals; drop symbols below min_score_to_alert
  5. DecisionEngine scores confidence and builds the Recommendation
  6. PortfolioEngine sizes the position
  7. RiskEngine attaches pass/fail checks
  8. persist results to memory; write audit events

bot.py and dashboard.py are thin adapters over this service.
Execution (paper only) is deliberately NOT part of scan(): scanning informs,
execute() acts, and only on an approved recommendation.
"""
import logging
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from trading_platform.application.ports import (
    AuditLogPort,
    BrokerPort,
    MarketDataPort,
    MemoryStore,
    Strategy,
)
from trading_platform.application.services.decision_engine import DecisionEngine
from trading_platform.application.services.learning_engine import LearningEngine
from trading_platform.application.services.meta_decision_engine import MetaDecisionEngine
from trading_platform.application.services.performance_tracker import PerformanceTracker
from trading_platform.application.services.portfolio_engine import PortfolioEngine
from trading_platform.application.services.risk_engine import RiskEngine
from trading_platform.application.services.self_critique_engine import (
    ScanSummary,
    SelfCritiqueEngine,
)
from trading_platform.config import Settings
from trading_platform.domain import Instrument, Order, OrderSide, Recommendation, Signal

logger = logging.getLogger(__name__)

SCAN_RESULTS_KEY = "scan_results"
WEIGHTS_KEY = "weights"
TRADE_LOG_KEY = "trade_log"
MAX_TRADE_LOG = 200


def build_reasoning(rec: Recommendation) -> str:
    """Human-readable 'why this decision' text for the explainability panel.

    Composed entirely from data already on the Recommendation, so it is
    always in sync with what the engines actually decided.
    """
    lines: list[str] = []
    direction = "BUY" if rec.direction.value == "long" else "SELL"
    lines.append(
        f"{direction} {rec.instrument.symbol} @ {rec.price:.2f} "
        f"({rec.instrument.market.value}) — confidence {rec.confidence:.0%}"
    )

    for sig in rec.signals:
        detail = ", ".join(f"{k}={v}" for k, v in sig.details.items())
        lines.append(
            f"• {sig.signal_type.value}: strength {sig.strength:.2f}"
            + (f" ({detail})" if detail else "")
        )

    lines.append(f"• Decision logic: {rec.rationale}")

    if rec.proposed_quantity > 0:
        lines.append(
            f"• Position sizing: {rec.proposed_quantity:g} shares ≈ "
            f"{rec.proposed_quantity * rec.price:,.0f} "
            f"(equity × base allocation × confidence)"
        )

    if rec.risk_checks:
        passed = [c for c in rec.risk_checks if c.passed]
        failed = [c for c in rec.risk_checks if not c.passed]
        lines.append(f"• Risk review: {len(passed)}/{len(rec.risk_checks)} checks passed")
        for c in failed:
            lines.append(f"  ✗ {c.rule_name}: {c.reason}")

    return "\n".join(lines)


def recommendation_to_dict(rec: Recommendation) -> dict:
    """Legacy-compatible serialization (ticker/score/signal_types/price/details)
    plus the new decision fields."""
    details: dict[str, str] = {}
    for sig in rec.signals:
        details.update(sig.details)
    return {
        "ticker": rec.instrument.symbol,
        "market": rec.instrument.market.value,
        "score": rec.score,
        "signal_types": [t.value for t in sorted({s.signal_type for s in rec.signals},
                                                 key=lambda t: t.value)],
        "price": rec.price,
        "details": details,
        "confidence": round(rec.confidence, 3),
        "rationale": rec.rationale,
        "reasoning": build_reasoning(rec),
        "proposed_quantity": rec.proposed_quantity,
        "approved": rec.approved,
        "risk_checks": [
            {"rule": c.rule_name, "passed": c.passed, "reason": c.reason}
            for c in rec.risk_checks
        ],
        "timestamp": rec.created_at.isoformat(timespec="seconds"),
    }


@dataclass(frozen=True)
class ScanReport:
    recommendations: tuple[Recommendation, ...]
    started_at: datetime
    finished_at: datetime
    symbols_scanned: int
    errors: tuple[str, ...] = ()

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()


class ScanService:
    def __init__(self, settings: Settings, strategies: tuple[Strategy, ...],
                 memory: MemoryStore, market_data: MarketDataPort,
                 decision_engine: DecisionEngine, portfolio_engine: PortfolioEngine,
                 risk_engine: RiskEngine, broker: BrokerPort, audit: AuditLogPort,
                 performance_tracker: PerformanceTracker | None = None,
                 meta_decision_engine: MetaDecisionEngine | None = None,
                 learning_engine: LearningEngine | None = None,
                 self_critique_engine: SelfCritiqueEngine | None = None,
                 auto_execute: bool = False):
        self._settings = settings
        self._strategies = strategies
        self._memory = memory
        self._market_data = market_data
        self._decision = decision_engine
        self._portfolio = portfolio_engine
        self._risk = risk_engine
        self._broker = broker
        self._audit = audit
        self._tracker = performance_tracker
        self._meta = meta_decision_engine
        self._learning = learning_engine
        self._critique = self_critique_engine
        # Auto-execution acts only through the injected broker, which is
        # PaperBroker in bootstrap (ADR-5: live trading needs owner approval).
        self._auto_execute = auto_execute

    def current_params(self) -> dict[str, float]:
        merged = dict(self._settings.strategy_params)
        merged.update(self._memory.load(WEIGHTS_KEY, {}))
        return merged

    def scan(self) -> ScanReport:
        started = datetime.now(timezone.utc)
        params = self.current_params()
        portfolio = self._broker.get_portfolio()
        self._audit.record("scan_started", {"symbols": len(self._settings.watchlist)})

        # Evaluate pending outcomes before generating new recommendations
        new_outcomes = []
        if self._tracker is not None:
            try:
                new_outcomes = self._tracker.update_outcomes()
            except Exception:
                logger.exception("performance tracker update_outcomes failed")

        recommendations: list[Recommendation] = []
        errors: list[str] = []

        for symbol in self._settings.watchlist:
            instrument = Instrument(symbol=symbol)
            signals: list[Signal] = []
            for strategy in self._strategies:
                try:
                    sig = strategy.evaluate(instrument, params)
                except Exception as exc:
                    msg = f"{symbol}/{strategy.name}: {exc}"
                    errors.append(msg)
                    logger.exception("strategy failed: %s", msg)
                    continue
                if sig is not None:
                    signals.append(sig)

            if len({s.signal_type for s in signals}) < params["min_score_to_alert"]:
                continue

            price = self._market_data.get_last_price(symbol)
            if price is None or price <= 0:
                errors.append(f"{symbol}: no price available")
                continue

            rec = self._decision.build(instrument, tuple(signals), price)

            # Meta decision: adjust confidence by strategy competition ranking
            if self._meta is not None:
                try:
                    rec = self._meta.adjust(rec)
                except Exception:
                    logger.exception("meta decision engine adjust failed for %s", symbol)

            quantity = self._portfolio.propose_quantity(portfolio, price, rec.confidence)
            rec = replace(rec, proposed_quantity=quantity)
            rec = self._risk.review(rec, portfolio)
            recommendations.append(rec)

            self._audit.record("recommendation", recommendation_to_dict(rec))
            if not rec.approved:
                failed = [c.rule_name for c in rec.risk_checks if not c.passed]
                self._audit.record("risk_rejected", {"ticker": symbol, "rules": failed})

            # Autonomous paper execution: approved + sized + not already held
            if (self._auto_execute and rec.approved and rec.proposed_quantity > 0
                    and not any(p.instrument.symbol == symbol for p in portfolio.positions)):
                try:
                    order = self.execute(rec)
                    self._log_trade(order, rec)
                    portfolio = self._broker.get_portfolio()  # refresh cash for next risk checks
                except Exception:
                    logger.exception("auto-execute failed for %s", symbol)

        recommendations.sort(key=lambda r: (r.score, r.confidence), reverse=True)
        finished = datetime.now(timezone.utc)

        self._memory.save(SCAN_RESULTS_KEY, {
            "timestamp": finished.isoformat(timespec="seconds"),
            "recommendations": [recommendation_to_dict(r) for r in recommendations],
        })
        self._audit.record("scan_completed", {
            "recommendations": len(recommendations),
            "errors": len(errors),
            "duration_seconds": round((finished - started).total_seconds(), 2),
        })

        report = ScanReport(
            recommendations=tuple(recommendations),
            started_at=started,
            finished_at=finished,
            symbols_scanned=len(self._settings.watchlist),
            errors=tuple(errors),
        )

        # Register new recommendations for future outcome tracking
        if self._tracker is not None:
            try:
                self._tracker.register(list(recommendations))
            except Exception:
                logger.exception("performance tracker register failed")

        # Extract lessons from newly completed outcomes
        if self._learning is not None and new_outcomes:
            try:
                strategy_perf = (
                    self._tracker.get_strategy_performance()
                    if self._tracker else {}
                )
                lessons = self._learning.extract_lessons(new_outcomes, strategy_perf)
                if lessons:
                    self._audit.record("lessons_extracted", {"count": len(lessons)})
            except Exception:
                logger.exception("learning engine extract_lessons failed")

        # Post-cycle self-critique
        if self._critique is not None:
            try:
                strategy_perf = (
                    self._tracker.get_strategy_performance()
                    if self._tracker else {}
                )
                summary = ScanSummary(
                    symbols_scanned=report.symbols_scanned,
                    recommendations_count=len(report.recommendations),
                    errors_count=len(report.errors),
                    error_symbols=tuple(
                        e.split(":")[0].strip() for e in report.errors
                    ),
                    duration_seconds=report.duration_seconds,
                )
                self._critique.critique(summary, new_outcomes, strategy_perf)
            except Exception:
                logger.exception("self critique engine failed")

        return report

    def last_scan_results(self) -> dict:
        """Persisted results of the most recent scan (survives restarts)."""
        return self._memory.load(SCAN_RESULTS_KEY, {"timestamp": "", "recommendations": []})

    def recent_trades(self, n: int = 50) -> list[dict]:
        """Most recent autonomous (paper) executions, newest first."""
        trades = self._memory.load(TRADE_LOG_KEY, {"trades": []})["trades"]
        return list(reversed(trades[-n:]))

    def _log_trade(self, order: Order, rec: Recommendation) -> None:
        store = self._memory.load(TRADE_LOG_KEY, {"trades": []})
        store["trades"].append({
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "order_id": order.id,
            "symbol": order.symbol,
            "market": rec.instrument.market.value,
            "side": order.side.value,
            "quantity": order.quantity,
            "price": order.price,
            "value": round(order.quantity * order.price, 2),
            "status": order.status.value,
            "reject_reason": order.reason,
            "confidence": round(rec.confidence, 3),
            "signal_types": [t.value for t in sorted({s.signal_type for s in rec.signals},
                                                     key=lambda t: t.value)],
            "reasoning": build_reasoning(rec),
        })
        store["trades"] = store["trades"][-MAX_TRADE_LOG:]
        self._memory.save(TRADE_LOG_KEY, store)

    def execute(self, rec: Recommendation) -> Order:
        """Execute an approved recommendation on the (paper) broker."""
        if not rec.approved:
            raise ValueError("cannot execute a recommendation that failed risk checks")
        if rec.proposed_quantity <= 0:
            raise ValueError("cannot execute a recommendation with zero quantity")
        if rec.direction.value != "long":
            raise ValueError("paper broker supports long entries only")

        order = self._broker.submit_market_order(
            rec.instrument.symbol, OrderSide.BUY, rec.proposed_quantity, rec.price)
        self._audit.record("order", {
            "id": order.id, "symbol": order.symbol, "side": order.side.value,
            "quantity": order.quantity, "price": order.price,
            "status": order.status.value, "reason": order.reason,
        })
        return order
