"""Detect-drift MCP tool implementation (S21-4, #436).

Wires the ``detect_drift`` MCP tool onto the existing drift-detector service
(:func:`src.services.drift_detector.detect_drift`) — the same code path the
``valdo detect-drift`` CLI command
(:func:`src.commands.detect_drift_command.run_detect_drift`) and the
``POST /api/v1/files/detect-drift`` REST endpoint drive. Like the other action
tools (S21-1 ``db_compare`` / S21-2 ``reconcile_all`` / S21-3 ``mask_file``)
this module is a thin adapter — no drift-detection logic lives here; the tool
registration itself stays in :mod:`src.mcp.server`.

The tool lets an agent check whether a batch file's on-disk layout has drifted
from the field layout declared in its mapping JSON. The agent points at the
data file and the mapping JSON; the service samples the file and returns a
drift report — for fixed-width mappings, a per-field position/width drift
heuristic; for delimited mappings (CSV / pipe / TSV), a header-column
comparison.

A genuine drift finding is a RESULT (``drifted == True``), NOT an error. Tool
errors are reserved for caller-fixable problems: a missing/blank argument, or a
mapping file that cannot be found or parsed. A missing or unreadable *file*, or
an unsupported mapping format, surfaces in the report's ``skipped`` / ``reason``
keys rather than as an error — that matches the service contract the CLI and
REST endpoint already expose.

The response carries only the structured drift report (field NAMES, declared /
detected positions and lengths, severities, reasons) — never any raw field
value from the inspected file, and no secrets.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

from mcp.server.fastmcp.exceptions import ToolError

from src.services.drift_detector import detect_drift

__all__ = [
    "detect_drift_payload",
    "DETECT_DRIFT_DESCRIPTION",
]

logger = logging.getLogger(__name__)


def detect_drift_payload(file: str, mapping: str) -> Dict[str, Any]:
    """Detect schema drift between a file and its mapping; return the report.

    Thin adapter over :func:`src.services.drift_detector.detect_drift`. The
    *mapping* JSON is loaded here (the service wants a parsed dict); the
    sampling, the fixed-width position heuristic, and the delimited
    header-comparison all live in the service layer. The *file* is never
    modified.

    Args:
        file: Path to the batch data file to inspect.
        mapping: Path to the mapping JSON. Must contain a ``fields`` list and
            optionally a ``format`` / ``file_format`` key (``csv`` | ``pipe`` |
            ``tsv`` | ``fixed-width``; absent defaults to fixed-width).

    Returns:
        The drift-report dict the service produces, with at minimum:

        - ``drifted``: ``True`` when at least one field has drifted.
        - ``fields``: a list of per-field drift findings. For fixed-width,
          each entry has ``name``, ``expected_start``, ``expected_length``,
          ``actual_start``, ``actual_length``, ``severity``. For delimited,
          each entry adds a ``reason`` (``column_missing`` |
          ``unexpected_column`` | ``column_count_mismatch``).

        When the check cannot run, the report carries ``skipped: True`` and a
        ``reason`` (e.g. ``file_not_found``, ``read_error``,
        ``unsupported_format``, ``too_short``, ``no_fields``) — those are
        reported in the result, NOT raised.

    Raises:
        ToolError: When *file* or *mapping* is missing/blank, or the mapping
            file cannot be found or parsed. These are caller-fixable problems.
            A genuine drift finding is NOT raised — it surfaces in
            ``report['drifted']``.
    """
    if not file or not isinstance(file, str):
        raise ToolError("file is required and must be a non-empty string")
    if not mapping or not isinstance(mapping, str):
        raise ToolError("mapping is required and must be a non-empty string")

    mapping_path = Path(mapping)
    if not mapping_path.exists():
        raise ToolError(f"Mapping file not found: {mapping}")
    try:
        mapping_config = json.loads(mapping_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ToolError(f"Failed to load mapping file: {exc}") from exc

    try:
        return detect_drift(file, mapping_config)
    except (FileNotFoundError, ValueError) as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface infra failures as ToolError
        logger.exception("detect_drift tool failed for file=%r", file)
        raise ToolError(f"Drift detection failed: {exc}") from exc


DETECT_DRIFT_DESCRIPTION = (
    "Detect schema drift between a batch file and its mapping, returning a "
    "structured drift report. Accepts the input file path and a mapping JSON "
    "path (its 'fields' list names the columns and, for fixed-width, their "
    "positions/lengths; an optional 'format' key selects csv | pipe | tsv | "
    "fixed-width, defaulting to fixed-width). For fixed-width mappings the "
    "service samples the file and flags fields whose content has shifted "
    "position/width; for delimited mappings it compares the file's header "
    "columns (or column count) against the mapped field names. Wraps the "
    "existing drift-detector service — the same code path the 'valdo "
    "detect-drift' CLI and the detect-drift REST endpoint use; the input file "
    "is never modified. Returns 'drifted' (bool) and 'fields' (per-field "
    "findings: name, expected/actual start+length, severity, and for delimited "
    "files a reason: column_missing | unexpected_column | "
    "column_count_mismatch). When the check cannot run (file not found/"
    "unreadable, unsupported format, too few sample lines, no mapped fields) "
    "the report carries 'skipped' and 'reason' — that is a RESULT, not an "
    "error. The response carries no raw field values. Raises a tool error only "
    "for caller-fixable problems: a missing/blank argument, or a mapping file "
    "that cannot be found or parsed."
)
