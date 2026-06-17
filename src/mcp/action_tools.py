"""Action MCP tool implementations for Valdo (EF-S4).

This module wires the first set of *mutating* MCP tools onto Valdo's
existing service layer. Three tools are exposed:

* ``validate_file`` — kicks off a validation run for ``(source, file_path)``
  and returns a freshly-minted ``run_id`` plus ``started_at`` ISO-8601 UTC
  timestamp.
* ``get_run_status`` — returns the lifecycle status of a run started via
  ``validate_file`` (or, for older runs, falls back to the run-history
  backend used by EF-S2's ``list_recent_runs``).
* ``get_violations`` — pages through the violations produced by a run,
  with an optional severity filter.

All three are thin adapters around
:func:`src.services.validate_service.run_validate_service`. No validation
logic, run-history schema, or report shape is duplicated here — the rest
of the codebase stays the canonical source of truth.

Synchronous-execution note (revisited in a follow-up MR):
    The existing ``run_validate_service`` is a blocking call. Rather than
    spin up a thread / process pool inside the MCP layer (which would
    require careful lifecycle management against the FastAPI lifespan and
    is out of scope for EF-S4), ``validate_file`` runs the validation
    synchronously and stores the result in an in-process registry keyed
    by the freshly-minted ``run_id``. The agent-visible contract is
    unchanged — the agent receives a ``run_id`` and polls
    ``get_run_status`` / ``get_violations``. The MCP transport itself is
    fast (sub-second on small files); larger files will block the MCP
    request for the duration of validation. EF-S5 (or a follow-up) will
    convert this to a true background worker once the worker-pool story
    lands.

Run registry (S6-1, #386):
    Runs are tracked by the adapter in
    :mod:`src.mcp.run_registry`. The factory
    :func:`~src.mcp.run_registry.make_run_registry` picks a database
    backend (``APP_MCP_RUN_REGISTRY`` table) when the shared SQLAlchemy
    engine is reachable, else falls back to a process-local in-memory
    store with a logged WARNING. Database-backed records survive a
    FastAPI restart; the in-memory fallback does not. EF-S2's
    ``run_history_service`` fallback for older runs in
    :func:`get_run_status_payload` is unchanged.

Idempotency on ``validate_file``:
    A second call with the same ``(source, file_path)`` while the first
    run is still ``queued`` or ``running`` returns the *existing*
    ``run_id`` rather than starting a duplicate run. Once the first run
    reaches ``completed`` or ``failed``, a subsequent call starts a new
    one. This matches the EF-S4 AC and protects against an agent
    accidentally double-triggering a long-running validation.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from mcp.server.fastmcp.exceptions import ToolError

from src.mcp.run_registry import RunRecord, RunRegistry, make_run_registry
from src.mcp.tools import _SOURCES_DIR, _discover_source_names, _relpath

__all__ = [
    "validate_file_payload",
    "get_run_status_payload",
    "get_violations_payload",
    "VALIDATE_FILE_DESCRIPTION",
    "GET_RUN_STATUS_DESCRIPTION",
    "GET_VIOLATIONS_DESCRIPTION",
    "MAX_PAGE_SIZE",
    "DEFAULT_PAGE_SIZE",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default and ceiling page sizes for ``get_violations``. The default mirrors
# the contract carried over from the existing reports renderer, and the
# ceiling protects the MCP transport from a runaway agent asking for tens
# of thousands of violations in a single JSON-RPC frame.
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

# Severity values recognised by ``get_violations``. The engine itself uses
# additional buckets (``critical``, ``info``) that we map into the
# canonical three for the MCP surface so the agent does not have to know
# the internal taxonomy. See :func:`_canonical_severity`.
_VALID_SEVERITIES = {"error", "warning", "info"}

# Run-status enum values. Kept module-level so tests can pin them without
# round-tripping through the JSON-RPC layer.
_STATUS_QUEUED = "queued"
_STATUS_RUNNING = "running"
_STATUS_COMPLETED = "completed"
_STATUS_FAILED = "failed"
_TERMINAL_STATUSES = frozenset({_STATUS_COMPLETED, _STATUS_FAILED})

# Background-job feature gate (S9-5, ADR 0021). When truthy, ``validate_file``
# may ENQUEUE the run (write ``queued`` and return) instead of driving the
# engine inline; the ``valdo run-job-worker`` process picks the row up.
#
# Default flipped ON in S10-2 (#397): the worker runtime is now production-real
# and the systemd unit ships in the RPM. To avoid stranding a run where no
# worker is deployed (e.g. local dev), the enqueue path is gated on a *live
# worker* check (see :func:`validate_file_payload`): if async is enabled but no
# worker has heartbeated recently, ``validate_file`` falls back to a synchronous
# inline run so the call always completes. Set the flag to a falsey value to
# force the legacy always-inline behaviour.
_ASYNC_VALIDATE_FLAG = "VALDO_MCP_ASYNC_VALIDATE"

# Worker-liveness window (S10-2). ``validate_file`` treats a worker as "live"
# (and therefore takes the fast enqueue path) when it has heartbeated within
# this many seconds. The default (60s) is comfortably larger than the worker's
# default poll interval (2s) so a busy/looping worker always stays "live"
# between iterations. Overridable via ``VALDO_MCP_WORKER_LIVENESS_SECONDS``.
_WORKER_LIVENESS_FLAG = "VALDO_MCP_WORKER_LIVENESS_SECONDS"
_DEFAULT_WORKER_LIVENESS_SECONDS = 60.0

# Truthy/falsey tokens for parsing boolean env flags.
_TRUTHY = {"1", "true", "yes", "on"}
_FALSEY = {"0", "false", "no", "off"}


def _async_validate_enabled() -> bool:
    """Return whether ``validate_file`` should prefer the enqueue path.

    Reads :data:`_ASYNC_VALIDATE_FLAG` at call time (not import time) so a
    deployment / test can toggle it without reimporting the module.

    Default flipped ON in S10-2 (#397): unset is treated as enabled. Falsey
    values (``0``, ``false``, ``no``, ``off``) force the legacy always-inline
    path; any other explicit value is treated as enabled. Even when enabled,
    the enqueue path only runs when a live worker is present — see
    :func:`validate_file_payload` — so an unset flag with no worker still
    completes synchronously.

    Returns:
        ``True`` when the async/enqueue path is enabled, else ``False``.
    """
    raw = os.getenv(_ASYNC_VALIDATE_FLAG)
    if raw is None or raw.strip() == "":
        return True  # S10-2: ON by default.
    return raw.strip().lower() not in _FALSEY


def _worker_liveness_seconds() -> float:
    """Return the worker-liveness window in seconds (S10-2).

    Reads :data:`_WORKER_LIVENESS_FLAG` at call time. Falls back to
    :data:`_DEFAULT_WORKER_LIVENESS_SECONDS` when unset or unparseable.

    Returns:
        The liveness window in seconds.
    """
    raw = os.getenv(_WORKER_LIVENESS_FLAG)
    if raw is None or raw.strip() == "":
        return _DEFAULT_WORKER_LIVENESS_SECONDS
    try:
        value = float(raw.strip())
    except ValueError:
        logger.warning(
            "%s=%r is not a number; using default %.0fs",
            _WORKER_LIVENESS_FLAG,
            raw,
            _DEFAULT_WORKER_LIVENESS_SECONDS,
        )
        return _DEFAULT_WORKER_LIVENESS_SECONDS
    return value if value > 0 else _DEFAULT_WORKER_LIVENESS_SECONDS


# ---------------------------------------------------------------------------
# Persistent run registry (S6-1, #386)
# ---------------------------------------------------------------------------
#
# EF-S4 used a process-local dict here. S6-1 replaces that with the
# :class:`~src.mcp.run_registry.RunRegistry` adapter so runs survive a
# FastAPI restart. The factory picks a database backend when one is
# reachable, else falls back to an in-memory dict (with a logged
# WARNING) so the server never refuses to boot — see
# ``src/mcp/run_registry.py`` for the resolution order.
#
# The registry is constructed lazily on first access so test fixtures
# can monkeypatch the environment (``DB_ADAPTER``, ``ORACLE_DSN``)
# before the first call. ``_reset_runs_for_tests`` drops the cached
# instance so each test gets a fresh registry.

# Backwards-compat alias — the legacy ``_RunRecord`` class is replaced
# by :class:`~src.mcp.run_registry.RunRecord`. The slots-less dataclass
# is API-compatible for every attribute access action_tools performed.
_RunRecord = RunRecord

# Lock guards lazy construction of the singleton registry only.
# Per-record concurrency is handled inside each backend implementation.
_REGISTRY_LOCK = threading.Lock()
_REGISTRY: Optional[RunRegistry] = None


def _get_registry() -> RunRegistry:
    """Return the singleton :class:`RunRegistry`, constructing on first call.

    Constructing the registry on first call (rather than at import time)
    lets test fixtures monkeypatch env vars before the database probe
    runs. The singleton is held in :data:`_REGISTRY`; the registry
    factory itself handles the database-vs-in-memory selection.

    Returns:
        The active :class:`RunRegistry` for this process.
    """
    global _REGISTRY
    if _REGISTRY is None:
        with _REGISTRY_LOCK:
            if _REGISTRY is None:
                _REGISTRY = make_run_registry()
    return _REGISTRY


def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string ending in ``Z``.

    Kept here (rather than imported) so tests can monkeypatch it
    deterministically without touching ``datetime.datetime.now``
    globally.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _existing_run(source: str, file_path: str) -> Optional[RunRecord]:
    """Return an in-flight run for *(source, file_path)*, else ``None``.

    Two runs are considered "the same" when their source and file_path
    match exactly. The file_type slot is intentionally not considered —
    re-triggering the same physical file with a different file_type tag
    would still be a duplicate validation and we should short-circuit it.

    Args:
        source: Canonical source name.
        file_path: File path as supplied by the caller (no normalisation).

    Returns:
        The existing :class:`RunRecord` when one is in a non-terminal
        state, else ``None``.
    """
    return _get_registry().find_inflight(source, file_path)


# ---------------------------------------------------------------------------
# Source / file-type resolution
# ---------------------------------------------------------------------------


def _load_source_overlay(source: str) -> Dict[str, Any]:
    """Read and parse the source overlay YAML for *source*.

    Args:
        source: Canonical source name (e.g. ``"SHAW"``). Must be a plain
            identifier — path traversal payloads are rejected here so the
            MCP surface stays sandboxed even if a future caller bypasses
            EF-S2's ``get_source_spec`` guard.

    Returns:
        Parsed YAML dict.

    Raises:
        ToolError: When the overlay file is missing or unparseable.
    """
    if not source or any(ch in source for ch in "/\\.\x00"):
        raise ToolError(
            f"Invalid source name {source!r}: must be a plain identifier "
            "(no path separators, no dots, no NUL)."
        )

    overlay = _SOURCES_DIR / f"{source}.yml"
    if not overlay.is_file():
        raise ToolError(
            f"Unknown source {source!r}: no overlay found at "
            f"{_relpath(overlay)}. Available sources: "
            f"{_discover_source_names() or '<none>'}"
        )

    try:
        with overlay.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ToolError(
            f"Failed to parse source overlay {_relpath(overlay)}: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ToolError(
            f"Source overlay {_relpath(overlay)} did not parse to a mapping"
        )
    return data


def _resolve_artefacts(
    source: str,
    file_path: str,
    file_type: Optional[str],
) -> Dict[str, Optional[str]]:
    """Return ``{mapping, rules, file_type}`` for the validation call.

    When *file_type* is provided we look it up against the source overlay's
    ``output_files[]`` (then ``input_files[]``) list by exact match. When
    it is omitted we attempt to infer it from the file basename against
    each entry's ``glob`` — a basic ``fnmatch`` rather than full shell
    glob semantics, because the values in the overlay are always plain
    wildcards (``"atoctran_shaw_*.txt"``).

    Args:
        source: Canonical source name.
        file_path: File path as supplied by the caller.
        file_type: Optional file-type tag from the overlay.

    Returns:
        Dict with ``mapping`` (repo-relative or None when the overlay's
        entry has no mapping pinned), ``rules`` (same), and the resolved
        ``file_type`` (uppercase). When inference fails the
        ``file_type``-derived keys may be ``None`` but the call still
        proceeds — ``run_validate_service`` accepts both.

    Raises:
        ToolError: When the source is unknown.
    """
    overlay = _load_source_overlay(source)

    entries: List[Dict[str, Any]] = []
    for key in ("output_files", "input_files"):
        for entry in overlay.get(key) or []:
            if isinstance(entry, dict):
                entries.append(entry)

    resolved_entry: Optional[Dict[str, Any]] = None
    resolved_file_type: Optional[str] = None

    if file_type:
        wanted = file_type.upper()
        for entry in entries:
            if str(entry.get("file_type") or "").upper() == wanted:
                resolved_entry = entry
                resolved_file_type = wanted
                break
    else:
        # Best-effort glob-based inference. The overlay uses simple
        # wildcards so ``fnmatch`` covers every case we ship today.
        from fnmatch import fnmatch
        basename = Path(file_path).name
        for entry in entries:
            pattern = entry.get("glob")
            if pattern and fnmatch(basename, pattern):
                resolved_entry = entry
                resolved_file_type = str(entry.get("file_type") or "").upper() or None
                break

    mapping = (resolved_entry or {}).get("mapping") or None
    rules = (resolved_entry or {}).get("rules") or None
    # The overlay sometimes pins ``rules: ""`` explicitly for multi-record
    # umbrellas (rules are inside the umbrella YAML). Normalise to None so
    # the service call does not see an empty string.
    if isinstance(rules, str) and not rules.strip():
        rules = None
    if isinstance(mapping, str) and not mapping.strip():
        mapping = None

    return {
        "mapping": mapping,
        "rules": rules,
        "file_type": resolved_file_type,
    }


# ---------------------------------------------------------------------------
# Violation canonicalisation
# ---------------------------------------------------------------------------


def _canonical_severity(raw: Any) -> str:
    """Return the canonical MCP severity for an engine severity value.

    The engine uses ``error``, ``warning``, ``info``, plus a legacy
    ``critical`` bucket on a handful of format checks. ``critical`` is
    surfaced as ``error`` so agents only ever see the three canonical
    buckets advertised in the tool description.

    Args:
        raw: Severity value from a violation dict; expected to be a
            string but defended against ``None``.

    Returns:
        One of ``"error"``, ``"warning"``, or ``"info"``. Unknown values
        fall through to ``"error"`` because surfacing an unknown bucket
        silently as ``info`` could hide real failures from an agent.
    """
    if not isinstance(raw, str):
        return "error"
    lowered = raw.lower()
    if lowered == "critical":
        return "error"
    if lowered in _VALID_SEVERITIES:
        return lowered
    return "error"


def _canonicalise_violations(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten the engine's errors/warnings/info into the MCP shape.

    The engine returns three parallel lists (``errors``, ``warnings``,
    ``info``) and a separate ``business_rules.violations`` slot. We merge
    them in the order ``errors -> warnings -> info`` so that — when an
    agent pages without a severity filter — the most actionable rows
    surface first.

    Each output entry has the shape::

        {
            "rule_id": str | None,
            "field": str | None,
            "severity": "error" | "warning" | "info",
            "message": str,
            "record_index": int | None,
            "actual_value": Any | None,
        }

    The engine's row counter is 1-based; we surface it verbatim under
    ``record_index`` because the rest of Valdo's reporting (HTML / CSV)
    uses the same convention. Translating to 0-based here would diverge
    from the canonical report and confuse agents that cross-reference
    the two.

    Args:
        result: Raw validation result dict from ``run_validate_service``.

    Returns:
        Flat list of canonicalised violations.
    """
    violations: List[Dict[str, Any]] = []
    for bucket_name in ("errors", "warnings", "info"):
        bucket = result.get(bucket_name) or []
        if not isinstance(bucket, list):
            continue
        for raw in bucket:
            if not isinstance(raw, dict):
                continue
            # rule_id is only present on rule-engine violations; we
            # default to the engine's ``code`` slot (used for the
            # built-in checks like ``FW_LEN_001``) so the agent always
            # has a stable identifier to quote back.
            rule_id = raw.get("rule_id") or raw.get("code") or raw.get("rule_name")
            violations.append(
                {
                    "rule_id": rule_id,
                    "field": raw.get("field"),
                    "severity": _canonical_severity(raw.get("severity")),
                    "message": str(raw.get("message") or ""),
                    "record_index": raw.get("row"),
                    "actual_value": raw.get("value")
                    if "value" in raw
                    else raw.get("actual_value"),
                }
            )
    return violations


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def _run_validate_synchronously(
    record: RunRecord,
    artefacts: Dict[str, Optional[str]],
    *,
    report_info: Optional[Dict[str, str]] = None,
    suppress_pii: bool = True,
) -> None:
    """Drive the validation service for *record* and update its state.

    Runs synchronously — see the module docstring for the rationale and
    the EF-S5 follow-up plan. Exceptions are caught and folded into the
    ``failed`` status; we never let an underlying service error propagate
    out of the MCP tool layer.

    Each status transition (``queued`` → ``running`` → terminal) is
    persisted to the registry so a concurrent ``get_run_status`` poll
    from a different worker sees the latest state. This is the durability
    contract S6-1 introduces — EF-S4's in-process dict was implicitly
    visible to every coroutine in the same worker; the persistent
    backend must be written through on every transition.

    Args:
        record: The freshly-constructed run record (status will be
            mutated from ``queued`` -> ``running`` -> terminal).
        artefacts: Mapping/rules paths resolved from the source overlay.
        report_info: When supplied (``include_report=True``, S23-4 / ADR
            0023), the validation also renders an HTML report into this
            dict's ``report_path``. When the run succeeds, the dict is
            mutated in place to carry ``report_uri`` / ``report_url`` /
            ``report_path``; when it fails, no report is produced.
        suppress_pii: Forwarded to :class:`ValidationReporter` when
            rendering a report (default ``True``, ADR 0023 §4).
    """
    # Import lazily so a missing optional dependency only blows up the
    # one tool call that needs it, not every MCP request.
    from src.services.validate_service import run_validate_service

    registry = _get_registry()

    record.status = _STATUS_RUNNING
    registry.put(record)

    try:
        result = run_validate_service(
            file=record.file_path,
            mapping=artefacts.get("mapping"),
            rules=artefacts.get("rules"),
            output=None,
            detailed=True,
        )
    except Exception as exc:  # noqa: BLE001 — surface any failure as ``failed``
        logger.exception("validate_file run %s failed", record.run_id)
        record.status = _STATUS_FAILED
        record.finished_at = _utcnow_iso()
        record.error_message = str(exc)
        registry.put(record)
        return

    record.violations = _canonicalise_violations(result if isinstance(result, dict) else {})
    record.status = _STATUS_COMPLETED
    record.finished_at = _utcnow_iso()
    registry.put(record)

    # S23-4 (#446) / ADR 0023: opt-in HTML report. We render here — in the
    # synchronous path, after a successful run — using the run_id the tool
    # already minted, so the report file is named ``<run_id>.html`` and the
    # ``report://<run_id>`` resolver finds it by a direct lookup.
    if report_info is not None and isinstance(result, dict):
        from src.reports.renderers.validation_renderer import ValidationReporter

        ValidationReporter().generate(
            result,
            report_info["report_path"],
            suppress_pii=suppress_pii,
        )
        report_info["rendered"] = "1"


def validate_file_payload(
    source: str,
    file_path: str,
    file_type: Optional[str] = None,
    include_report: bool = False,
    suppress_pii: bool = True,
) -> Dict[str, Any]:
    """Kick off a validation run and return its identifier.

    Args:
        source: Canonical source name (e.g. ``"SHAW"``). MUST already be
            declared in ``config/e2e/sources/<source>.yml``.
        file_path: Path to the data file to validate. Relative paths are
            resolved against the current working directory of the
            FastAPI process (the same convention the CLI uses).
        file_type: Optional file-type tag matching one of the
            ``output_files[].file_type`` / ``input_files[].file_type``
            entries in the source overlay. When omitted, the file_type
            is inferred by matching the basename against each entry's
            ``glob`` pattern.
        include_report: Opt-in HTML report (S23-4, ADR 0023). When
            ``False`` (default) behaviour is unchanged — JSON only, no
            HTML rendered. When ``True`` the run executes synchronously
            (so the report is ready before this call returns), renders a
            :class:`ValidationReporter` HTML report into
            ``<reports_dir>/<run_id>.html``, and the response also carries
            ``report_uri`` / ``report_url`` / ``report_path``.
        suppress_pii: Forwarded to the renderer when ``include_report`` is
            set (default ``True``, ADR 0023 §4). Ignored otherwise.

    Returns:
        ``{"run_id": "<uuid>", "started_at": "ISO-8601 UTC"}``. Poll
        :func:`get_run_status_payload` (or the ``get_run_status`` MCP
        tool) to discover when the run completes; then page through
        :func:`get_violations_payload` for the results. When
        ``include_report=True`` and the run succeeded, three additional
        keys are present: ``report_uri`` (``report://<run_id>``),
        ``report_url`` (``/reports/<run_id>.html``), and ``report_path``
        (absolute server path).

    Raises:
        ToolError: When the source is unknown, the file does not exist,
            or the source overlay cannot be parsed. An in-flight run for
            the same ``(source, file_path)`` is NOT an error; the
            existing ``run_id`` is returned instead.
    """
    if not file_path or not isinstance(file_path, str):
        raise ToolError("file_path is required and must be a string")

    if not Path(file_path).is_file():
        raise ToolError(f"File not found: {file_path!r}")

    # Resolve artefacts first so we surface "unknown source" before
    # touching the run registry. ``_resolve_artefacts`` raises ToolError
    # for unknown sources.
    artefacts = _resolve_artefacts(source, file_path, file_type)

    # Idempotency guard: return the existing run if one is still in
    # flight for the same (source, file_path).
    existing = _existing_run(source, file_path)
    if existing is not None:
        return {
            "run_id": existing.run_id,
            "started_at": existing.started_at,
        }

    run_id = uuid.uuid4().hex
    registry = _get_registry()
    record = RunRecord(
        run_id=run_id,
        source=source,
        file_path=file_path,
        file_type=artefacts.get("file_type") or (file_type.upper() if file_type else None),
        status=_STATUS_QUEUED,
        started_at=_utcnow_iso(),
    )
    # Always write the durable ``queued`` row first so a polling agent (and the
    # worker) can see the run regardless of which execution path we take.
    registry.put(record)

    # S23-4 (#446) / ADR 0023: when an HTML report is requested we render it
    # into ``<reports_dir>/<run_id>.html``. Build the target path up front so
    # the synchronous run can render straight into it.
    report_info: Optional[Dict[str, str]] = None
    if include_report:
        from src.mcp.resources.reports import (
            reports_dir,
            report_uri_for,
            report_url_for,
        )

        report_info = {
            "report_path": str(reports_dir() / f"{run_id}.html"),
        }

    # ADR 0021 / S10-2 liveness-aware dispatch:
    #
    # * async enabled (the default) AND a worker has heartbeated within the
    #   liveness window -> ENQUEUE only. The durable ``queued`` row is the work
    #   item; ``valdo run-job-worker`` claims and runs it out of band, so this
    #   call returns within ~100ms with no engine work.
    # * async disabled, OR async enabled but NO live worker is draining the
    #   queue -> run the validation INLINE so the call always completes. This is
    #   what makes flipping the default ON safe in environments (local dev) with
    #   no worker deployed — nothing is ever stranded in ``queued``.
    #
    # When include_report=True we force the INLINE path so the report is
    # produced before this call returns (the enqueue path defers rendering to
    # the worker, which is out of scope for S23-4 — see ADR 0023 §5).
    enqueue = (
        not include_report
        and _async_validate_enabled()
        and registry.has_live_worker(_worker_liveness_seconds())
    )
    if not enqueue:
        _run_validate_synchronously(
            record,
            artefacts,
            report_info=report_info,
            suppress_pii=suppress_pii,
        )

    payload: Dict[str, Any] = {
        "run_id": run_id,
        "started_at": record.started_at,
    }
    # Only surface the report fields when rendering actually happened (a failed
    # run produces no report) — absent, not null-padded (ADR 0023 §2).
    if report_info is not None and report_info.get("rendered"):
        payload["report_uri"] = report_uri_for(run_id)
        payload["report_url"] = report_url_for(run_id)
        payload["report_path"] = report_info["report_path"]

    return payload


def _lookup_run(run_id: str) -> RunRecord:
    """Return the registry record for *run_id* or raise ToolError.

    With S6-1's persistent backend, records survive a FastAPI restart
    when the database backend is reachable. The legacy "previous
    process lifetime" caveat now only applies when the registry has
    fallen back to in-memory mode (logged at boot).

    Args:
        run_id: Identifier returned by :func:`validate_file_payload`.

    Returns:
        The matching :class:`RunRecord`.

    Raises:
        ToolError: When no record exists in the registry.
    """
    if not isinstance(run_id, str) or not run_id:
        raise ToolError("run_id is required and must be a non-empty string")
    record = _get_registry().get(run_id)
    if record is None:
        raise ToolError(
            f"Unknown run_id {run_id!r}. The run may have been started in a "
            "previous process lifetime against an in-memory registry, or the "
            "id is wrong."
        )
    return record


def get_run_status_payload(run_id: str) -> Dict[str, Any]:
    """Return the lifecycle status of a validation run.

    Args:
        run_id: Identifier returned by :func:`validate_file_payload`.

    Returns:
        ``{
            "run_id": str,
            "status": "queued" | "running" | "completed" | "failed",
            "started_at": ISO-8601 UTC str,
            "finished_at": ISO-8601 UTC str | None,
            "violation_count": int | None,
        }``.

        ``violation_count`` is ``None`` while the run is still in flight,
        and ``finished_at`` is ``None`` for the same reason. Once the run
        reaches a terminal state both fields are populated.

    Raises:
        ToolError: For unknown ``run_id`` values that aren't tracked
            in-process AND aren't in the run-history backend either. The
            fallback to the run-history service uses the same defensive
            ``_fetch_recent_runs_safe`` wrapper as EF-S2 — a missing
            Oracle DSN never raises here.
    """
    if not isinstance(run_id, str) or not run_id:
        raise ToolError("run_id is required and must be a non-empty string")

    record = _get_registry().get(run_id)

    if record is not None:
        violation_count: Optional[int]
        if record.status in _TERMINAL_STATUSES:
            violation_count = len(record.violations)
        else:
            violation_count = None
        return {
            "run_id": record.run_id,
            "status": record.status,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "violation_count": violation_count,
        }

    # Fallback: the run may live in the Oracle-backed run history. Reuse
    # the same safe-fetch helper as EF-S2's list_recent_runs so a missing
    # backend simply degrades to ToolError rather than blowing up.
    from src.mcp.tools import _fetch_recent_runs_safe

    history = _fetch_recent_runs_safe(limit=500)
    for entry in history:
        if entry.get("run_id") == run_id:
            # Map the run-history status onto our canonical lifecycle.
            raw_status = (entry.get("status") or "").upper()
            if raw_status in ("PASS", "FAIL", "PARTIAL"):
                status = _STATUS_COMPLETED
            elif raw_status == "RUNNING":
                status = _STATUS_RUNNING
            else:
                status = _STATUS_FAILED if raw_status else _STATUS_COMPLETED
            return {
                "run_id": run_id,
                "status": status,
                "started_at": entry.get("timestamp"),
                "finished_at": entry.get("timestamp"),
                # We only know the bucket totals, not the canonicalised
                # violation list, for historical runs — surface the
                # ``fail_count`` as a best-effort proxy.
                "violation_count": (
                    entry.get("fail_count")
                    if isinstance(entry.get("fail_count"), int)
                    else None
                ),
            }

    raise ToolError(
        f"Unknown run_id {run_id!r}. No matching in-process run and no "
        "matching row in the run-history backend."
    )


def get_violations_payload(
    run_id: str,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    severity: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a page of violations for a completed run.

    Args:
        run_id: Identifier returned by :func:`validate_file_payload`.
        page: 1-based page number. Values < 1 are clamped to 1.
        page_size: Maximum violations per page. Clamped to the range
            ``1..MAX_PAGE_SIZE``. Default :data:`DEFAULT_PAGE_SIZE`.
        severity: Optional case-insensitive filter — one of ``"error"``,
            ``"warning"``, ``"info"``. ``None`` returns all severities.

    Returns:
        ``{
            "run_id": str,
            "page": int,
            "page_size": int,
            "total_count": int,
            "has_more": bool,
            "violations": [ {...}, ... ],
        }``.

        ``total_count`` is the size of the filtered list (NOT the raw
        list before the severity filter), so an agent can drive its
        pagination loop off ``has_more`` without re-counting locally.

    Raises:
        ToolError: When the run does not exist in the in-process
            registry, or when ``severity`` is supplied but not one of
            the three canonical values.
    """
    record = _lookup_run(run_id)

    # Clamp the pagination knobs defensively. We accept rather than
    # reject silly inputs (negative page / zero page_size) so an agent
    # that drives this loop programmatically can't get stuck on a
    # one-shot ToolError that requires human intervention.
    effective_page = max(1, int(page) if isinstance(page, int) else 1)
    if isinstance(page_size, int):
        effective_page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    else:
        effective_page_size = DEFAULT_PAGE_SIZE

    # Severity filter is validated explicitly so the agent gets a useful
    # error message rather than a silent empty page when it mis-spells
    # the bucket name.
    filtered: List[Dict[str, Any]] = record.violations
    if severity is not None:
        if not isinstance(severity, str):
            raise ToolError("severity must be a string when supplied")
        normalised = severity.lower()
        if normalised not in _VALID_SEVERITIES:
            raise ToolError(
                f"Invalid severity {severity!r}: must be one of "
                f"{sorted(_VALID_SEVERITIES)} (case-insensitive)."
            )
        filtered = [v for v in record.violations if v.get("severity") == normalised]

    total_count = len(filtered)
    start = (effective_page - 1) * effective_page_size
    end = start + effective_page_size
    page_slice = filtered[start:end]
    has_more = end < total_count

    return {
        "run_id": run_id,
        "page": effective_page,
        "page_size": effective_page_size,
        "total_count": total_count,
        "has_more": has_more,
        "violations": page_slice,
    }


# ---------------------------------------------------------------------------
# Tool descriptions
# ---------------------------------------------------------------------------

VALIDATE_FILE_DESCRIPTION = (
    "Start a Valdo validation run for a source + file. Returns immediately "
    "with a run_id and started_at timestamp. When the background worker is "
    "enabled (VALDO_MCP_ASYNC_VALIDATE) the run is queued and executed "
    "out-of-band by the valdo run-job-worker process; otherwise it runs "
    "synchronously inside this call. Either way, poll get_run_status to "
    "discover when the run completes, then page get_violations for the "
    "results. Raises a tool error when the source is unknown, the file does "
    "not exist, or the source overlay cannot be parsed. A second call with "
    "the same source + file_path while a previous run is still in flight "
    "returns the existing run_id (no double-trigger)."
)

GET_RUN_STATUS_DESCRIPTION = (
    "Return the lifecycle status of a Valdo validation run started via "
    "validate_file. Status is one of queued, running, completed, failed. "
    "violation_count is null while the run is still in flight and the "
    "total flattened error+warning+info count once it terminates. Falls "
    "back to the run-history backend for older runs no longer held in "
    "memory. Raises a tool error for unknown run_id values."
)

GET_VIOLATIONS_DESCRIPTION = (
    "Page through the violations produced by a Valdo validation run. "
    "Supports optional case-insensitive severity filter (error | warning "
    "| info) and pagination (default page_size 50, max 200). Each entry "
    "carries rule_id, field, severity, message, record_index, and "
    "actual_value. Returns total_count of the filtered list and a "
    "has_more flag so an agent can drive its pagination loop without "
    "re-counting locally. Raises a tool error for unknown run_id values."
)


# ---------------------------------------------------------------------------
# Test-only helpers
# ---------------------------------------------------------------------------


def _reset_runs_for_tests() -> None:
    """Drop the singleton registry and any persisted rows.

    Exposed so the integration tests can run in isolation without
    accumulating state between cases. Two responsibilities:

    1. Clear the cached registry singleton so the *next* call to
       :func:`_get_registry` rebuilds it (picks up monkeypatched
       env vars).
    2. If a registry is currently constructed, clear its persisted
       rows via the backend-specific ``clear`` helper so a subsequent
       test starting with an explicit ``_get_registry`` call doesn't
       see leftover state.

    Production code MUST NOT call this — it would silently drop polling
    state for in-flight runs.
    """
    global _REGISTRY
    with _REGISTRY_LOCK:
        if _REGISTRY is not None:
            try:
                # Both backends expose ``clear()`` even though it is not
                # in the public protocol — it is test-only.
                _REGISTRY.clear()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001 — defensive: tests must never block on cleanup.
                pass
        _REGISTRY = None


# Keep ``time`` and ``threading`` referenced so static analysers don't
# strip the imports (the registry singleton lock and EF-S5 follow-up
# timing instrumentation both want them).
_ = time
_ = threading
