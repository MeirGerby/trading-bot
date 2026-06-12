"""Composition root: wires concrete infrastructure into the application.

The only place where infrastructure classes are instantiated together —
adapters (bot.py, dashboard.py) call build_scan_service() and stay thin.
"""
import os
from pathlib import Path

from trading_platform.application.services import (
    DecisionEngine,
    FeeCalculator,
    LearningEngine,
    MetaDecisionEngine,
    PerformanceTracker,
    PortfolioEngine,
    ScanService,
    ScreenerService,
    SelfCritiqueEngine,
)
from trading_platform.application.services.risk_engine import default_risk_engine
from trading_platform.application.strategies import (
    BreakoutStrategy,
    MeanReversionStrategy,
    MomentumStrategy,
    OptionsFlowStrategy,
    TrendFollowingStrategy,
)
from trading_platform.config import Settings
from trading_platform.infrastructure.audit import JsonlAuditLog
from trading_platform.infrastructure.fundamentals_data import YFinanceFundamentals
from trading_platform.infrastructure.market_data import YFinanceMarketData
from trading_platform.infrastructure.memory_store import JsonMemoryStore
from trading_platform.infrastructure.options_data import YFinanceOptionsData
from trading_platform.infrastructure.paper_broker import PaperBroker

# Shared market-data adapter so the screener reuses the scan loop's bar cache
_shared_market_data: YFinanceMarketData | None = None


def _get_market_data() -> YFinanceMarketData:
    global _shared_market_data
    if _shared_market_data is None:
        _shared_market_data = YFinanceMarketData()
    return _shared_market_data


def build_scan_service(settings: Settings | None = None,
                       base_dir: str | Path | None = None) -> ScanService:
    settings = settings or Settings.from_env()
    data_dir = Path(base_dir) if base_dir else Path(os.path.dirname(__file__)).parent / settings.data_dir

    memory = JsonMemoryStore(data_dir)
    market_data = _get_market_data()
    options_data = YFinanceOptionsData()

    tracker = PerformanceTracker(memory, market_data)

    return ScanService(
        settings=settings,
        strategies=(
            BreakoutStrategy(market_data),
            MomentumStrategy(market_data),
            OptionsFlowStrategy(options_data),
            MeanReversionStrategy(market_data),
            TrendFollowingStrategy(market_data),
        ),
        memory=memory,
        market_data=market_data,
        decision_engine=DecisionEngine(memory),
        portfolio_engine=PortfolioEngine(settings.risk_params),
        risk_engine=default_risk_engine(settings.risk_params),
        broker=PaperBroker(memory, settings.risk_params["paper_starting_cash"]),
        audit=JsonlAuditLog(data_dir / "audit.jsonl"),
        performance_tracker=tracker,
        meta_decision_engine=MetaDecisionEngine(tracker),
        learning_engine=LearningEngine(memory),
        self_critique_engine=SelfCritiqueEngine(memory),
        auto_execute=settings.auto_execute_paper,  # paper broker only (ADR-5)
    )


def build_screener_service(settings: Settings | None = None) -> ScreenerService:
    settings = settings or Settings.from_env()
    return ScreenerService(
        watchlist=settings.watchlist,
        market_data=_get_market_data(),
        fundamentals=YFinanceFundamentals(),
        fee_calculator=FeeCalculator(settings.fee_params),
    )
