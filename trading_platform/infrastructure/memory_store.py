"""JSON-file implementation of the MemoryStore port.

Adds what the legacy feedback.py persistence lacked:
- atomic writes (temp file + os.replace) so readers never see torn JSON
- flock-based write serialization, since the bot and dashboard containers
  share the data/ volume and both write weights/feedback
"""
import fcntl
import json
import logging
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path

from trading_platform.domain import FeedbackEvent

logger = logging.getLogger(__name__)

_KEY_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


class JsonMemoryStore:
    """Implements trading_platform.application.ports.MemoryStore."""

    def __init__(self, base_dir: str | Path):
        self._dir = Path(base_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        if not _KEY_RE.match(key):
            raise ValueError(f"invalid store key: {key!r}")
        return self._dir / f"{key}.json"

    def _lock_path(self, key: str) -> Path:
        return self._dir / f".{key}.lock"

    def load(self, key: str, default: dict) -> dict:
        path = self._path(key)
        if not path.exists():
            return deepcopy(default)
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.warning("corrupt or unreadable %s, returning default", path)
            return deepcopy(default)

    def save(self, key: str, value: dict) -> None:
        with open(self._lock_path(key), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self._write(key, value)

    def _write(self, key: str, value: dict) -> None:
        path = self._path(key)
        fd, tmp = tempfile.mkstemp(dir=self._dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(value, f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def append_feedback(self, event: FeedbackEvent) -> None:
        # Single lock around read-modify-write so concurrent appends don't drop entries.
        # Dict shape matches the legacy data/feedback.json format.
        with open(self._lock_path("feedback"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data = self.load("feedback", {"history": []})
            data["history"].append({
                "ticker": event.symbol,
                "signals": [s.value for s in event.signal_types],
                "positive": event.positive,
                "timestamp": event.timestamp.isoformat(),
                **({"notes": event.notes} if event.notes else {}),
            })
            self._write("feedback", data)
