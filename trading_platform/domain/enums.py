from enum import Enum


class SignalType(str, Enum):
    BREAKOUT = "breakout"
    MOMENTUM = "momentum"
    OPTIONS_FLOW = "options"
    MEAN_REVERSION = "mean_reversion"
    TREND_FOLLOWING = "trend_following"


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"


class AssetClass(str, Enum):
    EQUITY = "equity"
    ETF = "etf"
    OPTION = "option"


class OptionType(str, Enum):
    CALL = "call"
    PUT = "put"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    FILLED = "filled"
    REJECTED = "rejected"


class Market(str, Enum):
    US = "US"        # NYSE / NASDAQ
    TASE = "TASE"    # Tel Aviv Stock Exchange


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


def market_for_symbol(symbol: str) -> Market:
    """TASE tickers carry yfinance's .TA suffix; everything else is US."""
    return Market.TASE if symbol.upper().endswith(".TA") else Market.US
