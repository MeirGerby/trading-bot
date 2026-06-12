"""Environment-driven configuration, replacing module-level globals in config.py."""
import os
from dataclasses import dataclass, field

DEFAULT_US_WATCHLIST = (
    "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL", "AMD",
    "SMCI", "PLTR", "MSTR", "COIN", "HOOD", "RIVN", "LCID",
    "SPY", "QQQ", "IWM",
)

# TASE listings use yfinance's .TA suffix; prices arrive in agorot (ILA).
DEFAULT_TASE_WATCHLIST = (
    "TEVA.TA", "NICE.TA", "ESLT.TA", "ICL.TA",
    "LUMI.TA", "POLI.TA", "DSCT.TA", "MZTF.TA",
)

# The default initial watchlist — NOT a scanning limit. The symbol repository
# seeds the user watchlist from this on first run; symbols are added/removed
# dynamically at runtime (dashboard pins, /api/watchlist) without restart.
DEFAULT_WATCHLIST = DEFAULT_US_WATCHLIST + DEFAULT_TASE_WATCHLIST

DEFAULT_STRATEGY_PARAMS: dict[str, float] = {
    "breakout_volume_ratio": 2.0,
    "breakout_pct_from_high": 0.98,
    "momentum_rsi_min": 60,
    "momentum_price_above_ma": 20,
    "options_vol_oi_ratio": 2.0,
    "options_iv_percentile_min": 30,
    "mean_reversion_ma_period": 20,
    "mean_reversion_rsi_max": 35.0,
    "mean_reversion_pct_below_ma": 0.03,
    "trend_fast_ma": 20,
    "trend_slow_ma": 50,
    "trend_rsi_min": 50.0,
    "min_score_to_alert": 2,
}

DEFAULT_RISK_PARAMS: dict[str, float] = {
    "base_allocation_pct": 0.05,       # target position = equity * pct * confidence
    "max_position_pct": 0.10,          # single position cap vs equity
    "max_total_exposure_pct": 0.80,    # total invested cap vs equity
    "min_cash_reserve_pct": 0.10,      # cash floor after any trade
    "paper_starting_cash": 100_000.0,
    # Currency normalization: TASE prices arrive in agorot (ILA = ILS/100)
    "ils_to_usd": 0.27,               # ILS → USD conversion rate for portfolio math
    # Exit rules — thresholds that trigger autonomous SELL orders
    "stop_loss_pct": 0.08,            # exit when position is down >8% from entry
    "take_profit_pct": 0.20,          # exit when position is up >20% from entry
    "signal_decay_scans": 0.0,        # >0 enables decay exit when symbol has no signals
}

DEFAULT_FEE_PARAMS: dict[str, float] = {
    # US brokers (IBKR-style): cents per share, with a floor and a value cap
    "us_fee_per_share": 0.01,
    "us_fee_min": 1.0,
    "us_fee_max_pct_of_value": 0.01,   # fee never exceeds 1% of trade value
    # Israeli brokers: percentage of trade value with a minimum floor (ILS)
    "tase_fee_pct": 0.0008,            # 0.08%
    "tase_fee_min": 3.0,
}


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    watchlist: tuple[str, ...] = DEFAULT_WATCHLIST
    scan_interval_minutes: int = 15
    # Batching guards against vendor rate limits on large dynamic watchlists:
    # pause scan_throttle_seconds after every scan_batch_size symbols.
    scan_batch_size: int = 20
    scan_throttle_seconds: float = 2.0
    strategy_params: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_STRATEGY_PARAMS))
    risk_params: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_RISK_PARAMS))
    fee_params: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_FEE_PARAMS))
    # Autonomous execution stays paper-only (ADR-5): the broker wired in
    # bootstrap is PaperBroker; this flag never enables live trading.
    auto_execute_paper: bool = True
    data_dir: str = "data"

    def __post_init__(self) -> None:
        if self.scan_interval_minutes <= 0:
            raise ValueError("scan_interval_minutes must be positive")
        if not self.watchlist:
            raise ValueError("watchlist must not be empty")

    @classmethod
    def from_env(cls) -> "Settings":
        watchlist_env = os.getenv("WATCHLIST", "")
        watchlist = (
            tuple(t.strip().upper() for t in watchlist_env.split(",") if t.strip())
            if watchlist_env else DEFAULT_WATCHLIST
        )
        return cls(
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            watchlist=watchlist,
            scan_interval_minutes=int(os.getenv("SCAN_INTERVAL_MINUTES", "15")),
            scan_batch_size=int(os.getenv("SCAN_BATCH_SIZE", "20")),
            scan_throttle_seconds=float(os.getenv("SCAN_THROTTLE_SECONDS", "2.0")),
            auto_execute_paper=os.getenv("AUTO_EXECUTE_PAPER", "true").lower() in ("1", "true", "yes"),
            data_dir=os.getenv("DATA_DIR", "data"),
        )
