"""Creative Advisor Agent — daily heuristic engine that analyses platform health
and applies targeted parameter / watchlist improvements automatically.

Sources:
  - Internal: PerformanceTracker, LearningEngine, SelfCritiqueEngine, trade log,
    scan results, current strategy/risk params.
  - External: yfinance SPY/QQQ price history for market-regime detection.

Applies changes immediately (strategy params via "weights" memory key, risk
params via "risk_overrides" memory key, watchlist via SymbolRepositoryPort)
and persists a full audit of every idea and what it changed.
"""
import logging
import math
import uuid
from dataclasses import replace
from datetime import datetime, timezone

from trading_platform.application.ports import MarketDataPort, MemoryStore, SymbolRepositoryPort
from trading_platform.application.services.learning_engine import LearningEngine
from trading_platform.application.services.performance_tracker import PerformanceTracker
from trading_platform.application.services.self_critique_engine import SelfCritiqueEngine
from trading_platform.domain.models import Idea, IdeaAction

logger = logging.getLogger(__name__)

IDEAS_KEY = "ideas"
RISK_OVERRIDES_KEY = "risk_overrides"   # shared with scan_service
WEIGHTS_KEY = "weights"                  # shared with scan_service

# Strategy name (signal_type.value) → the primary threshold parameter.
# "up_is_strict" = raising the value makes the condition harder to satisfy.
_STRATEGY_PARAM = {
    "breakout":       ("breakout_volume_ratio",     "up_is_strict"),
    "momentum":       ("momentum_rsi_min",           "up_is_strict"),
    "options_flow":   ("options_vol_oi_ratio",       "up_is_strict"),
    "mean_reversion": ("mean_reversion_rsi_max",     "down_is_strict"),
    "trend_following":("trend_rsi_min",              "up_is_strict"),
}

_MIN_OUTCOMES_FOR_ANALYSIS = 5   # ignore strategies with fewer data points
_TRIVIAL_CHANGE_PCT = 0.03       # skip ideas that would move a param < 3%


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _make_action(action_type: str, key: str, old: float, new: float) -> IdeaAction:
    return IdeaAction(
        action_type=action_type,
        key=key,
        new_value=str(round(new, 6)),
        old_value=str(round(old, 6)),
    )


class IdeaEngine:
    MAX_IDEAS_HISTORY = 90  # ~3 months of daily cycles

    def __init__(
        self,
        memory: MemoryStore,
        market_data: MarketDataPort,
        symbol_repository: SymbolRepositoryPort,
        performance_tracker: PerformanceTracker | None = None,
        learning_engine: LearningEngine | None = None,
        self_critique_engine: SelfCritiqueEngine | None = None,
        max_change_pct: float = 0.30,
    ):
        self._memory = memory
        self._market_data = market_data
        self._symbol_repo = symbol_repository
        self._tracker = performance_tracker
        self._learning = learning_engine
        self._critique = self_critique_engine
        self._max_chg = max_change_pct

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_daily_cycle(
        self,
        current_strategy_params: dict[str, float],
        current_risk_params: dict[str, float],
    ) -> list[Idea]:
        """Generate, apply, and persist improvement ideas for the current cycle.

        Changes applied here take effect on the NEXT scan (strategy params via
        WEIGHTS_KEY, risk params via RISK_OVERRIDES_KEY).
        """
        # Work on mutable copies so each analyser sees the latest state
        s_params = dict(current_strategy_params)
        r_params = dict(current_risk_params)

        raw: list[Idea] = []
        raw += self._analyze_performance(s_params, r_params)
        raw += self._analyze_market_regime(r_params)
        raw += self._analyze_lessons(r_params)
        raw += self._analyze_exit_quality(r_params)
        raw += self._analyze_watchlist_health()
        raw += self._analyze_critiques(s_params)

        raw.sort(key=lambda i: {"high": 0, "medium": 1, "low": 2}[i.priority])

        applied: list[Idea] = []
        for idea in raw:
            idea = self._apply(idea)
            applied.append(idea)
            # Feed applied param changes back into the running copies
            for action in idea.actions:
                try:
                    v = float(action.new_value)
                except ValueError:
                    continue
                if action.action_type == "set_strategy_param":
                    s_params[action.key] = v
                elif action.action_type == "set_risk_param":
                    r_params[action.key] = v

        self._persist(applied)
        return applied

    def get_recent_ideas(self, n: int = 30) -> list[dict]:
        store = self._memory.load(IDEAS_KEY, {"ideas": []})
        return list(reversed(store["ideas"][-n:]))

    # ------------------------------------------------------------------
    # Heuristic analysers — each returns a list of proposed Idea objects
    # ------------------------------------------------------------------

    def _analyze_performance(
        self, s_params: dict[str, float], r_params: dict[str, float]
    ) -> list[Idea]:
        if self._tracker is None:
            return []
        perf = self._tracker.get_strategy_performance()
        if not perf:
            return []

        ideas: list[Idea] = []

        for strategy_name, sp in perf.items():
            if sp.total_evaluated < _MIN_OUTCOMES_FOR_ANALYSIS:
                continue
            if strategy_name not in _STRATEGY_PARAM:
                continue

            param_key, direction = _STRATEGY_PARAM[strategy_name]
            current_val = s_params.get(param_key)
            if current_val is None:
                continue

            if sp.win_rate < 0.35:
                # Tighten threshold → make signal harder to fire
                factor = 1.15 if direction == "up_is_strict" else 0.85
                new_val = self._clamp(current_val, current_val * factor)
                if self._is_significant(current_val, new_val):
                    ideas.append(self._make_idea(
                        category="strategy",
                        title=f"Tighten {strategy_name} threshold",
                        rationale=(f"{strategy_name} win_rate={sp.win_rate:.0%} < 35% "
                                   f"across {sp.total_evaluated} trades — "
                                   f"raising {param_key} from {current_val:.4g} → {new_val:.4g}"),
                        source="performance_analysis",
                        priority="high",
                        actions=(
                            _make_action("set_strategy_param", param_key, current_val, new_val),
                        ),
                    ))

            elif sp.win_rate > 0.65:
                # Relax threshold → catch more signals from this strong strategy
                factor = 0.90 if direction == "up_is_strict" else 1.10
                new_val = self._clamp(current_val, current_val * factor)
                if self._is_significant(current_val, new_val):
                    ideas.append(self._make_idea(
                        category="strategy",
                        title=f"Relax {strategy_name} threshold",
                        rationale=(f"{strategy_name} win_rate={sp.win_rate:.0%} > 65% — "
                                   f"easing {param_key} from {current_val:.4g} → {new_val:.4g}"),
                        source="performance_analysis",
                        priority="medium",
                        actions=(
                            _make_action("set_strategy_param", param_key, current_val, new_val),
                        ),
                    ))

            if sp.max_drawdown_pct > 0.20:
                alloc_key = "base_allocation_pct"
                alloc = r_params.get(alloc_key, 0.05)
                new_alloc = self._clamp(alloc, alloc * 0.85)
                if self._is_significant(alloc, new_alloc):
                    ideas.append(self._make_idea(
                        category="risk",
                        title=f"Reduce allocation — {strategy_name} drawdown too high",
                        rationale=(f"{strategy_name} max_drawdown={sp.max_drawdown_pct:.0%} > 20% — "
                                   f"reducing {alloc_key} from {alloc:.4g} → {new_alloc:.4g}"),
                        source="performance_analysis",
                        priority="high",
                        actions=(
                            _make_action("set_risk_param", alloc_key, alloc, new_alloc),
                        ),
                    ))

            if sp.profit_factor < 0.8:
                score_key = "min_score_to_alert"
                score = s_params.get(score_key, 2.0)
                if score > 1:
                    new_score = max(1.0, score - 1.0)
                    ideas.append(self._make_idea(
                        category="strategy",
                        title=f"Lower min_score_to_alert — {strategy_name} profit factor low",
                        rationale=(f"{strategy_name} profit_factor={sp.profit_factor:.2f} < 0.8 — "
                                   f"lowering min_score_to_alert from {score:.0f} → {new_score:.0f} "
                                   f"to generate more signal variety"),
                        source="performance_analysis",
                        priority="medium",
                        actions=(
                            _make_action("set_strategy_param", score_key, score, new_score),
                        ),
                    ))

        return ideas

    def _analyze_market_regime(self, r_params: dict[str, float]) -> list[Idea]:
        ideas: list[Idea] = []
        try:
            bars = self._market_data.get_daily_bars("SPY", 22)
        except Exception:
            return []
        if len(bars) < 12:
            return []

        closes = [b.close for b in bars]
        ten_day_return = (closes[-1] - closes[-11]) / closes[-11]

        # Realized volatility (annualised)
        daily_rets = [(closes[i] - closes[i - 1]) / closes[i - 1]
                      for i in range(1, len(closes))]
        if len(daily_rets) >= 10:
            mean = sum(daily_rets) / len(daily_rets)
            variance = sum((r - mean) ** 2 for r in daily_rets) / (len(daily_rets) - 1)
            realized_vol = math.sqrt(variance) * math.sqrt(252)
        else:
            realized_vol = 0.0

        if ten_day_return < -0.05:  # bearish regime
            alloc_key = "base_allocation_pct"
            alloc = r_params.get(alloc_key, 0.05)
            new_alloc = self._clamp(alloc, alloc * 0.85)

            stop_key = "stop_loss_pct"
            stop = r_params.get(stop_key, 0.08)
            new_stop = self._clamp(stop, stop * 1.10)

            actions = []
            rationale_parts = [f"SPY 10d return={ten_day_return:+.1%} — bearish regime"]
            if self._is_significant(alloc, new_alloc):
                actions.append(_make_action("set_risk_param", alloc_key, alloc, new_alloc))
                rationale_parts.append(f"{alloc_key}: {alloc:.4g}→{new_alloc:.4g}")
            if self._is_significant(stop, new_stop):
                actions.append(_make_action("set_risk_param", stop_key, stop, new_stop))
                rationale_parts.append(f"{stop_key}: {stop:.4g}→{new_stop:.4g}")

            if actions:
                ideas.append(self._make_idea(
                    category="market_context",
                    title="Defensive adjustment — bearish market regime",
                    rationale=" | ".join(rationale_parts),
                    source="market_data",
                    priority="high",
                    actions=tuple(actions),
                ))

        elif ten_day_return > 0.05:  # bullish regime
            tp_key = "take_profit_pct"
            tp = r_params.get(tp_key, 0.20)
            new_tp = self._clamp(tp, tp * 1.10)
            if self._is_significant(tp, new_tp):
                ideas.append(self._make_idea(
                    category="market_context",
                    title="Extend take-profit target — bullish market regime",
                    rationale=(f"SPY 10d return={ten_day_return:+.1%} — "
                               f"letting winners run: {tp_key} {tp:.4g}→{new_tp:.4g}"),
                    source="market_data",
                    priority="medium",
                    actions=(
                        _make_action("set_risk_param", tp_key, tp, new_tp),
                    ),
                ))

        if realized_vol > 0.25:  # high volatility
            pos_key = "max_position_pct"
            pos = r_params.get(pos_key, 0.10)
            new_pos = self._clamp(pos, pos * 0.85)
            if self._is_significant(pos, new_pos):
                ideas.append(self._make_idea(
                    category="risk",
                    title="Reduce max position size — high volatility",
                    rationale=(f"SPY realized vol={realized_vol:.0%} > 25% — "
                               f"{pos_key}: {pos:.4g}→{new_pos:.4g}"),
                    source="market_data",
                    priority="medium",
                    actions=(
                        _make_action("set_risk_param", pos_key, pos, new_pos),
                    ),
                ))

        return ideas

    def _analyze_lessons(self, r_params: dict[str, float]) -> list[Idea]:
        if self._learning is None:
            return []
        lessons = self._learning.get_recent_lessons(50)
        if not lessons:
            return []

        texts = [l.get("text", "") for l in lessons]
        stop_mentions = sum(1 for t in texts if "stop" in t.lower())
        overconf_mentions = sum(1 for t in texts if "OVERCONFIDENCE" in t)

        ideas: list[Idea] = []

        if stop_mentions >= 4:
            stop_key = "stop_loss_pct"
            stop = r_params.get(stop_key, 0.08)
            new_stop = self._clamp(stop, stop * 1.12)
            if self._is_significant(stop, new_stop):
                ideas.append(self._make_idea(
                    category="risk",
                    title="Widen stop-loss — recurring stop-out pattern in lessons",
                    rationale=(f"{stop_mentions} of last {len(lessons)} lessons mention 'stop' — "
                               f"stop-loss may be too tight: {stop_key} {stop:.4g}→{new_stop:.4g}"),
                    source="lessons",
                    priority="medium",
                    actions=(
                        _make_action("set_risk_param", stop_key, stop, new_stop),
                    ),
                ))

        if overconf_mentions >= 3:
            score_key = "min_score_to_alert"
            score = r_params.get(score_key, 2.0)  # lessons use strategy params but store via risk for simplicity
            # Actually min_score_to_alert is a strategy param, get from correct place
            # We don't have current_strategy_params here, so we'll skip and handle in _analyze_performance
            _ = score  # suppress lint

        return ideas

    def _analyze_exit_quality(self, r_params: dict[str, float]) -> list[Idea]:
        """Inspect trade_log exit reasons to calibrate stop/take-profit thresholds."""
        from trading_platform.application.services.scan_service import TRADE_LOG_KEY
        store = self._memory.load(TRADE_LOG_KEY, {"trades": []})
        trades = store.get("trades", [])

        exits = [t for t in trades if t.get("side") == "sell" or
                 any(s in t.get("reasoning", "") for s in ("stop-loss", "take-profit"))]
        if len(exits) < 5:
            return []

        stop_exits = sum(1 for t in exits if "stop-loss" in t.get("reasoning", ""))
        tp_exits = sum(1 for t in exits if "take-profit" in t.get("reasoning", ""))
        total = len(exits)

        ideas: list[Idea] = []

        if total > 0 and stop_exits / total > 0.60:
            stop_key = "stop_loss_pct"
            stop = r_params.get(stop_key, 0.08)
            new_stop = self._clamp(stop, stop * 1.10)
            if self._is_significant(stop, new_stop):
                ideas.append(self._make_idea(
                    category="risk",
                    title="Widen stop-loss — too many stop-loss exits",
                    rationale=(f"{stop_exits}/{total} exits ({stop_exits/total:.0%}) hit stop-loss — "
                               f"threshold may be too tight: {stop_key} {stop:.4g}→{new_stop:.4g}"),
                    source="exit_quality",
                    priority="medium",
                    actions=(
                        _make_action("set_risk_param", stop_key, stop, new_stop),
                    ),
                ))

        if total > 0 and tp_exits / total > 0.60:
            tp_key = "take_profit_pct"
            tp = r_params.get(tp_key, 0.20)
            new_tp = self._clamp(tp, tp * 1.10)
            if self._is_significant(tp, new_tp):
                ideas.append(self._make_idea(
                    category="risk",
                    title="Raise take-profit target — strong trend-following exits",
                    rationale=(f"{tp_exits}/{total} exits ({tp_exits/total:.0%}) hit take-profit — "
                               f"consider letting winners run: {tp_key} {tp:.4g}→{new_tp:.4g}"),
                    source="exit_quality",
                    priority="low",
                    actions=(
                        _make_action("set_risk_param", tp_key, tp, new_tp),
                    ),
                ))

        return ideas

    def _analyze_watchlist_health(self) -> list[Idea]:
        """Suggest watchlist pruning (never-recommended symbols) and benchmark additions."""
        ideas: list[Idea] = []
        watchlist = self._symbol_repo.get_watchlist()

        # Check for benchmark ETFs missing from watchlist
        benchmarks = {"SPY", "QQQ"}
        missing = benchmarks - set(watchlist)
        for symbol in sorted(missing):
            ideas.append(self._make_idea(
                category="watchlist",
                title=f"Add {symbol} as market benchmark",
                rationale=(f"{symbol} is a key market benchmark and not in watchlist — "
                           "adding improves strategy context and regime detection"),
                source="watchlist_health",
                priority="low",
                actions=(
                    IdeaAction(action_type="add_watchlist", key=symbol,
                               new_value=symbol, old_value="not_in_watchlist"),
                ),
            ))

        # Symbols that never generated a recommendation
        all_outcomes = []
        if self._tracker is not None:
            all_outcomes = self._tracker.get_all_outcomes()
        recommended_symbols = {o["symbol"] for o in all_outcomes}

        # Only suggest removal if: in watchlist, never recommended, watchlist is large
        if len(watchlist) > 15:
            inactive = [s for s in watchlist if s not in recommended_symbols
                        and s not in {"SPY", "QQQ", "IWM"}]
            for symbol in inactive[:2]:  # limit to 2 removals per cycle
                ideas.append(self._make_idea(
                    category="watchlist",
                    title=f"Remove {symbol} — no recommendations in history",
                    rationale=(f"{symbol} has never generated a recommendation — "
                               "removing to reduce scan overhead and rate-limit pressure"),
                    source="watchlist_health",
                    priority="low",
                    actions=(
                        IdeaAction(action_type="remove_watchlist", key=symbol,
                                   new_value="removed", old_value="in_watchlist"),
                    ),
                ))

        return ideas

    def _analyze_critiques(self, s_params: dict[str, float]) -> list[Idea]:
        """Parse the latest system critique for actionable suggestions."""
        if self._critique is None:
            return []
        critiques = self._critique.get_recent_critiques(3)
        if not critiques:
            return []

        ideas: list[Idea] = []
        latest = critiques[0]  # newest first
        suggestion = latest.get("improvement_suggestion", "").lower()
        worst = latest.get("worst_strategy", "")

        # If worst strategy is known and suggestion mentions threshold → tighten
        if worst and worst in _STRATEGY_PARAM and "threshold" in suggestion:
            param_key, direction = _STRATEGY_PARAM[worst]
            current_val = s_params.get(param_key)
            if current_val is not None:
                factor = 1.10 if direction == "up_is_strict" else 0.90
                new_val = self._clamp(current_val, current_val * factor)
                if self._is_significant(current_val, new_val):
                    ideas.append(self._make_idea(
                        category="strategy",
                        title=f"Critique-driven: tighten {worst} threshold",
                        rationale=(f"Latest system critique identifies '{worst}' as worst strategy "
                                   f"and suggests threshold review — "
                                   f"{param_key}: {current_val:.4g}→{new_val:.4g}"),
                        source="critiques",
                        priority="medium",
                        actions=(
                            _make_action("set_strategy_param", param_key, current_val, new_val),
                        ),
                    ))

        return ideas

    # ------------------------------------------------------------------
    # Apply & persist
    # ------------------------------------------------------------------

    def _apply(self, idea: Idea) -> Idea:
        """Write the idea's actions to memory / symbol repository."""
        strategy_overrides = self._memory.load(WEIGHTS_KEY, {})
        risk_overrides = self._memory.load(RISK_OVERRIDES_KEY, {})
        strategy_dirty = False
        risk_dirty = False

        for action in idea.actions:
            if action.action_type == "set_strategy_param":
                strategy_overrides[action.key] = float(action.new_value)
                strategy_dirty = True
            elif action.action_type == "set_risk_param":
                risk_overrides[action.key] = float(action.new_value)
                risk_dirty = True
            elif action.action_type == "add_watchlist":
                try:
                    self._symbol_repo.add_to_watchlist(action.key)
                except Exception:
                    logger.exception("IdeaEngine: failed to add %s to watchlist", action.key)
            elif action.action_type == "remove_watchlist":
                try:
                    self._symbol_repo.remove_from_watchlist(action.key)
                except Exception:
                    logger.exception("IdeaEngine: failed to remove %s from watchlist", action.key)

        if strategy_dirty:
            self._memory.save(WEIGHTS_KEY, strategy_overrides)
        if risk_dirty:
            self._memory.save(RISK_OVERRIDES_KEY, risk_overrides)

        return replace(idea, applied=True)

    def _persist(self, ideas: list[Idea]) -> None:
        store = self._memory.load(IDEAS_KEY, {"ideas": []})
        for idea in ideas:
            store["ideas"].append({
                "id": idea.id,
                "category": idea.category,
                "title": idea.title,
                "rationale": idea.rationale,
                "source": idea.source,
                "priority": idea.priority,
                "actions": [
                    {
                        "action_type": a.action_type,
                        "key": a.key,
                        "new_value": a.new_value,
                        "old_value": a.old_value,
                    }
                    for a in idea.actions
                ],
                "generated_at": idea.generated_at.isoformat(timespec="seconds"),
                "applied": idea.applied,
            })
        store["ideas"] = store["ideas"][-self.MAX_IDEAS_HISTORY:]
        self._memory.save(IDEAS_KEY, store)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _clamp(self, current: float, proposed: float) -> float:
        """Clamp proposed to ±max_change_pct of current value."""
        lo = current * (1.0 - self._max_chg)
        hi = current * (1.0 + self._max_chg)
        return max(lo, min(hi, proposed))

    @staticmethod
    def _is_significant(old: float, new: float) -> bool:
        if old == 0:
            return new != 0
        return abs(new - old) / abs(old) >= _TRIVIAL_CHANGE_PCT

    @staticmethod
    def _make_idea(*, category: str, title: str, rationale: str, source: str,
                   priority: str, actions: tuple[IdeaAction, ...]) -> Idea:
        return Idea(
            id=str(uuid.uuid4())[:8],
            category=category,
            title=title,
            rationale=rationale,
            source=source,
            priority=priority,
            actions=actions,
            generated_at=_utcnow(),
        )
