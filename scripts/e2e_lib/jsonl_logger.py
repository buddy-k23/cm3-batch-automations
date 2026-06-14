"""Structured JSON-Lines event logger for the E2E batch testing harness.

Per the prompt's ``<constraints>`` section, every wrapper script must emit a
structured JSON line per significant event to
``logs/e2e_{run_id}_{source}.jsonl`` (consistent with Valdo's
``audit.jsonl`` format).

Design
------
* One **line** per event, one event per call to :meth:`JsonlLogger.emit`.
* UTF-8, ``ensure_ascii=False`` so RHEL log shippers see human-readable text.
* ISO 8601 UTC timestamps with explicit ``Z`` suffix.
* Append-only: the log file is opened once in ``"a"`` mode and reused.
* Thread-safe through an internal ``threading.Lock`` so concurrent gates
  inside one wrapper run cannot interleave a single JSON record.
* Best-effort: a write failure NEVER raises out of :meth:`emit` because the
  harness must keep running and surface the failure via its own normal
  exit-code paths, not through logging.

Wire format
-----------
Each line is a self-contained JSON object with these reserved keys::

    {
      "ts":       "2026-05-13T20:30:00.123Z",   # ISO 8601 UTC
      "level":    "INFO" | "WARN" | "ERROR",
      "event":    "<short event name, snake_case>",
      "run_id":   "20260513_120000",
      "env":      "sit",
      "source":   "SRC_A",
      ...                                        # arbitrary event-specific keys
    }

Reserved keys are written first (stable order) for grep-friendliness, then
event-specific fields. None of the reserved keys may be overridden by a
caller's ``**fields`` — they are stamped from the :class:`JsonlLogger`
identity.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


_LEVELS = {"INFO", "WARN", "ERROR"}


def _utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 with millisecond precision + Z."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


@dataclass
class JsonlLogger:
    """Append-only JSON-Lines logger bound to one ``(run_id, env, source)``.

    Attributes:
        log_path: Destination file. Parent directory is created on first use.
        run_id: Run identifier stamped on every record.
        env: Environment name stamped on every record.
        source: Source identifier stamped on every record.
    """

    log_path: Path
    run_id: str
    env: str
    source: str
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _opened: bool = field(default=False, init=False)

    # ---------------------------------------------------------------- #
    # Factory
    # ---------------------------------------------------------------- #

    @classmethod
    def for_run(
        cls,
        log_root: Path,
        *,
        run_id: str,
        env: str,
        source: str,
    ) -> "JsonlLogger":
        """Build a logger writing to ``<log_root>/e2e_{run_id}_{source}.jsonl``."""
        log_root = Path(log_root)
        log_path = log_root / f"e2e_{run_id}_{source}.jsonl"
        return cls(log_path=log_path, run_id=run_id, env=env, source=source)

    # ---------------------------------------------------------------- #
    # Emit
    # ---------------------------------------------------------------- #

    def emit(self, event: str, level: str = "INFO", **fields: Any) -> None:
        """Write one structured event line.

        Args:
            event: Short snake_case identifier (e.g. ``"gate_started"``).
            level: ``"INFO"``, ``"WARN"``, or ``"ERROR"``. Unknown values are
                coerced to ``"INFO"`` so logging never breaks the run.
            **fields: Arbitrary event-specific data. Reserved keys (``ts``,
                ``level``, ``event``, ``run_id``, ``env``, ``source``) are
                silently dropped from ``fields`` to prevent identity spoofing.
        """
        if level not in _LEVELS:
            level = "INFO"
        for reserved in ("ts", "level", "event", "run_id", "env", "source"):
            fields.pop(reserved, None)

        record: Dict[str, Any] = {
            "ts": _utc_now_iso(),
            "level": level,
            "event": event,
            "run_id": self.run_id,
            "env": self.env,
            "source": self.source,
        }
        record.update(fields)

        try:
            line = json.dumps(record, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            # Fallback: drop the unserializable payload and log the failure.
            record = {
                "ts": _utc_now_iso(),
                "level": "ERROR",
                "event": "log_serialization_failed",
                "run_id": self.run_id,
                "env": self.env,
                "source": self.source,
                "original_event": event,
            }
            line = json.dumps(record, ensure_ascii=False)

        with self._lock:
            try:
                if not self._opened:
                    self.log_path.parent.mkdir(parents=True, exist_ok=True)
                    self._opened = True
                with self.log_path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError:
                # Logging must never crash the run. Swallow and continue.
                return

    # ---------------------------------------------------------------- #
    # Convenience shorthands
    # ---------------------------------------------------------------- #

    def info(self, event: str, **fields: Any) -> None:
        # Drop reserved keys up front so a caller that passes ``event=...``
        # as a kwarg does not collide with the positional ``event`` parameter.
        for reserved in ("event", "level"):
            fields.pop(reserved, None)
        self.emit(event, level="INFO", **fields)

    def warn(self, event: str, **fields: Any) -> None:
        for reserved in ("event", "level"):
            fields.pop(reserved, None)
        self.emit(event, level="WARN", **fields)

    def error(self, event: str, **fields: Any) -> None:
        for reserved in ("event", "level"):
            fields.pop(reserved, None)
        self.emit(event, level="ERROR", **fields)
