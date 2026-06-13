"""Append-only JSONL audit log implementing AuditLogPort.

One JSON object per line so the file is greppable, tail-able, and safe to
append concurrently (flock) from the bot and dashboard containers.
"""
import fcntl
import json
from datetime import datetime, timezone
from pathlib import Path


class JsonlAuditLog:
    """Implements trading_platform.application.ports.AuditLogPort."""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: str, payload: dict) -> None:
        line = json.dumps(
            {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "event": event, "payload": payload},
            ensure_ascii=False,
        )
        with open(self._path, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(line + "\n")

    def tail(self, n: int = 50) -> list[dict]:
        if not self._path.exists():
            return []
        with open(self._path) as f:
            lines = f.readlines()[-n:]
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
