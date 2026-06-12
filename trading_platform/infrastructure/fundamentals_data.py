"""yfinance fundamentals adapter implementing FundamentalsPort.

Ticker.info is a slow scraping call (~1-2 s per symbol) and frequently
missing fields for TASE listings and ETFs — every metric is optional and a
long TTL cache keeps screener refreshes cheap.
"""
import logging
import time
from collections.abc import Callable

from trading_platform.domain import FundamentalData

logger = logging.getLogger(__name__)


def _default_info_fetcher(symbol: str) -> dict:
    import yfinance as yf
    return yf.Ticker(symbol).info or {}


def _num(info: dict, key: str) -> float | None:
    value = info.get(key)
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value == value else None  # drop NaN


class YFinanceFundamentals:
    """Implements trading_platform.application.ports.FundamentalsPort."""

    def __init__(self, info_fetcher: Callable | None = None,
                 cache_ttl_seconds: float = 1800):
        self._fetch = info_fetcher or _default_info_fetcher
        self._ttl = cache_ttl_seconds
        self._cache: dict[str, tuple[float, FundamentalData | None]] = {}

    def get_fundamentals(self, symbol: str) -> FundamentalData | None:
        symbol = symbol.upper()
        cached = self._cache.get(symbol)
        if cached and time.time() - cached[0] < self._ttl:
            return cached[1]

        try:
            info = self._fetch(symbol)
        except Exception:
            logger.exception("fundamentals fetch failed for %s", symbol)
            self._cache[symbol] = (time.time(), None)
            return None

        if not info:
            self._cache[symbol] = (time.time(), None)
            return None

        dividend_yield = _num(info, "dividendYield")
        # yfinance sometimes reports dividendYield as percent (1.2) instead of
        # fraction (0.012); normalize anything above 1 as percent.
        if dividend_yield is not None and dividend_yield > 1.0:
            dividend_yield = dividend_yield / 100.0

        data = FundamentalData(
            symbol=symbol,
            name=info.get("shortName") or info.get("longName") or "",
            market_cap=_num(info, "marketCap"),
            target_price=_num(info, "targetMeanPrice"),
            dividend_yield=dividend_yield,
            roe=_num(info, "returnOnEquity"),
            price_to_book=_num(info, "priceToBook"),
            beta=_num(info, "beta"),
            currency=info.get("currency") or "USD",
        )
        self._cache[symbol] = (time.time(), data)
        return data
