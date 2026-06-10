"""
Entry point — starts the Telegram bot with the scheduled scanner.
"""
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

from bot import build_app


def main() -> None:
    app = build_app()
    logging.getLogger(__name__).info("Bot started. Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
