"""Position sizing and portfolio arithmetic."""
import math

from trading_platform.domain import PortfolioState


class PortfolioEngine:
    def __init__(self, risk_params: dict[str, float]):
        self._params = risk_params

    @staticmethod
    def equity(portfolio: PortfolioState) -> float:
        return portfolio.cash + portfolio.exposure()

    def propose_quantity(self, portfolio: PortfolioState, price: float,
                         confidence: float) -> float:
        """Whole-share quantity scaled by confidence within the base allocation."""
        if price <= 0:
            return 0.0
        target_value = self.equity(portfolio) * self._params["base_allocation_pct"] * confidence
        return float(math.floor(target_value / price))
