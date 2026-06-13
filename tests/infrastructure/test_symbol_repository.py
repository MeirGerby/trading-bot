"""Tests for the dynamic symbol universe and user watchlist."""
import pytest

from trading_platform.infrastructure.memory_store import JsonMemoryStore
from trading_platform.infrastructure.symbol_repository import JsonSymbolRepository
from trading_platform.infrastructure.symbol_universe_seed import seed_records


class FakeMarketData:
    """Knows prices only for the symbols it's given."""

    def __init__(self, prices=None):
        self.prices = prices or {}
        self.calls = []

    def get_daily_bars(self, symbol, lookback_days):
        return []

    def get_last_price(self, symbol):
        self.calls.append(symbol)
        return self.prices.get(symbol)


def make_repo(tmp_path, prices=None, default_watchlist=("AAPL", "TEVA.TA")):
    memory = JsonMemoryStore(tmp_path)
    return JsonSymbolRepository(memory, FakeMarketData(prices), default_watchlist), memory


class TestUniverse:
    def test_seed_contains_both_markets(self):
        records = seed_records()
        markets = {r["market"] for r in records}
        assert markets == {"US", "TASE"}
        assert len(records) > 150

    def test_search_exact_symbol_first(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        results = repo.search("AAPL")
        assert results[0]["symbol"] == "AAPL"

    def test_search_by_company_name(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        results = repo.search("teva")
        assert any(r["symbol"] == "TEVA.TA" for r in results)

    def test_search_prefix_matches(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        symbols = [r["symbol"] for r in repo.search("MS")]
        assert "MSFT" in symbols

    def test_search_market_filter(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        results = repo.search("B", limit=50, market="TASE")
        assert results
        assert all(r["market"] == "TASE" for r in results)

    def test_search_marks_watched(self, tmp_path):
        repo, _ = make_repo(tmp_path, default_watchlist=("AAPL",))
        result = repo.search("AAPL")[0]
        assert result["watched"] is True

    def test_search_unknown_symbol_validates_live(self, tmp_path):
        repo, _ = make_repo(tmp_path, prices={"ZZZQ": 12.5})
        results = repo.search("ZZZQ")
        assert results[0]["symbol"] == "ZZZQ"
        assert results[0]["market"] == "US"

    def test_lookup_seeded_symbol_skips_validation(self, tmp_path):
        repo, _ = make_repo(tmp_path)
        rec = repo.lookup("BEZQ.TA")
        assert rec["market"] == "TASE"
        assert repo._market_data.calls == []  # served from seed, no network

    def test_lookup_discovers_and_persists(self, tmp_path):
        repo, memory = make_repo(tmp_path, prices={"DIS": 95.0})
        rec = repo.lookup("DIS")  # not in seed
        assert rec == {"symbol": "DIS", "name": "", "market": "US"}
        # persisted — a fresh repo with no market data still finds it
        fresh = JsonSymbolRepository(memory, FakeMarketData(), ())
        assert fresh.lookup("DIS") is not None

    def test_lookup_invalid_symbol_returns_none(self, tmp_path):
        repo, _ = make_repo(tmp_path, prices={})
        assert repo.lookup("NOTREAL") is None


class TestWatchlist:
    def test_defaults_until_user_modifies(self, tmp_path):
        repo, _ = make_repo(tmp_path, default_watchlist=("AAPL", "MSFT"))
        assert repo.get_watchlist() == ("AAPL", "MSFT")

    def test_add_known_symbol(self, tmp_path):
        repo, _ = make_repo(tmp_path, default_watchlist=("AAPL",))
        assert repo.add_to_watchlist("NVDA") is True
        assert repo.get_watchlist() == ("AAPL", "NVDA")

    def test_add_unknown_invalid_symbol_rejected(self, tmp_path):
        repo, _ = make_repo(tmp_path, prices={})
        assert repo.add_to_watchlist("FAKE123") is False

    def test_add_validated_dynamic_symbol(self, tmp_path):
        repo, _ = make_repo(tmp_path, prices={"DIS": 95.0}, default_watchlist=("AAPL",))
        assert repo.add_to_watchlist("DIS") is True
        assert "DIS" in repo.get_watchlist()

    def test_remove_symbol(self, tmp_path):
        repo, _ = make_repo(tmp_path, default_watchlist=("AAPL", "MSFT"))
        assert repo.remove_from_watchlist("AAPL") is True
        assert repo.get_watchlist() == ("MSFT",)

    def test_remove_missing_returns_false(self, tmp_path):
        repo, _ = make_repo(tmp_path, default_watchlist=("AAPL",))
        assert repo.remove_from_watchlist("NVDA") is False

    def test_persists_across_instances(self, tmp_path):
        repo, memory = make_repo(tmp_path, default_watchlist=("AAPL",))
        repo.add_to_watchlist("MSFT")
        fresh = JsonSymbolRepository(memory, FakeMarketData(), ("AAPL",))
        assert fresh.get_watchlist() == ("AAPL", "MSFT")

    def test_add_is_idempotent(self, tmp_path):
        repo, _ = make_repo(tmp_path, default_watchlist=("AAPL",))
        repo.add_to_watchlist("AAPL")
        assert repo.get_watchlist() == ("AAPL",)
