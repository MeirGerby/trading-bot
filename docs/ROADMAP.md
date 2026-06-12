# Roadmap

## Milestones

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Requirements, architecture, folder structure, dev plan | ✅ Done |
| 2 | Core domain models, ports, configuration | ✅ Done |
| 3 | Market data layer — yfinance adapter behind `MarketDataPort`, caching, rate-limit handling | ✅ Done |
| 4 | Memory layer — `MemoryStore` port wrapping JSON persistence (weights, feedback, alerts, knowledge base) | ✅ Done |
| 5 | Strategy engine — migrate breakout/momentum/options into pluggable `Strategy` implementations; add registry | 🔶 Mostly done (options-flow pending an options-data port) |
| 6 | Risk engine — position sizing, exposure, volatility, liquidity, stop-loss/take-profit rule pipeline | Planned |
| 7 | Decision engine — combine signals + risk + learned preferences into scored `Recommendation` with rationale | Planned |
| 8 | Broker abstraction — `BrokerPort` with **paper-trading default**; live trading only with explicit owner approval | Planned |
| 9 | API & dashboard — migrate FastAPI dashboard onto services; recommendations with full reasoning | Planned |
| 10 | Hardening — test coverage, performance, docs, CI | Planned |

## Existing production features (must keep working through migration)

- Telegram alerts every 15 min (breakout / momentum / options signals)
- Feedback learning (`/good_TICKER`, `/bad_TICKER` adjust weights)
- Web dashboard: candlestick charts, alert history, weights, feedback stats
- Daily-trading live view with 5-minute background scans
- Docker deployment on Oracle Cloud VPS
