"""CLI command handler for the ``mask`` command.

Delegates to :class:`~src.services.masking_service.MaskingService`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click


def run_mask_command(
    file: str,
    mapping: str,
    rules: str | None,
    output: str,
    logger,
) -> None:
    """Execute the mask workflow.

    Args:
        file: Path to the input batch file.
        mapping: Path to the mapping JSON (defines field positions).
        rules: Optional path to masking rules JSON.  If not supplied every
            field is preserved (no masking).
        output: Destination path for the masked output file.
        logger: Logger instance for status messages.

    Raises:
        SystemExit: On any unrecoverable error.
    """
    from src.services.masking_service import MaskingService

    # Load mapping
    mapping_path = Path(mapping)
    if not mapping_path.exists():
        logger.error(f"Mapping file not found: {mapping}")
        sys.exit(1)

    with open(mapping_path, "r") as f:
        mapping_config = json.load(f)

    # Load masking rules (default: preserve all)
    masking_rules: dict = {"fields": {}}
    if rules:
        rules_path = Path(rules)
        if not rules_path.exists():
            logger.error(f"Masking rules file not found: {rules}")
            sys.exit(1)
        with open(rules_path, "r") as f:
            masking_rules = json.load(f)

    # Run masking
    svc = MaskingService()
    result = svc.mask_file(file, output, mapping_config, masking_rules)

    click.echo(f"Masked {result['records_masked']} records")
    click.echo(f"Output written to: {result['output_path']}")
