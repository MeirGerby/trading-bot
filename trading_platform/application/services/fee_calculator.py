"""FeeCalculator — estimates per-trade transaction fees by market.

Rules are parameter-driven (Settings.fee_params):
- US (NYSE/NASDAQ): flat cents-per-share with a minimum, capped at a
  percentage of trade value (the IBKR tiered model shape).
- TASE: percentage of trade value with a minimum floor, in ILS.
"""
from trading_platform.domain import Market, market_for_symbol


class FeeCalculator:
    def __init__(self, fee_params: dict[str, float]):
        self._p = fee_params

    def estimate(self, symbol: str, quantity: float, price: float) -> dict:
        """Fee for one side (buy or sell) of a trade; buy and sell cost the same.

        Returns {market, currency, buy_fee, sell_fee, round_trip, trade_value}.
        """
        market = market_for_symbol(symbol)
        value = max(0.0, quantity * price)

        if market is Market.TASE:
            fee = max(value * self._p["tase_fee_pct"], self._p["tase_fee_min"]) if value > 0 else 0.0
            currency = "ILS"
        else:
            if value > 0 and quantity > 0:
                fee = max(quantity * self._p["us_fee_per_share"], self._p["us_fee_min"])
                fee = min(fee, value * self._p["us_fee_max_pct_of_value"])
            else:
                fee = 0.0
            currency = "USD"

        return {
            "market": market.value,
            "currency": currency,
            "buy_fee": round(fee, 2),
            "sell_fee": round(fee, 2),
            "round_trip": round(fee * 2, 2),
            "trade_value": round(value, 2),
        }
