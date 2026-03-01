from __future__ import annotations

import json
from typing import Any, Optional


def run_validate_service(
    file: str,
    mapping: Optional[str] = None,
    rules: Optional[str] = None,
    output: Optional[str] = None,
    detailed: bool = True,
    strict_fixed_width: bool = False,
    strict_level: str = "format",
) -> dict[str, Any]:
    """Shared validate workflow used by CLI and run-tests orchestrator.

    Returns a dict with at least:
      total_rows    - number of rows processed
      error_count   - number of validation errors
      warning_count - number of validation warnings
      valid         - bool overall validity flag
    """
    from src.parsers.format_detector import FormatDetector
    from src.parsers.enhanced_validator import EnhancedFileValidator
    from src.parsers.fixed_width_parser import FixedWidthParser

    mapping_config: Optional[dict] = None
    if mapping:
        with open(mapping, "r", encoding="utf-8") as f:
            mapping_config = json.load(f)
        mapping_config["file_path"] = mapping

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

    if output:
        from pathlib import Path

        Path(output).parent.mkdir(parents=True, exist_ok=True)
        if output.lower().endswith(".json"):
            with open(output, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
        elif output.lower().endswith((".html", ".htm")):
            from src.reports.renderers.validation_renderer import ValidationReporter

            reporter = ValidationReporter()
            reporter.generate(result, output)

    return result


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
