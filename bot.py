"""
Telegram bot — thin adapter over trading_platform.ScanService.

Commands:
  /start       — welcome message
  /scan        — trigger manual scan now
  /weights     — show current signal weights
  /feedback    — show feedback history
  /good_TICKER — mark last alert for TICKER as good ✅
  /bad_TICKER  — mark last alert for TICKER as bad ❌
  /watchlist   — show current watchlist

All scanning/decision/risk logic lives in the platform; this module only
formats messages and records feedback.
"""
import asyncio
import json
import logging
import os
import re

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

import config
import feedback as fb
from trading_platform.application.services import recommendation_to_dict
from trading_platform.bootstrap import build_scan_service

logger = logging.getLogger(__name__)

scan_service = build_scan_service()

# In-memory: last alert per ticker → signal_types (for feedback linkage)
_last_alerts: dict[str, list[str]] = {}

_ALERTS_LOG = os.path.join(os.path.dirname(__file__), "data", "alerts_log.json")
_MAX_LOG_ENTRIES = 200
_MAX_ALERTS_PER_SCAN = 5

_MDV2_SPECIALS = set("_*[]()~`>#+-=|{}.!")
_ICONS = {"breakout": "🚀", "momentum": "📈", "options": "⚡"}


def _esc(text) -> str:
    return "".join("\\" + ch if ch in _MDV2_SPECIALS else ch for ch in str(text))


def format_recommendation(rec: dict) -> str:
    icons = "".join(_ICONS.get(t, "") for t in rec["signal_types"])
    lines = [
        f"{icons} *{_esc(rec['ticker'])}* — ניקוד {rec['score']}",
        _esc(f"מחיר: ${rec['price']:.2f}"),
        _esc(f"ביטחון: {rec['confidence'] * 100:.0f}%"),
    ]
    for key, value in rec["details"].items():
        lines.append(_esc(f"• {key}: {value}"))
    if not rec.get("approved", True):
        failed = ", ".join(c["rule"] for c in rec["risk_checks"] if not c["passed"])
        lines.append(_esc(f"⚠️ לא עבר בדיקות סיכון: {failed}"))
    ticker = rec["ticker"]
    lines.append(f"/good\\_{ticker} \\| /bad\\_{ticker}")
    return "\n".join(lines)


def log_alert(rec: dict) -> None:
    """Append a sent alert to the dashboard's alert history."""
    try:
        os.makedirs(os.path.dirname(_ALERTS_LOG), exist_ok=True)
        if os.path.exists(_ALERTS_LOG):
            with open(_ALERTS_LOG) as f:
                store = json.load(f)
        else:
            store = {"alerts": []}
        store["alerts"].append(rec)
        store["alerts"] = store["alerts"][-_MAX_LOG_ENTRIES:]
        with open(_ALERTS_LOG, "w") as f:
            json.dump(store, f, indent=2, ensure_ascii=False)
    except Exception:
        logger.exception("Failed to log alert for %s", rec.get("ticker"))


async def _scan_recommendations() -> list[dict]:
    report = await asyncio.to_thread(scan_service.scan)
    return [recommendation_to_dict(r) for r in report.recommendations]


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
    await update.message.reply_text("🔍 סורק שוק... זה יכול לקחת מספר דקות.")
    recs = await _scan_recommendations()

    if not recs:
        await update.message.reply_text("לא נמצאו הזדמנויות ברגע זה.")
        return

    for rec in recs[:_MAX_ALERTS_PER_SCAN]:
        _last_alerts[rec["ticker"]] = rec["signal_types"]
        log_alert(rec)
        await update.message.reply_text(format_recommendation(rec), parse_mode="MarkdownV2")


async def cmd_weights(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    weights = scan_service.current_params()
    lines = ["⚙️ *משקלי סיגנלים נוכחיים:*\n"]
    for k, v in weights.items():
        lines.append(f"  `{_esc(k)}`: {_esc(v)}")
    await update.message.reply_text("\n".join(lines), parse_mode="MarkdownV2")


async def cmd_feedback_summary(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(fb.get_feedback_summary())


async def cmd_watchlist(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    tickers = ", ".join(config.WATCHLIST)
    await update.message.reply_text(f"📋 *Watchlist:*\n{_esc(tickers)}", parse_mode="MarkdownV2")


async def cmd_good(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_feedback(update, positive=True)


async def cmd_bad(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_feedback(update, positive=False)


async def _handle_feedback(update: Update, positive: bool) -> None:
    text = update.message.text or ""
    match = re.match(r"^/(?:good|bad)[_\s]+([A-Z]+)", text.upper())
    if not match:
        await update.message.reply_text("שימוש: /good\\_TICKER או /bad\\_TICKER", parse_mode="MarkdownV2")
        return

    ticker = match.group(1)
    signal_types = _last_alerts.get(ticker, ["breakout"])  # fallback
    weights = fb.load_weights(config.DEFAULT_WEIGHTS)
    fb.record_feedback(ticker, signal_types, positive, weights)

    icon = "✅" if positive else "❌"
    direction = "גבוהים יותר" if positive else "נמוכים יותר"
    await update.message.reply_text(
        f"{icon} תודה\\! פידבק על *{ticker}* נשמר\\.\n"
        f"הסף לסיגנלים מסוג `{'`, `'.join(signal_types)}` יתכוונן להיות {direction}\\.",
        parse_mode="MarkdownV2",
    )


async def scheduled_scan(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Called by the job queue — scans and pushes alerts to the configured chat."""
    chat_id = config.TELEGRAM_CHAT_ID
    if not chat_id:
        logger.warning("TELEGRAM_CHAT_ID not set, skipping scheduled scan")
        return

    recs = await _scan_recommendations()
    if not recs:
        logger.info("Scheduled scan: no recommendations")
        return

    for rec in recs[:_MAX_ALERTS_PER_SCAN]:
        _last_alerts[rec["ticker"]] = rec["signal_types"]
        log_alert(rec)
        await ctx.bot.send_message(
            chat_id=chat_id,
            text=format_recommendation(rec),
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
