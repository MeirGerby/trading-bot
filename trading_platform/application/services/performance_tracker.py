"""PerformanceTracker — records recommendation outcomes and computes strategy metrics.

After every scan:
- New recommendations are registered as "pending" (entry price + timestamp stored).
- Pending recommendations older than HOLD_PERIOD_HOURS have their current price
  looked up; the return is computed and stored as a completed TradeOutcome.

Per-strategy metrics (win rate, Sharpe, etc.) are derived on-the-fly from
all stored outcomes.
"""
import logging
from datetime import datetime, timedelta, timezone

from trading_platform.application.ports import MarketDataPort, MemoryStore
from trading_platform.domain.models import Recommendation, StrategyPerformance, TradeOutcome

logger = logging.getLogger(__name__)

PENDING_KEY = "pending_outcomes"
OUTCOMES_KEY = "trade_outcomes"
HOLD_PERIOD_HOURS = 24  # evaluate return 24 h after recommendation


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PerformanceTracker:
    """Tracks recommendation outcomes and computes per-strategy performance metrics."""

    def __init__(self, memory: MemoryStore, market_data: MarketDataPort):
        self._memory = memory
        self._market_data = market_data

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, recommendations: list[Recommendation]) -> None:
        """Store new recommendations as pending for later outcome evaluation."""
        pending_store = self._memory.load(PENDING_KEY, {"items": []})
        existing_ids = {p["id"] for p in pending_store["items"]}
        now = _utcnow()

        for rec in recommendations:
            rec_id = f"{rec.instrument.symbol}-{rec.created_at.isoformat(timespec='seconds')}"
            if rec_id in existing_ids:
                continue
            pending_store["items"].append({
                "id": rec_id,
                "symbol": rec.instrument.symbol,
                "entry_price": rec.price,
                "entry_time": rec.created_at.isoformat(timespec="seconds"),
                "signal_types": [s.signal_type.value for s in rec.signals],
                "confidence": round(rec.confidence, 4),
                "registered_at": now.isoformat(timespec="seconds"),
            })

        self._memory.save(PENDING_KEY, pending_store)

    # ------------------------------------------------------------------
    # Outcome evaluation
    # ------------------------------------------------------------------

    def update_outcomes(self) -> list[TradeOutcome]:
        """Check prices for pending recs that have matured; return new outcomes."""
        pending_store = self._memory.load(PENDING_KEY, {"items": []})
        outcomes_store = self._memory.load(OUTCOMES_KEY, {"outcomes": []})

        now = _utcnow()
        cutoff = now - timedelta(hours=HOLD_PERIOD_HOURS)

        remaining: list[dict] = []
        new_outcomes: list[TradeOutcome] = []

        for item in pending_store["items"]:
            entry_time = datetime.fromisoformat(item["entry_time"])
            if entry_time.tzinfo is None:
                entry_time = entry_time.replace(tzinfo=timezone.utc)

            if entry_time > cutoff:
                remaining.append(item)
                continue

            symbol = item["symbol"]
            current_price = self._market_data.get_last_price(symbol)
            if current_price is None or current_price <= 0:
                remaining.append(item)
                continue

            entry_price = item["entry_price"]
            return_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0.0

            outcome = TradeOutcome(
                id=item["id"],
                symbol=symbol,
                entry_price=entry_price,
                exit_price=round(current_price, 4),
                entry_time=entry_time,
                checked_at=now,
                signal_types=tuple(item["signal_types"]),
                confidence=item["confidence"],
                return_pct=round(return_pct, 6),
            )
            new_outcomes.append(outcome)
            outcomes_store["outcomes"].append({
                "id": outcome.id,
                "symbol": outcome.symbol,
                "entry_price": outcome.entry_price,
                "exit_price": outcome.exit_price,
                "entry_time": outcome.entry_time.isoformat(timespec="seconds"),
                "checked_at": outcome.checked_at.isoformat(timespec="seconds"),
                "signal_types": list(outcome.signal_types),
                "confidence": outcome.confidence,
                "return_pct": outcome.return_pct,
                "is_win": outcome.is_win,
            })

        if new_outcomes:
            self._memory.save(PENDING_KEY, {"items": remaining})
            self._memory.save(OUTCOMES_KEY, outcomes_store)

        return new_outcomes

    # ------------------------------------------------------------------
    # Performance metrics
    # ------------------------------------------------------------------

    def get_all_outcomes(self) -> list[dict]:
        return self._memory.load(OUTCOMES_KEY, {"outcomes": []})["outcomes"]

    def get_strategy_performance(self) -> dict[str, StrategyPerformance]:
        """Compute per-strategy metrics from all completed outcomes."""
        outcomes = self.get_all_outcomes()
        now = _utcnow()
        buckets: dict[str, list[float]] = {}

        for outcome in outcomes:
            for sig_type in outcome.get("signal_types", []):
                buckets.setdefault(sig_type, []).append(outcome["return_pct"])

        result: dict[str, StrategyPerformance] = {}
        for strategy_name, returns in buckets.items():
            wins = sum(1 for r in returns if r > 0)
            losses = sum(1 for r in returns if r <= 0)
            result[strategy_name] = StrategyPerformance(
                strategy_name=strategy_name,
                total_evaluated=len(returns),
                wins=wins,
                losses=losses,
                returns=tuple(returns),
                computed_at=now,
            )
        return result

    def get_summary_dict(self) -> dict:
        perf = self.get_strategy_performance()
        pending_count = len(self._memory.load(PENDING_KEY, {"items": []})["items"])
        outcomes_count = len(self.get_all_outcomes())
        return {
            "total_evaluated": outcomes_count,
            "pending": pending_count,
            "strategies": {
                name: {
                    "total": sp.total_evaluated,
                    "wins": sp.wins,
                    "losses": sp.losses,
                    "win_rate": round(sp.win_rate, 3),
                    "avg_return_pct": round(sp.avg_return_pct * 100, 2),
                    "sharpe": round(sp.sharpe_ratio, 3),
                    "max_drawdown_pct": round(sp.max_drawdown_pct * 100, 2),
                    "profit_factor": round(sp.profit_factor, 3) if sp.profit_factor != float("inf") else None,
                }
                for name, sp in sorted(perf.items(), key=lambda x: -x[1].win_rate)
            },
        }
