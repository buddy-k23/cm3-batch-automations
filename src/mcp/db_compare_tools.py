"""DB-compare MCP tool implementation (S21-1, #433).

Wires the ``db_compare`` MCP tool onto the existing db-compare service
(:func:`src.services.db_file_compare_service.compare_db_to_file`). Like the
other action tools (EF-S4 / S7-4 / #407) this module is a thin adapter — no
business logic lives here; the tool registration itself stays in
:mod:`src.mcp.server`.

The tool lets an agent extract rows from a database table (or SQL query) on
whichever backend ``DB_ADAPTER`` selects (SQLite / PostgreSQL / Oracle),
write them to a temp file, and diff that against an actual batch file —
returning the same ``{workflow, compare}`` verdict the REST endpoint and the
CLI (``valdo db-compare``) produce.

A genuine comparison difference is a RESULT (``workflow.status == "failed"``),
not an error. Tool errors are reserved for caller-fixable problems: the
mapping file cannot be loaded / parsed, the actual file does not exist, no
table/query is supplied (or both are), or the adapter name is invalid.

Security: per-request connection values (``connection``) are forwarded to the
service's :func:`~src.services.db_file_compare_service.build_connection_override`
helper and on to the adapter; they are NEVER echoed back in the tool response,
which carries only the structured comparison verdict.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from mcp.server.fastmcp.exceptions import ToolError

from src.services.db_file_compare_service import (
    build_connection_override,
    compare_db_to_file,
)

__all__ = [
    "db_compare_payload",
    "DB_COMPARE_DESCRIPTION",
]

logger = logging.getLogger(__name__)


def _json_safe_verdict(verdict: Dict[str, Any]) -> Dict[str, Any]:
    """Return a JSON-serialisable copy of the db-compare verdict.

    The non-chunked compare path stores ``only_in_file1`` / ``only_in_file2``
    as :class:`pandas.DataFrame` objects (FastMCP cannot serialise those) and
    the result may carry NumPy scalar types. This adapter-layer normaliser:

    - replaces any DataFrame value with its row count (an integer), which is
      exactly the count the verdict's documented contract advertises and
      avoids leaking raw row data into the MCP response, and
    - round-trips the remainder through ``json.dumps(default=str)`` so any
      residual NumPy / non-native scalar collapses to a JSON-native value.

    No comparison logic is performed here — this is pure serialisation
    shaping for the transport.

    Args:
        verdict: The raw ``{workflow, compare, ...}`` dict from
            :func:`compare_db_to_file`.

    Returns:
        A JSON-serialisable deep copy of *verdict*.
    """
    def _coerce(value: Any) -> Any:
        if isinstance(value, pd.DataFrame):
            return len(value)
        if isinstance(value, dict):
            return {k: _coerce(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_coerce(v) for v in value]
        return value

    coerced = _coerce(verdict)
    # Final pass: collapse any residual non-native scalar (e.g. numpy.int64).
    return json.loads(json.dumps(coerced, default=str))


def _render_db_compare_report(verdict: Dict[str, Any]) -> Dict[str, str]:
    """Render the db-compare HTML report and return the report fields.

    S23-4 (#446) / ADR 0023: db_compare plugs into the comparison-style
    renderer (:class:`~src.reports.renderers.comparison_renderer.HTMLReporter`).
    The renderer consumes the verdict's ``compare`` sub-dict (the
    ``run_compare_service`` output: ``total_rows_file1/2``, ``matching_rows``,
    ``only_in_file1/2``, ``differences``). We mint a fresh report id, render
    into ``<reports_dir>/<id>.html``, and return the three response fields.

    Args:
        verdict: The RAW verdict from :func:`compare_db_to_file` (before the
            JSON-safe coercion that collapses DataFrames to counts), so the
            renderer sees the full ``differences`` list.

    Returns:
        ``{"report_uri", "report_url", "report_path"}`` (ADR 0023 §2).
    """
    from src.mcp.resources.reports import (
        reports_dir,
        report_uri_for,
        report_url_for,
    )
    from src.reports.renderers.comparison_renderer import HTMLReporter

    report_id = uuid.uuid4().hex
    report_path = reports_dir() / f"{report_id}.html"
    HTMLReporter().generate(verdict.get("compare", {}) or {}, str(report_path))
    return {
        "report_uri": report_uri_for(report_id),
        "report_url": report_url_for(report_id),
        "report_path": str(report_path),
    }


def db_compare_payload(
    mapping: str,
    actual_file: str,
    table: Optional[str] = None,
    query: Optional[str] = None,
    key_columns: Optional[List[str]] = None,
    db_adapter: Optional[str] = None,
    connection: Optional[Dict[str, Any]] = None,
    include_report: bool = False,
) -> Dict[str, Any]:
    """Compare a database extract against a file and return the verdict.

    Thin adapter over
    :func:`src.services.db_file_compare_service.compare_db_to_file`. The
    *mapping* JSON is loaded here (it supplies the ``fields`` list the service
    needs); the comparison itself, the adapter selection, and the temp-file
    plumbing all live in the service layer.

    Exactly one of *table* / *query* must be supplied — they map onto the
    service's single ``query_or_table`` parameter.

    Args:
        mapping: Path to a mapping JSON file. Must contain a ``fields`` list
            of column definitions (each with a ``name``).
        actual_file: Path to the actual batch file to compare the DB extract
            against. Must exist on disk.
        table: Bare table name to extract from. Mutually exclusive with
            *query*.
        query: A SQL ``SELECT`` statement to extract with. Mutually exclusive
            with *table*.
        key_columns: Optional column name(s) used as join keys during the
            comparison. When omitted, a row-by-row comparison is used.
        db_adapter: Optional adapter-name override (``sqlite`` / ``postgresql``
            / ``oracle``). When omitted, the env-configured ``DB_ADAPTER`` is
            used.
        connection: Optional dict of per-request connection values forwarded
            to :func:`build_connection_override` (e.g. ``db_host``, ``db_user``,
            ``db_password``, ``db_schema``, ``db_path``). Credentials are passed
            straight to the adapter and are NEVER echoed in the response.

    Returns:
        The structured verdict dict with two top-level keys:

        - ``workflow``: ``status`` (``passed`` | ``failed``),
          ``db_rows_extracted``, ``query_or_table``.
        - ``compare``: the full :func:`run_compare_service` output
          (``structure_compatible``, ``matching_rows``, ``only_in_file1`` /
          ``only_in_file2``, ``differences``, row counts).

    Raises:
        ToolError: When *mapping* is missing/blank, the mapping file cannot be
            found / parsed, neither (or both) of *table* / *query* is supplied,
            the *actual_file* does not exist, or the adapter name is invalid.
            A genuine comparison difference is NOT raised — it surfaces in the
            verdict's ``workflow.status``.
    """
    if not mapping or not isinstance(mapping, str):
        raise ToolError("mapping is required and must be a non-empty string")
    if not actual_file or not isinstance(actual_file, str):
        raise ToolError("actual_file is required and must be a non-empty string")

    # Exactly one of table / query — they collapse onto a single service arg.
    if bool(table) == bool(query):
        raise ToolError(
            "exactly one of 'table' or 'query' must be supplied (not both, not neither)"
        )
    query_or_table = query if query else table

    # --- Load the mapping JSON (path only; the service wants a parsed dict) --
    mapping_path = Path(mapping)
    if not mapping_path.exists():
        raise ToolError(f"Mapping file not found: {mapping}")
    try:
        mapping_config = json.loads(mapping_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ToolError(f"Failed to load mapping file: {exc}") from exc

    # --- Assemble the per-request connection override (validates adapter) ---
    try:
        connection_override = build_connection_override(
            db_host=(connection or {}).get("db_host"),
            db_user=(connection or {}).get("db_user"),
            db_password=(connection or {}).get("db_password"),
            db_schema=(connection or {}).get("db_schema"),
            db_adapter=db_adapter,
        )
        # SQLite is file-based: carry an explicit db_path through when supplied
        # alongside (or instead of) the host/user/password trio.
        db_path = (connection or {}).get("db_path")
        if db_path is not None:
            connection_override = {**(connection_override or {}), "db_path": db_path}
            if db_adapter is not None:
                connection_override["db_adapter"] = db_adapter
    except ValueError as exc:
        raise ToolError(str(exc)) from exc

    try:
        verdict = compare_db_to_file(
            query_or_table=query_or_table,
            mapping_config=mapping_config,
            actual_file=actual_file,
            output_format="json",
            key_columns=key_columns or None,
            connection_override=connection_override,
        )
        # S23-4 (#446) / ADR 0023: render the HTML report from the RAW verdict
        # (full ``differences`` list) BEFORE collapsing DataFrames to counts.
        report_fields = (
            _render_db_compare_report(verdict) if include_report else None
        )
        safe = _json_safe_verdict(verdict)
        if report_fields:
            safe.update(report_fields)
        return safe
    except (FileNotFoundError, ValueError) as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface infra failures as ToolError
        logger.exception("db_compare tool failed for mapping=%r", mapping)
        raise ToolError(f"DB compare failed: {exc}") from exc


DB_COMPARE_DESCRIPTION = (
    "Extract rows from a database table or SQL query and compare them against "
    "an actual batch file, returning a structured verdict. Accepts a mapping "
    "JSON path (its 'fields' list names the columns), the actual file path, "
    "exactly one of a table name OR a SQL query, optional key columns for "
    "row matching, an optional db_adapter override (sqlite | postgresql | "
    "oracle), and an optional connection dict for per-request credentials. "
    "Uses whichever backend DB_ADAPTER selects. Returns 'workflow' (status "
    "passed | failed, db_rows_extracted, query_or_table) and 'compare' "
    "(structure_compatible, matching_rows, only_in_file1/2, differences, row "
    "counts). A genuine difference is reported in the verdict (status=failed), "
    "NOT raised. Raises a tool error only for caller-fixable problems: mapping "
    "or actual file not found, neither/both of table/query supplied, or a bad "
    "adapter name. Connection credentials are never echoed in the response."
)
