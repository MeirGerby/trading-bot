"""Decision engine: aggregated signals → scored Recommendation.

Confidence blends two components:
- mean signal strength (how decisively thresholds were crossed)
- a learned prior per signal type from the owner's feedback history
  (positive ratio; 0.5 neutral when there is no history)

This is where the learning system influences decisions — weights tune the
strategies' thresholds, feedback priors tune the confidence.
"""
from trading_platform.application.ports import MemoryStore
from trading_platform.domain import Direction, Instrument, Recommendation, Signal

_STRENGTH_WEIGHT = 0.6
_PRIOR_WEIGHT = 0.4
_NEUTRAL_PRIOR = 0.5


class DecisionEngine:
    def __init__(self, memory: MemoryStore):
        self._memory = memory

    def _feedback_priors(self) -> dict[str, float]:
        history = self._memory.load("feedback", {"history": []}).get("history", [])
        counts: dict[str, list[int]] = {}  # type -> [positive, total]
        for entry in history:
            for sig_type in entry.get("signals", []):
                bucket = counts.setdefault(sig_type, [0, 0])
                bucket[0] += 1 if entry.get("positive") else 0
                bucket[1] += 1
        return {t: pos / total for t, (pos, total) in counts.items() if total > 0}

    def build(self, instrument: Instrument, signals: tuple[Signal, ...],
              price: float) -> Recommendation:
        priors = self._feedback_priors()
        types = sorted({s.signal_type for s in signals}, key=lambda t: t.value)

        avg_strength = sum(s.strength for s in signals) / len(signals)
        avg_prior = sum(priors.get(t.value, _NEUTRAL_PRIOR) for t in types) / len(types)
        confidence = max(0.0, min(1.0,
                                  _STRENGTH_WEIGHT * avg_strength + _PRIOR_WEIGHT * avg_prior))

        parts = [f"{s.signal_type.value} (strength {s.strength:.2f})" for s in signals]
        prior_parts = [
            f"{t.value} historical accuracy {priors[t.value]:.0%}"
            for t in types if t.value in priors
        ]
        rationale = f"{len(types)} signal types: " + ", ".join(parts)
        if prior_parts:
            rationale += "; " + ", ".join(prior_parts)

        return Recommendation(
            instrument=instrument,
            direction=Direction.LONG,
            signals=tuple(signals),
            price=price,
            confidence=confidence,
            rationale=rationale,
        )
