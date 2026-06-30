"""Service for Excel <-> DB comparison workflow (S24-2, Sprint 24).

Compares a sheet of *data* from an Excel workbook against a database extract,
in **either direction**, reusing the existing comparison machinery end-to-end:

1. Read the Excel sheet via :func:`src.parsers.excel_reader.read_excel_data`
   (S24-1) — a string-coerced DataFrame (ISO dates, ``.0``-stripped integer
   keys, blank -> ``""``, normalised headers).
2. Extract the DB side via :class:`~src.database.extractor.DataExtractor`
   (backend-agnostic — Oracle / PostgreSQL / SQLite via ``DB_ADAPTER`` or a
   per-request ``connection_override``), then **normalise it to match the Excel
   side**: column names through
   :func:`src.utils.column_names.normalize_column_name`, and every cell through
   the same coercion rules as the Excel reader (DB ``DATE`` -> ISO
   ``YYYY-MM-DD``, integer key -> plain string with no trailing ``.0``, NULL ->
   ``""``).  This is the join-correctness crux: without it, an Excel key
   ``12345`` would never join a DB ``12345`` rendered ``12345.0``/``int``, and an
   Excel date would never equal a DB ``DATE`` carrying a time component.
3. Write each side to a temp pipe-delimited file and delegate to
   :func:`src.services.compare_service.run_compare_service` — reusing the SAME
   result contract the renderers / :class:`HTMLReporter` already consume (no new
   result shape).

Direction (both ways)
---------------------
``direction`` mirrors the db-compare UI vocabulary (``db-to-file`` /
``file-to-db``, "X is source · Y is actual"). Here:

* :data:`EXCEL_AS_ACTUAL` (``"db-source"``) — **DB is source/expected** (file1),
  **Excel is actual** (file2).  ``only_in_file1`` = only-in-DB,
  ``only_in_file2`` = only-in-Excel.
* :data:`DB_AS_ACTUAL` (``"excel-source"``) — **Excel is source/expected**
  (file1), **DB is actual** (file2).  ``only_in_file1`` = only-in-Excel,
  ``only_in_file2`` = only-in-DB.

Whichever side is "source/expected" becomes ``file1`` and the other ``file2``,
flowing into ``run_compare_service`` exactly as ``db_file_compare_service`` does
(which always treats the DB extract as ``file1``).

Reuse, don't duplicate
----------------------
The adapter build (:func:`_build_adapter`), SQL-vs-table detection
(:func:`_is_sql_query`), connection-override assembly
(:func:`build_connection_override`), temp-file write, workflow-status derivation,
and HTML wiring are all imported from
:mod:`src.services.db_file_compare_service` — this service composes them rather
than re-implementing them.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.comparators.backends.factory import resolve_backend
from src.database.extractor import DataExtractor
from src.parsers.excel_reader import _coerce_cell, read_excel_data
from src.services.compare_service import run_compare_service
from src.services.db_file_compare_service import (
    _build_adapter,
    _compare_frames_duckdb,
    _determine_workflow_status,
    _df_to_temp_file,
    _is_sql_query,
    build_connection_override,  # re-exported for the CLI/API layers
)
from src.utils.column_names import normalize_column_name

__all__ = [
    "EXCEL_AS_ACTUAL",
    "DB_AS_ACTUAL",
    "VALID_DIRECTIONS",
    "build_connection_override",
    "compare_excel_to_db",
]

# Direction vocabulary — reuses the db-compare UI's "X is source · Y is actual"
# framing so the labels are consistent for users across CLI / API / UI / MCP.
#  EXCEL_AS_ACTUAL  -> DB is the source-of-truth (file1), Excel is the actual (file2).
#  DB_AS_ACTUAL     -> Excel is the source-of-truth (file1), DB is the actual (file2).
EXCEL_AS_ACTUAL = "db-source"
DB_AS_ACTUAL = "excel-source"
VALID_DIRECTIONS = frozenset({EXCEL_AS_ACTUAL, DB_AS_ACTUAL})


def _coerce_db_value(value: Any) -> str:
    """Coerce a single DB cell to the same stable string the Excel reader emits.

    The Excel reader (S24-1) already returns strings (ISO dates, ``.0``-stripped
    integer keys, blanks as ``""``).  A DB extract, by contrast, returns native
    Python objects — :class:`datetime.date` / :class:`datetime.datetime` for
    ``DATE``/``TIMESTAMP`` columns, :class:`int` for integer keys,
    :class:`decimal.Decimal` for ``NUMBER`` columns, ``None`` for ``NULL`` — so a
    naive ``str()`` would drift (``datetime.date(2024, 3, 17)`` -> the right ISO
    form, but ``datetime.datetime`` carries ``00:00:00``; an ``int`` is fine but
    a float ``12345.0`` is not).  This function renders each native type to the
    Excel reader's canonical form, then defers anything string-like to the shared
    :func:`src.parsers.excel_reader._coerce_cell` so both sides apply *identical*
    string rules.

    Args:
        value: A raw DB cell value from a DataFrame produced by the adapter.

    Returns:
        The cell rendered as a stable string matching the Excel side.
    """
    # NULL / NaN -> "" (matches Excel blank -> "").
    if value is None or (not isinstance(value, (str, date, datetime)) and pd.isna(value)):
        return ""

    # datetime first (datetime is a subclass of date): a pure-midnight datetime
    # is a DATE; render to ISO YYYY-MM-DD. A non-midnight datetime keeps its
    # full ISO form so genuine timestamps are not silently truncated.
    if isinstance(value, datetime):
        if (value.hour, value.minute, value.second, value.microsecond) == (0, 0, 0, 0):
            return value.date().isoformat()
        return value.isoformat(sep=" ")

    # Plain DATE column -> ISO YYYY-MM-DD.
    if isinstance(value, date):
        return value.isoformat()

    # Everything else (int, Decimal, float, str) -> str, then run the shared
    # Excel coercion so "12345.0" -> "12345", "2024-03-17 00:00:00" -> ISO, etc.
    return _coerce_cell(str(value))


def _normalize_db_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise a DB extract to line up with the Excel reader's output.

    Applies the join-correctness normalisations: column names through
    :func:`normalize_column_name` (UPPER + ``-`` -> ``_``) so they match the
    normalised Excel headers, and every cell through :func:`_coerce_db_value` so
    dates/ints/NULLs render identically to the Excel side.

    Args:
        df: Raw DB extract DataFrame from the adapter.

    Returns:
        A new DataFrame with normalised column names and string-coerced cells.
    """
    out = df.copy()
    out.columns = [normalize_column_name(c) for c in out.columns]
    if not out.empty:
        out = out.apply(lambda col: col.map(_coerce_db_value))
    return out


def compare_excel_to_db(
    excel_file: str,
    query_or_table: str,
    *,
    sheet: str | int | None = None,
    header_row: int = 0,
    key_columns: list[str] | str | None = None,
    direction: str = EXCEL_AS_ACTUAL,
    output_format: str = "json",
    output_path: str | None = None,
    connection_override: dict[str, Any] | None = None,
    backend: str | None = None,
) -> dict[str, Any]:
    """Compare a sheet of Excel data against a DB extract, in either direction.

    Reads the Excel sheet via :func:`~src.parsers.excel_reader.read_excel_data`,
    extracts the DB side via :class:`~src.database.extractor.DataExtractor`
    (honouring ``DB_ADAPTER`` and any *connection_override*), normalises the DB
    side to match the Excel string/ISO-date/integer coercions, writes both to
    temp pipe-delimited files, and delegates to
    :func:`~src.services.compare_service.run_compare_service` — returning the same
    result contract the renderers consume.

    Args:
        excel_file: Path to the ``.xlsx`` / ``.xls`` workbook.
        query_or_table: A SQL SELECT statement or a bare table name for the DB
            side (SQL detected via :func:`_is_sql_query`).
        sheet: Excel sheet selector — name (``str``), zero-based index (``int``),
            or ``None`` for the first sheet.
        header_row: Zero-based header row index for the Excel read.
        key_columns: Column name(s) used as join keys (comma-separated string or
            list). Normalised to the canonical UPPER/underscore form so they line
            up with both normalised sides. ``None`` -> row-by-row comparison.
        direction: One of :data:`EXCEL_AS_ACTUAL` (``"db-source"`` — DB is
            source/expected, Excel is actual) or :data:`DB_AS_ACTUAL`
            (``"excel-source"`` — Excel is source/expected, DB is actual).
            Decides which side is ``file1`` vs ``file2`` in the comparison.
        output_format: ``"json"`` or ``"html"``.  ``"html"`` (or an
            *output_path* ending ``.html``) renders an HTML report via the
            reused :class:`~src.reports.renderers.comparison_renderer.HTMLReporter`.
        output_path: Optional path for an HTML report.  When set and the resolved
            format is HTML, the report is written and its path recorded under the
            result's ``report_path`` key.
        connection_override: Optional per-request DB connection dict
            (``db_adapter`` + backend-specific connection values) — see
            :func:`src.services.db_file_compare_service.compare_db_to_file`.
            Credentials are passed straight to the adapter and never logged or
            returned in the result.
        backend: Optional explicit comparison-backend name (``native`` /
            ``pandas`` / ``duckdb`` / ``auto``), resolved by
            :func:`~src.comparators.backends.factory.resolve_backend`
            (explicit arg → ``COMPARISON_BACKEND`` env → ``native`` default).
            When the resolved backend is ``duckdb`` (S25-5) **and** key columns
            are supplied, the already-in-memory Excel frame and the normalised DB
            extract are registered **directly** into DuckDB and diffed — skipping
            both ``_df_to_temp_file`` writes — producing the **identical** result
            contract.  When ``native`` (the default), the two-temp-file path runs
            **unchanged**.  ``duckdb`` stays optional/lazy: when the package is
            absent, ``auto`` resolves to ``native`` (no import, no error).

    Returns:
        Dict with two top-level keys:

        * ``workflow`` — ``status`` (``"passed"``/``"failed"``),
          ``db_rows_extracted``, ``excel_rows_read``, ``query_or_table``,
          ``direction``.
        * ``compare`` — the full :func:`run_compare_service` output.

        Plus ``report_path`` when an HTML report is rendered.

    Raises:
        FileNotFoundError: When *excel_file* does not exist.
        ValueError: When *direction* is not a recognised value, or
            ``connection_override['db_adapter']`` is unrecognised.
        RuntimeError: When DB extraction fails (propagated from the adapter).
    """
    # --- Input validation ---------------------------------------------------
    if direction not in VALID_DIRECTIONS:
        raise ValueError(
            f"Invalid direction '{direction}'. Must be one of: "
            f"{', '.join(sorted(VALID_DIRECTIONS))}"
        )

    excel_path = Path(excel_file)
    if not excel_path.exists():
        raise FileNotFoundError(f"Excel file not found: {excel_file}")

    # --- Normalise key_columns to the canonical form -----------------------
    if isinstance(key_columns, str):
        raw_keys = [k.strip() for k in key_columns.split(",") if k.strip()]
    else:
        raw_keys = list(key_columns) if key_columns else []
    keys_list = [normalize_column_name(k) for k in raw_keys]
    keys_str = ",".join(keys_list) if keys_list else None

    # --- Excel side (S24-1) -------------------------------------------------
    excel_df = read_excel_data(
        excel_path,
        sheet=sheet,
        header_row=header_row,
        normalize_headers=True,
    )
    excel_rows_read = len(excel_df)

    # --- DB side ------------------------------------------------------------
    adapter = _build_adapter(connection_override)
    with adapter:
        extractor = DataExtractor(adapter)
        if _is_sql_query(query_or_table):
            db_df = extractor.extract_by_query(query_or_table)
        else:
            db_df = extractor.extract_table(query_or_table)
    db_rows_extracted = len(db_df)

    # Normalise the DB side to match the Excel coercions (the join-correctness
    # crux): normalised column names + ISO dates + plain-string integer keys.
    db_df = _normalize_db_frame(db_df)

    # --- Direction: choose which side is file1 (source) vs file2 (actual) ---
    # EXCEL_AS_ACTUAL ("db-source") -> DB is source (file1), Excel is actual (file2).
    # DB_AS_ACTUAL    ("excel-source") -> Excel is source (file1), DB is actual (file2).
    if direction == EXCEL_AS_ACTUAL:
        file1_df, file2_df = db_df, excel_df
    else:
        file1_df, file2_df = excel_df, db_df

    # --- Backend selection (S25-5) ------------------------------------------
    # Both sides are already in memory.  Resolve the active backend; the ``auto``
    # size probe uses the Excel file path on both sides (the DB extract has no
    # file), so ``auto`` only upgrades to duckdb when duckdb is importable AND the
    # Excel file is large.
    resolved_backend = resolve_backend(str(excel_path), str(excel_path), backend)

    # The DuckDB frame-direct path requires key columns; use it only when duckdb
    # is active AND keys are present, else fall through to the unchanged
    # two-temp-file + native path.
    use_frame_direct = resolved_backend == "duckdb" and bool(keys_list)

    if use_frame_direct:
        # Zero temp files: register the two in-memory frames straight into DuckDB.
        # Identical result contract to the two-temp-file + native path.
        compare_result = _compare_frames_duckdb(file1_df, file2_df, keys_list)
    else:
        # --- Native path (unchanged): write both sides to temp files ---------
        temp1: str | None = None
        temp2: str | None = None
        try:
            temp1 = _df_to_temp_file(file1_df)
            temp2 = _df_to_temp_file(file2_df)
            compare_result = run_compare_service(
                file1=temp1,
                file2=temp2,
                keys=keys_str,
                mapping=None,
                detailed=True,
                backend=resolved_backend,
            )
        finally:
            for tmp in (temp1, temp2):
                if tmp:
                    try:
                        Path(tmp).unlink(missing_ok=True)
                    except OSError:
                        pass

    # --- Build unified result ----------------------------------------------
    workflow_status = _determine_workflow_status(compare_result)
    result: dict[str, Any] = {
        "workflow": {
            "status": workflow_status,
            "db_rows_extracted": db_rows_extracted,
            "excel_rows_read": excel_rows_read,
            "query_or_table": query_or_table,
            "direction": direction,
        },
        "compare": compare_result,
    }

    # --- Optional HTML report (reuse the file-compare HTMLReporter) ---------
    if output_path:
        wants_html = output_format == "html" or output_path.lower().endswith(".html")
        if wants_html:
            from src.reports.renderers.comparison_renderer import HTMLReporter

            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            HTMLReporter().generate(compare_result, str(out))
            result["report_path"] = str(out)

    return result
