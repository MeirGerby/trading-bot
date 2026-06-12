"""LearningEngine — extracts structured lessons from completed trade outcomes.

After each batch of new outcomes is available, the engine:
1. Identifies which signal combinations had high/low win rates
2. Flags overconfident recommendations (high confidence, bad outcome)
3. Flags underconfident gems (low confidence, good outcome)
4. Detects per-ticker patterns (consistently good/bad with specific signals)
5. Stores structured Lesson records in memory
"""
import logging
import uuid
from collections import defaultdict
from datetime import timezone
from datetime import datetime

from trading_platform.application.ports import MemoryStore
from trading_platform.domain.models import Lesson, StrategyPerformance, TradeOutcome

logger = logging.getLogger(__name__)

LESSONS_KEY = "lessons"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LearningEngine:
    def __init__(self, memory: MemoryStore):
        self._memory = memory

    # ------------------------------------------------------------------
    # Lesson extraction
    # ------------------------------------------------------------------

    def extract_lessons(self, new_outcomes: list[TradeOutcome],
                        strategy_perf: dict[str, StrategyPerformance]) -> list[Lesson]:
        """Derive lessons from a batch of newly completed outcomes."""
        lessons: list[Lesson] = []
        for outcome in new_outcomes:
            texts = self._analyze_outcome(outcome, strategy_perf)
            for text in texts:
                lesson = Lesson(
                    id=str(uuid.uuid4())[:8],
                    symbol=outcome.symbol,
                    signal_types=outcome.signal_types,
                    outcome_return_pct=outcome.return_pct,
                    was_win=outcome.is_win,
                    confidence_at_entry=outcome.confidence,
                    lesson_text=text,
                    extracted_at=_utcnow(),
                )
                lessons.append(lesson)

        if lessons:
            self._persist_lessons(lessons)
        return lessons

    def _analyze_outcome(self, outcome: TradeOutcome,
                         strategy_perf: dict[str, StrategyPerformance]) -> list[str]:
        texts: list[str] = []
        ret_pct = outcome.return_pct * 100
        signals = ", ".join(outcome.signal_types) if outcome.signal_types else "unknown"
        win_word = "WIN" if outcome.is_win else "LOSS"

        # Basic outcome
        texts.append(
            f"{win_word}: {outcome.symbol} returned {ret_pct:+.2f}% "
            f"(confidence={outcome.confidence:.2f}, signals=[{signals}])"
        )

        # Overconfidence detection
        if outcome.confidence > 0.75 and not outcome.is_win:
            texts.append(
                f"OVERCONFIDENCE DETECTED: {outcome.symbol} had confidence "
                f"{outcome.confidence:.2f} but lost {abs(ret_pct):.2f}%. "
                f"Signals [{signals}] may be overfitting."
            )

        # Underconfidence detection (missed opportunity)
        if outcome.confidence < 0.55 and outcome.is_win and ret_pct > 3.0:
            texts.append(
                f"UNDERCONFIDENCE: {outcome.symbol} returned {ret_pct:+.2f}% "
                f"but confidence was only {outcome.confidence:.2f}. "
                f"Signals [{signals}] deserve higher weight."
            )

        # Per-strategy insights
        for sig_type in outcome.signal_types:
            perf = strategy_perf.get(sig_type)
            if perf and perf.total_evaluated >= 5:
                if not outcome.is_win and perf.win_rate < 0.4:
                    texts.append(
                        f"WEAK STRATEGY: {sig_type} has win rate "
                        f"{perf.win_rate:.0%} across {perf.total_evaluated} trades. "
                        f"Consider reducing weight."
                    )
                elif outcome.is_win and perf.win_rate > 0.65:
                    texts.append(
                        f"STRONG STRATEGY: {sig_type} win rate {perf.win_rate:.0%}. "
                        f"Consider increasing confidence weight."
                    )

        return texts

    # ------------------------------------------------------------------
    # Pattern aggregation
    # ------------------------------------------------------------------

    def get_signal_win_rates(self) -> dict[str, dict]:
        """Aggregate historical win rates per signal combination from lessons."""
        all_outcomes = self._memory.load("trade_outcomes", {"outcomes": []})["outcomes"]
        combo_stats: dict[str, dict] = defaultdict(lambda: {"wins": 0, "total": 0, "returns": []})

        for outcome in all_outcomes:
            key = "+".join(sorted(outcome.get("signal_types", [])))
            combo_stats[key]["wins"] += 1 if outcome.get("is_win") else 0
            combo_stats[key]["total"] += 1
            combo_stats[key]["returns"].append(outcome.get("return_pct", 0.0))

        result = {}
        for combo, stats in combo_stats.items():
            if stats["total"] > 0:
                returns = stats["returns"]
                result[combo] = {
                    "win_rate": round(stats["wins"] / stats["total"], 3),
                    "total": stats["total"],
                    "avg_return_pct": round(sum(returns) / len(returns) * 100, 2),
                }
        return dict(sorted(result.items(), key=lambda x: -x[1]["win_rate"]))

    def get_recent_lessons(self, n: int = 20) -> list[dict]:
        store = self._memory.load(LESSONS_KEY, {"lessons": []})
        lessons = store["lessons"]
        return lessons[-n:]

    def _persist_lessons(self, lessons: list[Lesson]) -> None:
        store = self._memory.load(LESSONS_KEY, {"lessons": []})
        for lesson in lessons:
            store["lessons"].append({
                "id": lesson.id,
                "symbol": lesson.symbol,
                "signal_types": list(lesson.signal_types),
                "return_pct": round(lesson.outcome_return_pct * 100, 2),
                "was_win": lesson.was_win,
                "confidence": lesson.confidence_at_entry,
                "text": lesson.lesson_text,
                "extracted_at": lesson.extracted_at.isoformat(timespec="seconds"),
            })
        # Keep last 500 lessons
        store["lessons"] = store["lessons"][-500:]
        self._memory.save(LESSONS_KEY, store)
