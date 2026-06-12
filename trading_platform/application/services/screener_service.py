"""ScreenerService — the unified dual-market stock screener grid.

For every watchlist symbol it assembles one row combining:
- vendor fundamentals (market cap, target price, dividend yield, ROE, P/B)
- risk metrics derived from price history (annual volatility, max drawdown)
  blended with beta into a low/medium/high risk level
- dynamically calculated transaction fees for a nominal position

TASE listings (.TA suffix) are priced in agorot (currency "ILA"); values
are normalized to ILS for display and fee calculation.
"""
import logging
import math
from datetime import datetime, timezone

from trading_platform.application.indicators import rsi, sma
from trading_platform.application.ports import (
    FundamentalsPort,
    MarketDataPort,
    SymbolRepositoryPort,
)
from trading_platform.application.services.fee_calculator import FeeCalculator
from trading_platform.domain import Bar, RiskLevel, RiskMetrics, market_for_symbol

logger = logging.getLogger(__name__)

NOMINAL_TRADE_VALUE = 5_000.0  # fee estimates are quoted for a position this size
_RISK_LOOKBACK_DAYS = 252


def compute_risk_metrics(symbol: str, bars: list[Bar],
                         beta: float | None) -> RiskMetrics:
    """Annualized volatility + max drawdown from daily closes, plus beta."""
    closes = [b.close for b in bars if b.close > 0]
    if len(closes) < 30:
        return RiskMetrics(symbol=symbol, beta=beta, level=RiskLevel.UNKNOWN)

    returns = [(b / a) - 1.0 for a, b in zip(closes, closes[1:])]
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    annual_vol = math.sqrt(variance) * math.sqrt(252)

    peak = closes[0]
    max_dd = 0.0
    for c in closes:
        peak = max(peak, c)
        max_dd = max(max_dd, (peak - c) / peak)

    return RiskMetrics(
        symbol=symbol,
        annual_volatility=round(annual_vol, 4),
        max_drawdown=round(max_dd, 4),
        beta=beta,
        level=classify_risk(annual_vol, beta, max_dd),
    )


def classify_risk(annual_vol: float, beta: float | None, max_dd: float) -> RiskLevel:
    """Score 0-6 across the three dimensions; <=1 low, <=3 medium, else high."""
    score = 0
    score += 0 if annual_vol < 0.25 else 1 if annual_vol < 0.45 else 2
    if beta is not None:
        score += 0 if abs(beta) < 0.9 else 1 if abs(beta) < 1.4 else 2
    score += 0 if max_dd < 0.15 else 1 if max_dd < 0.35 else 2
    return RiskLevel.LOW if score <= 1 else RiskLevel.MEDIUM if score <= 3 else RiskLevel.HIGH


def technical_snapshot(symbol: str, market_data: MarketDataPort) -> dict | None:
    """On-demand technical indicators for any symbol (global search view)."""
    bars = market_data.get_daily_bars(symbol, 252)
    if len(bars) < 21:
        return None

    closes = [b.close for b in bars]
    volumes = [b.volume for b in bars]
    price = closes[-1]
    prev = closes[-2] if len(closes) > 1 else price

    ma20 = sma(closes, 20)
    current_rsi = rsi(closes)
    high_52w = max(closes)
    avg_volume_20 = sum(volumes[-21:-1]) / 20 if len(volumes) >= 21 else 0.0
    vol_ratio = volumes[-1] / avg_volume_20 if avg_volume_20 > 0 else None

    return {
        "symbol": symbol,
        "market": market_for_symbol(symbol).value,
        "price": round(price, 2),
        "change_1d_pct": round((price / prev - 1) * 100, 2) if prev > 0 else None,
        "rsi": round(current_rsi, 1) if current_rsi is not None else None,
        "ma20": round(ma20, 2) if ma20 is not None else None,
        "pct_vs_ma20": round((price / ma20 - 1) * 100, 1) if ma20 else None,
        "high_52w": round(high_52w, 2),
        "pct_of_52w_high": round(price / high_52w * 100, 1) if high_52w > 0 else None,
        "volume_ratio": round(vol_ratio, 2) if vol_ratio is not None else None,
    }


class ScreenerService:
    def __init__(self, watchlist: tuple[str, ...], market_data: MarketDataPort,
                 fundamentals: FundamentalsPort, fee_calculator: FeeCalculator,
                 symbol_repository: SymbolRepositoryPort | None = None):
        self._watchlist = watchlist
        self._market_data = market_data
        self._fundamentals = fundamentals
        self._fees = fee_calculator
        self._symbols = symbol_repository

    def active_symbols(self) -> tuple[str, ...]:
        if self._symbols is not None:
            try:
                watchlist = self._symbols.get_watchlist()
                if watchlist:
                    return watchlist
            except Exception:
                logger.exception("symbol repository unavailable, using static watchlist")
        return self._watchlist

    def build_rows(self) -> dict:
        rows = []
        for symbol in self.active_symbols():
            try:
                rows.append(self._build_row(symbol))
            except Exception:
                logger.exception("screener row failed for %s", symbol)
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "rows": rows,
        }

    def _build_row(self, symbol: str) -> dict:
        fund = self._fundamentals.get_fundamentals(symbol)
        bars = self._market_data.get_daily_bars(symbol, _RISK_LOOKBACK_DAYS)
        price = bars[-1].close if bars else None

        # TASE quotes arrive in agorot (ILA = ILS/100); normalize to ILS
        scale = 0.01 if fund and fund.currency == "ILA" else 1.0
        display_price = price * scale if price is not None else None
        target = fund.target_price * scale if fund and fund.target_price else None

        risk = compute_risk_metrics(symbol, bars, fund.beta if fund else None)

        fees = None
        if display_price and display_price > 0:
            quantity = max(1.0, NOMINAL_TRADE_VALUE // display_price)
            fees = self._fees.estimate(symbol, quantity, display_price)

        upside_pct = None
        if target and display_price:
            upside_pct = round((target / display_price - 1) * 100, 1)

        return {
            "symbol": symbol,
            "name": fund.name if fund else "",
            "market": market_for_symbol(symbol).value,
            "currency": ("ILS" if scale != 1.0 else fund.currency) if fund else "USD",
            "price": round(display_price, 2) if display_price else None,
            "market_cap": fund.market_cap if fund else None,
            "target_price": round(target, 2) if target else None,
            "target_upside_pct": upside_pct,
            "dividend_yield_pct": round(fund.dividend_yield * 100, 2)
                if fund and fund.dividend_yield is not None else None,
            "roe_pct": round(fund.roe * 100, 1)
                if fund and fund.roe is not None else None,
            "price_to_book": round(fund.price_to_book, 2)
                if fund and fund.price_to_book is not None else None,
            "beta": round(fund.beta, 2) if fund and fund.beta is not None else None,
            "annual_volatility_pct": round(risk.annual_volatility * 100, 1)
                if risk.annual_volatility is not None else None,
            "max_drawdown_pct": round(risk.max_drawdown * 100, 1)
                if risk.max_drawdown is not None else None,
            "risk_level": risk.level.value,
            "fees": fees,
        }
