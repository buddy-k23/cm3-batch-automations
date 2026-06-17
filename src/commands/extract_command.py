"""CLI command handler for ``valdo extract`` (S-followup, #422).

Relocated verbatim from ``src/main.py`` so that ``main.py`` is a thin CLI
registration layer (Architecture Principle #1/#6).  The actual database work
— backend selection via the S15 adapter factory (ADR 0022 §4) and the S13.5-4
SQL hardening — lives in :class:`~src.database.extractor.DataExtractor` and the
adapter factory and carries through unchanged.  This module only orchestrates
mode selection, output writing, and the exit code.
"""

from __future__ import annotations

import sys
from typing import Any, Optional

import click


def run_extract_command(
    table: Optional[str],
    query: Optional[str],
    sql_file: Optional[str],
    output: str,
    limit: Optional[int],
    delimiter: str,
    logger: Any,
) -> None:
    """Extract data from a database to a flat file.

    Backend-agnostic (ADR 0022 §4): the active database is selected by the
    ``DB_ADAPTER`` environment variable (``oracle`` | ``postgresql`` |
    ``sqlite``) via the adapter factory, so the same command runs against any
    supported backend rather than Oracle only.

    Supports three mutually-exclusive modes:

    1. Table extraction: ``table=TABLENAME`` (optionally with ``limit``).
    2. Direct query: ``query="SELECT ..."``.
    3. SQL file: ``sql_file="path/to/query.sql"``.

    Args:
        table: Table name to extract (mode 1). Mutually exclusive with
            ``query`` and ``sql_file``.
        query: SQL query string to execute (mode 2).
        sql_file: Path to a file containing the SQL query (mode 3).
        output: Output flat-file path (required).
        limit: Optional row cap; only honoured in table mode.
        delimiter: Output field delimiter (e.g. ``"|"``).
        logger: Logger instance used for error reporting.

    Raises:
        SystemExit: With code ``1`` when no/too-many source options are
            provided, or when extraction fails.
    """
    # Validate input options
    options_provided = sum([bool(table), bool(query), bool(sql_file)])
    if options_provided == 0:
        click.echo(click.style('Error: Must provide one of --table, --query, or --sql-file', fg='red'))
        sys.exit(1)
    elif options_provided > 1:
        click.echo(click.style('Error: Only one of --table, --query, or --sql-file can be specified', fg='red'))
        sys.exit(1)

    try:
        from src.database.adapters.factory import get_database_adapter
        from src.database.extractor import DataExtractor

        # Resolve the backend from DB_ADAPTER and connect for the command's
        # lifetime; the context manager guarantees disconnect on exit/error.
        with get_database_adapter() as adapter:
            extractor = DataExtractor(adapter)

            # Determine extraction mode
            if sql_file:
                # Read SQL from file
                with open(sql_file, 'r') as f:
                    sql_query = f.read().strip()
                click.echo(f"\nExecuting SQL from file: {sql_file}")
                stats = extractor.extract_to_file(output_file=output, query=sql_query, delimiter=delimiter)
                click.echo(f"Extracted {stats['total_rows']} rows to {output}")

            elif query:
                # Use provided SQL query
                click.echo(f"\nExecuting custom query")
                stats = extractor.extract_to_file(output_file=output, query=query, delimiter=delimiter)
                click.echo(f"Extracted {stats['total_rows']} rows to {output}")

            else:
                # Table extraction
                click.echo(f"\nExtracting from table: {table}")

                if limit:
                    df = extractor.extract_table(table, limit=limit)
                    df.to_csv(output, sep=delimiter, index=False, header=False)
                    click.echo(f"Extracted {len(df)} rows to {output}")
                else:
                    stats = extractor.extract_to_file(table_name=table, output_file=output, delimiter=delimiter)
                    click.echo(f"Extracted {stats['total_rows']} rows to {output}")

        click.echo(click.style('✓ Extraction complete', fg='green'))

    except Exception as e:
        logger.error(f"Error extracting data: {e}")
        sys.exit(1)
