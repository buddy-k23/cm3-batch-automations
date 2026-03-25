from __future__ import annotations

import json
import logging
from typing import Any, Optional

_audit_logger = logging.getLogger(__name__)


def run_validate_service(
    file: str,
    mapping: Optional[str] = None,
    rules: Optional[str] = None,
    output: Optional[str] = None,
    detailed: bool = True,
    strict_fixed_width: bool = False,
    strict_level: str = "format",
    use_chunked: bool = False,
    chunk_size: int = 100_000,
    suppress_pii: bool = True,
) -> dict[str, Any]:
    """Shared validate workflow used by CLI and run-tests orchestrator.

    Returns a dict with at least:
      total_rows    - number of rows processed
      error_count   - number of validation errors
      warning_count - number of validation warnings
      valid         - bool overall validity flag

    Args:
        file: Path to the data file.
        mapping: Path to mapping JSON file.
        rules: Path to rules config file.
        output: Optional output path (.json or .html).
        detailed: Include detailed field analysis.
        strict_fixed_width: Enable strict fixed-width validation.
        strict_level: Validation strictness level ('format' or 'all').
        use_chunked: Route to ChunkedFileValidator for memory-efficient
            processing. Ignored for fixed-width mappings.
        chunk_size: Rows per chunk when use_chunked=True. Default 100,000.
        suppress_pii: When True (default), redact raw field values from
            HTML reports and CSV sidecars.
    """
    from src.parsers.format_detector import FormatDetector
    from src.parsers.enhanced_validator import EnhancedFileValidator
    from src.parsers.fixed_width_parser import FixedWidthParser
    from src.utils.audit_logger import get_audit_logger, file_hash as _file_hash

    audit = get_audit_logger()
    _audit_kwargs: dict[str, Any] = {"triggered_by": "service", "file": file}
    try:
        _audit_kwargs["file_hash"] = _file_hash(file)
    except OSError:
        pass
    if mapping:
        try:
            _audit_kwargs["mapping_hash"] = _file_hash(mapping)
        except OSError:
            pass
    audit.emit("test_run_started", **_audit_kwargs)

    mapping_config: Optional[dict] = None
    if mapping:
        with open(mapping, "r", encoding="utf-8") as f:
            mapping_config = json.load(f)
        mapping_config["file_path"] = mapping

    is_fixed_width = mapping_config and _is_fixed_width_mapping(mapping_config)

    # ── Chunked path (delimited files only) ───────────────────────────────────
    if use_chunked and not is_fixed_width:
        result = _run_chunked_validate(
            file=file,
            mapping_config=mapping_config,
            rules=rules,
            chunk_size=chunk_size,
            strict_fixed_width=strict_fixed_width,
            strict_level=strict_level,
        )
    else:
        # ── Standard path ─────────────────────────────────────────────────────
        if is_fixed_width:
            parser_class = FixedWidthParser
        else:
            detector = FormatDetector()
            try:
                parser_class = detector.get_parser_class(file)
            except Exception:
                if mapping_config and mapping_config.get("fields"):
                    parser_class = FixedWidthParser
                else:
                    raise

        if mapping_config and parser_class == FixedWidthParser:
            field_specs = _build_fixed_width_specs(mapping_config)
            parser = FixedWidthParser(file, field_specs)
        else:
            # When the mapping declares no header row, supply field names so the
            # parser assigns them directly instead of producing integer indices.
            mapping_has_header = bool(
                (mapping_config or {}).get("source", {}).get("has_header", True)
            )
            col_names = (
                [f["name"] for f in mapping_config.get("fields", []) if "name" in f]
                if (mapping_config and not mapping_has_header)
                else None
            )
            try:
                parser = parser_class(file, columns=col_names)
            except TypeError:
                parser = parser_class(file)

        validator = EnhancedFileValidator(parser, mapping_config, rules)
        result = validator.validate(
            detailed=detailed,
            strict_fixed_width=strict_fixed_width,
            strict_level=strict_level,
        )

    # Normalise counts so callers always get integers.
    result.setdefault("error_count", len(result.get("errors", [])))
    result.setdefault("warning_count", len(result.get("warnings", [])))
    result.setdefault("total_rows", result.get("row_count", 0))

    # If total_rows is still 0 (validator exited early), count non-empty lines.
    if not result.get("total_rows"):
        try:
            with open(file, encoding="utf-8", errors="replace") as fh:
                result["total_rows"] = sum(1 for line in fh if line.strip())
        except Exception:
            pass

    # Derive valid_rows from the set of unique row numbers that have errors.
    if not result.get("valid_rows"):
        affected = {
            e["row"] for e in result.get("errors", [])
            if isinstance(e.get("row"), int)
        }
        result["valid_rows"] = max(0, result.get("total_rows", 0) - len(affected))

    if output:
        from pathlib import Path

        Path(output).parent.mkdir(parents=True, exist_ok=True)
        if output.lower().endswith(".json"):
            with open(output, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
        elif output.lower().endswith((".html", ".htm")):
            from src.reports.renderers.validation_renderer import ValidationReporter

            reporter = ValidationReporter()
            reporter.generate(result, output, suppress_pii=suppress_pii)

    audit.emit(
        "test_run_completed",
        triggered_by="service",
        file=file,
        valid=result.get("valid"),
        error_count=result.get("error_count", 0),
        warning_count=result.get("warning_count", 0),
        total_rows=result.get("total_rows", 0),
    )

    return result


def _is_fixed_width_mapping(cfg: dict) -> bool:
    """Return True when the mapping defines fixed-width fields (each has a 'length')."""
    fields = cfg.get("fields", [])
    return bool(fields) and any("length" in f for f in fields)


def _build_fixed_width_specs(cfg: dict) -> list[tuple[str, int, int]]:
    field_specs = []
    current_pos = 0
    for field in cfg.get("fields", []):
        name = field["name"]
        length = int(field["length"])
        if field.get("position") is not None:
            start = int(field["position"]) - 1
        else:
            start = current_pos
        end = start + length
        field_specs.append((name, start, end))
        current_pos = end
    return field_specs


def _detect_delimiter(mapping_config: Optional[dict]) -> str:
    """Return the field delimiter for a delimited mapping config.

    Args:
        mapping_config: Parsed mapping JSON dict, or None.

    Returns:
        Single-character delimiter string. Checks source.delimiter first,
        then infers from source.format. Defaults to '|'.
    """
    if not mapping_config:
        return "|"
    source = mapping_config.get("source", {})
    explicit = source.get("delimiter")
    if explicit:
        return explicit
    fmt = source.get("format", "").lower()
    if "comma" in fmt or "csv" in fmt:
        return ","
    if "tab" in fmt or "tsv" in fmt:
        return "\t"
    return "|"


def _run_chunked_validate(
    file: str,
    mapping_config: Optional[dict],
    rules: Optional[str],
    chunk_size: int,
    strict_fixed_width: bool,
    strict_level: str,
) -> dict[str, Any]:
    """Run validation via ChunkedFileValidator.

    Args:
        file: Path to the data file.
        mapping_config: Parsed mapping dict (or None).
        rules: Path to rules config file (or None).
        chunk_size: Rows per chunk.
        strict_fixed_width: Enable strict fixed-width checks.
        strict_level: Strictness level ('format' or 'all').

    Returns:
        Raw result dict from ChunkedFileValidator (normalised by caller).

    Raises:
        ImportError: If ChunkedFileValidator cannot be imported.
        Exception: Any exception raised by ChunkedFileValidator.validate().
    """
    from src.parsers.chunked_validator import ChunkedFileValidator

    delimiter = _detect_delimiter(mapping_config)
    strict_fields = mapping_config.get("fields", []) if mapping_config else []
    has_header = bool(
        (mapping_config or {}).get("source", {}).get("has_header", True)
    )

    validator = ChunkedFileValidator(
        file_path=file,
        delimiter=delimiter,
        chunk_size=chunk_size,
        rules_config_path=rules,
        strict_fixed_width=strict_fixed_width,
        strict_level=strict_level,
        strict_fields=strict_fields,
        has_header=has_header,
    )
    return validator.validate(show_progress=False)
