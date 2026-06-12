"""Paper-trading broker implementing BrokerPort.

Simulates instant fills at the caller-provided price and persists the
portfolio through the MemoryStore (shared data/ volume, lock-protected).
Live-money brokers must NOT be wired in without explicit owner approval
(ADR-5).
"""
import uuid

from trading_platform.application.ports import MemoryStore
from trading_platform.domain import (
    Instrument,
    Order,
    OrderSide,
    OrderStatus,
    PortfolioState,
    Position,
)

STORE_KEY = "paper_portfolio"


class PaperBroker:
    """Implements trading_platform.application.ports.BrokerPort."""

    def __init__(self, memory: MemoryStore, starting_cash: float = 100_000.0):
        self._memory = memory
        self._starting_cash = starting_cash

    def _load(self) -> dict:
        return self._memory.load(STORE_KEY, {"cash": self._starting_cash, "positions": {}})

    def get_portfolio(self) -> PortfolioState:
        data = self._load()
        positions = tuple(
            Position(
                instrument=Instrument(symbol=sym),
                quantity=p["quantity"],
                avg_entry_price=p["avg_entry_price"],
            )
            for sym, p in data["positions"].items()
            if p["quantity"] > 0
        )
        return PortfolioState(cash=data["cash"], positions=positions)

    def submit_market_order(self, symbol: str, side: OrderSide,
                            quantity: float, price: float) -> Order:
        symbol = symbol.upper()

        def order(status: OrderStatus, reason: str = "") -> Order:
            return Order(id=uuid.uuid4().hex, symbol=symbol, side=side,
                         quantity=quantity, price=price, status=status, reason=reason)

        data = self._load()
        cost = quantity * price

        if side is OrderSide.BUY:
            if cost > data["cash"]:
                return order(OrderStatus.REJECTED,
                             f"insufficient cash: need {cost:.2f}, have {data['cash']:.2f}")
            data["cash"] -= cost
            pos = data["positions"].get(symbol, {"quantity": 0.0, "avg_entry_price": 0.0})
            new_qty = pos["quantity"] + quantity
            pos["avg_entry_price"] = (
                (pos["quantity"] * pos["avg_entry_price"] + cost) / new_qty
            )
            pos["quantity"] = new_qty
            data["positions"][symbol] = pos
        else:  # SELL
            pos = data["positions"].get(symbol)
            if pos is None or pos["quantity"] < quantity:
                held = pos["quantity"] if pos else 0
                return order(OrderStatus.REJECTED,
                             f"insufficient position: selling {quantity}, hold {held}")
            pos["quantity"] -= quantity
            data["cash"] += cost
            if pos["quantity"] <= 0:
                del data["positions"][symbol]

        self._memory.save(STORE_KEY, data)
        return order(OrderStatus.FILLED)
