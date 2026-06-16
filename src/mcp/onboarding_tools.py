"""MCP onboarding-tool implementations for Valdo (EF-S5).

This module wires the three "agent-driven onboarding" MCP tools onto the
existing onboarding service layer (EC-S6's :mod:`src.commands.onboard_source`
and the EC-track infer-mapping service in
:mod:`src.services.infer_mapping_service`). Three tools are exposed:

* ``upload_workbook_as_spec`` — copy a draft onboarding workbook into the
  agent's MCP sandbox after validating its schema. The BA conversation
  starts here: "give me your workbook and I'll tell you whether it's
  schema-clean".
* ``onboard_source_dry_run`` — preview the artefact tree that a fully
  validated workbook would produce, without touching disk. Returns a
  structured ``would_write`` list (path / bytes / kind) so the agent can
  render a checklist for the BA before any artefact lands in the repo.
* ``infer_mapping_from_sample`` — produce a draft field mapping from a
  CSV / pipe-delimited / fixed-width sample so the BA has a starting
  point for the ``*_Mapping`` sheets. A *hint*, not a final mapping.

All three are thin adapters around existing service-layer entry points;
no new business logic lives in this module. The split exists so the
filesystem + workbook + format-inference plumbing stays unit-testable
without standing up the full FastMCP transport.

Sandbox convention (EF-S5):
    ``upload_workbook_as_spec`` writes into
    ``~/.valdo/mcp_sandbox/<SOURCE_CODE>/onboarding.xlsx`` by default. The
    parent directory ``~/.valdo/`` is reserved for the EF-S7 auth bridge
    too — keeping every MCP-side state under a single dotdir simplifies
    cleanup (``rm -rf ~/.valdo``) and avoids polluting the repo working
    tree with sandbox artefacts. The path is fully overridable for tests
    via the module-level :data:`_SANDBOX_ROOT_OVERRIDE`.

Why a sandbox at all?
    EC-S6's ``valdo onboard-source`` writes straight to the repo's
    ``config/`` tree. That's the correct end-state for a committed
    onboarding, but the BA conversation needs an intermediate staging
    area where the agent can iterate without polluting the repo. The
    sandbox is that area; once the BA is happy with the dry-run preview,
    they (or the agent in a follow-up tool — out of scope for EF-S5)
    promote the sandbox workbook into the repo via the existing CLI.

Auth posture:
    These tools live behind the same dev-mode auth gate as the rest of
    the MCP surface (see :mod:`src.mcp.server`'s
    :class:`MCPAuthMiddleware`). EF-S7 replaces the gate with the LDAPS +
    X-API-Key bridge.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp.exceptions import ToolError

__all__ = [
    "upload_workbook_as_spec_payload",
    "onboard_source_dry_run_payload",
    "infer_mapping_from_sample_payload",
    "UPLOAD_WORKBOOK_AS_SPEC_DESCRIPTION",
    "ONBOARD_SOURCE_DRY_RUN_DESCRIPTION",
    "INFER_MAPPING_FROM_SAMPLE_DESCRIPTION",
    "DEFAULT_SANDBOX_FILENAME",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sandbox convention
# ---------------------------------------------------------------------------

# Default filename for the staged workbook inside the per-source sandbox
# directory. Kept as a constant so tests can assert on it without
# round-tripping through environment expansion.
DEFAULT_SANDBOX_FILENAME = "onboarding.xlsx"

# Tests inject a temp directory here via monkeypatch so the production
# ``~/.valdo/`` convention is not touched by the integration suite.
# Production code MUST NOT set this; the value of ``None`` triggers the
# default home-dir resolution in :func:`_sandbox_root`.
_SANDBOX_ROOT_OVERRIDE: Optional[Path] = None


def _sandbox_root() -> Path:
    """Return the root directory for MCP-staged workbooks.

    Resolution order:

    1. The module-level :data:`_SANDBOX_ROOT_OVERRIDE` (tests only).
    2. The ``VALDO_MCP_SANDBOX_ROOT`` environment variable, if set.
    3. ``~/.valdo/mcp_sandbox`` (the documented default).

    The directory is NOT created here — :func:`upload_workbook_as_spec_payload`
    creates the per-source subdirectory at write time. Keeping this read-only
    means simply *importing* this module never touches the filesystem.

    Returns:
        Absolute :class:`pathlib.Path` to the sandbox root. The path may
        not exist yet.
    """
    if _SANDBOX_ROOT_OVERRIDE is not None:
        return Path(_SANDBOX_ROOT_OVERRIDE)
    env_override = os.environ.get("VALDO_MCP_SANDBOX_ROOT")
    if env_override:
        return Path(env_override).expanduser()
    return Path.home() / ".valdo" / "mcp_sandbox"


def _validate_source_code(source_code: str) -> str:
    """Return *source_code* uppercased, after rejecting unsafe characters.

    The sandbox path is constructed from this value, so anything resembling
    a path traversal payload (``../``, NUL bytes, leading slashes) must be
    rejected here rather than at the filesystem layer. We deliberately
    require a plain identifier — alnum + underscore + dash — because the
    canonical source codes Valdo ships today (``SHAW``, ``ENCORE``, etc.)
    all fit that grammar.

    Args:
        source_code: Candidate source code from the workbook or the
            caller's tool arguments.

    Returns:
        The uppercased, validated source code.

    Raises:
        ToolError: When the value is empty, not a string, or contains
            anything outside the safe character set.
    """
    if not isinstance(source_code, str) or not source_code.strip():
        raise ToolError(
            "source_code is required and must be a non-empty string"
        )
    candidate = source_code.strip()
    safe_chars = set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
    )
    if not all(ch in safe_chars for ch in candidate):
        raise ToolError(
            f"Invalid source_code {source_code!r}: only alphanumerics, "
            "underscore, and dash are permitted (no path separators, no "
            "dots, no whitespace)."
        )
    return candidate.upper()


# ---------------------------------------------------------------------------
# upload_workbook_as_spec
# ---------------------------------------------------------------------------


def upload_workbook_as_spec_payload(
    workbook_path: str,
    source_code: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate a draft onboarding workbook and copy it into the MCP sandbox.

    This is the BA-onboarding entry point. The flow is:

    1. The agent (or BA via the agent) places a draft ``.xlsx`` somewhere
       the MCP server can read it.
    2. The agent invokes this tool with the path. The tool runs the EC-S1
       schema validator and the EC-S2 reader to confirm the workbook is
       well-formed, then copies it into
       ``<sandbox_root>/<SOURCE_CODE>/onboarding.xlsx``.
    3. The agent surfaces the returned ``sandbox_path`` back to the BA so
       subsequent tool calls (e.g. :func:`onboard_source_dry_run_payload`)
       can reference the staged copy.

    Schema validation is full EC-S1 — every required sheet, every
    required column. Warnings (e.g. unrecognised scratch sheets) are
    captured in the ``warnings`` array but do NOT block the upload.

    Args:
        workbook_path: Filesystem path to the draft ``.xlsx`` workbook
            on the MCP server's local disk. Relative paths are resolved
            against the FastAPI process's current working directory.
        source_code: Optional override for the source code used to
            name the sandbox subdirectory. When omitted, the value is
            read from the workbook's ``Source`` sheet
            (``SourceInfo.source_code``). Must be a plain identifier
            (alnum + underscore + dash, case-insensitive, uppercased
            before use).

    Returns:
        ``{
            "sandbox_path": "<absolute path of the staged workbook>",
            "source_code": "<UPPERCASE SOURCE CODE>",
            "sheet_count": N,
            "warnings": [
                {"sheet": ..., "cell": ..., "reason": ...},
                ...
            ],
        }``

        ``sheet_count`` is the number of *recognised* sheets in the
        workbook (the count returned by openpyxl, including scratch
        tabs). The ``warnings`` array lists every schema finding with
        ``severity == "warning"`` so the agent can flag them to the
        BA without raising.

    Raises:
        ToolError: When the workbook is missing, schema-invalid, or
            ``source_code`` cannot be resolved.
    """
    if not workbook_path or not isinstance(workbook_path, str):
        raise ToolError("workbook_path is required and must be a string")

    src_path = Path(workbook_path).expanduser()
    if not src_path.is_file():
        raise ToolError(f"Workbook file not found: {workbook_path!r}")

    # Schema validation BEFORE the reader so the agent gets a single
    # multi-line message listing every missing column / wrong sheet name
    # in one shot, rather than failing on the first reader-side issue.
    from src.onboarding.models import WorkbookReadError
    from src.onboarding.workbook_schema import (
        WorkbookSchemaError,
        assert_workbook_valid,
        validate_workbook,
    )

    try:
        assert_workbook_valid(src_path)
    except WorkbookSchemaError as exc:
        raise ToolError(str(exc)) from exc

    # Run the validator a second time (cheap) to collect the warnings the
    # raise path discards. The validator is intentionally idempotent.
    findings = validate_workbook(src_path)
    warnings = [
        {
            "sheet": f.sheet,
            "cell": f.cell,
            "reason": f.reason,
        }
        for f in findings
        if f.severity == "warning"
    ]

    # Now parse the workbook so we can derive the source code (if the
    # caller didn't provide one) and report the recognised sheet count.
    from src.onboarding.workbook_reader import read_workbook

    try:
        workbook = read_workbook(src_path)
    except (WorkbookReadError, WorkbookSchemaError) as exc:
        # Should not normally happen because we passed assert_workbook_valid
        # above, but the reader has tighter per-cell coercion checks (e.g.
        # boolean cells) that the schema validator does not. Surface those
        # as ToolError too.
        raise ToolError(str(exc)) from exc

    # Resolve the source_code: caller wins, then workbook fallback.
    if source_code is not None:
        resolved_code = _validate_source_code(source_code)
    elif workbook.source.source_code:
        resolved_code = _validate_source_code(workbook.source.source_code)
    else:  # pragma: no cover — schema validator would have caught this
        raise ToolError(
            "source_code is required: not provided and the workbook's "
            "Source sheet has no source_code value."
        )

    # Stage the workbook. We use shutil.copyfile (not copy) so the
    # destination permissions follow the umask of the FastAPI process
    # rather than inheriting the (possibly more permissive) source mode.
    sandbox_dir = _sandbox_root() / resolved_code
    sandbox_dir.mkdir(parents=True, exist_ok=True)
    sandbox_path = sandbox_dir / DEFAULT_SANDBOX_FILENAME
    try:
        shutil.copyfile(src_path, sandbox_path)
    except OSError as exc:  # pragma: no cover — defensive
        raise ToolError(
            f"Failed to copy workbook into sandbox at {sandbox_path}: {exc}"
        ) from exc

    # Sheet count from openpyxl. We use the read-only mode (same as the
    # schema validator) to keep the call cheap and avoid loading every
    # sheet's data into memory just to count tabs.
    from openpyxl import load_workbook

    try:
        wb = load_workbook(filename=str(src_path), read_only=True, data_only=True)
        sheet_count = len(wb.sheetnames)
        wb.close()
    except Exception:  # pragma: no cover — workbook already opened twice above
        sheet_count = 0

    # S13.5-2 (#415): staging a draft workbook as the source spec is an
    # operator-facing config mutation via the MCP surface. Audit it after
    # the copy succeeds, attributing it to the authenticated MCP principal
    # (set by the auth middleware / stdio token loader). No secret is
    # logged — only the principal's short handle.
    from src.mcp.auth import current_user
    from src.utils.audit_logger import audit_mutation

    principal = current_user()
    audit_mutation(
        resource_type="source_spec",
        resource_id=resolved_code,
        action="create",
        actor=principal.user if principal else "mcp",
        triggered_by="mcp",
    )

    return {
        "sandbox_path": str(sandbox_path),
        "source_code": resolved_code,
        "sheet_count": sheet_count,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# onboard_source_dry_run
# ---------------------------------------------------------------------------


# Kind tokens surfaced in the dry-run output. Kept as a module-level
# constant so tests can compare against the canonical set without
# duplicating the string literals.
_KIND_BY_CATEGORY: Dict[str, str] = {
    "source_yaml": "source_yaml",
    "mapping": "mapping_json",
    "rules": "rules_json",
    "reconciliation": "reconciliation_yaml",
    "sql": "sql",
}


def onboard_source_dry_run_payload(workbook_path: str) -> Dict[str, Any]:
    """Preview the artefact tree a workbook would produce, without touching disk.

    Calls the EC-S6 service-layer planner
    (:func:`src.commands.onboard_source._plan_writes`) in memory and
    surfaces the planned writes as a structured dict instead of the CLI's
    free-text summary. The returned shape is designed for an agent to
    drive a UI checklist:

    .. code-block:: python

        {
            "source_code": "SHAW",
            "would_write": [
                {
                    "path": "config/e2e/sources/SHAW.yml",
                    "bytes": 1234,
                    "kind": "source_yaml",
                },
                {
                    "path": "config/mappings/SHAW_TRANERT.yaml",
                    "bytes": 5678,
                    "kind": "mapping_json",
                },
                ...
            ],
            "summary": {"total_files": N, "total_bytes": N},
        }

    NO disk side effects. The CLI's normal mode is intentionally NOT
    invoked from here — this tool is the "preview" half of the
    upload/preview/promote flow.

    Args:
        workbook_path: Filesystem path to the ``.xlsx`` workbook. Either
            the original draft path OR the sandbox path returned by
            :func:`upload_workbook_as_spec_payload` is acceptable.

    Returns:
        A dict with ``source_code``, ``would_write`` (list of
        ``{path, bytes, kind}`` entries), and ``summary``
        (``{total_files, total_bytes}``).

    Raises:
        ToolError: When the workbook is missing, schema-invalid, or any
            of the EC-S3/S4/S5/ED-S1/ED-S2 emitters raise.
    """
    if not workbook_path or not isinstance(workbook_path, str):
        raise ToolError("workbook_path is required and must be a string")

    src_path = Path(workbook_path).expanduser()
    if not src_path.is_file():
        raise ToolError(f"Workbook file not found: {workbook_path!r}")

    # Lazy imports: the EC-S6 module pulls openpyxl + every emitter, and
    # we don't want to pay that cost on plain MCP handshakes.
    from src.commands.onboard_source import _plan_writes
    from src.onboarding.emitters import EmitterError
    from src.onboarding.models import WorkbookReadError
    from src.onboarding.workbook_reader import read_workbook
    from src.onboarding.workbook_schema import WorkbookSchemaError

    try:
        workbook = read_workbook(src_path)
    except (WorkbookReadError, WorkbookSchemaError) as exc:
        raise ToolError(str(exc)) from exc

    # Drive every emitter in-memory. Output root stays as Path.cwd() so
    # the surfaced paths mirror what the CLI would write (repo-relative
    # by the time we report them).
    try:
        plans = _plan_writes(
            workbook,
            output_root=Path.cwd(),
            source_dir=None,
            mapping_dir=None,
            rules_dir=None,
            reconciliation_dir=None,
            sql_dir=None,
            frozen_timestamp=None,
        )
    except EmitterError as exc:
        raise ToolError(f"Emitter error: {exc}") from exc

    cwd = Path.cwd()
    would_write: List[Dict[str, Any]] = []
    total_bytes = 0
    for plan in plans:
        size_bytes = len(plan.content.encode("utf-8"))
        total_bytes += size_bytes
        # Report repo-relative paths when possible (agents quote these
        # back to the BA); fall back to absolute if the path escapes the
        # cwd (e.g. ``--output-root /tmp/foo`` style — not used by
        # dry-run today but defended against).
        try:
            display_path = plan.path.resolve().relative_to(cwd).as_posix()
        except ValueError:
            display_path = plan.path.as_posix()
        would_write.append(
            {
                "path": display_path,
                "bytes": size_bytes,
                "kind": _KIND_BY_CATEGORY.get(plan.category, plan.category),
            }
        )

    return {
        "source_code": workbook.source.source_code,
        "would_write": would_write,
        "summary": {
            "total_files": len(would_write),
            "total_bytes": total_bytes,
        },
    }


# ---------------------------------------------------------------------------
# infer_mapping_from_sample
# ---------------------------------------------------------------------------


# Map the MCP-level ``format_hint`` values onto the
# :func:`src.services.infer_mapping_service.infer_mapping` ``format``
# argument values. The MCP layer accepts the more agent-friendly tokens
# (``csv``, ``fixed_width``, etc.) verbatim — there's a 1:1 correspondence
# today — but we keep the indirection so a future engine rename doesn't
# leak through.
_VALID_FORMAT_HINTS = frozenset(
    {"csv", "tsv", "pipe_delimited", "fixed_width"}
)


# Delimiters keyed by the format token the service surfaces. ``fixed_width``
# has no delimiter and is treated specially in :func:`_sniff_header_row`.
_DELIMITER_BY_FORMAT: Dict[str, str] = {
    "csv": ",",
    "tsv": "\t",
    "pipe_delimited": "|",
}


def _sniff_header_row(
    sample_path: Path, detected_format: Optional[str]
) -> Optional[List[str]]:
    """Best-effort sniff of the first row as a column-header row.

    Used only by :func:`infer_mapping_from_sample_payload` to upgrade the
    service's placeholder ``FIELD_NNN`` names to BA-friendly column
    names when the sample's first row looks like a header.

    Heuristic (intentionally conservative to avoid mis-classifying data
    rows as headers in random BA samples):

      * Delimited formats only — ``csv``, ``tsv``, ``pipe_delimited``.
        Fixed-width samples never have header rows by convention.
      * Every cell in the candidate row must be non-empty and contain
        no leading/trailing digits, no decimal points, and no
        currency symbols. Empty cells, digit-only cells, or decimal
        values disqualify the row.
      * The candidate row must contain at least one ASCII alpha
        character total (so a row of pure punctuation does not get
        treated as headers).

    Args:
        sample_path: The same path passed into the inference call.
        detected_format: The format the service auto-detected (or the
            caller's hint). ``None`` and ``fixed_width`` both short-circuit
            to ``None``.

    Returns:
        The cleaned header tokens (uppercased, surrounding whitespace
        stripped) when the first row qualifies, else ``None``.
    """
    if not detected_format or detected_format == "fixed_width":
        return None
    delimiter = _DELIMITER_BY_FORMAT.get(detected_format)
    if delimiter is None:
        return None

    try:
        with sample_path.open("r", encoding="utf-8", errors="replace") as fh:
            first_line = fh.readline().rstrip("\n").rstrip("\r")
    except OSError:
        return None

    if not first_line.strip():
        return None

    raw_cells = [c.strip() for c in first_line.split(delimiter)]
    if not raw_cells or any(not cell for cell in raw_cells):
        return None

    has_alpha_somewhere = False
    for cell in raw_cells:
        if any(ch.isdigit() or ch in ".$" for ch in cell):
            return None
        if any(ch.isalpha() for ch in cell):
            has_alpha_somewhere = True

    if not has_alpha_somewhere:
        return None

    # Uppercase + underscore-friendly canonical form. The BA workflow's
    # *_Mapping sheets use UPPER_SNAKE field names, so we surface the
    # same convention.
    return [cell.upper().replace(" ", "_") for cell in raw_cells]


def infer_mapping_from_sample_payload(
    sample_file_path: str,
    file_type: str,
    format_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """Produce a draft field mapping from a sample input file.

    Wraps :func:`src.services.infer_mapping_service.infer_mapping`. The
    service auto-detects the file format from the first few bytes
    unless ``format_hint`` is supplied. The output is a *draft* — the
    BA is expected to refine field names, data types, and lengths
    before pasting the rows into the workbook's ``*_Mapping`` sheet.

    Args:
        sample_file_path: Filesystem path to a sample data file. CSV /
            pipe-delimited / TSV / fixed-width are all supported.
        file_type: The logical file type the sample represents (e.g.
            ``"TRANERT"``, ``"ATOCTRAN"``). Surfaced verbatim in the
            output so the agent can later wire the draft into the
            correct ``*_Mapping`` sheet.
        format_hint: Optional explicit format. One of ``"csv"``, ``"tsv"``,
            ``"pipe_delimited"``, or ``"fixed_width"``. When omitted, the
            format is auto-detected from the file extension and content.

    Returns:
        ``{
            "file_type": "<FILE_TYPE>",
            "format": "<resolved format token>",
            "fields": [
                {
                    "name": "<draft name>",
                    "data_type": "<string | number | date>",
                    "position": <int | None>,   # fixed-width only
                    "length": <int | None>,
                    "target_name": "<draft target name>",
                },
                ...
            ],
            "warnings": [<one-line strings>],
        }``

        The ``warnings`` list captures any non-fatal hints the inference
        service surfaces (currently empty in 99% of happy-path runs but
        reserved for future enhancements like "all numeric values look
        like padded ints — consider data_type=number with leading_zeros").

    Raises:
        ToolError: When the file is missing, the format cannot be
            detected, the explicit hint is unknown, or the file is
            empty.
    """
    if not sample_file_path or not isinstance(sample_file_path, str):
        raise ToolError("sample_file_path is required and must be a string")
    if not isinstance(file_type, str) or not file_type.strip():
        raise ToolError("file_type is required and must be a non-empty string")

    src_path = Path(sample_file_path).expanduser()
    if not src_path.is_file():
        raise ToolError(f"Sample file not found: {sample_file_path!r}")

    resolved_hint: Optional[str] = None
    if format_hint is not None:
        if not isinstance(format_hint, str):
            raise ToolError("format_hint must be a string when supplied")
        normalised = format_hint.strip().lower()
        if normalised not in _VALID_FORMAT_HINTS:
            raise ToolError(
                f"Invalid format_hint {format_hint!r}: must be one of "
                f"{sorted(_VALID_FORMAT_HINTS)}."
            )
        resolved_hint = normalised

    # Reuse the existing service layer verbatim. No re-implementation —
    # any future improvement to inference (e.g. richer date-format
    # detection) lands once in the service and both the CLI and the MCP
    # surface inherit it.
    from src.services.infer_mapping_service import infer_mapping

    try:
        draft = infer_mapping(
            file_path=str(src_path),
            format=resolved_hint,
            sample_lines=100,
        )
    except FileNotFoundError as exc:
        raise ToolError(str(exc)) from exc
    except ValueError as exc:
        # Auto-detect failure or unsupported format. Surfaces the
        # service's hint about ``--format`` verbatim; rephrased here
        # to mention ``format_hint`` instead, the MCP-level knob.
        raise ToolError(
            f"{exc}".replace("--format", "format_hint")
        ) from exc

    # The CLI-facing infer_mapping service does not parse headers — it
    # assigns generic ``FIELD_001`` / ``FIELD_002`` placeholders for every
    # column. The MCP onboarding flow is BA-facing, and a BA always
    # provides a sample whose first row carries human-meaningful column
    # names. We sniff for that first row HERE (and only here — the CLI
    # contract stays unchanged) so the agent-surfaced draft has names
    # the BA can copy verbatim into the workbook. The header sniff is
    # best-effort: a row of all alpha+underscore tokens is treated as a
    # header; otherwise we leave the service-assigned names in place.
    header_names = _sniff_header_row(src_path, draft.get("source", {}).get("format"))

    # Re-shape the service output into the MCP-canonical mapping draft
    # shape advertised in the tool description. The service uses
    # ``transformations`` and ``validation_rules`` slots that the BA
    # workflow does not consume at the draft-suggestion stage; we drop
    # them here to keep the MCP payload small.
    detected_format = draft.get("source", {}).get("format")
    fields: List[Dict[str, Any]] = []
    raw_fields = draft.get("fields") or []
    for idx, field_entry in enumerate(raw_fields):
        # Prefer the sniffed header name when one is available; fall
        # back to the service's placeholder (``FIELD_001`` etc.) when
        # there's no header row.
        if header_names and idx < len(header_names):
            name = header_names[idx]
        else:
            name = field_entry.get("name")
        fields.append(
            {
                "name": name,
                "data_type": field_entry.get("data_type"),
                "position": field_entry.get("position"),
                "length": field_entry.get("length"),
                # The BA fills in the actual target name in the workbook;
                # we default to the inferred field name so the draft is
                # immediately copy-pasteable. Lowercase + underscore is
                # the canonical mapping convention (``target_name`` in
                # the *_Mapping sheets), so we surface it that way.
                "target_name": (
                    str(name).strip().lower() if isinstance(name, str) else None
                ),
            }
        )

    warnings: List[str] = []
    # Surface the service's draft marker as an info-style warning so the
    # agent has a one-liner to flash to the BA ("this is a starting point,
    # not a final mapping").
    if draft.get("_inferred"):
        warnings.append(
            "Draft mapping — refine field names, types, and lengths before "
            "committing to the workbook."
        )

    return {
        "file_type": file_type.strip(),
        "format": detected_format,
        "fields": fields,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Tool descriptions
# ---------------------------------------------------------------------------


UPLOAD_WORKBOOK_AS_SPEC_DESCRIPTION = (
    "Validate a draft onboarding workbook (.xlsx) and copy it into the "
    "MCP sandbox at ~/.valdo/mcp_sandbox/<SOURCE_CODE>/onboarding.xlsx. "
    "Runs the EC-S1 schema validator + EC-S2 reader; raises a tool error "
    "with a multi-line summary when any required sheet or column is "
    "missing. Warnings (e.g. unrecognised scratch sheets) are surfaced "
    "in the response's warnings array but do not block the upload. The "
    "source_code argument is optional — when omitted it is read from "
    "the workbook's Source sheet. Returns the absolute sandbox path so "
    "subsequent onboard_source_dry_run calls can reference the staged "
    "copy."
)

ONBOARD_SOURCE_DRY_RUN_DESCRIPTION = (
    "Preview the artefact tree a workbook would produce — source YAML, "
    "mapping JSON / umbrella YAML, rules JSON, reconciliation YAML, "
    "expected SQL — without touching disk. Returns a structured "
    "would_write list with path / bytes / kind for each artefact plus a "
    "summary (total_files, total_bytes). Wraps the EC-S6 onboard-source "
    "planner; raises a tool error on schema or emitter failures. The "
    "agent surfaces this as a checklist for the BA before any real "
    "write happens."
)

INFER_MAPPING_FROM_SAMPLE_DESCRIPTION = (
    "Produce a draft field mapping from a CSV / TSV / pipe-delimited / "
    "fixed-width sample input file. Returns {file_type, format, fields, "
    "warnings} where each field carries name, data_type, position "
    "(fixed-width only), length, and a draft target_name. This is a "
    "starting point for the BA's *_Mapping sheet — the agent surfaces "
    "the rows and the BA refines names, types, and lengths before "
    "committing. format_hint is optional; when omitted the format is "
    "auto-detected by extension + content sniff. Raises a tool error "
    "when the file is missing, empty, or the format cannot be "
    "determined."
)


# ---------------------------------------------------------------------------
# Test-only helpers
# ---------------------------------------------------------------------------


def _set_sandbox_root_for_tests(path: Optional[Path]) -> None:
    """Override the sandbox root for the duration of one test.

    The integration tests need a clean per-case sandbox so they don't
    pollute the developer's ``~/.valdo/`` tree. Production code MUST NOT
    call this — silently rerouting agent-visible state to a different
    directory would break the documented sandbox convention.

    Args:
        path: A directory to use as the sandbox root, or ``None`` to
            restore the default behaviour.
    """
    global _SANDBOX_ROOT_OVERRIDE
    _SANDBOX_ROOT_OVERRIDE = path
