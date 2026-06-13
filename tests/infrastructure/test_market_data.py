from datetime import datetime, timezone

import pandas as pd
import pytest

from trading_platform.application.ports import MarketDataPort
from trading_platform.infrastructure.market_data import YFinanceMarketData, _period_for


def make_df(rows: int = 10, start: str = "2026-01-01") -> pd.DataFrame:
    idx = pd.date_range(start, periods=rows, freq="D", tz="UTC")
    base = pd.Series(range(rows), index=idx, dtype=float) + 100
    return pd.DataFrame({
        "Open": base, "High": base + 2, "Low": base - 2, "Close": base + 1,
        "Volume": [1_000_000] * rows,
    })


class CountingDownloader:
    def __init__(self, df: pd.DataFrame):
        self.df = df
        self.calls = 0

    def __call__(self, symbol: str, period: str) -> pd.DataFrame:
        self.calls += 1
        return self.df


class TestPeriodMapping:
    @pytest.mark.parametrize("days,expected", [
        (3, "5d"), (5, "5d"), (20, "1mo"), (200, "1y"), (252, "1y"), (400, "2y"), (2000, "5y"),
    ])
    def test_buckets(self, days, expected):
        assert _period_for(days) == expected


class TestYFinanceMarketData:
    def test_satisfies_port_protocol(self):
        assert isinstance(YFinanceMarketData(downloader=CountingDownloader(make_df())), MarketDataPort)

    def test_converts_rows_to_bars(self):
        md = YFinanceMarketData(downloader=CountingDownloader(make_df(rows=10)))
        bars = md.get_daily_bars("AAPL", 10)
        assert len(bars) == 10
        assert bars[0].close == 101.0
        assert bars[0].time.tzinfo is not None

    def test_trims_to_lookback(self):
        md = YFinanceMarketData(downloader=CountingDownloader(make_df(rows=10)))
        assert len(md.get_daily_bars("AAPL", 4)) == 4

    def test_caches_within_ttl(self):
        dl = CountingDownloader(make_df())
        md = YFinanceMarketData(downloader=dl, cache_ttl_seconds=60)
        md.get_daily_bars("AAPL", 5)
        md.get_daily_bars("AAPL", 5)
        assert dl.calls == 1

    def test_skips_nan_rows(self):
        df = make_df(rows=5)
        df.iloc[2, df.columns.get_loc("Close")] = float("nan")
        md = YFinanceMarketData(downloader=CountingDownloader(df))
        assert len(md.get_daily_bars("AAPL", 5)) == 4

    def test_flattens_multiindex_columns(self):
        df = make_df(rows=3)
        df.columns = pd.MultiIndex.from_product([df.columns, ["AAPL"]])
        md = YFinanceMarketData(downloader=CountingDownloader(df))
        assert len(md.get_daily_bars("AAPL", 3)) == 3

    def test_download_failure_returns_empty(self):
        def boom(symbol, period):
            raise ConnectionError("rate limited")
        md = YFinanceMarketData(downloader=boom)
        assert md.get_daily_bars("AAPL", 5) == []

    def test_last_price(self):
        md = YFinanceMarketData(downloader=CountingDownloader(make_df(rows=5)))
        assert md.get_last_price("AAPL") == 105.0

    def test_last_price_none_when_no_data(self):
        md = YFinanceMarketData(downloader=CountingDownloader(make_df(rows=0)))
        assert md.get_last_price("AAPL") is None
