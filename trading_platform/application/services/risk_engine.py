"""Risk engine: runs every configured RiskRule against a recommendation.

Each rule returns a RiskCheckResult; the engine attaches the full list so
the audit trail shows exactly which rule rejected and why. A recommendation
is approved only if all checks pass (Recommendation.approved).

ExitEngine checks open positions against stop-loss, take-profit, and
(optionally) signal-decay thresholds, returning (symbol, reason) pairs for
positions that should be closed.
"""
from collections.abc import Sequence
from dataclasses import replace

from trading_platform.application.ports import RiskRule
from trading_platform.application.services.portfolio_engine import PortfolioEngine
from trading_platform.domain import PortfolioState, Recommendation, RiskCheckResult


def _equity(rec_or_portfolio, portfolio_engine: PortfolioEngine | None, portfolio: PortfolioState) -> float:
    if portfolio_engine is not None:
        return portfolio_engine.equity(portfolio)
    return portfolio.cash + portfolio.exposure()


def _value_usd(rec: Recommendation, portfolio_engine: PortfolioEngine | None) -> float:
    if portfolio_engine is not None:
        return portfolio_engine.position_value_usd(
            rec.proposed_quantity, rec.price, rec.instrument.symbol)
    return rec.proposed_quantity * rec.price


def _exposure_usd(portfolio: PortfolioState, portfolio_engine: PortfolioEngine | None) -> float:
    if portfolio_engine is not None:
        return portfolio_engine._normalized_exposure(portfolio)
    return portfolio.exposure()


# ---------------------------------------------------------------------------
# Entry risk rules (BUY-side checks)
# ---------------------------------------------------------------------------

class MaxPositionSizeRule:
    name = "max_position_size"

    def __init__(self, max_position_pct: float, portfolio_engine: PortfolioEngine | None = None):
        self._max_pct = max_position_pct
        self._pe = portfolio_engine

    def check(self, rec: Recommendation, portfolio: PortfolioState) -> RiskCheckResult:
        limit = _equity(None, self._pe, portfolio) * self._max_pct
        value = _value_usd(rec, self._pe)
        if value <= limit:
            return RiskCheckResult(self.name, True)
        return RiskCheckResult(self.name, False,
                               f"position {value:.2f} exceeds {self._max_pct:.0%} of equity ({limit:.2f})")


class MaxExposureRule:
    name = "max_exposure"

    def __init__(self, max_total_exposure_pct: float, portfolio_engine: PortfolioEngine | None = None):
        self._max_pct = max_total_exposure_pct
        self._pe = portfolio_engine

    def check(self, rec: Recommendation, portfolio: PortfolioState) -> RiskCheckResult:
        limit = _equity(None, self._pe, portfolio) * self._max_pct
        projected = _exposure_usd(portfolio, self._pe) + _value_usd(rec, self._pe)
        if projected <= limit:
            return RiskCheckResult(self.name, True)
        return RiskCheckResult(self.name, False,
                               f"projected exposure {projected:.2f} exceeds {self._max_pct:.0%} of equity")


class CashReserveRule:
    name = "cash_reserve"

    def __init__(self, min_cash_reserve_pct: float, portfolio_engine: PortfolioEngine | None = None):
        self._min_pct = min_cash_reserve_pct
        self._pe = portfolio_engine

    def check(self, rec: Recommendation, portfolio: PortfolioState) -> RiskCheckResult:
        reserve = _equity(None, self._pe, portfolio) * self._min_pct
        cost = _value_usd(rec, self._pe)
        remaining = portfolio.cash - cost
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


# ---------------------------------------------------------------------------
# Exit engine (SELL triggers — stop-loss, take-profit, signal decay)
# ---------------------------------------------------------------------------

class ExitEngine:
    """Scans open positions and flags those that hit an exit threshold.

    Three aggressive exit triggers, evaluated per open position:
      1. take-profit — unrealized P&L ≥ +take_profit_pct  → lock in the gain
      2. stop-loss   — unrealized P&L ≤ -stop_loss_pct     → cut the reversal
      3. signal-decay — the symbol no longer has an active momentum /
         trend-following signal this scan → exit regardless of P&L

    Returns a list of (symbol, reason) tuples; the caller decides whether to
    execute the corresponding SELL orders and computes realized P&L.

    TASE P&L is computed purely as a percentage — agorot cancel out, so no
    currency conversion is needed for exit-trigger math.
    """

    def __init__(self, stop_loss_pct: float = 0.005, take_profit_pct: float = 0.01,
                 signal_decay_enabled: bool = False):
        self._stop = stop_loss_pct
        self._target = take_profit_pct
        self._decay = signal_decay_enabled

    def check_exits(
        self,
        portfolio: PortfolioState,
        current_prices: dict[str, float],
        trend_supporting_symbols: set[str],
    ) -> list[tuple[str, str]]:
        """Return (symbol, reason) pairs for positions that should be closed.

        ``trend_supporting_symbols`` are the symbols that still produced a
        momentum or trend-following signal in the current scan. A held symbol
        absent from this set has lost its thesis and is flagged for a
        signal-decay exit.
        """
        exits: list[tuple[str, str]] = []
        for pos in portfolio.positions:
            symbol = pos.instrument.symbol
            price = current_prices.get(symbol)
            if price is None or pos.avg_entry_price <= 0:
                continue

            pnl_pct = (price - pos.avg_entry_price) / pos.avg_entry_price

            # Take-profit first: in a scalping regime the priority is to lock
            # in the small gain the moment the micro-target is reached.
            if pnl_pct >= self._target:
                exits.append((symbol,
                               f"take-profit: {pnl_pct:+.2%} ≥ +{self._target:.2%} target reached"))
            elif pnl_pct <= -self._stop:
                exits.append((symbol,
                               f"stop-loss: {pnl_pct:+.2%} ≤ -{self._stop:.2%} from entry"))
            elif self._decay and symbol not in trend_supporting_symbols:
                exits.append((symbol,
                               f"signal-decay: momentum/trend signal gone ({pnl_pct:+.2%} P&L)"))

        return exits


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def default_risk_engine(risk_params: dict[str, float],
                        portfolio_engine: PortfolioEngine | None = None) -> RiskEngine:
    pe = portfolio_engine or PortfolioEngine(risk_params)
    return RiskEngine([
        MaxPositionSizeRule(risk_params["max_position_pct"], pe),
        MaxExposureRule(risk_params["max_total_exposure_pct"], pe),
        CashReserveRule(risk_params["min_cash_reserve_pct"], pe),
    ])


def default_exit_engine(risk_params: dict[str, float]) -> ExitEngine:
    decay_enabled = (
        bool(risk_params.get("signal_decay_enabled", 0.0))
        or risk_params.get("signal_decay_scans", 0.0) > 0
    )
    return ExitEngine(
        stop_loss_pct=risk_params.get("stop_loss_pct", 0.005),
        take_profit_pct=risk_params.get("take_profit_pct", 0.01),
        signal_decay_enabled=decay_enabled,
    )
