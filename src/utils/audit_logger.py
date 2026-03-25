"""Splunk-compatible structured audit logger.

Emits one JSON object per line (JSONL) to a configurable log file and/or
stdout, designed for ingestion by Splunk Universal Forwarder with
``sourcetype=_json``.

Environment variables:
    AUDIT_LOG_PATH: Path to the JSONL audit log file.
        Default: ``logs/audit.jsonl``.
    CM3_ENVIRONMENT: Deployment environment tag included in every event.
        Default: ``DEV``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_LOG_PATH = "logs/audit.jsonl"
_DEFAULT_ENVIRONMENT = "DEV"
_HASH_BUF_SIZE = 65_536  # 64 KiB read chunks for SHA-256

# Valid event types
EVENT_TYPES = frozenset(
    {
        "test_run_started",
        "test_run_completed",
        "file_uploaded",
        "file_cleanup",
        "auth_failure",
        "suite_step_completed",
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def file_hash(path: str | Path) -> str:
    """Compute the SHA-256 hex digest of a file.

    Args:
        path: Filesystem path to the file.

    Returns:
        Lowercase hex-encoded SHA-256 hash string.

    Raises:
        FileNotFoundError: If *path* does not exist.
        OSError: On I/O errors.
    """
    sha = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(_HASH_BUF_SIZE)
            if not chunk:
                break
            sha.update(chunk)
    return sha.hexdigest()


# ---------------------------------------------------------------------------
# AuditLogger
# ---------------------------------------------------------------------------


class AuditLogger:
    """Structured audit logger that writes JSONL events.

    Each call to :meth:`emit` produces a single JSON line containing at
    minimum: ``event``, ``timestamp``, ``run_id``, ``environment``, and
    ``triggered_by``.  Additional keyword arguments are merged into the
    event payload.

    Args:
        log_path: Override for the JSONL file path.  When *None* the value
            of ``AUDIT_LOG_PATH`` is used (falling back to
            ``logs/audit.jsonl``).
        environment: Override for the environment tag.  When *None* the
            value of ``CM3_ENVIRONMENT`` is used (falling back to ``DEV``).
        write_to_stdout: Also write each event to stdout.  Defaults to
            False.

    Example::

        audit = AuditLogger()
        audit.emit(
            "test_run_started",
            triggered_by="api",
            file="data.dat",
            file_hash=file_hash("data.dat"),
        )
    """

    def __init__(
        self,
        log_path: Optional[str | Path] = None,
        environment: Optional[str] = None,
        write_to_stdout: bool = False,
    ) -> None:
        self._log_path = Path(
            log_path or os.getenv("AUDIT_LOG_PATH", _DEFAULT_LOG_PATH)
        )
        self._environment = (
            environment or os.getenv("CM3_ENVIRONMENT", _DEFAULT_ENVIRONMENT)
        )
        self._write_to_stdout = write_to_stdout
        self._run_id = uuid.uuid4().hex

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def run_id(self) -> str:
        """Return the unique run ID for this logger session."""
        return self._run_id

    @property
    def log_path(self) -> Path:
        """Return the resolved audit log file path."""
        return self._log_path

    @property
    def environment(self) -> str:
        """Return the configured environment tag."""
        return self._environment

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def emit(self, event_type: str, **kwargs: Any) -> dict[str, Any]:
        """Write a structured audit event.

        Args:
            event_type: One of the recognised event type strings (e.g.
                ``test_run_started``).  Unknown types are logged with a
                warning but still emitted.
            **kwargs: Arbitrary additional fields merged into the event
                payload.  Common keys include ``triggered_by``, ``file``,
                ``file_hash``, ``mapping``, ``mapping_hash``, ``result``,
                ``error``.

        Returns:
            The complete event dict that was written.
        """
        if event_type not in EVENT_TYPES:
            logger.warning("Unknown audit event type: %s", event_type)

        event: dict[str, Any] = {
            "event": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": self._run_id,
            "environment": self._environment,
        }
        # Ensure triggered_by has a default
        if "triggered_by" not in kwargs:
            kwargs["triggered_by"] = "system"
        event.update(kwargs)

        line = json.dumps(event, default=str)

        # Write to file
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            logger.exception("Failed to write audit event to %s", self._log_path)

        # Optionally write to stdout
        if self._write_to_stdout:
            print(line)  # noqa: T201

        return event


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_default_logger: Optional[AuditLogger] = None


def get_audit_logger() -> AuditLogger:
    """Return the module-level default AuditLogger (created on first call).

    Returns:
        The shared AuditLogger instance.
    """
    global _default_logger  # noqa: PLW0603
    if _default_logger is None:
        _default_logger = AuditLogger()
    return _default_logger
