"""MetaDecisionEngine — adjusts recommendation confidence based on strategy performance.

Strategy Competition:
- Each strategy (breakout, momentum, options, mean_reversion, trend_following) is
  ranked by historical win rate and Sharpe ratio.
- The confidence of a Recommendation is up/down-weighted based on the performance
  of the strategies that generated its signals.
- With insufficient data (< MIN_SAMPLES), falls back to equal weighting (neutral).
"""
from dataclasses import replace

from trading_platform.application.services.performance_tracker import PerformanceTracker
from trading_platform.domain.models import Recommendation

MIN_SAMPLES = 5       # minimum outcomes before strategy weight departs from neutral
NEUTRAL_WEIGHT = 0.5  # weight when no data, maps to no confidence adjustment


class MetaDecisionEngine:
    def __init__(self, tracker: PerformanceTracker):
        self._tracker = tracker

    # ------------------------------------------------------------------
    # Strategy weights (0–1 scale; 0.5 = neutral)
    # ------------------------------------------------------------------

    def strategy_weights(self) -> dict[str, float]:
        """Derive per-strategy weight from historical win rate.

        Weight = 0.5 when no data or balanced; higher for consistently
        profitable strategies; lower for consistently losing ones.
        """
        perf = self._tracker.get_strategy_performance()
        weights: dict[str, float] = {}
        for name, sp in perf.items():
            if sp.total_evaluated < MIN_SAMPLES:
                weights[name] = NEUTRAL_WEIGHT
            else:
                # Blend win_rate (0-1) with a Sharpe boost/penalty
                sharpe_bonus = max(-0.2, min(0.2, sp.sharpe_ratio * 0.05))
                weights[name] = max(0.1, min(0.9, sp.win_rate + sharpe_bonus))
        return weights

    # ------------------------------------------------------------------
    # Confidence adjustment
    # ------------------------------------------------------------------

    def adjust(self, rec: Recommendation) -> Recommendation:
        """Return a new Recommendation with confidence adjusted by strategy weights."""
        weights = self.strategy_weights()
        signal_weights = [
            weights.get(s.signal_type.value, NEUTRAL_WEIGHT)
            for s in rec.signals
        ]
        if not signal_weights:
            return rec

        avg_weight = sum(signal_weights) / len(signal_weights)

        # Scale: weight 0.5 → no change; 0.9 → +20%; 0.1 → -20%
        adjustment = (avg_weight - NEUTRAL_WEIGHT) * 0.4
        adjusted_confidence = max(0.0, min(1.0, rec.confidence + adjustment))

        if adjusted_confidence == rec.confidence:
            return rec

        meta_note = f"; meta-weight {avg_weight:.2f} → conf adj {adjustment:+.2f}"
        return replace(
            rec,
            confidence=round(adjusted_confidence, 4),
            rationale=rec.rationale + meta_note,
        )

    def leaderboard(self) -> list[dict]:
        """Return strategies ranked by win rate (descending)."""
        perf = self._tracker.get_strategy_performance()
        rows = []
        for name, sp in sorted(perf.items(), key=lambda x: -x[1].win_rate):
            rows.append({
                "strategy": name,
                "total": sp.total_evaluated,
                "wins": sp.wins,
                "losses": sp.losses,
                "win_rate": round(sp.win_rate, 3),
                "avg_return_pct": round(sp.avg_return_pct * 100, 2),
                "sharpe": round(sp.sharpe_ratio, 3),
                "max_drawdown_pct": round(sp.max_drawdown_pct * 100, 2),
                "profit_factor": round(sp.profit_factor, 3) if sp.profit_factor != float("inf") else None,
                "weight": round(self.strategy_weights().get(name, NEUTRAL_WEIGHT), 3),
            })
        return rows
