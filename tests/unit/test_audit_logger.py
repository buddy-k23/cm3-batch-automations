"""Unit tests for src/utils/audit_logger.py."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.utils.audit_logger import (
    AuditLogger,
    AuditWriteError,
    file_hash,
    get_audit_logger,
    verify_audit_log,
    AuditVerificationResult,
    EVENT_TYPES,
)


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
        """APP_ENVIRONMENT env var is reflected in events."""
        monkeypatch.setenv("APP_ENVIRONMENT", "PROD")
        monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "a.jsonl"))
        audit = AuditLogger()
        event = audit.emit("test_run_started", triggered_by="cli")
        assert event["environment"] == "PROD"

    def test_default_environment_is_dev(self, tmp_path: Path, monkeypatch):
        """Without APP_ENVIRONMENT the default is DEV."""
        monkeypatch.delenv("APP_ENVIRONMENT", raising=False)
        audit = AuditLogger(log_path=tmp_path / "a.jsonl")
        assert audit.environment == "DEV"


# ---------------------------------------------------------------------------
# Event types constant
# ---------------------------------------------------------------------------


class TestEventTypes:
    """Tests for the EVENT_TYPES constant."""

    def test_known_event_types(self):
        """All documented event types are present.

        S13.5-2 (#415) added the config-mutation + auth-flow event types;
        the original set must remain a subset so the older callers still
        emit recognised types.
        """
        original = {
            "test_run_started",
            "test_run_completed",
            "file_uploaded",
            "file_cleanup",
            "auth_failure",
            "suite_step_completed",
        }
        s13_5_2 = {
            "config_mutation",
            "ldap_login_failure",
            "ldap_login_success",
            "ldap_logout",
            "mcp_login_failure",
            "mcp_login_success",
            "mcp_revoke_failure",
            "mcp_revoke_forbidden",
            "mcp_revoke_success",
        }
        assert EVENT_TYPES == original | s13_5_2


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


# ---------------------------------------------------------------------------
# Tamper-evidence: sequence + hash chain (S13.5-1, #408)
# ---------------------------------------------------------------------------


class TestTamperEvidentFields:
    """Each emitted record carries seq, prev_hash, hash, hash_alg."""

    def test_genesis_record_seq_zero_prev_null(self, tmp_path: Path, monkeypatch):
        """The first record has seq 0 and prev_hash None."""
        monkeypatch.delenv("VALDO_AUDIT_HMAC_KEY", raising=False)
        audit = AuditLogger(log_path=tmp_path / "audit.jsonl")
        event = audit.emit("test_run_started", triggered_by="cli")
        assert event["seq"] == 0
        assert event["prev_hash"] is None
        assert len(event["hash"]) == 64  # sha256 hex
        assert event["hash_alg"] == "sha256"

    def test_seq_increments_monotonically(self, tmp_path: Path, monkeypatch):
        """Subsequent records increment seq by 1 and chain prev_hash."""
        monkeypatch.delenv("VALDO_AUDIT_HMAC_KEY", raising=False)
        audit = AuditLogger(log_path=tmp_path / "audit.jsonl")
        e0 = audit.emit("test_run_started", triggered_by="cli")
        e1 = audit.emit("test_run_completed", triggered_by="cli")
        e2 = audit.emit("file_cleanup", triggered_by="cli")
        assert [e0["seq"], e1["seq"], e2["seq"]] == [0, 1, 2]
        assert e1["prev_hash"] == e0["hash"]
        assert e2["prev_hash"] == e1["hash"]

    def test_hash_excludes_hash_field_itself(self, tmp_path: Path, monkeypatch):
        """The on-disk record's hash is recomputable from the rest of the payload."""
        monkeypatch.delenv("VALDO_AUDIT_HMAC_KEY", raising=False)
        audit = AuditLogger(log_path=tmp_path / "audit.jsonl")
        audit.emit("test_run_started", triggered_by="cli")
        result = verify_audit_log(tmp_path / "audit.jsonl")
        assert result.ok is True
        assert result.records_checked == 1


class TestVerifierCleanLog:
    """verify_audit_log accepts an untampered log."""

    def test_clean_log_verifies(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("VALDO_AUDIT_HMAC_KEY", raising=False)
        log = tmp_path / "audit.jsonl"
        audit = AuditLogger(log_path=log)
        for i in range(5):
            audit.emit("test_run_started", triggered_by="cli", n=i)
        result = verify_audit_log(log)
        assert isinstance(result, AuditVerificationResult)
        assert result.ok is True
        assert result.records_checked == 5
        assert result.error is None
        assert result.bad_seq is None

    def test_empty_log_verifies(self, tmp_path: Path):
        log = tmp_path / "empty.jsonl"
        log.write_text("")
        result = verify_audit_log(log)
        assert result.ok is True
        assert result.records_checked == 0

    def test_missing_log_reports_error(self, tmp_path: Path):
        result = verify_audit_log(tmp_path / "nope.jsonl")
        assert result.ok is False
        assert result.error is not None


class TestTamperDetection:
    """Mutating, removing, or reordering a record flags exactly that seq."""

    def _write_clean(self, log: Path) -> AuditLogger:
        audit = AuditLogger(log_path=log)
        for i in range(5):
            audit.emit("test_run_started", triggered_by="cli", n=i)
        return audit

    def test_mutated_payload_detected(self, tmp_path: Path, monkeypatch):
        """Changing a field in record seq=2 is detected at seq=2."""
        monkeypatch.delenv("VALDO_AUDIT_HMAC_KEY", raising=False)
        log = tmp_path / "audit.jsonl"
        self._write_clean(log)
        lines = log.read_text().splitlines()
        rec = json.loads(lines[2])
        rec["n"] = 999  # tamper, but leave hash unchanged
        lines[2] = json.dumps(rec)
        log.write_text("\n".join(lines) + "\n")

        result = verify_audit_log(log)
        assert result.ok is False
        assert result.bad_seq == 2

    def test_removed_record_detected(self, tmp_path: Path, monkeypatch):
        """Deleting record seq=2 breaks the chain (gap / prev_hash mismatch)."""
        monkeypatch.delenv("VALDO_AUDIT_HMAC_KEY", raising=False)
        log = tmp_path / "audit.jsonl"
        self._write_clean(log)
        lines = log.read_text().splitlines()
        del lines[2]
        log.write_text("\n".join(lines) + "\n")

        result = verify_audit_log(log)
        assert result.ok is False
        # After removing seq=2, the line now at index 2 has seq=3 -> gap at 2.
        assert result.bad_seq == 2

    def test_reordered_records_detected(self, tmp_path: Path, monkeypatch):
        """Swapping two records breaks the prev_hash chain."""
        monkeypatch.delenv("VALDO_AUDIT_HMAC_KEY", raising=False)
        log = tmp_path / "audit.jsonl"
        self._write_clean(log)
        lines = log.read_text().splitlines()
        lines[1], lines[2] = lines[2], lines[1]
        log.write_text("\n".join(lines) + "\n")

        result = verify_audit_log(log)
        assert result.ok is False
        assert result.bad_seq == 1


class TestChainContinuityAcrossRestart:
    """A new AuditLogger on the same path continues the chain."""

    def test_restart_continues_chain(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("VALDO_AUDIT_HMAC_KEY", raising=False)
        log = tmp_path / "audit.jsonl"
        first = AuditLogger(log_path=log)
        first.emit("test_run_started", triggered_by="cli")
        first.emit("test_run_completed", triggered_by="cli")

        # Simulate a process restart: brand-new instance, same path.
        second = AuditLogger(log_path=log)
        e = second.emit("file_cleanup", triggered_by="cli")
        assert e["seq"] == 2  # continues from where first left off

        result = verify_audit_log(log)
        assert result.ok is True
        assert result.records_checked == 3


class TestHmacKeyed:
    """HMAC-keyed integrity vs unkeyed SHA-256 fallback."""

    def test_keyed_records_use_hmac_alg(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("VALDO_AUDIT_HMAC_KEY", "super-secret-key")
        audit = AuditLogger(log_path=tmp_path / "audit.jsonl")
        e = audit.emit("test_run_started", triggered_by="cli")
        assert e["hash_alg"] == "hmac-sha256"

    def test_keyed_log_verifies_with_correct_key(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("VALDO_AUDIT_HMAC_KEY", "correct-key")
        log = tmp_path / "audit.jsonl"
        audit = AuditLogger(log_path=log)
        for i in range(3):
            audit.emit("test_run_started", triggered_by="cli", n=i)
        result = verify_audit_log(log, key="correct-key")
        assert result.ok is True
        assert result.records_checked == 3

    def test_keyed_log_fails_with_wrong_key(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("VALDO_AUDIT_HMAC_KEY", "correct-key")
        log = tmp_path / "audit.jsonl"
        audit = AuditLogger(log_path=log)
        audit.emit("test_run_started", triggered_by="cli")
        result = verify_audit_log(log, key="WRONG-key")
        assert result.ok is False
        assert result.bad_seq == 0

    def test_unkeyed_fallback_is_sha256(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("VALDO_AUDIT_HMAC_KEY", raising=False)
        audit = AuditLogger(log_path=tmp_path / "audit.jsonl")
        e = audit.emit("test_run_started", triggered_by="cli")
        assert e["hash_alg"] == "sha256"

    def test_verifier_autodetects_alg_per_record(self, tmp_path: Path, monkeypatch):
        """A keyed log verifies even when caller passes key explicitly."""
        monkeypatch.setenv("VALDO_AUDIT_HMAC_KEY", "k")
        log = tmp_path / "audit.jsonl"
        AuditLogger(log_path=log).emit("test_run_started", triggered_by="cli")
        # hash_alg recorded on disk is hmac-sha256 -> verifier needs key.
        result = verify_audit_log(log, key="k")
        assert result.ok is True


class TestWriteFailureRaises:
    """emit no longer swallows the audit-FILE write error."""

    def test_file_write_failure_raises_audit_write_error(
        self, tmp_path: Path, monkeypatch
    ):
        monkeypatch.delenv("VALDO_AUDIT_HMAC_KEY", raising=False)
        audit = AuditLogger(log_path=tmp_path / "audit.jsonl")

        import builtins

        real_open = builtins.open

        def boom(path, *args, **kwargs):
            if str(path).endswith("audit.jsonl") and "a" in (
                args[0] if args else kwargs.get("mode", "")
            ):
                raise OSError("disk full")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", boom)
        with pytest.raises(AuditWriteError):
            audit.emit("test_run_started", triggered_by="cli")

    def test_stdout_failure_does_not_raise(self, tmp_path: Path, monkeypatch):
        """A failing stdout/echo write must NOT raise (best-effort only)."""
        monkeypatch.delenv("VALDO_AUDIT_HMAC_KEY", raising=False)
        audit = AuditLogger(log_path=tmp_path / "audit.jsonl", write_to_stdout=True)

        def boom(*args, **kwargs):
            raise OSError("broken pipe")

        monkeypatch.setattr("builtins.print", boom)
        # File write succeeds; stdout fails but is swallowed.
        event = audit.emit("test_run_started", triggered_by="cli")
        assert event["seq"] == 0
