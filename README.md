# Trading Bot → Autonomous Trading Platform

A Telegram market-alert bot evolving into a layered autonomous trading
platform. Scans a watchlist for breakout / momentum / unusual-options
signals, sends Telegram alerts, learns from owner feedback, and serves a
live web dashboard with candlestick charts and real-time recommendations.

## Running (Docker)

```bash
cp .env.example .env   # fill TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
docker compose up -d --build
```

- Bot: Telegram polling, scans every 15 min
- Dashboard: http://localhost:8080 (charts, alerts, daily-trading live view)

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

## Project docs

| Doc | Purpose |
|-----|---------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Layers, dependency rules, migration plan |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Phase milestones |
| [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) | Sprint, backlog, debt, risks |
| [docs/KNOWLEDGE_BASE.md](docs/KNOWLEDGE_BASE.md) | Learned rules and preferences |

Legacy flat modules (`bot.py`, `scanner.py`, `feedback.py`, `dashboard.py`)
remain the production entry points while functionality migrates into the
`trading_platform/` package.
