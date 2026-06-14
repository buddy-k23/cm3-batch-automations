"""CLI command handler for multi-record-type file validation.

Thin orchestration layer: loads the YAML config, delegates to
:class:`~src.validators.multi_record_validator.MultiRecordValidator`, and
prints a human-readable summary to stdout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import click


def run_multi_record_command(
    file: str,
    multi_record_config: str,
    output: Optional[str],
    logger,
) -> None:
    """Run multi-record validation and print results to stdout.

    Args:
        file: Path to the data file to validate.
        multi_record_config: Path to the YAML multi-record config file.
        output: Optional output path (.json) for the aggregate result.
        logger: Logger instance for error/info messages.

    Raises:
        SystemExit: Exits with code 1 when validation errors are found or the
            config cannot be loaded.
    """
    import yaml

    from src.config.multi_record_config import MultiRecordConfig
    from src.validators.multi_record_validator import MultiRecordValidator

    # --- Load YAML config ---
    try:
        with open(multi_record_config, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except Exception as exc:
        logger.error("Failed to load multi-record config '%s': %s", multi_record_config, exc)
        sys.exit(1)

    try:
        config = MultiRecordConfig(**raw)
    except Exception as exc:
        logger.error("Invalid multi-record config: %s", exc)
        sys.exit(1)

    # --- Validate ---
    validator = MultiRecordValidator()
    result = validator.validate(file, config)

    # --- Print summary ---
    total = result.get("total_rows", 0)
    cross_violations = result.get("cross_type_violations", [])
    error_count = sum(1 for v in cross_violations if v.get("severity") == "error")
    warning_count = sum(1 for v in cross_violations if v.get("severity") == "warning")

    if result.get("valid"):
        click.echo(click.style("✓ Multi-record validation passed", fg="green"))
    else:
        click.echo(click.style("✗ Multi-record validation failed", fg="red"))

    click.echo(f"\nTotal Rows     : {total:,}")
    click.echo(f"Error Count    : {error_count}")
    click.echo(f"Warning Count  : {warning_count}")

    type_results = result.get("record_type_results", {})
    if type_results:
        click.echo("\nRecord Type Summary:")
        for type_name, type_result in type_results.items():
            rows = type_result.get("total_rows", type_result.get("row_count", "?"))
            valid = "✓" if type_result.get("valid", True) else "✗"
            click.echo(f"  {valid} {type_name}: {rows} rows")

    if cross_violations:
        click.echo(click.style(f"\nCross-type violations ({len(cross_violations)}):", fg="yellow"))
        for v in cross_violations[:10]:
            sev_color = "red" if v.get("severity") == "error" else "yellow"
            click.echo(
                click.style(f"  • [{v.get('severity', 'unknown')}] {v.get('message', '')}", fg=sev_color)
            )
        if len(cross_violations) > 10:
            click.echo(f"  ... and {len(cross_violations) - 10} more")

    # --- Write output ---
    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        if output.lower().endswith(".json"):
            with open(output, "w", encoding="utf-8") as fh:
                json.dump(result, fh, indent=2)
            click.echo(f"\n✓ Multi-record validation report: {output}")
        else:
            click.echo(
                click.style(
                    f"\nUnsupported output type '{Path(output).suffix}'. Use .json",
                    fg="yellow",
                )
            )

    if not result.get("valid"):
        sys.exit(1)
