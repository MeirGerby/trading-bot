"""Environment-driven configuration, replacing module-level globals in config.py."""
import os
from dataclasses import dataclass, field

DEFAULT_WATCHLIST = (
    "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL", "AMD",
    "SMCI", "PLTR", "MSTR", "COIN", "HOOD", "RIVN", "LCID",
    "SPY", "QQQ", "IWM",
)

DEFAULT_STRATEGY_PARAMS: dict[str, float] = {
    "breakout_volume_ratio": 2.0,
    "breakout_pct_from_high": 0.98,
    "momentum_rsi_min": 60,
    "momentum_price_above_ma": 20,
    "options_vol_oi_ratio": 2.0,
    "options_iv_percentile_min": 30,
    "min_score_to_alert": 2,
}

DEFAULT_RISK_PARAMS: dict[str, float] = {
    "base_allocation_pct": 0.05,       # target position = equity * pct * confidence
    "max_position_pct": 0.10,          # single position cap vs equity
    "max_total_exposure_pct": 0.80,    # total invested cap vs equity
    "min_cash_reserve_pct": 0.10,      # cash floor after any trade
    "paper_starting_cash": 100_000.0,
}


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    watchlist: tuple[str, ...] = DEFAULT_WATCHLIST
    scan_interval_minutes: int = 15
    strategy_params: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_STRATEGY_PARAMS))
    risk_params: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_RISK_PARAMS))
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
            data_dir=os.getenv("DATA_DIR", "data"),
        )
