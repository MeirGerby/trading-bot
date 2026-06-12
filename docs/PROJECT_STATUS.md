# Project Status

_Last updated: 2026-06-12_

## 📋 Current sprint

- Phases 4–5 delivered: JsonMemoryStore (atomic writes + flock), feedback.py migrated onto it,
  indicators (Wilder RSI, SMA), Breakout + Momentum strategies behind the Strategy port
- Next sprint: ScanService orchestration + options-data port

## ✅ Completed

- Cloud deployment (Docker + docker-compose on Oracle Cloud VPS; Railway config kept)
- Web dashboard: alerts table, weights, feedback stats
- Real-time candlestick charts (lightweight-charts, 1d/5d/1mo)
- Daily-trading live view: background scans every 5 min, 5-second UI polling, manual rescan
- Phase 1: architecture docs, roadmap, knowledge base, project status tracking
- Phase 2: `trading_platform` package — domain models with validation, Protocol ports, env-driven settings, unit tests
- Phase 3: `YFinanceMarketData` adapter — TTL cache, MultiIndex flattening, NaN-row skipping, injectable downloader (9 tests)
- Phase 4: `JsonMemoryStore` — atomic temp-file writes, flock serialization across bot/dashboard containers,
  legacy-compatible feedback format; `feedback.py` migrated with regression tests
- Phase 5 (partial): pure-python indicators (proper Wilder RSI replacing legacy approximation, SMA);
  `BreakoutStrategy` + `MomentumStrategy` with normalized strength scores

## 📝 Backlog (priority order)

1. `OptionsDataPort` + port the options-flow check (completes Phase 5)
2. `ScanService` orchestration; make `bot.py` and `dashboard.py` thin adapters
3. Phase 6: risk rule pipeline
4. Persist dashboard scan cache to disk (survive restarts)
5. CI workflow (pytest on push)

## ⚠️ Technical debt

- `scanner.py` still duplicates strategy logic now in `trading_platform.application.strategies` — converges when ScanService lands and bot.py switches over
- New strategies use proper Wilder RSI; legacy scanner uses an approximation — RSI values will shift slightly at switchover (document for the owner)
- `dashboard.py` background-scan thread shares no lock with request handlers (GIL-safe for current dict swaps, but fragile)
- `options_iv_percentile_min` weight exists but is unused by scanner logic
- No CI; tests run locally only
- Scan results stored only in memory — lost on container restart

## 🚨 Risks

- **yfinance reliability:** unofficial API, rate limits, breaking changes — mitigated by adapter isolation (Phase 3)
- **Single-file JSON persistence:** no concurrent-write safety between bot and dashboard containers sharing `data/` volume
- **Free-tier VPS:** Oracle can reclaim Always-Free instances; document re-deploy procedure
- **Future live trading:** financial risk — gated behind explicit owner approval, paper-first
