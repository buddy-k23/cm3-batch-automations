"""Service for DB extract → file comparison workflow.

Orchestrates three steps:
1. Extract data from Oracle using a SQL query or table name.
2. Write the extracted DataFrame to a temp pipe-delimited file.
3. Compare that file against an actual batch file using the standard
   run_compare_service pipeline.

The result dict always contains two top-level keys:
- ``workflow`` — metadata about the extraction step.
- ``compare`` — the raw output from run_compare_service.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

from src.database.connection import OracleConnection
from src.database.extractor import DataExtractor
from src.services.compare_service import run_compare_service
from src.transforms.transform_orchestrator import TransformEngine

# SQL keywords that unambiguously identify a query string vs. a table name.
_SQL_KEYWORDS = frozenset(["select", "with", "from"])


def _is_sql_query(query_or_table: str) -> bool:
    """Return True when *query_or_table* appears to be a SQL statement.

    Detection is based on the presence of SQL keywords (SELECT, WITH, FROM)
    as the first or any token in the lowercased string. A plain table name
    such as ``SHAW_SRC_P327`` or ``CM3INT.FOO`` contains no such keywords.

    Args:
        query_or_table: Either a bare table name or a SQL SELECT statement.

    Returns:
        True if the string contains SQL keyword tokens, False otherwise.
    """
    tokens = set(query_or_table.lower().split())
    return bool(tokens & _SQL_KEYWORDS)


def _df_to_temp_file(df: pd.DataFrame, delimiter: str = "|") -> str:
    """Write a DataFrame to a named temp pipe-delimited file.

    The file is created in the system temp directory with a ``.txt`` suffix.
    The caller is responsible for deleting the file when finished.

    Args:
        df: DataFrame to serialise.
        delimiter: Column separator. Defaults to ``"|"``.

    Returns:
        Absolute path string of the created temp file.
    """
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".txt",
        delete=False,
        encoding="utf-8",
    ) as fh:
        df.to_csv(fh, sep=delimiter, index=False)
        return fh.name


def _determine_workflow_status(compare_result: dict[str, Any]) -> str:
    """Derive a pass/fail status string from compare service output.

    A result is considered 'passed' when:
    - The files are structurally compatible, AND
    - There are zero rows with differences, zero rows only in file 1, and
      zero rows only in file 2.

    Args:
        compare_result: Raw dict returned by run_compare_service.

    Returns:
        ``"passed"`` or ``"failed"``.
    """
    if not compare_result.get("structure_compatible", True):
        return "failed"

    rows_with_diffs = compare_result.get(
        "rows_with_differences",
        compare_result.get("differences", 0),
    )
    only_in_1 = compare_result.get("only_in_file1", 0)
    only_in_2 = compare_result.get("only_in_file2", 0)

    if rows_with_diffs or only_in_1 or only_in_2:
        return "failed"
    return "passed"


def compare_db_to_file(
    query_or_table: str,
    mapping_config: dict[str, Any],
    actual_file: str,
    output_format: str = "json",
    key_columns: list[str] | str | None = None,
    apply_transforms: bool = False,
) -> dict[str, Any]:
    """Extract data from Oracle, format it, and compare against an actual batch file.

    Workflow:
    1. Validate inputs (actual_file must exist).
    2. Connect to Oracle via environment variables and extract data using
       either a SQL query or a table name.
    3. Optionally apply field-level transforms to each DB row via
       :class:`~src.transforms.transform_orchestrator.TransformEngine`.
    4. Write the (possibly transformed) rows to a temporary pipe-delimited file.
    5. Delegate to :func:`~src.services.compare_service.run_compare_service`
       for the structural and row-level comparison.
    6. Clean up the temp file.
    7. Return a unified result dict with ``workflow`` and ``compare`` sections.

    Args:
        query_or_table: A SQL SELECT statement or a bare Oracle table name.
        mapping_config: Parsed mapping JSON dict (must contain a ``fields``
            list with ``name`` entries).
        actual_file: Path to the actual batch file to compare against.
        output_format: Desired output format for downstream use (``"json"``
            or ``"html"``). Currently informational only.
        key_columns: Column name(s) used as join keys during comparison.
            May be a comma-separated string or a list. When None, row-by-row
            comparison is used.
        apply_transforms: When ``True``, each DB row is passed through
            :class:`~src.transforms.transform_orchestrator.TransformEngine`
            before comparison, applying the field-level transforms defined in
            *mapping_config*.  Defaults to ``False`` (no transformation).

    Returns:
        Dict with two top-level keys:

        - ``workflow``: status, db_rows_extracted, query_or_table
        - ``compare``: full output of run_compare_service

    Raises:
        FileNotFoundError: When *actual_file* does not exist on disk.
        RuntimeError: When Oracle extraction fails (propagated from
            DataExtractor).
        ValueError: When mapping_config contains no ``fields`` list.
    """
    # --- Input validation ---------------------------------------------------
    actual_path = Path(actual_file)
    if not actual_path.exists():
        raise FileNotFoundError(f"Actual file not found: {actual_file}")

    if not mapping_config.get("fields"):
        raise ValueError("mapping_config must contain a 'fields' list")

    # --- Normalise key_columns ----------------------------------------------
    if isinstance(key_columns, str):
        key_columns_list: list[str] | None = [k.strip() for k in key_columns.split(",") if k.strip()]
    else:
        key_columns_list = list(key_columns) if key_columns else None

    keys_str = ",".join(key_columns_list) if key_columns_list else None

    # --- DB Extraction -------------------------------------------------------
    connection = OracleConnection.from_env()
    extractor = DataExtractor(connection)

    if _is_sql_query(query_or_table):
        df = extractor.extract_by_query(query_or_table)
    else:
        df = extractor.extract_table(query_or_table)

    db_rows_extracted = len(df)

    # --- Optionally apply field transforms to each row ----------------------
    transform_details: list | None = None
    if apply_transforms:
        engine = TransformEngine(mapping_config)
        raw_rows = df.to_dict(orient="records")
        transformed_rows = []
        transform_details = []
        for raw_row in raw_rows:
            transformed = engine.apply(raw_row)
            transformed_rows.append(transformed)
            for field_name in transformed:
                transform_details.append({
                    "field": field_name,
                    "source_value": str(raw_row.get(field_name, "")),
                    "transformed_value": str(transformed.get(field_name, "")),
                    "file_value": "",  # populated post-comparison if available
                })
        df = pd.DataFrame(transformed_rows)

    # --- Write DB data to temp file -----------------------------------------
    temp_path: str | None = None
    try:
        temp_path = _df_to_temp_file(df)

        # --- Comparison ------------------------------------------------------
        compare_result = run_compare_service(
            file1=temp_path,
            file2=str(actual_path),
            keys=keys_str,
            mapping=None,  # mapping_config is already parsed; not a file path
            detailed=True,
        )
    finally:
        if temp_path:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except OSError:
                pass

    # --- Build unified result ------------------------------------------------
    workflow_status = _determine_workflow_status(compare_result)

    result: dict[str, Any] = {
        "workflow": {
            "status": workflow_status,
            "db_rows_extracted": db_rows_extracted,
            "query_or_table": query_or_table,
        },
        "compare": compare_result,
    }
    if transform_details is not None:
        result["transform_details"] = transform_details
    return result
