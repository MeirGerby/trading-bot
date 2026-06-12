"""Tests for IdeaEngine heuristics and apply/persist logic."""
import json
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from trading_platform.application.services.idea_engine import (
    IdeaEngine,
    IDEAS_KEY,
    RISK_OVERRIDES_KEY,
    WEIGHTS_KEY,
)
from trading_platform.domain.models import Idea, IdeaAction
from trading_platform.infrastructure.memory_store import JsonMemoryStore


# ─── Fixtures ────────────────────────────────────────────────────────────────

def _temp_memory():
    d = tempfile.mkdtemp()
    return JsonMemoryStore(Path(d))


def _make_engine(memory=None, market_data=None, symbol_repo=None,
                  tracker=None, learning=None, critique=None):
    memory = memory or _temp_memory()
    if market_data is None:
        market_data = MagicMock()
        market_data.get_daily_bars.return_value = []
    if symbol_repo is None:
        symbol_repo = MagicMock()
        symbol_repo.get_watchlist.return_value = []
    return IdeaEngine(
        memory=memory,
        market_data=market_data,
        symbol_repository=symbol_repo,
        performance_tracker=tracker,
        learning_engine=learning,
        self_critique_engine=critique,
    )


def _fake_strategy_perf(win_rate=0.5, max_drawdown_pct=0.05,
                         profit_factor=1.2, total_evaluated=10):
    sp = MagicMock()
    sp.win_rate = win_rate
    sp.max_drawdown_pct = max_drawdown_pct
    sp.profit_factor = profit_factor
    sp.total_evaluated = total_evaluated
    return sp


def _fake_bar(close: float):
    b = MagicMock()
    b.close = close
    return b


S_PARAMS = {
    "breakout_volume_ratio": 1.5,
    "momentum_rsi_min": 60.0,
    "options_vol_oi_ratio": 1.2,
    "mean_reversion_rsi_max": 35.0,
    "trend_rsi_min": 50.0,
    "min_score_to_alert": 2.0,
}
R_PARAMS = {
    "stop_loss_pct": 0.08,
    "take_profit_pct": 0.20,
    "base_allocation_pct": 0.05,
    "max_position_pct": 0.10,
}


# ─── Performance analyser ─────────────────────────────────────────────────────

class TestAnalyzePerformance:
    def test_low_win_rate_tightens_threshold(self):
        tracker = MagicMock()
        tracker.get_strategy_performance.return_value = {
            "momentum": _fake_strategy_perf(win_rate=0.25, total_evaluated=8)
        }
        eng = _make_engine(tracker=tracker)
        ideas = eng._analyze_performance(dict(S_PARAMS), dict(R_PARAMS))
        tighten = [i for i in ideas if "Tighten" in i.title and "momentum" in i.title]
        assert tighten, "should propose tightening momentum threshold"
        action = tighten[0].actions[0]
        assert action.action_type == "set_strategy_param"
        assert action.key == "momentum_rsi_min"
        # up_is_strict → value should increase
        assert float(action.new_value) > float(action.old_value)

    def test_high_win_rate_relaxes_threshold(self):
        tracker = MagicMock()
        tracker.get_strategy_performance.return_value = {
            "breakout": _fake_strategy_perf(win_rate=0.70, total_evaluated=12)
        }
        eng = _make_engine(tracker=tracker)
        ideas = eng._analyze_performance(dict(S_PARAMS), dict(R_PARAMS))
        relax = [i for i in ideas if "Relax" in i.title]
        assert relax, "should propose relaxing threshold on strong strategy"
        assert float(relax[0].actions[0].new_value) < float(relax[0].actions[0].old_value)

    def test_high_drawdown_reduces_allocation(self):
        tracker = MagicMock()
        tracker.get_strategy_performance.return_value = {
            "momentum": _fake_strategy_perf(win_rate=0.50, max_drawdown_pct=0.25)
        }
        eng = _make_engine(tracker=tracker)
        ideas = eng._analyze_performance(dict(S_PARAMS), dict(R_PARAMS))
        alloc_ideas = [i for i in ideas if "allocation" in i.title.lower()]
        assert alloc_ideas

    def test_skips_strategy_with_too_few_outcomes(self):
        tracker = MagicMock()
        tracker.get_strategy_performance.return_value = {
            "momentum": _fake_strategy_perf(win_rate=0.10, total_evaluated=3)
        }
        eng = _make_engine(tracker=tracker)
        ideas = eng._analyze_performance(dict(S_PARAMS), dict(R_PARAMS))
        assert not ideas, "should skip strategies with < 5 outcomes"

    def test_no_tracker_returns_empty(self):
        eng = _make_engine(tracker=None)
        assert eng._analyze_performance(dict(S_PARAMS), dict(R_PARAMS)) == []


# ─── Market regime analyser ───────────────────────────────────────────────────

class TestAnalyzeMarketRegime:
    def _bars(self, prices):
        return [_fake_bar(p) for p in prices]

    def test_bearish_regime_defensive_adjustment(self):
        prices = [100.0] * 12  # flat for 11 then drop
        prices[-1] = 92.0      # 10d return ≈ -8%
        md = MagicMock()
        md.get_daily_bars.return_value = self._bars(prices)
        eng = _make_engine(market_data=md)
        ideas = eng._analyze_market_regime(dict(R_PARAMS))
        bearish = [i for i in ideas if "bearish" in i.title.lower()]
        assert bearish, "should flag bearish regime"
        keys_changed = {a.key for idea in bearish for a in idea.actions}
        assert "base_allocation_pct" in keys_changed or "stop_loss_pct" in keys_changed

    def test_bullish_regime_extends_take_profit(self):
        prices = [100.0] * 12
        prices[-1] = 108.0  # +8%
        md = MagicMock()
        md.get_daily_bars.return_value = self._bars(prices)
        eng = _make_engine(market_data=md)
        ideas = eng._analyze_market_regime(dict(R_PARAMS))
        bullish = [i for i in ideas if "bullish" in i.title.lower()]
        assert bullish
        assert any(a.key == "take_profit_pct" for idea in bullish for a in idea.actions)

    def test_insufficient_bars_returns_empty(self):
        md = MagicMock()
        md.get_daily_bars.return_value = self._bars([100.0] * 5)
        eng = _make_engine(market_data=md)
        assert eng._analyze_market_regime(dict(R_PARAMS)) == []

    def test_market_data_exception_returns_empty(self):
        md = MagicMock()
        md.get_daily_bars.side_effect = Exception("network error")
        eng = _make_engine(market_data=md)
        assert eng._analyze_market_regime(dict(R_PARAMS)) == []


# ─── Clamp & significance ─────────────────────────────────────────────────────

class TestGuardRails:
    def test_clamp_limits_to_30pct(self):
        eng = _make_engine()
        result = eng._clamp(100.0, 200.0)  # proposed +100% → clamped to +30%
        assert result == pytest.approx(130.0)

    def test_clamp_limits_decrease_to_30pct(self):
        eng = _make_engine()
        result = eng._clamp(100.0, 10.0)   # proposed -90% → clamped to -30%
        assert result == pytest.approx(70.0)

    def test_is_significant_above_threshold(self):
        assert IdeaEngine._is_significant(100.0, 104.0)  # +4% > 3%

    def test_is_significant_below_threshold(self):
        assert not IdeaEngine._is_significant(100.0, 101.5)  # +1.5% < 3%

    def test_is_significant_zero_old(self):
        assert IdeaEngine._is_significant(0.0, 1.0)
        assert not IdeaEngine._is_significant(0.0, 0.0)


# ─── Apply & persist ─────────────────────────────────────────────────────────

class TestApplyAndPersist:
    def _make_idea(self, actions):
        return Idea(
            id="test1234",
            category="strategy",
            title="test idea",
            rationale="test",
            source="performance_analysis",
            priority="high",
            actions=tuple(actions),
            generated_at=datetime.now(timezone.utc),
            applied=False,
        )

    def test_apply_writes_strategy_param(self):
        mem = _temp_memory()
        eng = _make_engine(memory=mem)
        idea = self._make_idea([
            IdeaAction("set_strategy_param", "momentum_rsi_min", "66.0", "60.0")
        ])
        applied = eng._apply(idea)
        assert applied.applied
        weights = mem.load(WEIGHTS_KEY, {})
        assert weights.get("momentum_rsi_min") == pytest.approx(66.0)

    def test_apply_writes_risk_param(self):
        mem = _temp_memory()
        eng = _make_engine(memory=mem)
        idea = self._make_idea([
            IdeaAction("set_risk_param", "stop_loss_pct", "0.088", "0.08")
        ])
        eng._apply(idea)
        overrides = mem.load(RISK_OVERRIDES_KEY, {})
        assert overrides.get("stop_loss_pct") == pytest.approx(0.088)

    def test_apply_calls_add_watchlist(self):
        mem = _temp_memory()
        repo = MagicMock()
        repo.get_watchlist.return_value = []
        repo.add_to_watchlist.return_value = True
        eng = _make_engine(memory=mem, symbol_repo=repo)
        idea = self._make_idea([
            IdeaAction("add_watchlist", "SPY", "SPY", "not_in_watchlist")
        ])
        eng._apply(idea)
        repo.add_to_watchlist.assert_called_once_with("SPY")

    def test_persist_stores_ideas_in_memory(self):
        mem = _temp_memory()
        eng = _make_engine(memory=mem)
        idea = self._make_idea([
            IdeaAction("set_strategy_param", "momentum_rsi_min", "60.0", "66.0")
        ])
        eng._persist([idea])
        store = mem.load(IDEAS_KEY, {"ideas": []})
        assert len(store["ideas"]) == 1
        assert store["ideas"][0]["id"] == "test1234"

    def test_persist_caps_at_max_history(self):
        mem = _temp_memory()
        eng = _make_engine(memory=mem)
        # pre-fill with max+10 entries
        store = {"ideas": [{"id": str(i)} for i in range(eng.MAX_IDEAS_HISTORY + 10)]}
        mem.save(IDEAS_KEY, store)
        idea = self._make_idea([IdeaAction("set_risk_param", "stop_loss_pct", "0.08", "0.088")])
        eng._persist([idea])
        result = mem.load(IDEAS_KEY, {"ideas": []})
        assert len(result["ideas"]) <= eng.MAX_IDEAS_HISTORY


# ─── run_daily_cycle integration ──────────────────────────────────────────────

class TestRunDailyCycle:
    def test_run_returns_applied_ideas(self):
        tracker = MagicMock()
        tracker.get_strategy_performance.return_value = {
            "momentum": _fake_strategy_perf(win_rate=0.20, total_evaluated=10)
        }
        tracker.get_all_outcomes.return_value = []
        md = MagicMock()
        md.get_daily_bars.return_value = []
        learning = MagicMock()
        learning.get_recent_lessons.return_value = []
        critique = MagicMock()
        critique.get_recent_critiques.return_value = []
        repo = MagicMock()
        repo.get_watchlist.return_value = ["AAPL", "MSFT"]
        mem = _temp_memory()

        eng = IdeaEngine(
            memory=mem, market_data=md, symbol_repository=repo,
            performance_tracker=tracker, learning_engine=learning,
            self_critique_engine=critique,
        )
        applied = eng.run_daily_cycle(dict(S_PARAMS), dict(R_PARAMS))
        assert all(i.applied for i in applied)

    def test_get_recent_ideas_returns_newest_first(self):
        mem = _temp_memory()
        store = {"ideas": [{"id": str(i), "title": f"idea {i}"} for i in range(10)]}
        mem.save(IDEAS_KEY, store)
        eng = _make_engine(memory=mem)
        recent = eng.get_recent_ideas(3)
        assert len(recent) == 3
        assert recent[0]["id"] == "9"  # newest last in list → reversed → first here
