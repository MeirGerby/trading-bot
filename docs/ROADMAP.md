# Roadmap

## Milestones

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Requirements, architecture, folder structure, dev plan | ✅ Done |
| 2 | Core domain models, ports, configuration | ✅ Done |
| 3 | Market data layer — yfinance adapter behind `MarketDataPort`, caching, rate-limit handling | ✅ Done |
| 4 | Memory layer — `MemoryStore` port wrapping JSON persistence (weights, feedback, alerts, knowledge base) | ✅ Done |
| 5 | Strategy engine — breakout/momentum/options as pluggable `Strategy` implementations | ✅ Done |
| 6 | Risk engine — position sizing, exposure, cash-reserve rule pipeline (volatility/liquidity/stop-loss rules pending) | ✅ Core done |
| 7 | Decision engine — signals + learned feedback priors → scored `Recommendation` with rationale | ✅ Done |
| 8 | Broker abstraction — `BrokerPort` + `PaperBroker` (paper only; live trading requires explicit owner approval) | ✅ Done |
| 9 | API & dashboard — bot.py and dashboard.py are thin adapters over ScanService; audit log JSONL | ✅ Core done (audit/portfolio UI pending) |
| 10 | Hardening — test coverage, performance, docs, CI | ⏳ In progress (113 tests; CI pending) |

## Existing production features (must keep working through migration)

- Telegram alerts every 15 min (breakout / momentum / options signals)
- Feedback learning (`/good_TICKER`, `/bad_TICKER` adjust weights)
- Web dashboard: candlestick charts, alert history, weights, feedback stats
- Daily-trading live view with 5-minute background scans
- Docker deployment on Oracle Cloud VPS
