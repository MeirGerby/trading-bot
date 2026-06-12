# Knowledge Base

Persistent project memory. Updated whenever new evidence or feedback appears.

## User preferences

- **Hosting:** free-tier only (rejected Railway when it asked for payment; chose Oracle Cloud Always-Free VPS + Docker)
- **Language:** Hebrew UI and communication; code and docs in English
- **Dashboard style:** simple tables and numbers over heavy visuals; dark mode
- **Wants:** real-time candlestick charts (1d/5d/1mo), live recommendations refreshed every 5 seconds

## Trading rules (current, learned from legacy system)

- Alert threshold: signal score ≥ 2 of 3 signal types (breakout, momentum, options flow)
- Feedback nudges thresholds ±5% per event (LEARNING_RATE = 0.05)
- Max 5 alerts per scan cycle to avoid spam

## Operational lessons

- yfinance full scan of 18 tickers takes 5–18 minutes → never scan synchronously in a request path; use background thread + in-memory cache
- yfinance returns MultiIndex columns in newer versions → flatten before use
- lightweight-charts: chart must be initialized while container is visible (width > 0); pin exact CDN versions
- Oracle VM clones default branch — deployment docs must mention checking out the working branch

## Risk rules (to be enforced by Phase 6 risk engine)

- Live-money broker integration requires explicit owner approval (paper trading default)

## Open questions

- Which broker for Phase 8? (paper-first; candidates: Alpaca paper API, IBKR)
- Should the watchlist become dynamic (screener-based) instead of the fixed 18 tickers?

## Improvement ideas

- Replace per-ticker yfinance calls with batched download to cut scan time
- Persist scan results so dashboard survives restarts
- Add confidence scoring based on historical feedback per signal type
