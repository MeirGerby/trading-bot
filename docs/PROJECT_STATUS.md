# Project Status

_Last updated: 2026-06-12 (dual-market + autonomous execution session)_

## 📋 Current sprint

- Delivered: dual-market coverage (US + TASE), fundamentals screener grid (market cap,
  target price, dividend yield, ROE, P/B, risk level, dynamic fees), autonomous paper
  execution with a live trade feed and per-decision explainability panels, intelligence
  layer (PerformanceTracker, LearningEngine, MetaDecisionEngine, SelfCritiqueEngine),
  2 new strategies (mean reversion, trend following), 6-tab dashboard
- Next sprint: CI workflow + Simulation Engine (backtest old vs new policy before adoption)

## ✅ Completed

- Cloud deployment (Docker on Oracle Cloud VPS), dashboard with charts, daily-trading live view
- Phases 1–8: domain models, ports, adapters, memory store, strategies, risk/decision/portfolio
  engines, PaperBroker — see ROADMAP for the per-phase breakdown
- ScanService pipeline (config → memory → portfolio → strategies → decision → meta-decision →
  risk → auto-execute (paper) → persist → audit → outcome tracking → lessons → self-critique)
- Intelligence layer: 24h outcome evaluation, per-strategy win rate / Sharpe / max-drawdown /
  profit factor, strategy-competition confidence adjustment, structured lessons, post-cycle critiques
- Dual market: TASE watchlist (.TA suffix), agorot→ILS normalization, market tags end-to-end
- Screener: YFinanceFundamentals adapter (TTL-cached Ticker.info), risk classification from
  volatility/beta/drawdown, parameter-driven fee calculator (US per-share, TASE percentage)
- Autonomous paper trading: approved + sized recommendations auto-execute through PaperBroker,
  trade log with full reasoning persisted, live feed in UI (ADR-5 still enforced)
- Explainability: build_reasoning() renders signals, decision logic, sizing, and risk review
  for every recommendation and trade ("Why this decision?" panels)
- 158 tests passing, including full-pipeline integration tests (fake market data only)

## 📝 Backlog (priority order)

1. CI workflow — pytest on every push
2. Simulation Engine — backtest old vs new policy before adopting parameter changes
3. Risk rules: volatility, liquidity, drawdown, stop-loss/take-profit levels (+ position exits:
   the agent currently only enters; add SELL logic driven by stop/target/signal decay)
4. Real-time push (WebSockets) to replace 5s polling on the live-trading tab
5. Batched yfinance downloads for the whole watchlist in one request
6. Knowledge-base auto-update from feedback patterns

## ⚠️ Technical debt

- bot and dashboard containers scan independently (15-min and 5-min cadences) — duplicated
  work; with auto-execution enabled in both, the held-position check is the only dedupe guard
- `config.py` and `Settings` coexist; legacy modules still read config.py
- `options_iv_percentile_min` param still unused (kept for weight-format compatibility)
- TASE prices flow through the pipeline in agorot (vendor-native); UI converts for display,
  paper P&L for TASE positions mixes currencies with USD positions in the portfolio totals
- RSI is now proper Wilder smoothing — values differ slightly from the legacy approximation

## 🚨 Risks

- yfinance reliability (unofficial API) — isolated in adapters; failures degrade to empty results
- Ticker.info fundamentals are scraped and slow (~1-2s/symbol) — 30-min TTL cache + background
  refresh thread keep the screener responsive
- Free-tier VPS reclaim risk — redeploy procedure documented in README
- Live trading remains gated: PaperBroker only; auto_execute acts solely through the injected
  broker, which bootstrap wires to PaperBroker. Live-money integration requires explicit owner
  approval (ADR-5)
