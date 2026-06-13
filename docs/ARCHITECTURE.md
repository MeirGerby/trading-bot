# Architecture

## Overview

The platform evolves from a single-purpose Telegram alert bot into a layered,
testable trading platform. The legacy flat modules (`bot.py`, `scanner.py`,
`feedback.py`, `dashboard.py`) keep running in production while functionality
migrates into the `trading_platform` package, layer by layer.

## Layers

```
┌──────────────────────────────────────────────────────────────┐
│ Interfaces        bot.py (Telegram) · dashboard.py (FastAPI)  │
│                   — thin adapters over ScanService            │
├──────────────────────────────────────────────────────────────┤
│ Application       Ports (Protocols) · Strategies · Indicators │
│                   ScanService · DecisionEngine · RiskEngine   │
│                   PortfolioEngine                             │
├──────────────────────────────────────────────────────────────┤
│ Domain            Pure models, no I/O:                        │
│                   Instrument · Bar · Signal · Recommendation  │
│                   RiskCheckResult · PortfolioState · Order    │
│                   OptionContract · FeedbackEvent              │
├──────────────────────────────────────────────────────────────┤
│ Infrastructure    YFinanceMarketData · YFinanceOptionsData    │
│                   JsonMemoryStore · PaperBroker               │
│                   JsonlAuditLog                               │
└──────────────────────────────────────────────────────────────┘
```

## Scan pipeline (ScanService.scan)

```
settings + learned weights (memory)
        │
        ▼
portfolio state (broker) ──────────────┐
        │                              │
per symbol: Breakout · Momentum ·      │
            OptionsFlow strategies     │
        │ signals                      │
        ▼                              │
min_score filter → DecisionEngine      │
  (confidence = 0.6·strength +         │
   0.4·feedback prior)                 │
        │                              ▼
        ▼                    PortfolioEngine sizing
RiskEngine (position size ·  ◄─────────┘
  exposure · cash reserve)
        │
        ▼
persist scan_results (memory) + audit JSONL
```

Execution is separate from scanning: `ScanService.execute(rec)` places a
paper order only for an approved recommendation.

**Dependency rule:** arrows point inward only. Domain imports nothing from
other layers. Application depends on Domain. Infrastructure implements
Application ports. Interfaces wire everything together.

## Package layout

```
trading_platform/
  domain/        models.py, enums.py        — pure dataclasses, validated
  application/   ports.py                   — typing.Protocol interfaces
                 services/ (next phase)     — ScanService, RiskService
  infrastructure/ (next phase)              — yfinance, JSON store, telegram
  config/        settings.py                — env-driven Settings dataclass
tests/           mirrors package structure  — pytest
docs/            this file + ROADMAP, PROJECT_STATUS, KNOWLEDGE_BASE
```

## Key decisions (ADR summary)

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | Evolve alongside legacy code, not big-bang rewrite | Bot is deployed and in daily use; zero-downtime migration |
| 2 | `typing.Protocol` ports instead of ABC inheritance | Structural typing, no forced base classes, easy test doubles |
| 3 | Plain dataclasses over Pydantic for domain | Domain stays dependency-free; Pydantic reserved for API edges |
| 4 | JSON file persistence retained for now | Single-user scale; swap behind `MemoryStore` port when needed |
| 5 | Broker execution will default to **paper trading**; live-money integration requires explicit owner approval | Risk containment |
| 6 | Strategies receive data ports via constructor, not evaluate() args | Different strategies need different data (bars vs option chains); keeps Strategy interface uniform for ScanService |
| 7 | Scanning and execution are separate operations | A scan must never trade implicitly; execute() acts only on approved recommendations |
| 8 | Audit log is append-only JSONL with flock | Greppable, tail-able, concurrent-append-safe across containers |

## Migration status

1. ✅ Domain models + ports
2. ✅ Strategies behind the `Strategy` port (`application/strategies.py`); legacy `scanner.py` deleted
3. ✅ Feedback/weights behind `MemoryStore` (`feedback.py` is a thin facade)
4. ✅ `ScanService` orchestrates; `bot.py` and `dashboard.py` are thin adapters
5. ✅ Risk engine reviews every `Recommendation` before it is surfaced
