"""Service wrapper for multi-record file validation.

Thin orchestration layer used by the API router to validate a batch file
against a multi-record YAML config string without requiring a filesystem path
for the config (the caller passes the YAML text directly).
"""

from __future__ import annotations

from typing import Any

import yaml


def run_multi_record_validate_service(
    file_path: str,
    config_yaml: str,
) -> dict[str, Any]:
    """Validate *file_path* against a multi-record config parsed from *config_yaml*.

    Args:
        file_path: Absolute path to the data file to validate.
        config_yaml: Raw YAML text of the multi-record config.

    Returns:
        Dict with keys: ``valid`` (bool), ``total_rows`` (int),
        ``record_type_results`` (dict), ``cross_type_violations`` (list).

    Raises:
        ValueError: When *config_yaml* cannot be parsed or is structurally invalid.
    """
    from src.config.multi_record_config import MultiRecordConfig
    from src.validators.multi_record_validator import MultiRecordValidator

    try:
        raw = yaml.safe_load(config_yaml)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError("Multi-record config must be a YAML mapping")

    try:
        config = MultiRecordConfig(**raw)
    except Exception as exc:
        raise ValueError(f"Invalid multi-record config structure: {exc}") from exc

    validator = MultiRecordValidator()
    return validator.validate(file_path, config)
