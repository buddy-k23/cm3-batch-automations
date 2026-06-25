"""Excel<->DB-compare MCP tool implementation (S24-4, Sprint 24).

Wires the ``excel_db_compare`` MCP tool onto the existing Excel<->DB
reconciliation service
(:func:`src.services.excel_db_compare_service.compare_excel_to_db`, S24-2). Like
the other action tools (S21-1 ``db_compare`` / #407 ``reconcile_mapping``) this
module is a thin adapter — NO comparison logic lives here; the tool registration
itself stays in :mod:`src.mcp.server`.

The tool lets an agent compare a sheet of *data* from an Excel workbook against
a database extract (table or SQL query) on whichever backend ``DB_ADAPTER``
selects (SQLite / PostgreSQL / Oracle), in EITHER direction
(``db-source`` — DB is source/expected; ``excel-source`` — Excel is
source/expected), returning a BOUNDED verdict.

A genuine comparison difference is a RESULT (``workflow.status == "failed"``),
not an error. Tool errors are reserved for caller-fixable problems: the Excel
file does not exist, no table/query is supplied (or both are), the direction is
invalid, or the adapter name is invalid.

Redaction posture (ADR 0023 — parity with, and tighter than, ``db_compare``):
the response carries ONLY bounded counts (row counts, matching / only-in /
differences counts), the structure-compatibility flag, the workflow status, and
— when an HTML report is requested — a retrievable report HANDLE (a
``report://`` URI / path), never inline rows. Unlike ``db_compare`` (which
forwards the raw ``differences`` list and its field values), this tool collapses
``only_in_*`` AND ``differences`` to integer counts so NO raw row / cell value
ever crosses the transport. Per-request connection values (``connection``) — in
particular ``db_password`` — are forwarded to the adapter and are NEVER echoed
back in the tool response.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp.exceptions import ToolError

from src.services.db_file_compare_service import build_connection_override
from src.services.excel_db_compare_service import (
    DB_AS_ACTUAL,
    EXCEL_AS_ACTUAL,
    VALID_DIRECTIONS,
    compare_excel_to_db,
)

__all__ = [
    "excel_db_compare_payload",
    "EXCEL_DB_COMPARE_DESCRIPTION",
]

logger = logging.getLogger(__name__)


def _as_count(value: Any) -> int:
    """Return an integer count for a verdict field that may be a list/frame.

    The :func:`run_compare_service` contract carries ``only_in_file1`` /
    ``only_in_file2`` as :class:`pandas.DataFrame` objects and ``differences``
    as a list of per-row diff dicts (each holding RAW field values). For the
    bounded, no-PII MCP response we only ever expose their cardinality — never
    their contents. ``len()`` covers DataFrame / list / dict / str uniformly;
    an already-int value (some paths pre-count) passes straight through.

    Args:
        value: A verdict field (DataFrame, list, dict, int, or None).

    Returns:
        The element count as a plain ``int`` (``0`` for ``None``).
    """
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    try:
        return len(value)
    except TypeError:
        return 0


def _bounded_compare(compare: Dict[str, Any], direction: str) -> Dict[str, Any]:
    """Collapse the raw compare result to a bounded, no-PII counts summary.

    Builds the MCP-safe ``compare`` sub-dict: every figure is an integer count
    and NO raw row / cell value is carried through. In addition to the
    contract's native ``only_in_file1`` / ``only_in_file2`` keys (kept for REST
    / CLI parity), direction-aware aliases ``only_in_db`` / ``only_in_excel``
    are added so an agent never has to remember which side is ``file1``:

    * ``db-source`` (DB is source/file1): ``only_in_file1`` -> only-in-DB,
      ``only_in_file2`` -> only-in-Excel.
    * ``excel-source`` (Excel is source/file1): ``only_in_file1`` ->
      only-in-Excel, ``only_in_file2`` -> only-in-DB.

    Args:
        compare: The raw ``run_compare_service`` output sub-dict.
        direction: The resolved direction constant (``db-source`` /
            ``excel-source``).

    Returns:
        A JSON-safe dict of bounded counts plus the structure-compatibility
        flag — never any raw row data.
    """
    only_in_file1 = _as_count(compare.get("only_in_file1"))
    only_in_file2 = _as_count(compare.get("only_in_file2"))

    if direction == EXCEL_AS_ACTUAL:  # "db-source" — DB is file1
        only_in_db, only_in_excel = only_in_file1, only_in_file2
    else:  # "excel-source" — Excel is file1
        only_in_excel, only_in_db = only_in_file1, only_in_file2

    return {
        "structure_compatible": bool(compare.get("structure_compatible", False)),
        "total_rows_file1": _as_count(compare.get("total_rows_file1")),
        "total_rows_file2": _as_count(compare.get("total_rows_file2")),
        "matching_rows": _as_count(compare.get("matching_rows")),
        "only_in_file1": only_in_file1,
        "only_in_file2": only_in_file2,
        "only_in_db": only_in_db,
        "only_in_excel": only_in_excel,
        "differences": _as_count(compare.get("differences")),
    }


def _render_report(result: Dict[str, Any]) -> Dict[str, str]:
    """Render the comparison HTML report and return the ADR 0023 report fields.

    Mirrors :func:`src.mcp.db_compare_tools._render_db_compare_report`: the
    Excel<->DB compare reuses the SAME comparison-style renderer
    (:class:`~src.reports.renderers.comparison_renderer.HTMLReporter`) because
    the service emits the same ``run_compare_service`` result contract. We mint
    a fresh report id, render into ``<reports_dir>/<id>.html`` from the RAW
    ``compare`` sub-dict (full ``differences`` so the on-disk report is
    complete), and return the three response handle fields — NOT inline rows.

    Args:
        result: The RAW verdict from :func:`compare_excel_to_db` (before the
            bounded coercion), so the renderer sees the full ``differences``.

    Returns:
        ``{"report_uri", "report_url", "report_path"}`` (ADR 0023 §2).
    """
    from src.mcp.resources.reports import (
        report_uri_for,
        report_url_for,
        reports_dir,
    )
    from src.reports.renderers.comparison_renderer import HTMLReporter

    report_id = uuid.uuid4().hex
    report_path = reports_dir() / f"{report_id}.html"
    HTMLReporter().generate(result.get("compare", {}) or {}, str(report_path))
    return {
        "report_uri": report_uri_for(report_id),
        "report_url": report_url_for(report_id),
        "report_path": str(report_path),
    }


def excel_db_compare_payload(
    excel_file: str,
    table: Optional[str] = None,
    query: Optional[str] = None,
    sheet: Optional[str] = None,
    header_row: int = 0,
    key_columns: Optional[List[str]] = None,
    direction: str = EXCEL_AS_ACTUAL,
    db_adapter: Optional[str] = None,
    connection: Optional[Dict[str, Any]] = None,
    include_report: bool = False,
) -> Dict[str, Any]:
    """Compare an Excel sheet against a database extract and return the verdict.

    Thin adapter over
    :func:`src.services.excel_db_compare_service.compare_excel_to_db` (S24-2).
    The Excel read, the DB extract, the both-side normalisation, the
    comparison, and any HTML rendering all live in the service layer; this
    adapter only validates caller-fixable args, assembles the connection
    override, and BOUNDS the response (counts only, no raw rows, no secrets).

    Exactly one of *table* / *query* must be supplied — they map onto the
    service's single ``query_or_table`` parameter.

    Args:
        excel_file: Path to the ``.xlsx`` / ``.xls`` workbook to read. Must
            exist on disk.
        table: Bare table name to extract from. Mutually exclusive with
            *query*.
        query: A SQL ``SELECT`` statement to extract with. Mutually exclusive
            with *table*.
        sheet: Excel sheet selector — a sheet NAME. ``None`` reads the first
            sheet. (The service also accepts a numeric index; the MCP surface
            keeps this a name/None for a simpler agent contract.)
        header_row: Zero-based header row index for the Excel read.
        key_columns: Optional column name(s) used as join keys during the
            comparison. When omitted, a row-by-row comparison is used.
        direction: One of ``"db-source"`` (DB is source/expected, Excel is
            actual) or ``"excel-source"`` (Excel is source/expected, DB is
            actual). Defaults to ``"db-source"``.
        db_adapter: Optional adapter-name override (``sqlite`` / ``postgresql``
            / ``oracle``). When omitted, the env-configured ``DB_ADAPTER`` is
            used.
        connection: Optional dict of per-request connection values forwarded
            to :func:`build_connection_override` (e.g. ``db_host``, ``db_user``,
            ``db_password``, ``db_schema``, ``db_path``). Credentials are passed
            straight to the adapter and are NEVER echoed in the response.
        include_report: When ``True`` an HTML report is rendered and a
            retrievable report HANDLE (``report_uri`` / ``report_url`` /
            ``report_path``) is added to the response — never inline rows
            (ADR 0023).

    Returns:
        A bounded, JSON-safe verdict dict with two top-level keys:

        - ``workflow``: ``status`` (``passed`` | ``failed``),
          ``db_rows_extracted``, ``excel_rows_read``, ``query_or_table``,
          ``direction``.
        - ``compare``: bounded counts only — ``structure_compatible``,
          ``total_rows_file1`` / ``total_rows_file2``, ``matching_rows``,
          ``only_in_file1`` / ``only_in_file2`` (REST parity) plus
          direction-aware ``only_in_db`` / ``only_in_excel``, and
          ``differences`` (a COUNT, never the raw diff rows).

        Plus ``report_uri`` / ``report_url`` / ``report_path`` when
        *include_report* is ``True``.

    Raises:
        ToolError: When *excel_file* is missing/blank or not found, neither (or
            both) of *table* / *query* is supplied, *direction* is invalid, or
            the adapter name is invalid. A genuine comparison difference is NOT
            raised — it surfaces in the verdict's ``workflow.status``.
    """
    if not excel_file or not isinstance(excel_file, str):
        raise ToolError("excel_file is required and must be a non-empty string")

    # Exactly one of table / query — they collapse onto a single service arg.
    if bool(table) == bool(query):
        raise ToolError(
            "exactly one of 'table' or 'query' must be supplied (not both, not neither)"
        )
    query_or_table = query if query else table

    if direction not in VALID_DIRECTIONS:
        raise ToolError(
            f"invalid direction '{direction}'. Must be one of: "
            f"{', '.join(sorted(VALID_DIRECTIONS))}"
        )

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
        verdict = compare_excel_to_db(
            excel_file=excel_file,
            query_or_table=query_or_table,
            sheet=sheet,
            header_row=header_row,
            key_columns=key_columns or None,
            direction=direction,
            output_format="json",
            connection_override=connection_override,
        )
    except FileNotFoundError as exc:
        raise ToolError(str(exc)) from exc
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface infra failures as ToolError
        logger.exception("excel_db_compare tool failed for excel_file=%r", excel_file)
        raise ToolError(f"Excel<->DB compare failed: {exc}") from exc

    # --- Build the BOUNDED response (counts only; no raw rows; no secrets) ---
    workflow = dict(verdict.get("workflow", {}) or {})
    bounded: Dict[str, Any] = {
        "workflow": workflow,
        "compare": _bounded_compare(verdict.get("compare", {}) or {}, direction),
    }

    # ADR 0023: render from the RAW verdict (full differences) BEFORE bounding,
    # then attach only the retrievable report HANDLE — never inline rows.
    if include_report:
        bounded.update(_render_report(verdict))

    # Final pass: collapse any residual non-native scalar (e.g. numpy.int64)
    # so the transport never chokes on a NumPy type.
    return json.loads(json.dumps(bounded, default=str))


EXCEL_DB_COMPARE_DESCRIPTION = (
    "Compare a sheet of data from an Excel workbook against a database table "
    "or SQL query extract, in either direction, returning a bounded verdict. "
    "Accepts the Excel file path, exactly one of a table name OR a SQL query, "
    "an optional sheet name and header_row, optional key columns for row "
    "matching, a direction ('db-source' = DB is source/expected, Excel is "
    "actual | 'excel-source' = Excel is source/expected, DB is actual), an "
    "optional db_adapter override (sqlite | postgresql | oracle), an optional "
    "connection dict for per-request credentials, and include_report. Uses "
    "whichever backend DB_ADAPTER selects. Returns 'workflow' (status passed | "
    "failed, db_rows_extracted, excel_rows_read, query_or_table, direction) and "
    "'compare' (structure_compatible, matching_rows, only_in_db, only_in_excel, "
    "differences — all COUNTS, never raw rows). A genuine difference is reported "
    "in the verdict (status=failed), NOT raised. Raises a tool error only for "
    "caller-fixable problems: Excel file not found, neither/both of table/query "
    "supplied, a bad direction, or a bad adapter name. The response carries no "
    "raw row values and no connection credentials."
)
