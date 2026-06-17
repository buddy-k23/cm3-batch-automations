"""Export-failed-rows MCP tool implementation (S22-3, #440).

Wires the ``export_failed_rows`` MCP tool onto the existing Valdo validate +
error-extract code path — the same
:func:`~src.services.validate_service.run_validate_service` plus
:func:`~src.services.error_extractor.extract_error_rows` that the
``valdo validate --export-errors`` CLI flag
(:func:`src.commands.validate_command.run_validate_command`) and the
``POST /api/v1/files/export-errors`` REST endpoint
(:func:`src.api.routers.files.export_errors`) drive. Like the S21 action tools
(``mask_file`` / ``detect_drift`` / ``extract_table``) and the S22-1 / S22-2 tools
(``parse_file`` / ``run_etl_pipeline``), this module is a THIN adapter — no
validation or extraction logic lives here. The validation, the failed-row
collection, and the original-format write-back all stay in the service layer; the
tool registration itself stays in :mod:`src.mcp.server`.

The tool lets an agent run a validation and obtain ONLY the rows that failed —
written back out to *output* in the file's original format (header preserved for
delimited files) so the agent can hand a remediation file to a human or feed it to
a downstream fixer — without ever loading the failed rows into the agent's context.

CRITICAL PII posture: the response carries ONLY the export-file PATH and the
counts (failed/total/valid + the overall validity flag). It NEVER echoes the raw
failed-row values, the per-row errors, or the field contents. The failed rows
exist solely in the on-disk export file the caller already controls.

ToolError is reserved for caller-fixable problems: a missing/blank ``file`` or
``output`` argument, an input file or mapping file that cannot be found or parsed,
or an unexpected validation/extraction failure. A clean file (zero failed rows) is
a RESULT, not an error — the caller inspects ``failed_row_count``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from mcp.server.fastmcp.exceptions import ToolError

__all__ = [
    "export_failed_rows_payload",
    "EXPORT_FAILED_ROWS_DESCRIPTION",
]

logger = logging.getLogger(__name__)


def export_failed_rows_payload(
    file: str,
    mapping: str,
    output: str,
    rules: Optional[str] = None,
    use_chunked: bool = False,
) -> Dict[str, Any]:
    """Validate a file and export only its failed rows to *output*.

    Thin adapter over the Valdo validate + error-extract path (the same code path
    the ``valdo validate --export-errors`` CLI flag and the
    ``POST /api/v1/files/export-errors`` REST endpoint drive). The file is
    validated against *mapping* (and optional *rules*); every row that produced a
    validation error is written to *output* in the file's ORIGINAL format —
    delimited files keep their header row, fixed-width files get the raw failed
    lines. No validation or extraction logic lives here; it all stays in
    :func:`~src.services.validate_service.run_validate_service` and
    :func:`~src.services.error_extractor.extract_error_rows`. The input *file* is
    never modified.

    A clean file (zero failed rows) is a RESULT, not a tool error: the export
    file is still created (header-only for delimited, empty for fixed-width) and
    ``failed_row_count`` is ``0``.

    CRITICAL PII posture: the returned dict carries ONLY the export-file path and
    the counts. It NEVER includes the raw failed-row values, the field contents,
    or the per-row error detail — those live solely in the on-disk export file.

    Args:
        file: Path to the batch file to validate. Required, non-blank. Never
            modified.
        mapping: Path to the mapping JSON the file is validated against. Required,
            non-blank.
        output: Destination path for the exported failed rows. Required,
            non-blank. Parent directories are created automatically.
        rules: Optional path to a rules-config JSON applied during validation.
        use_chunked: Route validation through the memory-efficient chunked
            validator (delimited files only; ignored for fixed-width mappings).
            Defaults to ``False``.

    Returns:
        A counts-only dict (NO raw row data) with:

        - ``output_path``: str — the *output* path echoed back verbatim.
        - ``failed_row_count``: int — number of failed rows written to *output*.
        - ``total_rows``: int — total data rows processed.
        - ``valid_rows``: int — rows that passed validation.
        - ``valid``: bool — overall validity flag (``True`` only when there were
          no errors).

    Raises:
        ToolError: For caller-fixable problems: *file*/*mapping*/*output*
            missing/blank, the input file or mapping file not found, an
            unparseable mapping, or an unexpected validation/extraction failure.
    """
    if not file or not isinstance(file, str):
        raise ToolError("file is required and must be a non-empty string")
    if not mapping or not isinstance(mapping, str):
        raise ToolError("mapping is required and must be a non-empty string")
    if not output or not isinstance(output, str):
        raise ToolError("output is required and must be a non-empty string")

    if not Path(file).exists():
        raise ToolError(f"File not found: {file}")
    if not Path(mapping).exists():
        raise ToolError(f"Mapping file not found: {mapping}")

    # Lazy imports keep the heavy service deps out of module import time and mirror
    # the CLI command's / API router's import sites.
    from src.services.validate_service import run_validate_service
    from src.services.error_extractor import extract_error_rows

    try:
        result = run_validate_service(
            file=file,
            mapping=mapping,
            rules=rules,
            use_chunked=use_chunked,
        )
        export_result = extract_error_rows(
            file_path=file,
            validation_result=result,
            output_path=output,
        )
    except (ValueError, json.JSONDecodeError) as exc:
        # Unparseable mapping / malformed input — caller-fixable.
        raise ToolError(str(exc)) from exc
    except FileNotFoundError as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface infra failures as ToolError
        logger.exception("export_failed_rows tool failed for file=%r", file)
        raise ToolError(f"Export failed: {exc}") from exc

    # Counts-only response. We deliberately read only scalar counts off the
    # validation result — NEVER the 'errors' list or any field values — so no raw
    # failed-row content can leak over the MCP transport.
    total_rows = int(result.get("total_rows", 0) or 0)
    valid_rows = int(result.get("valid_rows", 0) or 0)
    return {
        "output_path": export_result["output_path"],
        "failed_row_count": int(export_result["exported_rows"]),
        "total_rows": total_rows,
        "valid_rows": valid_rows,
        "valid": bool(result.get("valid", False)),
    }


EXPORT_FAILED_ROWS_DESCRIPTION = (
    "Validate a batch file against a mapping and export ONLY the rows that failed "
    "validation to an output file, in the file's original format (delimited files "
    "keep their header row; fixed-width files get the raw failed lines) — so an "
    "agent can produce a remediation file without loading the failed rows into "
    "its context. Accepts the input file path (required), the mapping JSON path "
    "(required), the output path for the failed rows (required), an optional "
    "rules-config JSON path, and an optional use_chunked flag (memory-efficient "
    "validation for large delimited files). Wraps the existing Valdo validate + "
    "error-extract path — the same code the 'valdo validate --export-errors' CLI "
    "flag and the POST /api/v1/files/export-errors endpoint use; the input file "
    "is never modified. Returns 'output_path' (the export file written), "
    "'failed_row_count' (rows written), 'total_rows', 'valid_rows', and 'valid' "
    "(overall flag). CRITICAL: the response carries ONLY the path and the counts "
    "— it NEVER includes the raw failed-row values or field contents (those live "
    "solely in the on-disk export file). A clean file (zero failed rows) is a "
    "RESULT, not an error — inspect 'failed_row_count'. Raises a tool error only "
    "for caller-fixable problems: a missing/blank file, mapping, or output "
    "argument, an input file or mapping file that cannot be found or parsed, or "
    "an unexpected validation/extraction failure."
)
