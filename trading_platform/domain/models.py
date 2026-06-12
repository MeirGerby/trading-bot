"""Pure domain models. No I/O, no framework imports — stdlib only."""
from dataclasses import dataclass, field
from datetime import datetime, timezone

from trading_platform.domain.enums import (
    AssetClass,
    Direction,
    OptionType,
    OrderSide,
    OrderStatus,
    SignalType,
)


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
    proposed_quantity: float = 0.0  # sized by PortfolioEngine; 0 = informational only
    risk_checks: tuple[RiskCheckResult, ...] = ()
    created_at: datetime = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        if not self.signals:
            raise ValueError("recommendation requires at least one signal")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")
        if self.price <= 0:
            raise ValueError(f"price must be positive, got {self.price}")
        if self.proposed_quantity < 0:
            raise ValueError("proposed_quantity must be >= 0")

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


@dataclass(frozen=True)
class OptionContract:
    """One row of an option chain, as needed by the options-flow strategy."""
    underlying: str
    option_type: OptionType
    strike: float
    expiration: str  # ISO date string, as provided by data vendors
    volume: float
    open_interest: float
    implied_volatility: float = 0.0

    @property
    def vol_oi_ratio(self) -> float:
        return self.volume / self.open_interest if self.open_interest > 0 else 0.0


@dataclass(frozen=True)
class TradeOutcome:
    """Records what actually happened after a recommendation was surfaced."""
    id: str                          # symbol + entry_time ISO
    symbol: str
    entry_price: float
    exit_price: float                # price at evaluation time
    entry_time: datetime
    checked_at: datetime
    signal_types: tuple[str, ...]    # string values, JSON-friendly
    confidence: float
    return_pct: float                # (exit - entry) / entry

    @property
    def is_win(self) -> bool:
        return self.return_pct > 0.0


@dataclass(frozen=True)
class Lesson:
    """A structured piece of knowledge extracted from outcomes."""
    id: str
    symbol: str
    signal_types: tuple[str, ...]
    outcome_return_pct: float
    was_win: bool
    confidence_at_entry: float
    lesson_text: str
    extracted_at: datetime


@dataclass(frozen=True)
class StrategyPerformance:
    """Aggregated performance metrics for one strategy."""
    strategy_name: str
    total_evaluated: int
    wins: int
    losses: int
    returns: tuple[float, ...]       # list of return_pct values
    computed_at: datetime

    @property
    def win_rate(self) -> float:
        total = self.wins + self.losses
        return self.wins / total if total > 0 else 0.5

    @property
    def avg_return_pct(self) -> float:
        return sum(self.returns) / len(self.returns) if self.returns else 0.0

    @property
    def sharpe_ratio(self) -> float:
        if len(self.returns) < 2:
            return 0.0
        import math
        avg = self.avg_return_pct
        variance = sum((r - avg) ** 2 for r in self.returns) / (len(self.returns) - 1)
        std = math.sqrt(variance)
        return avg / std if std > 0 else 0.0

    @property
    def max_drawdown_pct(self) -> float:
        if not self.returns:
            return 0.0
        peak = equity = 1.0
        drawdown = 0.0
        for r in self.returns:
            equity *= 1.0 + r
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak
            if dd > drawdown:
                drawdown = dd
        return drawdown

    @property
    def profit_factor(self) -> float:
        gross_win = sum(r for r in self.returns if r > 0)
        gross_loss = abs(sum(r for r in self.returns if r < 0))
        return gross_win / gross_loss if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0


@dataclass(frozen=True)
class SystemCritique:
    """Post-cycle structured self-assessment."""
    cycle_id: str
    biggest_mistake: str
    biggest_success: str
    detected_bias: str
    missing_data: str
    improvement_suggestion: str
    top_strategy: str
    worst_strategy: str
    generated_at: datetime


@dataclass(frozen=True)
class Order:
    """A broker order. Paper trading only until live trading is authorized."""
    id: str
    symbol: str
    side: OrderSide
    quantity: float
    price: float
    status: OrderStatus
    reason: str = ""
    created_at: datetime = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.price <= 0:
            raise ValueError("price must be positive")
