"""CLI command handler for ``valdo detect-drift``.

Loads a mapping JSON, calls the drift detector service, prints a summary
table to stdout, and exits with an appropriate code.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import click

from src.services.drift_detector import detect_drift


def run_detect_drift(
    file_path: str,
    mapping_id: str,
    output_path: Optional[str] = None,
    mappings_dir: str = "config/mappings",
) -> int:
    """Run drift detection and print results to stdout.

    Loads the mapping JSON from ``{mappings_dir}/{mapping_id}.json``, calls
    :func:`~src.services.drift_detector.detect_drift`, prints a summary table
    of any drifted fields, and returns an exit code.

    Args:
        file_path: Path to the batch data file to inspect.
        mapping_id: Stem of the mapping JSON file (no ``.json`` extension).
        output_path: Optional filesystem path to write the full JSON result.
            When ``None``, no file is written.
        mappings_dir: Directory that contains mapping JSON files.
            Defaults to ``config/mappings``.

    Returns:
        ``0`` when no drift is detected or all drifted fields have
        severity ``'warning'``; ``1`` when at least one field has
        severity ``'error'``.
    """
    mapping_file = Path(mappings_dir) / f"{mapping_id}.json"
    if not mapping_file.exists():
        click.echo(
            click.style(
                f"Error: Mapping '{mapping_id}' not found at {mapping_file}",
                fg="red",
            )
        )
        return 2

    with open(mapping_file, "r", encoding="utf-8") as fh:
        mapping = json.load(fh)

    result = detect_drift(file_path, mapping)

    drifted_fields = result.get("fields", [])
    has_error = any(f.get("severity") == "error" for f in drifted_fields)

    if not result.get("drifted"):
        click.echo(click.style("No drift detected — file layout matches mapping.", fg="green"))
    else:
        # Print table header
        click.echo(click.style("Schema drift detected:", fg="yellow"))
        click.echo(
            f"  {'Field':<20} {'Exp.Start':>10} {'Act.Start':>10} "
            f"{'Exp.Len':>8} {'Severity':<10}"
        )
        click.echo("  " + "-" * 65)
        for field in drifted_fields:
            severity = field.get("severity", "warning")
            color = "red" if severity == "error" else "yellow"
            click.echo(
                click.style(
                    f"  {field.get('name', ''):<20} "
                    f"{str(field.get('expected_start', '?')):>10} "
                    f"{str(field.get('actual_start', '?')):>10} "
                    f"{str(field.get('expected_length', '?')):>8} "
                    f"{severity:<10}",
                    fg=color,
                )
            )

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
        click.echo(f"\nJSON report written to: {output_path}")

    return 1 if has_error else 0
