"""yfinance adapter implementing MarketDataPort.

yfinance is an unofficial API (see docs/PROJECT_STATUS.md risks), so all
access goes through this adapter: TTL caching, MultiIndex flattening, and
row validation live here and nowhere else.
"""
import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone

from trading_platform.domain import Bar

logger = logging.getLogger(__name__)

# yfinance only accepts fixed period strings; pick the smallest that covers
# the requested lookback, then trim.
_PERIOD_BUCKETS = [(5, "5d"), (30, "1mo"), (90, "3mo"), (180, "6mo"), (365, "1y"), (730, "2y")]


def _period_for(lookback_days: int) -> str:
    for limit, period in _PERIOD_BUCKETS:
        if lookback_days <= limit:
            return period
    return "5y"


def _default_downloader(symbol: str, period: str):
    import yfinance as yf
    return yf.download(symbol, period=period, interval="1d", progress=False, auto_adjust=True)


class YFinanceMarketData:
    """Implements trading_platform.application.ports.MarketDataPort."""

    def __init__(self, downloader: Callable | None = None, cache_ttl_seconds: float = 300):
        self._downloader = downloader or _default_downloader
        self._ttl = cache_ttl_seconds
        self._cache: dict[str, tuple[float, list[Bar]]] = {}

    def get_daily_bars(self, symbol: str, lookback_days: int) -> list[Bar]:
        symbol = symbol.upper()
        cache_key = f"{symbol}:{_period_for(lookback_days)}"
        cached = self._cache.get(cache_key)
        if cached and time.time() - cached[0] < self._ttl:
            return cached[1][-lookback_days:]

        try:
            df = self._downloader(symbol, _period_for(lookback_days))
        except Exception:
            logger.exception("download failed for %s", symbol)
            return []
        if df is None or df.empty:
            return []

        if hasattr(df.columns, "levels"):
            df.columns = df.columns.get_level_values(0)

        bars: list[Bar] = []
        for ts, row in df.iterrows():
            try:
                bar = Bar(
                    time=ts.to_pydatetime().replace(tzinfo=ts.tzinfo or timezone.utc),
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=float(row.get("Volume", 0) or 0),
                )
            except (ValueError, KeyError, TypeError):
                # NaN rows and occasional bad ticks from yfinance — skip, don't fail the scan
                continue
            bars.append(bar)

        self._cache[cache_key] = (time.time(), bars)
        return bars[-lookback_days:]

    def get_last_price(self, symbol: str) -> float | None:
        bars = self.get_daily_bars(symbol, 5)
        return bars[-1].close if bars else None
