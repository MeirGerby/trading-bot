"""Application ports — structural interfaces implemented by infrastructure.

Implementations live in trading_platform.infrastructure.
Test doubles can satisfy these Protocols without inheritance.

ADR-6: strategies receive their data dependencies via constructor, not via
evaluate() arguments — different strategies need different ports (bars vs
option chains), and constructor injection keeps the Strategy interface
uniform for the ScanService.
"""
from typing import Protocol, runtime_checkable

from trading_platform.domain import (
    Bar,
    FeedbackEvent,
    FundamentalData,
    Instrument,
    OptionContract,
    Order,
    OrderSide,
    PortfolioState,
    Recommendation,
    RiskCheckResult,
    Signal,
)


@runtime_checkable
class MarketDataPort(Protocol):
    def get_daily_bars(self, symbol: str, lookback_days: int) -> list[Bar]: ...

    def get_last_price(self, symbol: str) -> float | None: ...


@runtime_checkable
class OptionsDataPort(Protocol):
    def get_option_contracts(self, symbol: str, max_expirations: int = 2) -> list[OptionContract]: ...


@runtime_checkable
class FundamentalsPort(Protocol):
    def get_fundamentals(self, symbol: str) -> FundamentalData | None: ...


@runtime_checkable
class Strategy(Protocol):
    """Data dependencies are injected at construction (ADR-6)."""

    @property
    def name(self) -> str: ...

    def evaluate(self, instrument: Instrument, params: dict[str, float]) -> Signal | None: ...


@runtime_checkable
class RiskRule(Protocol):
    @property
    def name(self) -> str: ...

    def check(self, recommendation: Recommendation,
              portfolio: PortfolioState) -> RiskCheckResult: ...


@runtime_checkable
class MemoryStore(Protocol):
    def load(self, key: str, default: dict) -> dict: ...

    def save(self, key: str, value: dict) -> None: ...

    def append_feedback(self, event: FeedbackEvent) -> None: ...


@runtime_checkable
class BrokerPort(Protocol):
    """Paper trading only until live trading is explicitly authorized."""

    def get_portfolio(self) -> PortfolioState: ...

    def submit_market_order(self, symbol: str, side: OrderSide,
                            quantity: float, price: float) -> Order: ...


@runtime_checkable
class AuditLogPort(Protocol):
    def record(self, event: str, payload: dict) -> None: ...


@runtime_checkable
class Notifier(Protocol):
    def send(self, message: str) -> None: ...
