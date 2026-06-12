# Architecture

## Overview

The platform evolves from a single-purpose Telegram alert bot into a layered,
testable trading platform. The legacy flat modules (`bot.py`, `scanner.py`,
`feedback.py`, `dashboard.py`) keep running in production while functionality
migrates into the `trading_platform` package, layer by layer.

## Layers

```
┌─────────────────────────────────────────────────────────┐
│ Interfaces        Telegram bot · FastAPI dashboard · CLI │
├─────────────────────────────────────────────────────────┤
│ Application       Ports (Protocols) · Use-case services  │
│                   ScanService · FeedbackService          │
├─────────────────────────────────────────────────────────┤
│ Domain            Pure models, no I/O:                   │
│                   Instrument · Bar · Signal              │
│                   Recommendation · RiskCheckResult       │
│                   PortfolioState · FeedbackEvent         │
├─────────────────────────────────────────────────────────┤
│ Infrastructure    yfinance adapter · JSON memory store   │
│                   Telegram notifier · (later: brokers)   │
└─────────────────────────────────────────────────────────┘
```

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

## Migration plan

1. Domain models + ports (this phase)
2. Move scanner strategies behind `Strategy` port → `infrastructure/strategies/`
3. Move feedback/weights behind `MemoryStore` port
4. `ScanService` orchestrates; `bot.py` and `dashboard.py` become thin adapters
5. Risk engine consumes `Recommendation` before any notification
