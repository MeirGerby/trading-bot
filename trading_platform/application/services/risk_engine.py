"""Risk engine: runs every configured RiskRule against a recommendation.

Each rule returns a RiskCheckResult; the engine attaches the full list so
the audit trail shows exactly which rule rejected and why. A recommendation
is approved only if all checks pass (Recommendation.approved).
"""
from collections.abc import Sequence
from dataclasses import replace

from trading_platform.application.ports import RiskRule
from trading_platform.application.services.portfolio_engine import PortfolioEngine
from trading_platform.domain import PortfolioState, Recommendation, RiskCheckResult


def _proposed_value(rec: Recommendation) -> float:
    return rec.proposed_quantity * rec.price


class MaxPositionSizeRule:
    name = "max_position_size"

    def __init__(self, max_position_pct: float):
        self._max_pct = max_position_pct

    def check(self, rec: Recommendation, portfolio: PortfolioState) -> RiskCheckResult:
        limit = PortfolioEngine.equity(portfolio) * self._max_pct
        value = _proposed_value(rec)
        if value <= limit:
            return RiskCheckResult(self.name, True)
        return RiskCheckResult(self.name, False,
                               f"position {value:.2f} exceeds {self._max_pct:.0%} of equity ({limit:.2f})")


class MaxExposureRule:
    name = "max_exposure"

    def __init__(self, max_total_exposure_pct: float):
        self._max_pct = max_total_exposure_pct

    def check(self, rec: Recommendation, portfolio: PortfolioState) -> RiskCheckResult:
        limit = PortfolioEngine.equity(portfolio) * self._max_pct
        projected = portfolio.exposure() + _proposed_value(rec)
        if projected <= limit:
            return RiskCheckResult(self.name, True)
        return RiskCheckResult(self.name, False,
                               f"projected exposure {projected:.2f} exceeds {self._max_pct:.0%} of equity")


class CashReserveRule:
    name = "cash_reserve"

    def __init__(self, min_cash_reserve_pct: float):
        self._min_pct = min_cash_reserve_pct

    def check(self, rec: Recommendation, portfolio: PortfolioState) -> RiskCheckResult:
        reserve = PortfolioEngine.equity(portfolio) * self._min_pct
        remaining = portfolio.cash - _proposed_value(rec)
        if remaining >= reserve:
            return RiskCheckResult(self.name, True)
        return RiskCheckResult(self.name, False,
                               f"cash after trade {remaining:.2f} below reserve {reserve:.2f}")


class RiskEngine:
    def __init__(self, rules: Sequence[RiskRule]):
        self._rules = tuple(rules)

    def review(self, rec: Recommendation, portfolio: PortfolioState) -> Recommendation:
        checks = tuple(rule.check(rec, portfolio) for rule in self._rules)
        return replace(rec, risk_checks=checks)


def default_risk_engine(risk_params: dict[str, float]) -> RiskEngine:
    return RiskEngine([
        MaxPositionSizeRule(risk_params["max_position_pct"]),
        MaxExposureRule(risk_params["max_total_exposure_pct"]),
        CashReserveRule(risk_params["min_cash_reserve_pct"]),
    ])
