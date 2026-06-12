"""Application ports — structural interfaces implemented by infrastructure.

Implementations live in trading_platform.infrastructure (Phases 3–8).
Test doubles can satisfy these Protocols without inheritance.
"""
from typing import Protocol, runtime_checkable

from trading_platform.domain import (
    Bar,
    FeedbackEvent,
    Instrument,
    PortfolioState,
    Recommendation,
    RiskCheckResult,
    Signal,
)


@runtime_checkable
class MarketDataPort(Protocol):
    """Phase 3: yfinance adapter; later possibly broker feeds."""

    def get_daily_bars(self, symbol: str, lookback_days: int) -> list[Bar]: ...

    def get_last_price(self, symbol: str) -> float | None: ...


@runtime_checkable
class Strategy(Protocol):
    """Phase 5: breakout / momentum / options-flow implementations."""

    @property
    def name(self) -> str: ...

    def evaluate(self, instrument: Instrument, market_data: MarketDataPort,
                 params: dict[str, float]) -> Signal | None: ...


@runtime_checkable
class RiskRule(Protocol):
    """Phase 6: position sizing, exposure, volatility, liquidity, drawdown."""

    @property
    def name(self) -> str: ...

    def check(self, recommendation: Recommendation,
              portfolio: PortfolioState) -> RiskCheckResult: ...


@runtime_checkable
class MemoryStore(Protocol):
    """Phase 4: persistent project memory (weights, feedback, knowledge)."""

    def load(self, key: str, default: dict) -> dict: ...

    def save(self, key: str, value: dict) -> None: ...

    def append_feedback(self, event: FeedbackEvent) -> None: ...


@runtime_checkable
class Notifier(Protocol):
    """Telegram today; extensible to email/webhooks."""

    def send(self, message: str) -> None: ...
