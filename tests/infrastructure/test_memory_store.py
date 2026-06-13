import json
from datetime import datetime, timezone

import pytest

from trading_platform.application.ports import MemoryStore
from trading_platform.domain import FeedbackEvent, SignalType
from trading_platform.infrastructure.memory_store import JsonMemoryStore


@pytest.fixture
def store(tmp_path):
    return JsonMemoryStore(tmp_path)


class TestJsonMemoryStore:
    def test_satisfies_port_protocol(self, store):
        assert isinstance(store, MemoryStore)

    def test_load_missing_returns_default_copy(self, store):
        default = {"history": []}
        loaded = store.load("nope", default)
        loaded["history"].append("x")
        assert default == {"history": []}

    def test_save_load_roundtrip_unicode(self, store):
        store.save("weights", {"note": "ממוצע", "rsi": 63.0})
        assert store.load("weights", {}) == {"note": "ממוצע", "rsi": 63.0}

    def test_corrupt_file_returns_default(self, store, tmp_path):
        (tmp_path / "bad.json").write_text("{not json")
        assert store.load("bad", {"ok": True}) == {"ok": True}

    def test_rejects_path_traversal_keys(self, store):
        with pytest.raises(ValueError):
            store.load("../etc/passwd", {})

    def test_no_temp_files_left_behind(self, store, tmp_path):
        store.save("a", {"x": 1})
        leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
        assert leftovers == []

    def test_append_feedback_uses_legacy_format(self, store, tmp_path):
        store.append_feedback(FeedbackEvent(
            symbol="NVDA",
            signal_types=(SignalType.BREAKOUT, SignalType.MOMENTUM),
            positive=True,
            timestamp=datetime(2026, 6, 12, tzinfo=timezone.utc),
        ))
        data = json.loads((tmp_path / "feedback.json").read_text())
        entry = data["history"][0]
        assert entry["ticker"] == "NVDA"
        assert entry["signals"] == ["breakout", "momentum"]
        assert entry["positive"] is True
        assert "notes" not in entry

    def test_append_feedback_preserves_existing(self, store):
        store.save("feedback", {"history": [{"ticker": "OLD"}]})
        store.append_feedback(FeedbackEvent(symbol="NEW", signal_types=(), positive=False))
        history = store.load("feedback", {})["history"]
        assert [e["ticker"] for e in history] == ["OLD", "NEW"]
