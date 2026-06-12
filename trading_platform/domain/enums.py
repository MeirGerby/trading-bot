from enum import Enum


class SignalType(str, Enum):
    BREAKOUT = "breakout"
    MOMENTUM = "momentum"
    OPTIONS_FLOW = "options"


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"


class AssetClass(str, Enum):
    EQUITY = "equity"
    ETF = "etf"
    OPTION = "option"
