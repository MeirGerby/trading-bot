# Project Status

_Last updated: 2026-06-12_

## 📋 Current sprint

- Phases 1–3 delivered this session: docs, domain models, ports, settings, yfinance market-data adapter
- Next sprint: Phase 4 memory layer

## ✅ Completed

- Cloud deployment (Docker + docker-compose on Oracle Cloud VPS; Railway config kept)
- Web dashboard: alerts table, weights, feedback stats
- Real-time candlestick charts (lightweight-charts, 1d/5d/1mo)
- Daily-trading live view: background scans every 5 min, 5-second UI polling, manual rescan
- Phase 1: architecture docs, roadmap, knowledge base, project status tracking
- Phase 2: `trading_platform` package — domain models with validation, Protocol ports, env-driven settings, unit tests
- Phase 3: `YFinanceMarketData` adapter — TTL cache, MultiIndex flattening, NaN-row skipping, injectable downloader (9 tests)

## 📝 Backlog (priority order)

1. Phase 4: `JsonMemoryStore` implementing `MemoryStore`; migrate `feedback.py` onto it
2. Phase 5: port breakout/momentum/options checks to `Strategy` implementations + registry
3. `ScanService` orchestration; make `bot.py` and `dashboard.py` thin adapters
4. Phase 6: risk rule pipeline
5. Persist dashboard scan cache to disk (survive restarts)
6. CI workflow (pytest on push)

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
