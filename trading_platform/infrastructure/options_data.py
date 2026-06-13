"""yfinance option-chain adapter implementing OptionsDataPort."""
import logging
from collections.abc import Callable

from trading_platform.domain import OptionContract, OptionType

logger = logging.getLogger(__name__)


def _default_ticker_factory(symbol: str):
    import yfinance as yf
    return yf.Ticker(symbol)


class YFinanceOptionsData:
    """Implements trading_platform.application.ports.OptionsDataPort."""

    def __init__(self, ticker_factory: Callable | None = None):
        self._ticker_factory = ticker_factory or _default_ticker_factory

    def get_option_contracts(self, symbol: str, max_expirations: int = 2) -> list[OptionContract]:
        try:
            ticker = self._ticker_factory(symbol.upper())
            expirations = list(ticker.options or [])[:max_expirations]
        except Exception:
            logger.exception("failed to list expirations for %s", symbol)
            return []

        contracts: list[OptionContract] = []
        for expiration in expirations:
            try:
                chain = ticker.option_chain(expiration)
            except Exception:
                logger.warning("failed to fetch chain %s %s", symbol, expiration)
                continue
            for df, opt_type in ((chain.calls, OptionType.CALL), (chain.puts, OptionType.PUT)):
                for _, row in df.iterrows():
                    try:
                        contracts.append(OptionContract(
                            underlying=symbol.upper(),
                            option_type=opt_type,
                            strike=float(row["strike"]),
                            expiration=expiration,
                            volume=float(row.get("volume") or 0),
                            open_interest=float(row.get("openInterest") or 0),
                            implied_volatility=float(row.get("impliedVolatility") or 0),
                        ))
                    except (ValueError, TypeError, KeyError):
                        continue  # NaN/malformed rows
        return contracts
