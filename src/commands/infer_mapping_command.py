"""CLI command handler for infer-mapping."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click


def run_infer_mapping_command(
    file: str,
    format: str | None,
    output: str | None,
    sample_lines: int,
    logger,
) -> None:
    """Execute the infer-mapping workflow.

    Args:
        file: Path to the sample data file.
        format: Optional format override (``fixed_width``, ``pipe_delimited``,
            ``csv``, ``tsv``).  ``None`` triggers auto-detection.
        output: Optional output JSON path.  When ``None``, the mapping is
            written to stdout.
        sample_lines: Number of lines to analyse.
        logger: Logger instance for structured messages.

    Raises:
        SystemExit: On any unrecoverable error.
    """
    from src.services.infer_mapping_service import infer_mapping

    try:
        mapping = infer_mapping(
            file_path=file,
            format=format,
            sample_lines=sample_lines,
        )
    except (FileNotFoundError, ValueError) as exc:
        logger.error(str(exc))
        sys.exit(1)

    json_str = json.dumps(mapping, indent=2)

    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        with open(output, "w", encoding="utf-8") as fh:
            fh.write(json_str + "\n")
        click.echo(f"Draft mapping written to: {output}")
        click.echo(f"Fields inferred: {len(mapping.get('fields', []))}")
        click.echo(f"Format detected: {mapping['source']['format']}")
    else:
        click.echo(json_str)
