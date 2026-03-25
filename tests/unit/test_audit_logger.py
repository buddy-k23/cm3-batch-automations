"""Unit tests for src/utils/audit_logger.py."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.utils.audit_logger import AuditLogger, file_hash, get_audit_logger, EVENT_TYPES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_file(path: Path, content: str = "hello") -> Path:
    """Write a small file and return its path."""
    path.write_text(content)
    return path


# ---------------------------------------------------------------------------
# file_hash tests
# ---------------------------------------------------------------------------


class TestFileHash:
    """Tests for the file_hash helper."""

    def test_returns_sha256_hex_digest(self, tmp_path: Path):
        """SHA-256 of a known string matches the expected value."""
        f = _make_file(tmp_path / "data.txt", "hello")
        digest = file_hash(f)
        # SHA-256("hello") is well-known
        assert digest == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"

    def test_empty_file_hash(self, tmp_path: Path):
        """Empty file has deterministic SHA-256."""
        f = _make_file(tmp_path / "empty.txt", "")
        digest = file_hash(f)
        # SHA-256 of empty input
        assert digest == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    def test_large_file_hash(self, tmp_path: Path):
        """Hash works for files larger than the internal buffer."""
        content = "x" * 200_000  # > 64 KiB
        f = _make_file(tmp_path / "big.txt", content)
        digest = file_hash(f)
        assert len(digest) == 64  # hex SHA-256 is 64 chars

    def test_file_not_found_raises(self, tmp_path: Path):
        """Missing file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            file_hash(tmp_path / "missing.txt")


# ---------------------------------------------------------------------------
# AuditLogger.emit tests
# ---------------------------------------------------------------------------


class TestAuditLoggerEmit:
    """Tests for AuditLogger event emission."""

    def test_emit_writes_jsonl(self, tmp_path: Path):
        """Each emit() appends exactly one JSON line to the log file."""
        log_file = tmp_path / "audit.jsonl"
        audit = AuditLogger(log_path=log_file)
        audit.emit("test_run_started", triggered_by="cli")
        audit.emit("test_run_completed", triggered_by="cli", result="pass")

        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 2

    def test_event_structure(self, tmp_path: Path):
        """Emitted events contain all required top-level keys."""
        log_file = tmp_path / "audit.jsonl"
        audit = AuditLogger(log_path=log_file, environment="STAGING")
        event = audit.emit("test_run_started", triggered_by="api", file="data.dat")

        assert event["event"] == "test_run_started"
        assert "timestamp" in event
        assert event["run_id"] == audit.run_id
        assert event["environment"] == "STAGING"
        assert event["triggered_by"] == "api"
        assert event["file"] == "data.dat"

    def test_default_triggered_by(self, tmp_path: Path):
        """When triggered_by is omitted it defaults to 'system'."""
        log_file = tmp_path / "audit.jsonl"
        audit = AuditLogger(log_path=log_file)
        event = audit.emit("file_cleanup")
        assert event["triggered_by"] == "system"

    def test_event_is_valid_json_on_disk(self, tmp_path: Path):
        """The line written to disk is parseable JSON matching the returned dict."""
        log_file = tmp_path / "audit.jsonl"
        audit = AuditLogger(log_path=log_file)
        returned = audit.emit("file_uploaded", triggered_by="api", size=42)

        line = log_file.read_text().strip()
        parsed = json.loads(line)
        assert parsed == returned

    def test_run_id_consistent(self, tmp_path: Path):
        """All events from the same logger share the same run_id."""
        log_file = tmp_path / "audit.jsonl"
        audit = AuditLogger(log_path=log_file)
        e1 = audit.emit("test_run_started", triggered_by="cli")
        e2 = audit.emit("test_run_completed", triggered_by="cli")
        assert e1["run_id"] == e2["run_id"]
        assert len(e1["run_id"]) == 32  # uuid4 hex

    def test_unknown_event_type_still_emitted(self, tmp_path: Path):
        """Unknown event types emit a warning but still produce output."""
        log_file = tmp_path / "audit.jsonl"
        audit = AuditLogger(log_path=log_file)
        event = audit.emit("unknown_event", triggered_by="test")
        assert event["event"] == "unknown_event"
        assert log_file.exists()

    def test_extra_kwargs_merged(self, tmp_path: Path):
        """Arbitrary keyword arguments appear in the event dict."""
        log_file = tmp_path / "audit.jsonl"
        audit = AuditLogger(log_path=log_file)
        event = audit.emit(
            "test_run_started",
            triggered_by="cli",
            mapping_hash="abc123",
            total_rows=500,
        )
        assert event["mapping_hash"] == "abc123"
        assert event["total_rows"] == 500

    def test_creates_parent_directories(self, tmp_path: Path):
        """Emit creates intermediate directories for the log file."""
        log_file = tmp_path / "deep" / "nested" / "audit.jsonl"
        audit = AuditLogger(log_path=log_file)
        audit.emit("test_run_started", triggered_by="cli")
        assert log_file.exists()


# ---------------------------------------------------------------------------
# AuditLogger — stdout
# ---------------------------------------------------------------------------


class TestAuditLoggerStdout:
    """Tests for stdout output."""

    def test_write_to_stdout(self, tmp_path: Path, capsys):
        """When write_to_stdout=True the event JSON is printed."""
        log_file = tmp_path / "audit.jsonl"
        audit = AuditLogger(log_path=log_file, write_to_stdout=True)
        audit.emit("test_run_started", triggered_by="cli")

        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert parsed["event"] == "test_run_started"

    def test_no_stdout_by_default(self, tmp_path: Path, capsys):
        """By default nothing is printed to stdout."""
        log_file = tmp_path / "audit.jsonl"
        audit = AuditLogger(log_path=log_file)
        audit.emit("test_run_started", triggered_by="cli")
        assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# AuditLogger — environment config
# ---------------------------------------------------------------------------


class TestAuditLoggerConfig:
    """Tests for environment-variable-driven configuration."""

    def test_env_var_log_path(self, tmp_path: Path, monkeypatch):
        """AUDIT_LOG_PATH env var controls the log file location."""
        target = tmp_path / "custom.jsonl"
        monkeypatch.setenv("AUDIT_LOG_PATH", str(target))
        audit = AuditLogger()
        assert audit.log_path == target

    def test_env_var_environment(self, monkeypatch, tmp_path: Path):
        """CM3_ENVIRONMENT env var is reflected in events."""
        monkeypatch.setenv("CM3_ENVIRONMENT", "PROD")
        monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "a.jsonl"))
        audit = AuditLogger()
        event = audit.emit("test_run_started", triggered_by="cli")
        assert event["environment"] == "PROD"

    def test_default_environment_is_dev(self, tmp_path: Path, monkeypatch):
        """Without CM3_ENVIRONMENT the default is DEV."""
        monkeypatch.delenv("CM3_ENVIRONMENT", raising=False)
        audit = AuditLogger(log_path=tmp_path / "a.jsonl")
        assert audit.environment == "DEV"


# ---------------------------------------------------------------------------
# Event types constant
# ---------------------------------------------------------------------------


class TestEventTypes:
    """Tests for the EVENT_TYPES constant."""

    def test_known_event_types(self):
        """All documented event types are present."""
        expected = {
            "test_run_started",
            "test_run_completed",
            "file_uploaded",
            "file_cleanup",
            "auth_failure",
            "suite_step_completed",
        }
        assert EVENT_TYPES == expected


# ---------------------------------------------------------------------------
# get_audit_logger singleton
# ---------------------------------------------------------------------------


class TestGetAuditLogger:
    """Tests for the module-level singleton factory."""

    def test_returns_audit_logger(self, monkeypatch, tmp_path: Path):
        """get_audit_logger returns an AuditLogger instance."""
        import src.utils.audit_logger as mod

        monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "s.jsonl"))
        monkeypatch.setattr(mod, "_default_logger", None)
        result = get_audit_logger()
        assert isinstance(result, AuditLogger)

    def test_singleton_returns_same_instance(self, monkeypatch, tmp_path: Path):
        """Repeated calls return the same object."""
        import src.utils.audit_logger as mod

        monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "s.jsonl"))
        monkeypatch.setattr(mod, "_default_logger", None)
        a = get_audit_logger()
        b = get_audit_logger()
        assert a is b
