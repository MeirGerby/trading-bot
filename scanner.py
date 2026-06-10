"""
Market scanner — detects breakout, momentum, and options unusual activity.
Returns Signal objects that the bot can format and send.
"""
import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    ticker: str
    signal_types: list[str]      # e.g. ["breakout", "momentum"]
    score: int                   # number of signals triggered
    price: float
    details: dict = field(default_factory=dict)

    def format_message(self) -> str:
        icons = {"breakout": "🚀", "momentum": "📈", "options": "⚡"}
        type_str = " ".join(icons.get(t, "•") + t for t in self.signal_types)
        lines = [
            f"*{self.ticker}* — {type_str}",
            f"מחיר: ${self.price:.2f}",
        ]
        for k, v in self.details.items():
            lines.append(f"  • {k}: {v}")
        lines.append(f"\n💬 /good\\_{self.ticker} | /bad\\_{self.ticker}")
        return "\n".join(lines)


def _safe_download(ticker: str, period: str = "6mo", interval: str = "1d") -> Optional[pd.DataFrame]:
    try:
        df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
        if df is None or df.empty or len(df) < 20:
            return None
        # Flatten MultiIndex columns if present
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception as e:
        logger.warning(f"Failed to download {ticker}: {e}")
        return None


def check_breakout(ticker: str, weights: dict) -> Optional[dict]:
    df = _safe_download(ticker)
    if df is None:
        return None

    close = df["Close"]
    volume = df["Volume"]

    current_price = float(close.iloc[-1])
    high_52w = float(close.rolling(252, min_periods=50).max().iloc[-1])
    avg_volume_20 = float(volume.iloc[-21:-1].mean())
    last_volume = float(volume.iloc[-1])

    vol_ratio = last_volume / avg_volume_20 if avg_volume_20 > 0 else 0
    price_vs_high = current_price / high_52w if high_52w > 0 else 0

    min_vol_ratio = weights.get("breakout_volume_ratio", 2.0)
    min_price_pct = weights.get("breakout_pct_from_high", 0.98)

    if vol_ratio >= min_vol_ratio and price_vs_high >= min_price_pct:
        return {
            "volume_ratio": f"{vol_ratio:.1f}x ממוצע",
            "52w_high": f"${high_52w:.2f} ({price_vs_high*100:.1f}%)",
        }
    return None


def check_momentum(ticker: str, weights: dict) -> Optional[dict]:
    df = _safe_download(ticker)
    if df is None:
        return None

    close = df["Close"].squeeze()
    ma_period = int(weights.get("momentum_price_above_ma", 20))
    rsi_min = float(weights.get("momentum_rsi_min", 60))

    ma = close.rolling(ma_period).mean()
    current_price = float(close.iloc[-1])
    current_ma = float(ma.iloc[-1])

    # RSI calculation
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = float((100 - 100 / (1 + rs)).iloc[-1])

    price_above_ma = current_price > current_ma

    if price_above_ma and rsi >= rsi_min:
        return {
            "RSI": f"{rsi:.1f}",
            f"MA{ma_period}": f"${current_ma:.2f} (מחיר {((current_price/current_ma)-1)*100:.1f}% מעל)",
        }
    return None


def check_options_activity(ticker: str, weights: dict) -> Optional[dict]:
    try:
        stock = yf.Ticker(ticker)
        expirations = stock.options
        if not expirations:
            return None

        # Look at the nearest 2 expirations
        vol_oi_threshold = float(weights.get("options_vol_oi_ratio", 2.0))
        best = None
        best_ratio = 0.0

        for exp in expirations[:2]:
            chain = stock.option_chain(exp)
            for df in [chain.calls, chain.puts]:
                if df.empty:
                    continue
                df = df.copy()
                df = df[df["openInterest"] > 100]  # filter low OI
                if df.empty:
                    continue
                df["vol_oi"] = df["volume"].fillna(0) / df["openInterest"].replace(0, np.nan)
                top = df.nlargest(1, "vol_oi")
                if top.empty:
                    continue
                ratio = float(top["vol_oi"].iloc[0])
                if ratio > best_ratio:
                    best_ratio = ratio
                    option_type = "CALL" if df is chain.calls else "PUT"
                    strike = float(top["strike"].iloc[0])
                    iv = float(top["impliedVolatility"].iloc[0]) if "impliedVolatility" in top else 0
                    best = {
                        "סוג": option_type,
                        "strike": f"${strike:.2f}",
                        "פקיעה": exp,
                        "Vol/OI": f"{ratio:.1f}x",
                        "IV": f"{iv*100:.0f}%",
                    }

        if best and best_ratio >= vol_oi_threshold:
            return best
    except Exception as e:
        logger.warning(f"Options check failed for {ticker}: {e}")
    return None


def scan_ticker(ticker: str, weights: dict) -> Optional[Signal]:
    """Run all checks on a single ticker and return a Signal if score >= threshold."""
    signal_types = []
    details = {}
    price = 0.0

    breakout = check_breakout(ticker, weights)
    if breakout:
        signal_types.append("breakout")
        details.update(breakout)

    momentum = check_momentum(ticker, weights)
    if momentum:
        signal_types.append("momentum")
        details.update(momentum)

    options = check_options_activity(ticker, weights)
    if options:
        signal_types.append("options")
        details.update(options)

    if not signal_types:
        return None

    # Get current price
    try:
        info = yf.Ticker(ticker).fast_info
        price = float(getattr(info, "last_price", 0) or 0)
    except Exception:
        pass

    score = len(signal_types)
    min_score = int(weights.get("min_score_to_alert", 1))
    if score < min_score:
        return None

    return Signal(ticker=ticker, signal_types=signal_types, score=score, price=price, details=details)


def scan_all(watchlist: list[str], weights: dict) -> list[Signal]:
    """Scan all tickers and return signals sorted by score."""
    results = []
    for ticker in watchlist:
        try:
            sig = scan_ticker(ticker, weights)
            if sig:
                results.append(sig)
                logger.info(f"Signal: {ticker} — {sig.signal_types} (score {sig.score})")
        except Exception as e:
            logger.error(f"Error scanning {ticker}: {e}")
    return sorted(results, key=lambda s: s.score, reverse=True)
