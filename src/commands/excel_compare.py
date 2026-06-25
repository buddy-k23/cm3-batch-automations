"""CLI command handler for the Excel <-> DB comparison workflow (S24-2).

Thin orchestration: validate/forward inputs, delegate the full workflow to
:func:`~src.services.excel_db_compare_service.compare_excel_to_db`, print a
human-readable summary, write a ``.json`` report here / let the service render
``.html``, and exit non-zero on mismatch — matching the exit-code convention of
``valdo compare`` / ``valdo db-compare`` so CI gates fail on differences.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click

from src.services.excel_db_compare_service import compare_excel_to_db


def run_excel_compare_command(
    excel_file: str,
    query_or_table: str,
    sheet: str | int | None,
    header_row: int,
    key_columns: str | None,
    direction: str,
    output_format: str,
    output: str | None,
    logger: Any,
    connection_override: dict[str, Any] | None = None,
) -> None:
    """Execute the Excel <-> DB comparison workflow from the CLI.

    Delegates to
    :func:`~src.services.excel_db_compare_service.compare_excel_to_db`, prints a
    summary, and optionally writes a report.  ``.html`` output (or
    ``output_format == "html"``) is rendered by the service; any other extension
    is written as machine JSON here — the consistent output contract.

    Args:
        excel_file: Path to the Excel workbook.
        query_or_table: SQL SELECT statement or bare table name for the DB side.
        sheet: Excel sheet selector (name, zero-based index, or ``None``).
        header_row: Zero-based header row index for the Excel read.
        key_columns: Comma-separated key column names (or ``None`` for row-by-row).
        direction: ``"db-source"`` (DB is source, Excel actual) or
            ``"excel-source"`` (Excel is source, DB actual).
        output_format: ``"json"`` or ``"html"``.
        output: Optional report path. ``.html`` -> HTML (rendered by the
            service), else JSON written here.
        logger: Logger used for error messages.
        connection_override: Optional per-request DB connection override
            forwarded to the service (e.g. ``{"db_adapter": "sqlite", ...}``).

    Raises:
        SystemExit: ``1`` on any error (missing file, DB failure) or when a
            mismatch is detected (exit-code parity with ``compare`` /
            ``db-compare``).
    """
    # Resolve the output contract (.html -> HTML rendered by service, else JSON).
    wants_html = bool(output) and (
        output_format == "html" or str(output).lower().endswith(".html")
    )
    html_output_path = output if wants_html else None

    try:
        result = compare_excel_to_db(
            excel_file=excel_file,
            query_or_table=query_or_table,
            sheet=sheet,
            header_row=header_row,
            key_columns=key_columns or None,
            direction=direction,
            output_format=output_format,
            output_path=html_output_path,
            connection_override=connection_override,
        )
    except FileNotFoundError as exc:
        logger.error(str(exc))
        sys.exit(1)
    except ValueError as exc:
        logger.error(str(exc))
        sys.exit(1)
    except RuntimeError as exc:
        logger.error(f"DB extraction failed: {exc}")
        sys.exit(1)

    workflow = result.get("workflow", {})
    compare = result.get("compare", {})

    def _count(val: Any) -> int:
        try:
            return len(val)
        except TypeError:
            return int(val) if val else 0

    rows_with_diffs = compare.get(
        "rows_with_differences", compare.get("differences", 0)
    )

    click.echo("\nExcel <-> DB Comparison Summary")
    click.echo(f"  Direction:          {workflow.get('direction', direction)}")
    click.echo(f"  Query / Table:      {workflow.get('query_or_table', query_or_table)}")
    click.echo(f"  Excel rows read:    {workflow.get('excel_rows_read', 0)}")
    click.echo(f"  DB rows extracted:  {workflow.get('db_rows_extracted', 0)}")
    click.echo(f"  Matching rows:      {compare.get('matching_rows', 0)}")
    click.echo(f"  Only in file1:      {_count(compare.get('only_in_file1', 0))}")
    click.echo(f"  Only in file2:      {_count(compare.get('only_in_file2', 0))}")
    click.echo(f"  Rows with diffs:    {_count(rows_with_diffs)}")

    status = workflow.get("status", "unknown")
    if status == "passed":
        click.echo(click.style("\n  PASS", fg="green"))
    else:
        click.echo(click.style("\n  FAIL", fg="red"))

    # --- Optional report output ---------------------------------------------
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

    # --- Exit-code contract (parity with compare / db-compare) --------------
    if status != "passed":
        sys.exit(1)
