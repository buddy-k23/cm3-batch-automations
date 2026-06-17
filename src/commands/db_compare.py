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
    apply_transforms: bool = False,
    connection_override: dict[str, Any] | None = None,
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
        output: Optional file path to write the result report.  A ``.html``
            path (or ``output_format == "html"``) writes a real HTML comparison
            report; any other extension writes machine JSON — the consistent
            output contract (``.html`` -> HTML, ``.json`` -> JSON).
        logger: Logger instance used for error messages.
        apply_transforms: When ``True``, field-level transforms defined in
            the mapping are applied to each DB row before comparison.
            Defaults to ``False``.
        connection_override: Optional per-request DB connection override passed
            straight through to
            :func:`~src.services.db_file_compare_service.compare_db_to_file`
            (e.g. ``{"db_adapter": "sqlite", "db_path": ...}``).

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

    # --- Resolve the output contract (.html -> HTML, else JSON) --------------
    # When the user requests an HTML report, the service renders it directly
    # (reusing the file-compare HTMLReporter); JSON is written by this command.
    wants_html = bool(output) and (
        output_format == "html" or str(output).lower().endswith(".html")
    )
    html_output_path = output if wants_html else None

    # --- Delegate to service layer -------------------------------------------
    try:
        result = compare_db_to_file(
            query_or_table=query_or_table,
            mapping_config=mapping_config,
            actual_file=actual_file,
            output_format=output_format,
            key_columns=key_columns or None,
            apply_transforms=apply_transforms,
            connection_override=connection_override,
            output_path=html_output_path,
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
    # HTML reports are rendered by the service (path recorded in
    # ``result['report_path']``); JSON reports are written here.  This keeps the
    # consistent output contract: ``.html`` -> HTML, anything else -> JSON.
    if output:
        if wants_html:
            click.echo(f"\nReport written to: {result.get('report_path', output)}")
        else:
            output_path = Path(output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(result, indent=2, default=str), encoding="utf-8"
            )
            click.echo(f"\nReport written to: {output}")
