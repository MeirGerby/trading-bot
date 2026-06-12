"""Tests for the dual-market screener: fees, risk classification, grid rows,
fundamentals adapter, and autonomous paper execution."""
from datetime import datetime, timedelta, timezone

import pytest

from trading_platform.application.services import (
    DecisionEngine,
    FeeCalculator,
    PortfolioEngine,
    ScanService,
    ScreenerService,
)
from trading_platform.application.services.risk_engine import default_risk_engine
from trading_platform.application.services.screener_service import (
    classify_risk,
    compute_risk_metrics,
)
from trading_platform.application.strategies import BreakoutStrategy, MomentumStrategy
from trading_platform.config import Settings
from trading_platform.config.settings import DEFAULT_FEE_PARAMS, DEFAULT_RISK_PARAMS
from trading_platform.domain import Bar, FundamentalData, Market, RiskLevel, market_for_symbol
from trading_platform.infrastructure.audit import JsonlAuditLog
from trading_platform.infrastructure.fundamentals_data import YFinanceFundamentals
from trading_platform.infrastructure.memory_store import JsonMemoryStore
from trading_platform.infrastructure.paper_broker import PaperBroker


def make_bars(closes, volumes=None):
    volumes = volumes or [1_000_000.0] * len(closes)
    start = datetime(2025, 6, 1, tzinfo=timezone.utc)
    return [Bar(time=start + timedelta(days=i), open=c, high=c * 1.01, low=c * 0.99,
                close=c, volume=v)
            for i, (c, v) in enumerate(zip(closes, volumes))]


class FakeMarketData:
    def __init__(self, bars_by_symbol):
        self.bars_by_symbol = bars_by_symbol

    def get_daily_bars(self, symbol, lookback_days):
        return self.bars_by_symbol.get(symbol, [])[-lookback_days:]

    def get_last_price(self, symbol):
        bars = self.bars_by_symbol.get(symbol, [])
        return bars[-1].close if bars else None


class FakeFundamentals:
    def __init__(self, by_symbol):
        self.by_symbol = by_symbol

    def get_fundamentals(self, symbol):
        return self.by_symbol.get(symbol)


# ---------------------------------------------------------------------------
# Market detection
# ---------------------------------------------------------------------------

class TestMarketDetection:
    def test_us_symbol(self):
        assert market_for_symbol("AAPL") is Market.US

    def test_tase_symbol(self):
        assert market_for_symbol("TEVA.TA") is Market.TASE

    def test_case_insensitive(self):
        assert market_for_symbol("teva.ta") is Market.TASE


# ---------------------------------------------------------------------------
# FeeCalculator
# ---------------------------------------------------------------------------

class TestFeeCalculator:
    def setup_method(self):
        self.calc = FeeCalculator(DEFAULT_FEE_PARAMS)

    def test_us_per_share_with_minimum(self):
        # 50 shares * $0.01 = $0.50 → below $1 minimum → $1
        fees = self.calc.estimate("AAPL", 50, 100.0)
        assert fees["buy_fee"] == 1.0
        assert fees["currency"] == "USD"
        assert fees["market"] == "US"

    def test_us_per_share_above_minimum(self):
        # 500 shares * $0.01 = $5
        fees = self.calc.estimate("AAPL", 500, 100.0)
        assert fees["buy_fee"] == 5.0

    def test_us_fee_capped_at_pct_of_value(self):
        # 1000 shares at $0.05: per-share fee = $10, but value = $50 → cap 1% = $0.50
        fees = self.calc.estimate("PENNY", 1000, 0.05)
        assert fees["buy_fee"] == 0.5

    def test_tase_percentage_with_floor(self):
        # 0.08% of 1000 = 0.8 → below 3 ILS floor → 3
        fees = self.calc.estimate("TEVA.TA", 10, 100.0)
        assert fees["buy_fee"] == 3.0
        assert fees["currency"] == "ILS"
        assert fees["market"] == "TASE"

    def test_tase_percentage_above_floor(self):
        # 0.08% of 100,000 = 80 ILS
        fees = self.calc.estimate("TEVA.TA", 1000, 100.0)
        assert fees["buy_fee"] == 80.0

    def test_round_trip_doubles_one_side(self):
        fees = self.calc.estimate("AAPL", 500, 100.0)
        assert fees["round_trip"] == 10.0

    def test_zero_quantity(self):
        fees = self.calc.estimate("AAPL", 0, 100.0)
        assert fees["buy_fee"] == 0.0


# ---------------------------------------------------------------------------
# Risk classification
# ---------------------------------------------------------------------------

class TestRiskClassification:
    def test_low_risk(self):
        assert classify_risk(0.15, 0.8, 0.10) is RiskLevel.LOW

    def test_high_risk(self):
        assert classify_risk(0.60, 2.0, 0.50) is RiskLevel.HIGH

    def test_medium_risk(self):
        assert classify_risk(0.30, 1.0, 0.20) is RiskLevel.MEDIUM

    def test_missing_beta_still_classifies(self):
        assert classify_risk(0.15, None, 0.10) is RiskLevel.LOW

    def test_compute_metrics_flat_series_is_low(self):
        bars = make_bars([100.0 + (i % 2) * 0.1 for i in range(100)])
        metrics = compute_risk_metrics("X", bars, beta=0.5)
        assert metrics.level is RiskLevel.LOW
        assert metrics.annual_volatility is not None
        assert metrics.max_drawdown < 0.01

    def test_compute_metrics_crash_series_has_drawdown(self):
        closes = [100.0] * 50 + [100.0 - i * 1.5 for i in range(1, 31)]
        metrics = compute_risk_metrics("X", make_bars(closes), beta=None)
        assert metrics.max_drawdown == pytest.approx(0.45, abs=0.01)

    def test_insufficient_history_is_unknown(self):
        metrics = compute_risk_metrics("X", make_bars([100.0] * 5), beta=1.0)
        assert metrics.level is RiskLevel.UNKNOWN


# ---------------------------------------------------------------------------
# ScreenerService
# ---------------------------------------------------------------------------

class TestScreenerService:
    def _build(self):
        bars = make_bars([100.0 + i * 0.1 for i in range(120)])
        market_data = FakeMarketData({"AAPL": bars, "TEVA.TA": bars})
        fundamentals = FakeFundamentals({
            "AAPL": FundamentalData(
                symbol="AAPL", name="Apple Inc.", market_cap=3e12,
                target_price=130.0, dividend_yield=0.005, roe=1.5,
                price_to_book=45.0, beta=1.2, currency="USD"),
            "TEVA.TA": FundamentalData(
                symbol="TEVA.TA", name="Teva", market_cap=2e10,
                target_price=15000.0, dividend_yield=None, roe=0.08,
                price_to_book=1.4, beta=0.9, currency="ILA"),
        })
        return ScreenerService(("AAPL", "TEVA.TA", "MISSING"), market_data,
                               fundamentals, FeeCalculator(DEFAULT_FEE_PARAMS))

    def test_builds_row_per_symbol_with_fundamentals(self):
        result = self._build().build_rows()
        rows = {r["symbol"]: r for r in result["rows"]}
        assert rows["AAPL"]["name"] == "Apple Inc."
        assert rows["AAPL"]["market"] == "US"
        assert rows["AAPL"]["market_cap"] == 3e12
        assert rows["AAPL"]["dividend_yield_pct"] == 0.5
        assert rows["AAPL"]["roe_pct"] == 150.0
        assert rows["AAPL"]["risk_level"] in ("low", "medium", "high")

    def test_tase_prices_normalized_from_agorot(self):
        rows = {r["symbol"]: r for r in self._build().build_rows()["rows"]}
        teva = rows["TEVA.TA"]
        assert teva["market"] == "TASE"
        assert teva["currency"] == "ILS"
        # bars end at ~111.9 agorot → ~1.12 ILS
        assert teva["price"] == pytest.approx(1.12, abs=0.02)
        assert teva["target_price"] == pytest.approx(150.0)
        assert teva["fees"]["currency"] == "ILS"

    def test_missing_data_row_still_present(self):
        rows = {r["symbol"]: r for r in self._build().build_rows()["rows"]}
        missing = rows["MISSING"]
        assert missing["price"] is None
        assert missing["risk_level"] == "unknown"
        assert missing["fees"] is None

    def test_upside_computed_from_target(self):
        rows = {r["symbol"]: r for r in self._build().build_rows()["rows"]}
        # AAPL price ~111.9, target 130 → ~16% upside
        assert rows["AAPL"]["target_upside_pct"] == pytest.approx(16.2, abs=1.0)


# ---------------------------------------------------------------------------
# YFinanceFundamentals adapter
# ---------------------------------------------------------------------------

class TestFundamentalsAdapter:
    def test_extracts_fields(self):
        adapter = YFinanceFundamentals(info_fetcher=lambda s: {
            "shortName": "Apple Inc.", "marketCap": 3e12, "targetMeanPrice": 250.0,
            "dividendYield": 0.0044, "returnOnEquity": 1.6, "priceToBook": 50.0,
            "beta": 1.25, "currency": "USD",
        })
        data = adapter.get_fundamentals("AAPL")
        assert data.name == "Apple Inc."
        assert data.dividend_yield == 0.0044
        assert data.beta == 1.25

    def test_normalizes_percent_style_dividend_yield(self):
        adapter = YFinanceFundamentals(info_fetcher=lambda s: {"dividendYield": 1.2})
        assert adapter.get_fundamentals("X").dividend_yield == pytest.approx(0.012)

    def test_fetch_failure_returns_none_and_caches(self):
        calls = []
        def boom(s):
            calls.append(s)
            raise ConnectionError("down")
        adapter = YFinanceFundamentals(info_fetcher=boom)
        assert adapter.get_fundamentals("X") is None
        assert adapter.get_fundamentals("X") is None  # served from cache
        assert len(calls) == 1


# ---------------------------------------------------------------------------
# Autonomous paper execution
# ---------------------------------------------------------------------------

HOT_BARS = make_bars(
    [100.0 + i * 0.5 for i in range(50)] + [130.0],
    [1_000_000.0] * 50 + [3_000_000.0],
)


def build_auto_service(tmp_path, auto_execute=True):
    settings = Settings(watchlist=("HOT",))
    market_data = FakeMarketData({"HOT": HOT_BARS})
    memory = JsonMemoryStore(tmp_path)
    broker = PaperBroker(memory, 100_000.0)
    service = ScanService(
        settings=settings,
        strategies=(BreakoutStrategy(market_data), MomentumStrategy(market_data)),
        memory=memory,
        market_data=market_data,
        decision_engine=DecisionEngine(memory),
        portfolio_engine=PortfolioEngine(DEFAULT_RISK_PARAMS),
        risk_engine=default_risk_engine(DEFAULT_RISK_PARAMS),
        broker=broker,
        audit=JsonlAuditLog(tmp_path / "audit.jsonl"),
        auto_execute=auto_execute,
    )
    return service, broker


class TestAutoExecution:
    def test_approved_recommendation_is_executed_on_paper(self, tmp_path):
        service, broker = build_auto_service(tmp_path)
        service.scan()
        portfolio = broker.get_portfolio()
        assert len(portfolio.positions) == 1
        assert portfolio.positions[0].instrument.symbol == "HOT"
        assert portfolio.cash < 100_000.0

    def test_trade_logged_with_reasoning(self, tmp_path):
        service, _ = build_auto_service(tmp_path)
        service.scan()
        trades = service.recent_trades()
        assert len(trades) == 1
        trade = trades[0]
        assert trade["symbol"] == "HOT"
        assert trade["side"] == "buy"
        assert trade["status"] == "filled"
        assert "BUY HOT" in trade["reasoning"]
        assert "Risk review" in trade["reasoning"]

    def test_held_position_not_rebought(self, tmp_path):
        service, broker = build_auto_service(tmp_path)
        service.scan()
        service.scan()  # second scan — HOT already held
        assert len(service.recent_trades()) == 1
        assert len(broker.get_portfolio().positions) == 1

    def test_disabled_auto_execute_does_not_trade(self, tmp_path):
        service, broker = build_auto_service(tmp_path, auto_execute=False)
        service.scan()
        assert broker.get_portfolio().positions == ()
        assert service.recent_trades() == []
