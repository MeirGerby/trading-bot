# Project Status

_Last updated: 2026-06-12_

## 📋 Current sprint

- Phase 2: domain models, application ports, configuration — **delivered this session**

## ✅ Completed

- Cloud deployment (Docker + docker-compose on Oracle Cloud VPS; Railway config kept)
- Web dashboard: alerts table, weights, feedback stats
- Real-time candlestick charts (lightweight-charts, 1d/5d/1mo)
- Daily-trading live view: background scans every 5 min, 5-second UI polling, manual rescan
- Phase 1: architecture docs, roadmap, knowledge base, project status tracking
- Phase 2: `trading_platform` package — domain models with validation, Protocol ports, env-driven settings, unit tests

## 📝 Backlog (priority order)

1. Phase 3: `YFinanceMarketData` adapter implementing `MarketDataPort` (batched downloads, cache, retry)
2. Phase 4: `JsonMemoryStore` implementing `MemoryStore`; migrate `feedback.py` onto it
3. Phase 5: port breakout/momentum/options checks to `Strategy` implementations + registry
4. `ScanService` orchestration; make `bot.py` and `dashboard.py` thin adapters
5. Phase 6: risk rule pipeline
6. Persist dashboard scan cache to disk (survive restarts)
7. CI workflow (pytest on push)

## ⚠️ Technical debt

- Legacy flat modules (`scanner.py`, `feedback.py`) duplicate concepts now modeled in `trading_platform.domain` — converge in Phases 3–5
- `dashboard.py` background-scan thread shares no lock with request handlers (GIL-safe for current dict swaps, but fragile)
- `options_iv_percentile_min` weight exists but is unused by scanner logic
- No CI; tests run locally only
- Scan results stored only in memory — lost on container restart

## 🚨 Risks

- **yfinance reliability:** unofficial API, rate limits, breaking changes — mitigated by adapter isolation (Phase 3)
- **Single-file JSON persistence:** no concurrent-write safety between bot and dashboard containers sharing `data/` volume
- **Free-tier VPS:** Oracle can reclaim Always-Free instances; document re-deploy procedure
- **Future live trading:** financial risk — gated behind explicit owner approval, paper-first
