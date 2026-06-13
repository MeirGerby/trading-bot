"""Position sizing and portfolio arithmetic.

TASE prices (yfinance .TA tickers) arrive in agorot (ILA = ILS/100).
All portfolio math normalises them to USD using ils_to_usd from risk_params
so equity, exposure, and position sizing are all in a single currency.
"""
import math

from trading_platform.domain import PortfolioState

_DEFAULT_ILS_USD = 0.27  # fallback when not in risk_params


class PortfolioEngine:
    def __init__(self, risk_params: dict[str, float]):
        self._params = risk_params
        self._ils_usd: float = risk_params.get("ils_to_usd", _DEFAULT_ILS_USD)

    # ------------------------------------------------------------------
    # Currency helpers
    # ------------------------------------------------------------------

    def _to_usd(self, price: float, symbol: str) -> float:
        """Convert a vendor-native price to USD.  TASE agorot → USD."""
        if symbol.endswith(".TA"):
            return price * 0.01 * self._ils_usd
        return price

    def _normalized_exposure(self, portfolio: PortfolioState) -> float:
        """Sum of open-position values converted to USD."""
        total = 0.0
        for p in portfolio.positions:
            total += self._to_usd(p.cost_basis, p.instrument.symbol)
        return total

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def equity(self, portfolio: PortfolioState) -> float:
        """USD-normalised portfolio equity (cash + positions)."""
        return portfolio.cash + self._normalized_exposure(portfolio)

    def position_value_usd(self, quantity: float, price: float, symbol: str) -> float:
        """Convert quantity × price to USD."""
        return self._to_usd(quantity * price, symbol)

    def propose_quantity(self, portfolio: PortfolioState, price: float,
                         confidence: float, symbol: str = "") -> float:
        """Whole-share count scaled by confidence within the base allocation.

        `symbol` is used for currency normalisation; omit for US-only callers.
        """
        if price <= 0:
            return 0.0
        target_value = self.equity(portfolio) * self._params["base_allocation_pct"] * confidence
        price_usd = self._to_usd(price, symbol)
        if price_usd <= 0:
            return 0.0
        return float(math.floor(target_value / price_usd))
