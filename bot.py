"""
Telegram bot — sends alerts and handles user feedback commands.

Commands:
  /start       — welcome message
  /scan        — trigger manual scan now
  /weights     — show current signal weights
  /feedback    — show feedback history
  /good_TICKER — mark last alert for TICKER as good ✅
  /bad_TICKER  — mark last alert for TICKER as bad ❌
  /watchlist   — show current watchlist
"""
import json
import logging
import os
import re
from datetime import datetime

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

import config
import feedback as fb
from scanner import scan_all

logger = logging.getLogger(__name__)

# In-memory: last alert per ticker → signal_types (for feedback linkage)
_last_alerts: dict[str, list[str]] = {}

_ALERTS_LOG = os.path.join(os.path.dirname(__file__), "data", "alerts_log.json")
_MAX_LOG_ENTRIES = 200


def log_alert(sig) -> None:
    """Append a sent signal to the persistent alerts log."""
    try:
        os.makedirs(os.path.dirname(_ALERTS_LOG), exist_ok=True)
        if os.path.exists(_ALERTS_LOG):
            with open(_ALERTS_LOG) as f:
                store = json.load(f)
        else:
            store = {"alerts": []}

        store["alerts"].append({
            "ticker": sig.ticker,
            "score": sig.score,
            "signal_types": sig.signal_types,
            "price": sig.price,
            "details": sig.details,
            "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
        })
        store["alerts"] = store["alerts"][-_MAX_LOG_ENTRIES:]

        with open(_ALERTS_LOG, "w") as f:
            json.dump(store, f, indent=2, ensure_ascii=False)
    except Exception:
        logger.exception("Failed to log alert for %s", sig.ticker)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *Market Opportunity Agent* מוכן!\n\n"
        "פקודות:\n"
        "/scan — סריקה ידנית\n"
        "/weights — הגדרות סיגנלים\n"
        "/feedback — היסטוריית פידבק\n"
        "/watchlist — רשימת מניות\n"
        "/good\\_TICKER | /bad\\_TICKER — פידבק על התראה",
        parse_mode="MarkdownV2",
    )


async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    weights = fb.load_weights(config.DEFAULT_WEIGHTS)
    await update.message.reply_text("🔍 סורק שוק... זה יכול לקחת כ-30 שניות.")
    signals = scan_all(config.WATCHLIST, weights)

    if not signals:
        await update.message.reply_text("לא נמצאו הזדמנויות ברגע זה.")
        return

    for sig in signals[:5]:  # cap at 5 per scan
        _last_alerts[sig.ticker] = sig.signal_types
        log_alert(sig)
        await update.message.reply_text(sig.format_message(), parse_mode="MarkdownV2")


async def cmd_weights(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    weights = fb.load_weights(config.DEFAULT_WEIGHTS)
    lines = ["⚙️ *משקלי סיגנלים נוכחיים:*\n"]
    for k, v in weights.items():
        lines.append(f"  `{k}`: {v}")
    await update.message.reply_text("\n".join(lines), parse_mode="MarkdownV2")


async def cmd_feedback_summary(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    summary = fb.get_feedback_summary()
    await update.message.reply_text(summary)


async def cmd_watchlist(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    tickers = ", ".join(config.WATCHLIST)
    await update.message.reply_text(f"📋 *Watchlist:*\n{tickers}", parse_mode="MarkdownV2")


async def cmd_good(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_feedback(update, positive=True)


async def cmd_bad(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_feedback(update, positive=False)


async def _handle_feedback(update: Update, positive: bool) -> None:
    text = update.message.text or ""
    # Extract ticker from /good_AAPL or /bad_AAPL
    match = re.match(r"^/(?:good|bad)[_\s]+([A-Z]+)", text.upper())
    if not match:
        await update.message.reply_text("שימוש: /good\\_TICKER או /bad\\_TICKER", parse_mode="MarkdownV2")
        return

    ticker = match.group(1)
    signal_types = _last_alerts.get(ticker, ["breakout"])  # fallback
    weights = fb.load_weights(config.DEFAULT_WEIGHTS)
    updated = fb.record_feedback(ticker, signal_types, positive, weights)

    icon = "✅" if positive else "❌"
    direction = "גבוהים יותר" if positive else "נמוכים יותר"
    await update.message.reply_text(
        f"{icon} תודה\\! פידבק על *{ticker}* נשמר\\.\n"
        f"הסף לסיגנלים מסוג `{'`, `'.join(signal_types)}` יתכוונן להיות {direction}\\.",
        parse_mode="MarkdownV2",
    )


async def scheduled_scan(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Called by APScheduler — scans and pushes alerts to the configured chat."""
    chat_id = config.TELEGRAM_CHAT_ID
    if not chat_id:
        logger.warning("TELEGRAM_CHAT_ID not set, skipping scheduled scan")
        return

    weights = fb.load_weights(config.DEFAULT_WEIGHTS)
    signals = scan_all(config.WATCHLIST, weights)

    if not signals:
        logger.info("Scheduled scan: no signals found")
        return

    for sig in signals[:5]:
        _last_alerts[sig.ticker] = sig.signal_types
        log_alert(sig)
        await ctx.bot.send_message(
            chat_id=chat_id,
            text=sig.format_message(),
            parse_mode="MarkdownV2",
        )


def build_app() -> Application:
    token = config.TELEGRAM_BOT_TOKEN
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set in .env")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("weights", cmd_weights))
    app.add_handler(CommandHandler("feedback", cmd_feedback_summary))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))

    # /good_AAPL and /bad_AAPL — registered as prefix handlers via MessageHandler
    from telegram.ext import MessageHandler, filters

    app.add_handler(MessageHandler(filters.Regex(r"^/good"), cmd_good))
    app.add_handler(MessageHandler(filters.Regex(r"^/bad"), cmd_bad))

    # Scheduled job — runs every SCAN_INTERVAL_MINUTES
    interval = config.SCAN_INTERVAL_MINUTES * 60
    app.job_queue.run_repeating(scheduled_scan, interval=interval, first=60)

    return app
