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
    SymbolRepositoryPort,
)
from trading_platform.application.services.decision_engine import DecisionEngine
from trading_platform.application.services.learning_engine import LearningEngine
from trading_platform.application.services.meta_decision_engine import MetaDecisionEngine
from trading_platform.application.services.performance_tracker import PerformanceTracker
from trading_platform.application.services.portfolio_engine import PortfolioEngine
from trading_platform.application.services.risk_engine import (
    ExitEngine,
    RiskEngine,
    default_exit_engine,
    default_risk_engine,
)
from trading_platform.application.services.self_critique_engine import (
    ScanSummary,
    SelfCritiqueEngine,
)
from trading_platform.config import Settings
from trading_platform.domain import (
    Instrument,
    Order,
    OrderSide,
    Recommendation,
    Signal,
    SignalType,
    market_for_symbol,
)

logger = logging.getLogger(__name__)

SCAN_RESULTS_KEY = "scan_results"
WEIGHTS_KEY = "weights"
TRADE_LOG_KEY = "trade_log"
RISK_OVERRIDES_KEY = "risk_overrides"   # written by IdeaEngine; read at scan time
MAX_TRADE_LOG = 200

# Signals whose presence justifies *holding* an open position. When none of
# these are active for a held symbol, the signal-decay exit fires.
TREND_SUPPORTING_SIGNALS = frozenset({SignalType.MOMENTUM, SignalType.TREND_FOLLOWING})


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
                 auto_execute: bool = False,
                 symbol_repository: SymbolRepositoryPort | None = None,
                 exit_engine: ExitEngine | None = None):
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
        self._symbols = symbol_repository
        self._exit_engine = exit_engine

    def active_watchlist(self) -> tuple[str, ...]:
        """Dynamic watchlist from the symbol repository; settings fallback.

        Read at scan time, so dashboard pins/unpins apply without restart.
        """
        if self._symbols is not None:
            try:
                watchlist = self._symbols.get_watchlist()
                if watchlist:
                    return watchlist
            except Exception:
                logger.exception("symbol repository unavailable, using settings watchlist")
        return self._settings.watchlist

    def current_params(self) -> dict[str, float]:
        merged = dict(self._settings.strategy_params)
        merged.update(self._memory.load(WEIGHTS_KEY, {}))
        return merged

    def effective_risk_params(self) -> dict[str, float]:
        """Risk params in force this cycle: settings defaults + IdeaEngine overrides.

        Used by the dashboard to surface live take-profit / stop-loss targets.
        """
        merged = dict(self._settings.risk_params)
        merged.update(self._memory.load(RISK_OVERRIDES_KEY, {}))
        return merged

    def _resolve_runtime_params(
        self,
    ) -> tuple[PortfolioEngine, RiskEngine, ExitEngine | None]:
        """Return (possibly fresh) engines reflecting any IdeaEngine risk overrides.

        If the IdeaEngine wrote changes to RISK_OVERRIDES_KEY since the last
        build, we reconstruct lightweight engine instances for this scan only.
        The module-level instances (self._portfolio etc.) are kept as defaults.
        """
        overrides = self._memory.load(RISK_OVERRIDES_KEY, {})
        if not overrides:
            return self._portfolio, self._risk, self._exit_engine
        merged = {**self._settings.risk_params, **overrides}
        pe = PortfolioEngine(merged)
        re = default_risk_engine(merged, pe)
        ee = default_exit_engine(merged) if self._exit_engine is not None else None
        return pe, re, ee

    def scan(self) -> ScanReport:
        started = datetime.now(timezone.utc)
        params = self.current_params()
        portfolio_engine, risk_engine, exit_engine = self._resolve_runtime_params()
        portfolio = self._broker.get_portfolio()
        watchlist = self.active_watchlist()
        self._audit.record("scan_started", {"symbols": len(watchlist)})

        # Evaluate pending outcomes before generating new recommendations
        new_outcomes = []
        if self._tracker is not None:
            try:
                new_outcomes = self._tracker.update_outcomes()
            except Exception:
                logger.exception("performance tracker update_outcomes failed")

        recommendations: list[Recommendation] = []
        errors: list[str] = []

        batch_size = max(1, int(self._settings.scan_batch_size))
        throttle = max(0.0, float(self._settings.scan_throttle_seconds))

        # ── Phase 1: evaluate every strategy across the watchlist ──────────
        # Collect signals per symbol up front so the exit pass can run before
        # any new entry consumes cash, and so signal-decay sees the live state.
        signals_by_symbol: dict[str, list[Signal]] = {}
        trend_supporting_symbols: set[str] = set()

        for i, symbol in enumerate(watchlist):
            # Rate-limit guard for large dynamic watchlists: pause between batches
            if i and i % batch_size == 0 and throttle > 0:
                time.sleep(throttle)

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

            if not signals:
                continue
            signals_by_symbol[symbol] = signals
            if any(s.signal_type in TREND_SUPPORTING_SIGNALS for s in signals):
                trend_supporting_symbols.add(symbol)

        # ── Phase 2: exit pass — inspect open positions BEFORE new entries ──
        # Stop-loss / take-profit / signal-decay liquidations free up capital
        # and prevent re-buying a name we are simultaneously exiting.
        exited_symbols: set[str] = set()
        if self._auto_execute and exit_engine is not None:
            exited_symbols = self._exit_pass(trend_supporting_symbols, exit_engine)
            portfolio = self._broker.get_portfolio()  # refresh after liquidations

        # ── Phase 3: entry evaluation for symbols above the alert threshold ─
        for symbol, signals in signals_by_symbol.items():
            if len({s.signal_type for s in signals}) < params["min_score_to_alert"]:
                continue

            price = self._market_data.get_last_price(symbol)
            if price is None or price <= 0:
                errors.append(f"{symbol}: no price available")
                continue

            instrument = Instrument(symbol=symbol)
            rec = self._decision.build(instrument, tuple(signals), price)

            # Meta decision: adjust confidence by strategy competition ranking
            if self._meta is not None:
                try:
                    rec = self._meta.adjust(rec)
                except Exception:
                    logger.exception("meta decision engine adjust failed for %s", symbol)

            quantity = portfolio_engine.propose_quantity(portfolio, price, rec.confidence, symbol)
            rec = replace(rec, proposed_quantity=quantity)
            rec = risk_engine.review(rec, portfolio)
            recommendations.append(rec)

            self._audit.record("recommendation", recommendation_to_dict(rec))
            if not rec.approved:
                failed = [c.rule_name for c in rec.risk_checks if not c.passed]
                self._audit.record("risk_rejected", {"ticker": symbol, "rules": failed})

            # Autonomous paper execution: approved + sized + not already held +
            # not a name we just liquidated this cycle (avoids immediate churn).
            if (self._auto_execute and rec.approved and rec.proposed_quantity > 0
                    and symbol not in exited_symbols
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
            symbols_scanned=len(watchlist),
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

    def _log_exit_trade(self, order: Order, reason: str,
                        realized_pnl: float, realized_pnl_pct: float) -> None:
        store = self._memory.load(TRADE_LOG_KEY, {"trades": []})
        store["trades"].append({
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "order_id": order.id,
            "symbol": order.symbol,
            "market": market_for_symbol(order.symbol).value,
            "side": order.side.value,
            "quantity": order.quantity,
            "price": order.price,
            "value": round(order.quantity * order.price, 2),
            "status": order.status.value,
            "reject_reason": order.reason,
            "confidence": 1.0,
            "signal_types": ["exit"],
            "realized_pnl": round(realized_pnl, 2),
            "realized_pnl_pct": round(realized_pnl_pct * 100, 2),
            "reasoning": reason,
        })
        store["trades"] = store["trades"][-MAX_TRADE_LOG:]
        self._memory.save(TRADE_LOG_KEY, store)

    def _exit_pass(self, trend_supporting_symbols: set[str],
                   exit_engine: ExitEngine) -> set[str]:
        """Liquidate open positions hitting stop-loss / take-profit / signal-decay.

        Returns the set of symbols that were successfully sold this cycle, so
        the entry phase can avoid re-buying them.
        """
        portfolio = self._broker.get_portfolio()
        exited: set[str] = set()
        if not portfolio.positions:
            return exited

        current_prices: dict[str, float] = {}
        for pos in portfolio.positions:
            p = self._market_data.get_last_price(pos.instrument.symbol)
            if p is not None:
                current_prices[pos.instrument.symbol] = p

        exits = exit_engine.check_exits(portfolio, current_prices, trend_supporting_symbols)

        for symbol, reason in exits:
            pos = next((p for p in portfolio.positions if p.instrument.symbol == symbol), None)
            if pos is None:
                continue
            price = current_prices.get(symbol)
            if price is None:
                continue
            # Realized P&L in vendor-native units; pct is currency-agnostic.
            realized_pnl = (price - pos.avg_entry_price) * pos.quantity
            realized_pnl_pct = (
                (price - pos.avg_entry_price) / pos.avg_entry_price
                if pos.avg_entry_price > 0 else 0.0
            )
            try:
                order = self._broker.submit_market_order(
                    symbol, OrderSide.SELL, pos.quantity, price)
                self._audit.record("exit_order", {
                    "symbol": symbol, "reason": reason,
                    "quantity": pos.quantity, "price": price,
                    "realized_pnl_pct": round(realized_pnl_pct * 100, 2),
                    "status": order.status.value, "reject_reason": order.reason,
                })
                self._log_exit_trade(order, reason, realized_pnl, realized_pnl_pct)
                if order.status.value == "filled":
                    exited.add(symbol)
                portfolio = self._broker.get_portfolio()  # refresh after each exit
            except Exception:
                logger.exception("exit execution failed for %s", symbol)

        return exited

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
