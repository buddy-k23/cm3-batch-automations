"""CLI command handler for the DB extract → file comparison workflow."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click

from src.services.db_file_compare_service import compare_db_to_file


def run_db_compare_command(
    query_or_table: str,
    mapping: str,
    actual_file: str,
    output_format: str,
    key_columns: str | None,
    output: str | None,
    logger: Any,
) -> None:
    """Execute the DB extract → file comparison workflow from the CLI.

    Loads the mapping JSON from *mapping*, delegates the full workflow to
    :func:`~src.services.db_file_compare_service.compare_db_to_file`, prints
    a human-readable summary, and optionally writes a JSON report.

    Args:
        query_or_table: SQL SELECT statement or bare Oracle table name.
        mapping: Path to the JSON mapping config file.
        actual_file: Path to the actual batch file to compare against.
        output_format: Output format for the report (``"json"`` or ``"html"``).
        key_columns: Comma-separated key column names for row-level matching.
            Pass ``None`` or empty string for row-by-row comparison.
        output: Optional file path to write the JSON result report.
        logger: Logger instance used for error messages.

    Raises:
        SystemExit: On any error (mapping not found, DB failure, etc.).
    """
    # --- Validate mapping file exists before hitting the DB -----------------
    mapping_path = Path(mapping)
    if not mapping_path.exists():
        logger.error(f"Mapping file not found: {mapping}")
        sys.exit(1)

    try:
        mapping_config = json.loads(mapping_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error(f"Failed to load mapping file: {exc}")
        sys.exit(1)

    # --- Delegate to service layer -------------------------------------------
    try:
        result = compare_db_to_file(
            query_or_table=query_or_table,
            mapping_config=mapping_config,
            actual_file=actual_file,
            output_format=output_format,
            key_columns=key_columns or None,
        )
    except FileNotFoundError as exc:
        logger.error(str(exc))
        sys.exit(1)
    except RuntimeError as exc:
        logger.error(f"DB extraction failed: {exc}")
        sys.exit(1)

    # --- Print summary -------------------------------------------------------
    workflow = result.get("workflow", {})
    compare = result.get("compare", {})

    click.echo("\nDB Extract → File Comparison Summary")
    click.echo(f"  Query / Table:      {workflow.get('query_or_table', query_or_table)}")
    click.echo(f"  DB rows extracted:  {workflow.get('db_rows_extracted', 0)}")
    click.echo(f"  Actual file rows:   {compare.get('total_rows_file2', 0)}")
    click.echo(f"  Matching rows:      {compare.get('matching_rows', 0)}")
    click.echo(f"  Only in DB:         {compare.get('only_in_file1', 0)}")
    click.echo(f"  Only in file:       {compare.get('only_in_file2', 0)}")

    rows_with_diffs = compare.get(
        "rows_with_differences", compare.get("differences", 0)
    )
    click.echo(f"  Rows with diffs:    {rows_with_diffs}")

    status = workflow.get("status", "unknown")
    if status == "passed":
        click.echo(click.style("\n  PASS", fg="green"))
    else:
        click.echo(click.style("\n  FAIL", fg="red"))

    # --- Optional report output ----------------------------------------------
    if output:
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        click.echo(f"\nReport written to: {output}")
