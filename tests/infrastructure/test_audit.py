from trading_platform.application.ports import AuditLogPort
from trading_platform.infrastructure.audit import JsonlAuditLog


class TestJsonlAuditLog:
    def test_satisfies_port(self, tmp_path):
        assert isinstance(JsonlAuditLog(tmp_path / "a.jsonl"), AuditLogPort)

    def test_records_and_tails(self, tmp_path):
        log = JsonlAuditLog(tmp_path / "audit.jsonl")
        log.record("scan_started", {"symbols": 18})
        log.record("scan_completed", {"recommendations": 2})
        entries = log.tail()
        assert len(entries) == 2
        assert entries[0]["event"] == "scan_started"
        assert entries[1]["payload"]["recommendations"] == 2
        assert "ts" in entries[0]

    def test_tail_limits(self, tmp_path):
        log = JsonlAuditLog(tmp_path / "audit.jsonl")
        for i in range(10):
            log.record("e", {"i": i})
        assert [e["payload"]["i"] for e in log.tail(3)] == [7, 8, 9]

    def test_tail_missing_file(self, tmp_path):
        assert JsonlAuditLog(tmp_path / "nope.jsonl").tail() == []

    def test_unicode_payload(self, tmp_path):
        log = JsonlAuditLog(tmp_path / "audit.jsonl")
        log.record("note", {"msg": "סריקה הושלמה"})
        assert log.tail()[0]["payload"]["msg"] == "סריקה הושלמה"
