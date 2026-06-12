"""Regression tests for the legacy feedback facade after migrating its
persistence onto JsonMemoryStore — production bot behavior must not change."""
import importlib

import pytest

import feedback


@pytest.fixture
def fb(tmp_path, monkeypatch):
    from trading_platform.infrastructure.memory_store import JsonMemoryStore
    monkeypatch.setattr(feedback, "_store", JsonMemoryStore(tmp_path))
    return feedback


class TestLegacyFeedbackFacade:
    def test_load_weights_merges_defaults(self, fb):
        fb.save_weights({"momentum_rsi_min": 70})
        merged = fb.load_weights({"momentum_rsi_min": 60, "new_key": 1})
        assert merged == {"momentum_rsi_min": 70, "new_key": 1}

    def test_positive_feedback_raises_thresholds_5pct(self, fb):
        weights = {"breakout_volume_ratio": 2.0, "breakout_pct_from_high": 0.98}
        updated = fb.record_feedback("AAPL", ["breakout"], positive=True, weights=weights)
        assert updated["breakout_volume_ratio"] == 2.1
        assert updated["breakout_pct_from_high"] == round(0.98 * 1.05, 4)

    def test_negative_feedback_lowers_thresholds(self, fb):
        weights = {"momentum_rsi_min": 60.0}
        updated = fb.record_feedback("AAPL", ["momentum"], positive=False, weights=weights)
        assert updated["momentum_rsi_min"] == 57.0

    def test_summary_empty(self, fb):
        assert fb.get_feedback_summary() == "אין פידבק עדיין."

    def test_summary_counts(self, fb):
        fb.record_feedback("AAPL", ["breakout"], True, {})
        fb.record_feedback("MSFT", ["momentum"], False, {})
        summary = fb.get_feedback_summary()
        assert "סה\"כ פידבק: 2" in summary
        assert "חיובי: 1" in summary
