"""CLI command handler for generating synthetic test data files.

Loads a mapping JSON, generates rows using the field generator service,
and writes output in the format declared by the mapping (fixed_width,
pipe_delimited, csv, tsv).

The public entry point is :func:`run_generate_test_data_command`.
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Optional

import click
import yaml


# Format → delimiter mapping (fixed_width handled separately)
_DELIMITERS: dict[str, str] = {
    "pipe_delimited": "|",
    "csv": ",",
    "tsv": "\t",
}

_SUPPORTED_FORMATS = frozenset(("fixed_width", "pipe_delimited", "csv", "tsv"))


def _detect_format(mapping: dict) -> str:
    """Extract the file format from a mapping dict.

    Args:
        mapping: Parsed mapping JSON dict.

    Returns:
        Format string: 'fixed_width', 'pipe_delimited', 'csv', or 'tsv'.

    Raises:
        click.ClickException: If format is missing or unrecognised.
    """
    fmt = (mapping.get("source") or {}).get("format", "")
    if fmt not in _SUPPORTED_FORMATS:
        raise click.ClickException(
            f"Unsupported or missing source.format in mapping: '{fmt}'. "
            "Expected one of: fixed_width, pipe_delimited, csv, tsv."
        )
    return fmt


def _row_to_fixed_width(row: dict, fields: list) -> str:
    """Concatenate field values in declaration order for fixed-width output.

    Args:
        row: Dict mapping field_name to padded value string.
        fields: Ordered field definition list from the mapping.

    Returns:
        Single fixed-width line (no trailing newline).
    """
    return "".join(row[f["name"]] for f in fields)


def _row_to_delimited(row: dict, fields: list, delimiter: str) -> str:
    """Join field values with delimiter for delimited output.

    Values are stripped of padding since delimited formats don't require it.

    Args:
        row: Dict mapping field_name to value string.
        fields: Ordered field definition list from the mapping.
        delimiter: Column separator character.

    Returns:
        Single delimited line (no trailing newline).
    """
    return delimiter.join(row[f["name"]].strip() for f in fields)


def run_generate_test_data_command(
    mapping: Optional[str],
    rows: Optional[int],
    output: str,
    seed: int = 42,
    inject_errors: Optional[dict] = None,
    multi_record: Optional[str] = None,
    detail_rows: Optional[int] = None,
) -> None:
    """Load mapping JSON, generate rows, and write to output file.

    Args:
        mapping: Path to mapping JSON file (mutually exclusive with multi_record).
        rows: Number of data rows to generate (must be >= 1 for single-mapping mode).
            Pass None to trigger a clear error message when omitted.
        output: Output file path.
        seed: Random seed for reproducibility.
        inject_errors: Optional dict of {error_type: count} for error injection
            (used by Task 3).
        multi_record: Path to multi-record YAML config (mutually exclusive with
            mapping, used by Task 4). (fixed-width output only)
        detail_rows: Number of detail rows per group for multi-record mode.

    Raises:
        click.ClickException: On invalid input (rows < 1, bad format, mutual
            exclusion violations).
    """
    from src.services.test_data_generator import generate_file

    if mapping and multi_record:
        raise click.ClickException("--mapping and --multi-record are mutually exclusive.")

    if multi_record:
        from src.services.test_data_generator import generate_multi_record_file
        with open(multi_record, "r", encoding="utf-8") as fh:
            mr_config = yaml.safe_load(fh)
        effective_rows = detail_rows or 10
        records = generate_multi_record_file(mr_config, effective_rows, seed=seed)
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
            for _rt_key, row, fields in records:
                fh.write(_row_to_fixed_width(row, fields) + "\n")
        click.echo(f"Generated {len(records)} multi-record rows -> {output}")
        return

    if not mapping:
        raise click.ClickException("Either --mapping or --multi-record is required.")

    if rows is None or rows < 1:
        raise click.ClickException("--rows must be at least 1.")

    with open(mapping, "r", encoding="utf-8") as fh:
        mapping_config = json.load(fh)

    generated_rows = generate_file(mapping_config, row_count=rows, seed=seed)

    # Error injection (Task 3 populates this path)
    if inject_errors:
        from src.services.test_data_generator import inject_errors as do_inject
        rng = random.Random(seed)
        generated_rows = do_inject(generated_rows, inject_errors, mapping_config.get("fields", []), rng)

    fmt = _detect_format(mapping_config)
    fields = mapping_config.get("fields", [])

    # Resolve delimiter: prefer explicit mapping source.delimiter, then format default
    delimiter = (mapping_config.get("source") or {}).get("delimiter") or _DELIMITERS.get(fmt, "|")

    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        for row in generated_rows:
            if fmt == "fixed_width":
                line = _row_to_fixed_width(row, fields)
            else:
                line = _row_to_delimited(row, fields, delimiter)
            fh.write(line + "\n")

    click.echo(f"Generated {len(generated_rows)} rows -> {output} ({fmt})")
