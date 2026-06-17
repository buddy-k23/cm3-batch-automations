"""Extract MCP tool implementation (S21-5, #437).

Wires the ``extract_table`` MCP tool onto the existing adapter-based extract
path — the same :class:`~src.database.extractor.DataExtractor` (S15-1, #405)
that the ``valdo extract`` CLI command
(:func:`src.commands.extract_command.run_extract_command`) drives. Like the
other action tools (S21-1 ``db_compare`` / S21-2 ``reconcile_all`` / S21-3
``mask_file`` / S21-4 ``detect_drift``) this module is a thin adapter — no
extraction logic lives here; the SQL building, the backend selection via the
S15 adapter factory (ADR 0022 §4), and the file writing all stay in
:class:`DataExtractor` and the chosen adapter. The tool registration itself
stays in :mod:`src.mcp.server`.

Backend-agnostic (ADR 0022 §4): the active database is selected by the
``DB_ADAPTER`` environment variable (``oracle`` | ``postgresql`` | ``sqlite``)
via the adapter factory, optionally overridden per-call with ``db_adapter``.

Security (S13.5-4, #410 — preserved on the MCP path):
    Because every mode routes through the same :class:`DataExtractor`, the
    S13.5-4 SQL hardening carries through unchanged: SQL identifiers (table /
    column names) are *allow-listed* by
    :func:`~src.database.extractor._validate_identifier`, the row ``limit`` is
    *bound* as a parameter (never interpolated) and must be a positive integer,
    and a raw free-form ``WHERE`` clause is rejected on the table-based path.
    A malicious table/column (e.g. ``"X; DROP TABLE Y"``) is therefore rejected
    before any SQL executes. The tool surfaces those as caller-fixable
    :class:`ToolError`s.

The response carries only the output-file PATH and the extracted row count —
never any extracted row data, and no database credentials or connection
secrets.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp.exceptions import ToolError

__all__ = [
    "extract_table_payload",
    "EXTRACT_TABLE_DESCRIPTION",
]

logger = logging.getLogger(__name__)


def extract_table_payload(
    table: Optional[str] = None,
    query: Optional[str] = None,
    columns: Optional[List[str]] = None,
    limit: Optional[int] = None,
    output: str = "",
    delimiter: str = "|",
    db_adapter: Optional[str] = None,
) -> Dict[str, Any]:
    """Extract DB rows to a flat file and return the output path + row count.

    Thin adapter over :class:`src.database.extractor.DataExtractor` (the same
    code path :func:`src.commands.extract_command.run_extract_command` drives).
    The backend is resolved by the adapter factory
    (:func:`~src.database.adapters.factory.get_database_adapter`) from
    ``DB_ADAPTER`` (or the optional *db_adapter* override) and connected for the
    call's lifetime via a context manager that guarantees disconnect.

    Exactly one of *table* / *query* must be supplied (mutually exclusive
    modes). *columns* and *limit* apply only to table mode. No extraction logic
    lives here — the S13.5-4 identifier/limit hardening fires inside
    :class:`DataExtractor`, so a malicious table/column/limit is rejected on
    this path too.

    Args:
        table: Bare (optionally schema-qualified) table name to extract from.
            Allow-list validated inside :class:`DataExtractor`. Mutually
            exclusive with *query*.
        query: A full SQL ``SELECT`` statement to extract with (the explicit
            "operator supplies the whole statement" entry point). Mutually
            exclusive with *table*.
        columns: Optional list of column names to project (table mode only;
            each name allow-list validated). When omitted, all columns (``*``)
            are extracted.
        limit: Optional positive-integer row cap (table mode only; bound as a
            parameter, never interpolated). Rejected if not a positive integer.
        output: Output flat-file path (required). The file is written by the
            adapter in the requested delimited format.
        delimiter: Output field delimiter. Defaults to ``"|"``.
        db_adapter: Optional adapter-name override (``sqlite`` / ``postgresql``
            / ``oracle``). When omitted, the env-configured ``DB_ADAPTER`` is
            used. SQLite reads its ``DB_PATH`` from the environment.

    Returns:
        A dict with:

        - ``output_file``: the path the rows were written to (echoes *output*).
        - ``row_count``: the number of data rows extracted (int).
        - ``mode``: ``"table"`` or ``"query"`` — which extraction path ran.
        - ``db_adapter``: the resolved backend name.

        The response carries NO extracted row data and NO credentials.

    Raises:
        ToolError: For caller-fixable problems: *output* missing/blank, neither
            (or both) of *table* / *query* supplied, an invalid adapter name, a
            malicious / invalid SQL identifier (table or column), a non-positive
            *limit*, or an extraction failure. These are the S13.5-4 rejections
            and the ordinary validation failures surfaced as tool errors.
    """
    # --- Argument validation (caller-fixable) --------------------------------
    if not output or not isinstance(output, str):
        raise ToolError("output is required and must be a non-empty string")

    # Exactly one of table / query — they select mutually-exclusive modes.
    if bool(table) == bool(query):
        raise ToolError(
            "exactly one of 'table' or 'query' must be supplied (not both, not neither)"
        )

    # Imported lazily so a missing optional DB driver fails at connect time
    # (inside the factory/adapter) rather than at module import.
    from src.database.adapters.factory import get_database_adapter
    from src.database.extractor import DataExtractor

    # Resolve the backend (validates the adapter name) before connecting.
    try:
        adapter = get_database_adapter(db_adapter)
    except ValueError as exc:
        # Bad adapter name is caller-fixable.
        raise ToolError(str(exc)) from exc

    resolved_adapter = db_adapter or os.getenv("DB_ADAPTER", "oracle")

    try:
        with adapter:
            extractor = DataExtractor(adapter)

            if query:
                mode = "query"
                stats = extractor.extract_to_file(
                    output_file=output, query=query, delimiter=delimiter
                )
                row_count = stats["total_rows"]
            else:
                # Table mode. limit (table-only) goes through extract_table so
                # the limit is bound as a parameter; otherwise extract_to_file.
                mode = "table"
                if limit is not None:
                    df = extractor.extract_table(table, columns=columns, limit=limit)
                    df.to_csv(output, sep=delimiter, index=False, header=False)
                    row_count = len(df)
                else:
                    stats = extractor.extract_to_file(
                        table_name=table,
                        output_file=output,
                        columns=columns,
                        delimiter=delimiter,
                    )
                    row_count = stats["total_rows"]
    except ValueError as exc:
        # S13.5-4 identifier/limit/raw-WHERE rejections (IdentifierValidationError
        # is a ValueError subclass) and other validation failures — all
        # caller-fixable. The message never carries credentials.
        raise ToolError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface infra failures as ToolError
        # Log internally but keep the response free of connection secrets.
        logger.exception("extract_table tool failed for output=%r", output)
        raise ToolError(f"Extraction failed: {exc}") from exc

    return {
        "output_file": output,
        "row_count": int(row_count),
        "mode": mode,
        "db_adapter": resolved_adapter,
    }


EXTRACT_TABLE_DESCRIPTION = (
    "Extract rows from a database table or SQL query to a delimited flat file, "
    "returning the output file path and the extracted row count. Accepts "
    "exactly one of a table name OR a SQL SELECT query, an output file path "
    "(required), an optional column list and positive-integer row limit (table "
    "mode only), an optional output delimiter (default '|'), and an optional "
    "db_adapter override (sqlite | postgresql | oracle). Uses whichever backend "
    "DB_ADAPTER selects. Wraps the existing adapter-based DataExtractor — the "
    "same code path the 'valdo extract' CLI uses. SQL hardening is preserved on "
    "this path: identifiers (table/column names) are allow-listed, the limit is "
    "bound as a parameter, and raw WHERE clauses are rejected — a malicious "
    "table/column is refused before any SQL runs. Returns 'output_file', "
    "'row_count', 'mode' (table | query), and the resolved 'db_adapter'. The "
    "response carries no extracted row data and no database credentials. Raises "
    "a tool error only for caller-fixable problems: a missing output path, "
    "neither/both of table/query supplied, a bad adapter name, an invalid SQL "
    "identifier, a non-positive limit, or an extraction failure."
)
