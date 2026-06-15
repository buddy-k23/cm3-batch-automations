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

In-process run registry:
    Runs are tracked in :data:`_RUNS`, a module-level dict mapping
    ``run_id`` to a :class:`_RunRecord`. The registry is intentionally
    process-local and lossy across restarts — older runs are surfaced via
    the Oracle-backed ``run_history_service`` fallback in
    :func:`get_run_status_payload`. Concurrent access is guarded by
    :data:`_RUNS_LOCK` because FastMCP can dispatch tool calls from a
    threadpool (each JSON-RPC request runs in its own worker).

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
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from mcp.server.fastmcp.exceptions import ToolError

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


# ---------------------------------------------------------------------------
# In-process run registry
# ---------------------------------------------------------------------------


class _RunRecord:
    """In-memory record of a single validation run.

    The MCP layer does not need the full result dict in memory beyond
    extracting the status + violation counts + violation list, but we
    retain the source/file_path/file_type triple so :func:`_existing_run`
    can short-circuit duplicate ``validate_file`` calls.

    Attributes:
        run_id: UUID4 string, also used as the dict key in :data:`_RUNS`.
        source: Canonical source name (e.g. ``"SHAW"``).
        file_path: Absolute or repo-relative path of the file being
            validated.
        file_type: Resolved file-type token from the source overlay (e.g.
            ``"TRANERT"``) or ``None`` when the caller did not specify and
            we inferred from filename.
        status: One of ``queued`` / ``running`` / ``completed`` / ``failed``.
        started_at: ISO-8601 UTC string captured at run construction.
        finished_at: ISO-8601 UTC string set when status moves to a
            terminal value; ``None`` otherwise.
        violations: Flat list of canonicalised violation dicts (see
            :func:`_canonicalise_violations`). Empty list until the run
            completes.
        error_message: Populated on ``failed`` with the exception text
            from the underlying service call.
    """

    __slots__ = (
        "run_id",
        "source",
        "file_path",
        "file_type",
        "status",
        "started_at",
        "finished_at",
        "violations",
        "error_message",
    )

    def __init__(
        self,
        run_id: str,
        source: str,
        file_path: str,
        file_type: Optional[str],
    ) -> None:
        self.run_id = run_id
        self.source = source
        self.file_path = file_path
        self.file_type = file_type
        self.status = _STATUS_QUEUED
        self.started_at = _utcnow_iso()
        self.finished_at: Optional[str] = None
        self.violations: List[Dict[str, Any]] = []
        self.error_message: Optional[str] = None


# Module-level dict + lock so FastMCP's threadpool dispatch is safe. We
# accept the memory cost of holding all violations for every started run
# because EF-S4 is dev-mode-only; EF-S7 / production deployment will swap
# this out for a persistent store.
_RUNS: Dict[str, _RunRecord] = {}
_RUNS_LOCK = threading.Lock()


def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string ending in ``Z``.

    Kept here (rather than imported) so tests can monkeypatch it
    deterministically without touching ``datetime.datetime.now``
    globally.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _existing_run(source: str, file_path: str) -> Optional[_RunRecord]:
    """Return an in-flight run for *(source, file_path)*, else ``None``.

    Two runs are considered "the same" when their source and file_path
    match exactly. The file_type slot is intentionally not considered —
    re-triggering the same physical file with a different file_type tag
    would still be a duplicate validation and we should short-circuit it.

    Args:
        source: Canonical source name.
        file_path: File path as supplied by the caller (no normalisation).

    Returns:
        The existing :class:`_RunRecord` when one is in a non-terminal
        state, else ``None``.
    """
    with _RUNS_LOCK:
        for record in _RUNS.values():
            if record.source != source:
                continue
            if record.file_path != file_path:
                continue
            if record.status in _TERMINAL_STATUSES:
                continue
            return record
    return None


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


def _run_validate_synchronously(record: _RunRecord, artefacts: Dict[str, Optional[str]]) -> None:
    """Drive the validation service for *record* and update its state.

    Runs synchronously — see the module docstring for the rationale and
    the EF-S5 follow-up plan. Exceptions are caught and folded into the
    ``failed`` status; we never let an underlying service error propagate
    out of the MCP tool layer.

    Args:
        record: The freshly-constructed run record (status will be
            mutated from ``queued`` -> ``running`` -> terminal).
        artefacts: Mapping/rules paths resolved from the source overlay.
    """
    # Import lazily so a missing optional dependency only blows up the
    # one tool call that needs it, not every MCP request.
    from src.services.validate_service import run_validate_service

    record.status = _STATUS_RUNNING
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
        return

    record.violations = _canonicalise_violations(result if isinstance(result, dict) else {})
    record.status = _STATUS_COMPLETED
    record.finished_at = _utcnow_iso()


def validate_file_payload(
    source: str,
    file_path: str,
    file_type: Optional[str] = None,
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

    Returns:
        ``{"run_id": "<uuid>", "started_at": "ISO-8601 UTC"}``. Poll
        :func:`get_run_status_payload` (or the ``get_run_status`` MCP
        tool) to discover when the run completes; then page through
        :func:`get_violations_payload` for the results.

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
    record = _RunRecord(
        run_id=run_id,
        source=source,
        file_path=file_path,
        file_type=artefacts.get("file_type") or (file_type.upper() if file_type else None),
    )
    with _RUNS_LOCK:
        _RUNS[run_id] = record

    _run_validate_synchronously(record, artefacts)

    return {
        "run_id": run_id,
        "started_at": record.started_at,
    }


def _lookup_run(run_id: str) -> _RunRecord:
    """Return the in-process record for *run_id* or raise ToolError.

    Args:
        run_id: Identifier returned by :func:`validate_file_payload`.

    Returns:
        The matching :class:`_RunRecord`.

    Raises:
        ToolError: When no record exists. Older runs that were started
            in a previous process lifetime are surfaced via the
            ``run_history_service`` fallback in
            :func:`get_run_status_payload`; the violation list, however,
            is only available while the in-process record survives.
    """
    if not isinstance(run_id, str) or not run_id:
        raise ToolError("run_id is required and must be a non-empty string")
    with _RUNS_LOCK:
        record = _RUNS.get(run_id)
    if record is None:
        raise ToolError(
            f"Unknown run_id {run_id!r}. The run may have been started in a "
            "previous process lifetime, or the id is wrong."
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

    with _RUNS_LOCK:
        record = _RUNS.get(run_id)

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
    "with a run_id and started_at timestamp; the validation itself runs "
    "synchronously inside this call today (EF-S5 will move it to a "
    "background worker). Poll get_run_status to discover when the run "
    "completes, then page get_violations for the results. Raises a tool "
    "error when the source is unknown, the file does not exist, or the "
    "source overlay cannot be parsed. A second call with the same source "
    "+ file_path while a previous run is still in flight returns the "
    "existing run_id (no double-trigger)."
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
    """Clear the in-process run registry.

    Exposed so the integration tests can run in isolation without
    accumulating state between cases. Production code MUST NOT call
    this — it would silently drop polling state for in-flight runs.
    """
    with _RUNS_LOCK:
        _RUNS.clear()


# Re-export for symmetry with src.mcp.tools (which also exposes its
# fetch helper). Marked private to discourage drift.
_RUNS_REGISTRY = _RUNS

# Keep ``time`` referenced so static analysers don't strip the import
# (used by future EF-S5 timing instrumentation).
_ = time
