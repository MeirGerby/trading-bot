"""SelfCritiqueEngine — generates structured post-cycle self-assessments.

After every scan the engine:
- Identifies the biggest mistake and biggest success in recent outcomes
- Detects systematic biases (e.g. overconfidence, ticker concentration)
- Flags missing data issues from the scan report
- Produces an improvement suggestion
- Stores the critique in persistent memory
"""
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from trading_platform.application.ports import MemoryStore
from trading_platform.domain.models import StrategyPerformance, SystemCritique, TradeOutcome

logger = logging.getLogger(__name__)

CRITIQUES_KEY = "critiques"


@dataclass(frozen=True)
class ScanSummary:
    """Lightweight scan summary passed to SelfCritiqueEngine to avoid circular imports."""
    symbols_scanned: int
    recommendations_count: int
    errors_count: int
    error_symbols: tuple[str, ...] = ()
    duration_seconds: float = 0.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SelfCritiqueEngine:
    def __init__(self, memory: MemoryStore):
        self._memory = memory

    def critique(
        self,
        report: ScanSummary,
        recent_outcomes: list[TradeOutcome],
        strategy_perf: dict[str, StrategyPerformance],
    ) -> SystemCritique:
        cycle_id = str(uuid.uuid4())[:8]

        biggest_mistake = self._find_biggest_mistake(recent_outcomes)
        biggest_success = self._find_biggest_success(recent_outcomes)
        detected_bias = self._detect_bias(recent_outcomes, report)
        missing_data = self._find_missing_data(report)
        improvement = self._suggest_improvement(strategy_perf, recent_outcomes)
        top_strategy, worst_strategy = self._rank_strategies(strategy_perf)

        critique = SystemCritique(
            cycle_id=cycle_id,
            biggest_mistake=biggest_mistake,
            biggest_success=biggest_success,
            detected_bias=detected_bias,
            missing_data=missing_data,
            improvement_suggestion=improvement,
            top_strategy=top_strategy,
            worst_strategy=worst_strategy,
            generated_at=_utcnow(),
        )
        self._persist(critique, report)
        return critique

    # ------------------------------------------------------------------
    # Analysis helpers
    # ------------------------------------------------------------------

    def _find_biggest_mistake(self, outcomes: list[TradeOutcome]) -> str:
        losses = [o for o in outcomes if not o.is_win]
        if not losses:
            return "No losses in this batch — no mistake to report."
        worst = min(losses, key=lambda o: o.return_pct)
        return (
            f"{worst.symbol} lost {abs(worst.return_pct) * 100:.2f}% "
            f"(entry {worst.entry_price:.2f} → exit {worst.exit_price:.2f}). "
            f"Signals: {', '.join(worst.signal_types)}. "
            f"Confidence was {worst.confidence:.2f}."
        )

    def _find_biggest_success(self, outcomes: list[TradeOutcome]) -> str:
        wins = [o for o in outcomes if o.is_win]
        if not wins:
            return "No wins in this batch."
        best = max(wins, key=lambda o: o.return_pct)
        return (
            f"{best.symbol} gained {best.return_pct * 100:.2f}% "
            f"(entry {best.entry_price:.2f} → exit {best.exit_price:.2f}). "
            f"Signals: {', '.join(best.signal_types)}. "
            f"Confidence was {best.confidence:.2f}."
        )

    def _detect_bias(self, outcomes: list[TradeOutcome], report: ScanSummary) -> str:
        if not outcomes:
            return "No outcomes available for bias detection."

        # Overconfidence bias
        overconf_losses = [
            o for o in outcomes if not o.is_win and o.confidence > 0.7
        ]
        if len(overconf_losses) > len(outcomes) * 0.4:
            return (
                f"OVERCONFIDENCE BIAS: {len(overconf_losses)}/{len(outcomes)} "
                f"high-confidence recommendations were losers. "
                f"Confidence calibration needs review."
            )

        # Ticker concentration
        ticker_counts: dict[str, int] = {}
        for o in outcomes:
            ticker_counts[o.symbol] = ticker_counts.get(o.symbol, 0) + 1
        top_ticker = max(ticker_counts, key=lambda k: ticker_counts[k])
        if ticker_counts[top_ticker] > len(outcomes) * 0.4 and len(ticker_counts) > 1:
            return (
                f"CONCENTRATION BIAS: {ticker_counts[top_ticker]}/{len(outcomes)} "
                f"recommendations were for {top_ticker}. Diversify watchlist scanning."
            )

        # Scan errors
        if report.symbols_scanned > 0 and report.errors_count > report.symbols_scanned * 0.3:
            return (
                f"DATA BIAS: {report.errors_count} scan errors out of "
                f"{report.symbols_scanned} symbols. Results may be skewed toward "
                f"tickers with better data availability."
            )

        return "No systematic bias detected in this batch."

    def _find_missing_data(self, report: ScanSummary) -> str:
        if not report.errors_count:
            return "No missing data issues detected."
        unique_errors = list(dict.fromkeys(
            e.split(":")[0].strip() for e in report.error_symbols
        ))[:5]
        suffix = f": {', '.join(unique_errors)}" if unique_errors else ""
        return (
            f"{report.errors_count} symbols had errors{suffix}. "
            f"Likely yfinance rate-limit or market-closed issues."
        )

    def _suggest_improvement(
        self,
        strategy_perf: dict[str, StrategyPerformance],
        outcomes: list[TradeOutcome],
    ) -> str:
        if not strategy_perf:
            return "Insufficient data for improvement suggestions. Run more scans."

        # Find weakest strategy with enough data
        weak = [
            (name, sp) for name, sp in strategy_perf.items()
            if sp.total_evaluated >= 5 and sp.win_rate < 0.4
        ]
        if weak:
            name, sp = min(weak, key=lambda x: x[1].win_rate)
            return (
                f"Consider disabling or retuning '{name}' strategy: "
                f"win rate {sp.win_rate:.0%} over {sp.total_evaluated} trades. "
                f"Tighten its entry thresholds or reduce its confidence weight."
            )

        # Find best opportunity
        strong = [
            (name, sp) for name, sp in strategy_perf.items()
            if sp.total_evaluated >= 5 and sp.win_rate > 0.65
        ]
        if strong:
            name, sp = max(strong, key=lambda x: x[1].win_rate)
            return (
                f"'{name}' is performing well ({sp.win_rate:.0%} win rate). "
                f"Consider increasing its min_score weight or confidence boost."
            )

        # Overconfidence check
        overconf = [o for o in outcomes if not o.is_win and o.confidence > 0.7]
        if len(overconf) > 2:
            return (
                f"{len(overconf)} high-confidence recommendations lost. "
                f"Lower feedback_prior_weight or require higher signal convergence."
            )

        return "Current system performance is balanced. Continue accumulating outcome data."

    def _rank_strategies(
        self, perf: dict[str, StrategyPerformance]
    ) -> tuple[str, str]:
        qualified = {
            name: sp for name, sp in perf.items()
            if sp.total_evaluated >= 3
        }
        if not qualified:
            return ("insufficient data", "insufficient data")
        top = max(qualified, key=lambda k: qualified[k].win_rate)
        worst = min(qualified, key=lambda k: qualified[k].win_rate)
        return top, worst

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist(self, critique: SystemCritique, report: ScanSummary) -> None:
        store = self._memory.load(CRITIQUES_KEY, {"critiques": []})
        store["critiques"].append({
            "cycle_id": critique.cycle_id,
            "biggest_mistake": critique.biggest_mistake,
            "biggest_success": critique.biggest_success,
            "detected_bias": critique.detected_bias,
            "missing_data": critique.missing_data,
            "improvement_suggestion": critique.improvement_suggestion,
            "top_strategy": critique.top_strategy,
            "worst_strategy": critique.worst_strategy,
            "scan_duration_s": round(report.duration_seconds, 1),
            "recommendations": report.recommendations_count,
            "errors": report.errors_count,
            "generated_at": critique.generated_at.isoformat(timespec="seconds"),
        })
        # Keep last 100 critiques
        store["critiques"] = store["critiques"][-100:]
        self._memory.save(CRITIQUES_KEY, store)

    def get_recent_critiques(self, n: int = 10) -> list[dict]:
        store = self._memory.load(CRITIQUES_KEY, {"critiques": []})
        return list(reversed(store["critiques"][-n:]))
