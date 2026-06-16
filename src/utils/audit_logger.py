"""Splunk-compatible, tamper-evident structured audit logger.

Emits one JSON object per line (JSONL) to a configurable log file and/or
stdout, designed for ingestion by Splunk Universal Forwarder with
``sourcetype=_json``.

Tamper-evidence (S13.5-1, #408)
-------------------------------
Every record carries a monotonic ``seq`` (genesis ``seq`` 0, ``prev_hash``
``None``) plus ``prev_hash``, ``hash`` and ``hash_alg`` fields forming a
hash chain::

    hash = MAC(canonical_payload_excluding_hash)

where ``canonical`` is ``json.dumps(record_without_hash, sort_keys=True,
separators=(",", ":"))``.  Because ``seq`` and ``prev_hash`` are part of the
record (and therefore part of the MAC input), the chain binds both record
order and lineage: reordering, mutating, or removing any record breaks the
chain.

When ``VALDO_AUDIT_HMAC_KEY`` is set (read via the secrets provider), the MAC
is **HMAC-SHA256** and ``hash_alg`` is ``"hmac-sha256"``.  When unset the
logger falls back to a plain **SHA-256** chain (``hash_alg`` ``"sha256"``) and
logs ONCE at WARNING that integrity is unkeyed (an attacker with write access
can recompute an unkeyed chain).

Failure handling
----------------
Unlike the previous implementation, :meth:`AuditLogger.emit` no longer
silently swallows audit-file write errors.  A failed write to the audit file
raises :class:`AuditWriteError` (fail-closed: if the security event cannot be
recorded, the calling operation should not silently proceed).  The optional
best-effort stdout/echo write never raises.

Environment variables:
    AUDIT_LOG_PATH: Path to the JSONL audit log file.
        Default: ``logs/audit.jsonl``.
    APP_ENVIRONMENT: Deployment environment tag included in every event.
        Default: ``DEV``.
    VALDO_AUDIT_HMAC_KEY: Optional HMAC key for keyed integrity.  When unset,
        falls back to an unkeyed SHA-256 chain.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import uuid
from dataclasses import dataclass
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

_HMAC_KEY_ENV = "VALDO_AUDIT_HMAC_KEY"
_ALG_HMAC = "hmac-sha256"
_ALG_SHA256 = "sha256"

# Fields excluded from the canonical payload when computing the record hash.
_HASH_FIELD = "hash"

# Module-level guard so the "unkeyed integrity" warning is logged only once.
_unkeyed_warning_logged = False

# Valid event types
EVENT_TYPES = frozenset(
    {
        "test_run_started",
        "test_run_completed",
        "file_uploaded",
        "file_cleanup",
        "auth_failure",
        "suite_step_completed",
        # S13.5-2 (#415) — auth-failure + config-mutation coverage.
        # ``auth_failure`` already existed (API-key path); these add the
        # config/mapping/rule/masking mutation event plus the MCP/LDAP
        # auth-flow events that were emitted ad hoc before this story so
        # they no longer trip the "unknown event type" warning.
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
)

# Recognised resource types for config_mutation events (S13.5-2). Kept as a
# set so callers can validate against it, but emit() does not reject unknown
# values — the audit record is informational, not a gate.
MUTATION_RESOURCE_TYPES = frozenset(
    {"mapping", "rules", "masking", "source_spec"}
)

# Recognised mutation actions.
MUTATION_ACTIONS = frozenset({"create", "update", "delete"})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AuditWriteError(RuntimeError):
    """Raised when a security-relevant audit event cannot be persisted.

    Fail-closed signal: the audit-file write failed, so the caller's
    security-relevant operation should not silently proceed.  Callers at a
    request/CLI boundary should translate this into a handled error response
    or non-zero exit, never a bare traceback to the client.
    """


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


def _canonical_payload(record: dict[str, Any]) -> bytes:
    """Return the deterministic canonical byte payload for hashing.

    The ``hash`` field is excluded; all other fields (including ``seq`` and
    ``prev_hash``) are serialised with sorted keys and compact separators so
    the encoding is reproducible by the verifier.

    Args:
        record: The full record dict (may or may not contain ``hash``).

    Returns:
        UTF-8 encoded canonical JSON bytes.
    """
    payload = {k: v for k, v in record.items() if k != _HASH_FIELD}
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")


def _compute_hash(record: dict[str, Any], *, key: Optional[str]) -> tuple[str, str]:
    """Compute the chain hash for a record.

    Args:
        record: The record dict (without a meaningful ``hash`` field).
        key: HMAC key.  When falsy, a plain SHA-256 digest is used.

    Returns:
        A ``(digest_hex, hash_alg)`` tuple.
    """
    payload = _canonical_payload(record)
    if key:
        digest = hmac.new(key.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        return digest, _ALG_HMAC
    return hashlib.sha256(payload).hexdigest(), _ALG_SHA256


def _recompute_for_verify(
    record: dict[str, Any], *, hash_alg: str, key: Optional[str]
) -> Optional[str]:
    """Recompute a record's hash during verification, honouring its alg.

    Args:
        record: The on-disk record dict.
        hash_alg: The ``hash_alg`` stored in the record.
        key: Caller-supplied verification key (HMAC).

    Returns:
        The recomputed hex digest, or ``None`` if the alg is unknown or a
        keyed record was given no key.
    """
    payload = _canonical_payload(record)
    if hash_alg == _ALG_HMAC:
        if not key:
            return None
        return hmac.new(key.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    if hash_alg == _ALG_SHA256:
        return hashlib.sha256(payload).hexdigest()
    return None


def _read_hmac_key() -> Optional[str]:
    """Read the audit HMAC key via the secrets provider.

    Returns:
        The configured key, or ``None`` when unset/empty.
    """
    try:
        from src.utils.secrets import get_secrets_provider

        value = get_secrets_provider().get_secret(_HMAC_KEY_ENV, default="")
    except Exception:  # pragma: no cover — provider misconfig; degrade safely
        value = os.getenv(_HMAC_KEY_ENV, "")
    return value or None


# ---------------------------------------------------------------------------
# AuditLogger
# ---------------------------------------------------------------------------


class AuditLogger:
    """Tamper-evident structured audit logger that writes JSONL events.

    Each call to :meth:`emit` produces a single JSON line containing at
    minimum: ``event``, ``timestamp``, ``run_id``, ``environment``,
    ``triggered_by``, plus the tamper-evidence fields ``seq``, ``prev_hash``,
    ``hash`` and ``hash_alg``.  Additional keyword arguments are merged into
    the event payload.

    The logger is a single-writer, append-only chain.  Across process
    restarts it reads the last line of the existing log to resume the chain
    from the correct ``seq``/``prev_hash``.

    Args:
        log_path: Override for the JSONL file path.  When *None* the value
            of ``AUDIT_LOG_PATH`` is used (falling back to
            ``logs/audit.jsonl``).
        environment: Override for the environment tag.  When *None* the
            value of ``APP_ENVIRONMENT`` is used (falling back to ``DEV``).
        write_to_stdout: Also write each event to stdout (best-effort).
            Defaults to False.

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
            environment or os.getenv("APP_ENVIRONMENT", _DEFAULT_ENVIRONMENT)
        )
        self._write_to_stdout = write_to_stdout
        self._run_id = uuid.uuid4().hex
        # Chain state, lazily initialised from disk on first emit.
        self._last_seq: Optional[int] = None
        self._last_hash: Optional[str] = None
        self._chain_loaded = False

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
    # Chain bootstrap
    # ------------------------------------------------------------------

    def _load_chain_state(self) -> None:
        """Read the last record (if any) to resume the seq/hash chain.

        Cached after the first call so subsequent emits avoid re-reading the
        file.  A missing or empty file means the chain starts at genesis.
        """
        if self._chain_loaded:
            return
        self._chain_loaded = True
        try:
            if not self._log_path.exists():
                return
            last_line = ""
            with open(self._log_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    if stripped:
                        last_line = stripped
            if not last_line:
                return
            last = json.loads(last_line)
            self._last_seq = int(last["seq"])
            self._last_hash = last.get(_HASH_FIELD)
        except (OSError, ValueError, KeyError):
            # Corrupt/unreadable tail: do not crash on bootstrap.  Verification
            # via verify_audit_log() is the authoritative integrity check.
            logger.warning(
                "Could not load audit chain state from %s; "
                "starting a fresh chain segment.",
                self._log_path,
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def emit(self, event_type: str, **kwargs: Any) -> dict[str, Any]:
        """Write a tamper-evident structured audit event.

        Args:
            event_type: One of the recognised event type strings (e.g.
                ``test_run_started``).  Unknown types are logged with a
                warning but still emitted.
            **kwargs: Arbitrary additional fields merged into the event
                payload.  Common keys include ``triggered_by``, ``file``,
                ``file_hash``, ``mapping``, ``mapping_hash``, ``result``,
                ``error``.

        Returns:
            The complete event dict that was written, including ``seq``,
            ``prev_hash``, ``hash`` and ``hash_alg``.

        Raises:
            AuditWriteError: If the audit-file write fails.  This is
                intentional fail-closed behaviour and must be handled at the
                request/CLI boundary (not surfaced as a bare traceback).
        """
        if event_type not in EVENT_TYPES:
            logger.warning("Unknown audit event type: %s", event_type)

        self._load_chain_state()

        seq = 0 if self._last_seq is None else self._last_seq + 1
        prev_hash = self._last_hash

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

        # Tamper-evidence fields participate in the canonical payload.
        event["seq"] = seq
        event["prev_hash"] = prev_hash

        key = _read_hmac_key()
        self._warn_if_unkeyed(key)
        # hash_alg must be part of the canonical payload so the verifier
        # (which sees it on disk) recomputes an identical MAC.
        event["hash_alg"] = _ALG_HMAC if key else _ALG_SHA256
        digest, _ = _compute_hash(event, key=key)
        event[_HASH_FIELD] = digest

        line = json.dumps(event, default=str)

        # Write to file — fail-closed: a failure here raises AuditWriteError.
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as exc:
            logger.error(
                "Failed to write audit event to %s: %s", self._log_path, exc
            )
            raise AuditWriteError(
                f"Failed to write audit event to {self._log_path}"
            ) from exc

        # Persist chain state only after a successful write.
        self._last_seq = seq
        self._last_hash = digest

        # Optionally write to stdout — best-effort, must never raise.
        if self._write_to_stdout:
            try:
                print(line)  # noqa: T201
            except OSError:
                logger.warning("Best-effort audit stdout write failed; ignoring.")

        return event

    @staticmethod
    def _warn_if_unkeyed(key: Optional[str]) -> None:
        """Log a one-time WARNING when running with an unkeyed hash chain."""
        global _unkeyed_warning_logged  # noqa: PLW0603
        if not key and not _unkeyed_warning_logged:
            _unkeyed_warning_logged = True
            logger.warning(
                "%s is not set: audit integrity uses an UNKEYED SHA-256 chain. "
                "An attacker with write access can recompute the chain. Set %s "
                "for HMAC-keyed tamper-evidence.",
                _HMAC_KEY_ENV,
                _HMAC_KEY_ENV,
            )


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


@dataclass
class AuditVerificationResult:
    """Outcome of verifying an audit log's integrity chain.

    Attributes:
        ok: True when every record verified and the chain is intact.
        records_checked: Number of records read and verified.
        error: Human-readable description of the first problem, or None.
        bad_seq: The ``seq`` at which verification first failed, or None.
    """

    ok: bool
    records_checked: int
    error: Optional[str] = None
    bad_seq: Optional[int] = None


def verify_audit_log(
    path: str | Path, *, key: Optional[str] = None
) -> AuditVerificationResult:
    """Verify the integrity chain of a JSONL audit log.

    Walks the log line by line and checks, for each record:

    * ``seq`` starts at 0 and increments by exactly 1 (gap detection);
    * ``prev_hash`` matches the previous record's ``hash`` (None for genesis);
    * the stored ``hash`` recomputes from the canonical payload using the
      record's own ``hash_alg`` (HMAC records need *key*).

    Reports the FIRST broken link via ``bad_seq``.

    Args:
        path: Path to the JSONL audit log.
        key: HMAC key used to verify ``hmac-sha256`` records.  Ignored for
            ``sha256`` records.  When omitted but the env key is set, the env
            key is used.

    Returns:
        An :class:`AuditVerificationResult`.
    """
    log_path = Path(path)
    if key is None:
        key = _read_hmac_key()

    try:
        raw = log_path.read_text(encoding="utf-8")
    except OSError as exc:
        return AuditVerificationResult(
            ok=False, records_checked=0, error=f"Cannot read {log_path}: {exc}"
        )

    expected_seq = 0
    prev_hash: Optional[str] = None
    checked = 0

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        try:
            record = json.loads(stripped)
        except json.JSONDecodeError as exc:
            return AuditVerificationResult(
                ok=False,
                records_checked=checked,
                error=f"Malformed JSON at expected seq {expected_seq}: {exc}",
                bad_seq=expected_seq,
            )

        seq = record.get("seq")
        if seq != expected_seq:
            return AuditVerificationResult(
                ok=False,
                records_checked=checked,
                error=f"Sequence gap: expected {expected_seq}, found {seq}",
                bad_seq=expected_seq,
            )

        if record.get("prev_hash") != prev_hash:
            return AuditVerificationResult(
                ok=False,
                records_checked=checked,
                error=f"prev_hash mismatch at seq {seq} (chain broken)",
                bad_seq=seq,
            )

        stored_hash = record.get(_HASH_FIELD)
        hash_alg = record.get("hash_alg", _ALG_SHA256)
        recomputed = _recompute_for_verify(record, hash_alg=hash_alg, key=key)
        if recomputed is None:
            return AuditVerificationResult(
                ok=False,
                records_checked=checked,
                error=(
                    f"Cannot verify seq {seq}: alg {hash_alg!r} unknown or "
                    f"HMAC key missing"
                ),
                bad_seq=seq,
            )
        if not hmac.compare_digest(str(stored_hash), recomputed):
            return AuditVerificationResult(
                ok=False,
                records_checked=checked,
                error=f"Hash mismatch at seq {seq} (record tampered)",
                bad_seq=seq,
            )

        prev_hash = stored_hash
        expected_seq += 1
        checked += 1

    return AuditVerificationResult(ok=True, records_checked=checked)


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


# ---------------------------------------------------------------------------
# Convenience emitters (S13.5-2, #415)
# ---------------------------------------------------------------------------


def audit_mutation(
    *,
    resource_type: str,
    resource_id: str,
    action: str,
    actor: str,
    triggered_by: str = "api",
    outcome: str = "success",
    correlation_id: Optional[str] = None,
    **extra: Any,
) -> dict[str, Any]:
    """Emit a ``config_mutation`` audit event for a config/mapping/rule/masking change.

    This is the single cohesive sink for the SOX-relevant mutation events
    (S13.5-2, #415) so the many operator-facing entry points
    (mapping/rules/masking upload, source-spec promotion) do not each
    hand-roll an :meth:`AuditLogger.emit` call with drifting field names.

    The record joins the tamper-evident chain via the shared
    :func:`get_audit_logger` instance (S13.5-1), so it carries ``seq``,
    ``prev_hash``, ``hash`` and ``hash_alg`` like every other event.

    Args:
        resource_type: What was changed — one of
            :data:`MUTATION_RESOURCE_TYPES` (``mapping`` | ``rules`` |
            ``masking`` | ``source_spec``). Not enforced; an unrecognised
            value is still recorded.
        resource_id: The resource's identifier/name (e.g. the mapping id).
        action: The mutation verb — one of :data:`MUTATION_ACTIONS`
            (``create`` | ``update`` | ``delete``).
        actor: The authenticated principal that performed the change
            (e.g. ``apikey:abc123`` or an LDAP user/DN). NEVER pass a raw
            secret/key value here.
        triggered_by: Surface that drove the change (``api`` | ``mcp`` |
            ``cli``). Defaults to ``api``.
        outcome: ``success`` (default) or ``failure``.
        correlation_id: Optional request/run correlation id for lineage.
        **extra: Additional non-secret fields merged into the event.

    Returns:
        The complete event dict that was written.

    Raises:
        AuditWriteError: If the audit-file write fails (fail-closed —
            handle at the request boundary, do not surface a bare
            traceback to the client).
    """
    payload: dict[str, Any] = {
        "resource_type": resource_type,
        "resource_id": resource_id,
        "action": action,
        "actor": actor,
        "triggered_by": triggered_by,
        "outcome": outcome,
    }
    if correlation_id is not None:
        payload["correlation_id"] = correlation_id
    payload.update(extra)
    return get_audit_logger().emit("config_mutation", **payload)


def audit_auth_failure(
    *,
    auth_kind: str,
    reason: str,
    client_ip: str = "unknown",
    principal: Optional[str] = None,
    triggered_by: str = "api",
    **extra: Any,
) -> dict[str, Any]:
    """Emit an ``auth_failure`` audit event (``outcome="failure"``).

    Cohesive sink for the auth-failure coverage (S13.5-2, #415). The
    secret/token value is NEVER an argument — callers pass the attempted
    *principal* (e.g. a key-id suffix or username) if known, never the raw
    credential.

    Fail-closed (S13.5-1): a genuine audit-write outage raises
    :class:`AuditWriteError`. Callers MUST emit the failure event *before*
    raising the 401/403 so the security event is on the chain, but must
    still surface the original auth error to the client (see the API-key
    and MCP auth sites for the documented ordering).

    Args:
        auth_kind: How the caller tried to authenticate (``api_key`` |
            ``mcp_api_key`` | ``mcp_bearer`` | ``ldap`` | ``session``).
        reason: Short machine reason (``missing_api_key`` |
            ``invalid_api_key`` | ...). Never the credential value.
        client_ip: Proxy-corrected client IP (S9-1). Defaults to
            ``"unknown"`` when the request has no client.
        principal: Attempted identity if known (username, key-id suffix);
            ``None`` when unknown. Never a secret.
        triggered_by: Surface (``api`` | ``mcp`` | ``web_ui``).
        **extra: Additional non-secret fields merged into the event.

    Returns:
        The complete event dict that was written.

    Raises:
        AuditWriteError: If the audit-file write fails.
    """
    payload: dict[str, Any] = {
        "outcome": "failure",
        "auth_kind": auth_kind,
        "reason": reason,
        "client_ip": client_ip,
        "triggered_by": triggered_by,
    }
    if principal is not None:
        payload["principal"] = principal
    payload.update(extra)
    return get_audit_logger().emit("auth_failure", **payload)
