# Project Status

_Last updated: 2026-06-12 (ScanService session)_

## 📋 Current sprint

- Delivered: ScanService orchestration, DecisionEngine, RiskEngine, PortfolioEngine,
  PaperBroker, JSONL audit log, options-flow strategy; bot.py + dashboard.py rewired
  as thin adapters; legacy scanner.py deleted
- Next sprint: CI workflow + portfolio/audit dashboard views

## ✅ Completed

- Cloud deployment (Docker on Oracle Cloud VPS), dashboard with charts, daily-trading live view
- Phases 1–4: docs, domain models, ports, settings, market-data adapter, memory store
- Phase 5: Breakout / Momentum / OptionsFlow strategies (constructor-injected ports, ADR-6)
- Phase 6: RiskEngine — max position size, max exposure, cash reserve rules with audit reasons
- Phase 7: DecisionEngine — confidence = 0.6·signal strength + 0.4·feedback prior, rationale text
- Phase 8: PaperBroker — instant fills, weighted-avg positions, persisted via lock-protected store
- ScanService pipeline (config → memory → portfolio → strategies → decision → risk → persist → audit)
- bot.py / dashboard.py thin adapters; scan results persist across restarts (was a backlog item)
- Performance: one bars download per ticker per scan (legacy downloaded twice); bot scans no
  longer block the Telegram event loop (asyncio.to_thread)
- 113 tests passing, including a full-pipeline integration test (real engines + store + broker
  + audit, fake market data)

## 📝 Backlog (priority order)

1. CI workflow — pytest on every push
2. Dashboard: paper-portfolio view + audit-trail view; "execute (paper)" button per recommendation
3. Risk rules: volatility, liquidity, drawdown, stop-loss/take-profit levels
4. Confidence-weighted alert ordering in Telegram (currently score-then-confidence)
5. Batched yfinance downloads for the whole watchlist in one request
6. Knowledge-base auto-update from feedback patterns

## ⚠️ Technical debt

- bot and dashboard containers scan independently (15-min and 5-min cadences) — duplicated
  work; consider a single scanner process publishing through the shared store
- `config.py` and `Settings` coexist; legacy modules still read config.py
- `options_iv_percentile_min` param still unused (kept for weight-format compatibility)
- Telegram alert formatting changed slightly (confidence line added, decimal formatting);
  visually verify first production alert
- RSI is now proper Wilder smoothing — values differ slightly from the legacy approximation

## 🚨 Risks

- yfinance reliability (unofficial API) — isolated in adapters; failures degrade to empty results
- Free-tier VPS reclaim risk — redeploy procedure documented in README
- Live trading remains gated: PaperBroker only; live-money integration requires explicit owner
  approval (ADR-5, enforced in ScanService.execute being broker-agnostic but wired paper-only)
