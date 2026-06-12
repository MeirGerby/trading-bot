import pytest

from trading_platform.application.ports import BrokerPort
from trading_platform.domain import OrderSide, OrderStatus
from trading_platform.infrastructure.memory_store import JsonMemoryStore
from trading_platform.infrastructure.paper_broker import PaperBroker


@pytest.fixture
def broker(tmp_path):
    return PaperBroker(JsonMemoryStore(tmp_path), starting_cash=10_000.0)


class TestPaperBroker:
    def test_satisfies_port(self, broker):
        assert isinstance(broker, BrokerPort)

    def test_initial_portfolio(self, broker):
        pf = broker.get_portfolio()
        assert pf.cash == 10_000.0
        assert pf.positions == ()

    def test_buy_fills_and_updates_portfolio(self, broker):
        order = broker.submit_market_order("AAPL", OrderSide.BUY, 10, 100.0)
        assert order.status is OrderStatus.FILLED
        pf = broker.get_portfolio()
        assert pf.cash == 9_000.0
        assert pf.positions[0].instrument.symbol == "AAPL"
        assert pf.positions[0].quantity == 10

    def test_buy_rejected_on_insufficient_cash(self, broker):
        order = broker.submit_market_order("AAPL", OrderSide.BUY, 1000, 100.0)
        assert order.status is OrderStatus.REJECTED
        assert "insufficient cash" in order.reason
        assert broker.get_portfolio().cash == 10_000.0

    def test_buy_merges_position_with_weighted_avg(self, broker):
        broker.submit_market_order("AAPL", OrderSide.BUY, 10, 100.0)
        broker.submit_market_order("AAPL", OrderSide.BUY, 10, 200.0)
        pos = broker.get_portfolio().positions[0]
        assert pos.quantity == 20
        assert pos.avg_entry_price == 150.0

    def test_sell_returns_cash_and_clears_position(self, broker):
        broker.submit_market_order("AAPL", OrderSide.BUY, 10, 100.0)
        order = broker.submit_market_order("AAPL", OrderSide.SELL, 10, 120.0)
        assert order.status is OrderStatus.FILLED
        pf = broker.get_portfolio()
        assert pf.cash == 10_200.0  # 10k - 1000 + 1200
        assert pf.positions == ()

    def test_sell_rejected_when_overselling(self, broker):
        broker.submit_market_order("AAPL", OrderSide.BUY, 5, 100.0)
        order = broker.submit_market_order("AAPL", OrderSide.SELL, 10, 100.0)
        assert order.status is OrderStatus.REJECTED
        assert "insufficient position" in order.reason

    def test_state_persists_across_instances(self, tmp_path):
        store = JsonMemoryStore(tmp_path)
        PaperBroker(store, 10_000.0).submit_market_order("AAPL", OrderSide.BUY, 10, 100.0)
        pf = PaperBroker(store, 10_000.0).get_portfolio()
        assert pf.cash == 9_000.0
        assert pf.positions[0].quantity == 10
