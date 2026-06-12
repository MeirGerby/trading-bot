"""Pure domain models. No I/O, no framework imports — stdlib only."""
from dataclasses import dataclass, field
from datetime import datetime, timezone

from trading_platform.domain.enums import AssetClass, Direction, SignalType


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Instrument:
    symbol: str
    asset_class: AssetClass = AssetClass.EQUITY
    name: str = ""

    def __post_init__(self) -> None:
        if not self.symbol or not self.symbol.isupper():
            raise ValueError(f"symbol must be non-empty uppercase, got {self.symbol!r}")


@dataclass(frozen=True)
class Bar:
    """One OHLCV bar."""
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        if not (self.low <= self.open <= self.high and self.low <= self.close <= self.high):
            raise ValueError(f"inconsistent OHLC: O={self.open} H={self.high} L={self.low} C={self.close}")
        if self.volume < 0:
            raise ValueError("volume must be >= 0")


@dataclass(frozen=True)
class Signal:
    """A single strategy's finding for one instrument."""
    instrument: Instrument
    signal_type: SignalType
    strength: float  # 0.0–1.0, how decisively the threshold was crossed
    details: dict[str, str] = field(default_factory=dict)
    generated_at: datetime = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        if not 0.0 <= self.strength <= 1.0:
            raise ValueError(f"strength must be in [0, 1], got {self.strength}")


@dataclass(frozen=True)
class RiskCheckResult:
    rule_name: str
    passed: bool
    reason: str = ""


@dataclass(frozen=True)
class Recommendation:
    """Decision-engine output: an actionable, risk-checked suggestion."""
    instrument: Instrument
    direction: Direction
    signals: tuple[Signal, ...]
    price: float
    confidence: float  # 0.0–1.0, blends signal strengths and learned feedback
    rationale: str
    risk_checks: tuple[RiskCheckResult, ...] = ()
    created_at: datetime = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        if not self.signals:
            raise ValueError("recommendation requires at least one signal")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")
        if self.price <= 0:
            raise ValueError(f"price must be positive, got {self.price}")

    @property
    def score(self) -> int:
        """Number of distinct signal types — matches legacy alert scoring."""
        return len({s.signal_type for s in self.signals})

    @property
    def approved(self) -> bool:
        return all(c.passed for c in self.risk_checks)


@dataclass(frozen=True)
class Position:
    instrument: Instrument
    quantity: float
    avg_entry_price: float

    @property
    def cost_basis(self) -> float:
        return self.quantity * self.avg_entry_price


@dataclass(frozen=True)
class PortfolioState:
    cash: float
    positions: tuple[Position, ...] = ()

    def exposure(self) -> float:
        return sum(p.cost_basis for p in self.positions)


@dataclass(frozen=True)
class FeedbackEvent:
    """Owner feedback on a recommendation, feeding the learning system."""
    symbol: str
    signal_types: tuple[SignalType, ...]
    positive: bool
    timestamp: datetime = field(default_factory=_utcnow)
    notes: str = ""
