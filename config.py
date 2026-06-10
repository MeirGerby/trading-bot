import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Watchlist — tickers to scan
WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL", "AMD",
    "SMCI", "PLTR", "MSTR", "COIN", "HOOD", "RIVN", "LCID",
    "SPY", "QQQ", "IWM",
]

# Scan interval in minutes
SCAN_INTERVAL_MINUTES = 15

# Signal thresholds (adjusted dynamically via feedback)
DEFAULT_WEIGHTS = {
    "breakout_volume_ratio": 2.0,    # volume vs 20d avg
    "breakout_pct_from_high": 0.98,  # price >= 98% of 52w high
    "momentum_rsi_min": 60,          # RSI threshold
    "momentum_price_above_ma": 20,   # MA period
    "options_vol_oi_ratio": 2.0,     # unusual options activity
    "options_iv_percentile_min": 30, # min IV percentile (avoid dead stocks)
    "min_score_to_alert": 2,         # minimum signal score to send alert
}
