"""JsonSymbolRepository — dynamic symbol universe and user watchlist.

Implements SymbolRepositoryPort on top of the lock-protected JsonMemoryStore
(shared between the bot and dashboard containers):

- Universe = in-code seed records + runtime-discovered symbols persisted
  under the "symbol_universe" key. lookup() of an unknown symbol validates
  it against live market data and adds it on success, so the effective
  universe is every symbol the data vendor can quote (full US + TASE).
- Watchlist = the user's pinned symbols under "user_watchlist", initialized
  from the configured default on first access. The scan pipeline and the
  screener both read it at run time, so pins apply without a restart.
"""
import logging

from trading_platform.application.ports import MarketDataPort, MemoryStore
from trading_platform.domain import market_for_symbol
from trading_platform.infrastructure.symbol_universe_seed import seed_records

logger = logging.getLogger(__name__)

UNIVERSE_KEY = "symbol_universe"
WATCHLIST_KEY = "user_watchlist"


class JsonSymbolRepository:
    """Implements trading_platform.application.ports.SymbolRepositoryPort."""

    def __init__(self, memory: MemoryStore, market_data: MarketDataPort | None = None,
                 default_watchlist: tuple[str, ...] = ()):
        self._memory = memory
        self._market_data = market_data
        self._default_watchlist = tuple(s.upper() for s in default_watchlist)
        self._seed = {r["symbol"]: r for r in seed_records()}

    # ------------------------------------------------------------------
    # Universe
    # ------------------------------------------------------------------

    def all_symbols(self) -> list[dict]:
        merged = dict(self._seed)
        for rec in self._memory.load(UNIVERSE_KEY, {"custom": []})["custom"]:
            merged.setdefault(rec["symbol"], rec)
        return list(merged.values())

    def search(self, query: str, limit: int = 20, market: str | None = None) -> list[dict]:
        q = query.strip().upper()
        if not q:
            return []
        watchlist = set(self.get_watchlist())

        exact, prefix, name_match = [], [], []
        for rec in self.all_symbols():
            if market and rec["market"] != market:
                continue
            sym = rec["symbol"]
            if sym == q or sym == f"{q}.TA":
                exact.append(rec)
            elif sym.startswith(q):
                prefix.append(rec)
            elif q in rec.get("name", "").upper():
                name_match.append(rec)

        results = exact + sorted(prefix, key=lambda r: r["symbol"]) \
            + sorted(name_match, key=lambda r: r["symbol"])

        # Nothing known matches a symbol-shaped query → try live validation
        if not results and len(q) <= 12 and all(c.isalnum() or c in ".-" for c in q):
            discovered = self.lookup(q)
            if discovered:
                results = [discovered]

        return [{**r, "watched": r["symbol"] in watchlist} for r in results[:limit]]

    def lookup(self, symbol: str) -> dict | None:
        symbol = symbol.strip().upper()
        if not symbol:
            return None
        known = self._seed.get(symbol)
        if known is None:
            for rec in self._memory.load(UNIVERSE_KEY, {"custom": []})["custom"]:
                if rec["symbol"] == symbol:
                    known = rec
                    break
        if known is not None:
            return dict(known)
        return self._discover(symbol)

    def _discover(self, symbol: str) -> dict | None:
        """Validate an unknown symbol against live data; persist if real."""
        if self._market_data is None:
            return None
        try:
            price = self._market_data.get_last_price(symbol)
        except Exception:
            logger.exception("symbol validation failed for %s", symbol)
            return None
        if price is None or price <= 0:
            return None

        rec = {"symbol": symbol, "name": "", "market": market_for_symbol(symbol).value}
        store = self._memory.load(UNIVERSE_KEY, {"custom": []})
        if not any(r["symbol"] == symbol for r in store["custom"]):
            store["custom"].append(rec)
            self._memory.save(UNIVERSE_KEY, store)
        return rec

    # ------------------------------------------------------------------
    # Watchlist
    # ------------------------------------------------------------------

    def get_watchlist(self) -> tuple[str, ...]:
        stored = self._memory.load(WATCHLIST_KEY, {}).get("symbols")
        if stored is None:
            return self._default_watchlist
        return tuple(stored)

    def add_to_watchlist(self, symbol: str) -> bool:
        symbol = symbol.strip().upper()
        if self.lookup(symbol) is None:
            return False
        current = list(self.get_watchlist())
        if symbol not in current:
            current.append(symbol)
            self._memory.save(WATCHLIST_KEY, {"symbols": current})
        return True

    def remove_from_watchlist(self, symbol: str) -> bool:
        symbol = symbol.strip().upper()
        current = list(self.get_watchlist())
        if symbol not in current:
            return False
        current.remove(symbol)
        self._memory.save(WATCHLIST_KEY, {"symbols": current})
        return True
