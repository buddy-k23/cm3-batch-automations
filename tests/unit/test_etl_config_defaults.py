"""Unit tests for EA-S1 implicit-defaults Pydantic models in
:mod:`src.pipeline.etl_config`.

Validates that:
  * Boilerplate fields (``strict_fixed_width``, ``strict_level``,
    ``tolerance.*``, ``thresholds.max_errors``) carry sensible implicit
    defaults so source YAMLs can omit them.
  * Explicit values supplied in YAML survive validation unchanged.
  * The existing committed SHAW.yml still loads through ``SourceConfig``
    (regression guard for the planned EA-S3 boilerplate strip).
  * A minimal BFIN-style YAML (single output entry, no boilerplate)
    validates without exception.

The models under test are schema-only and not yet wired into
``PathResolver.source_config()``; these tests pin the contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.pipeline.etl_config import (
    InputFileConfig,
    OutputFileConfig,
    SourceConfig,
    ThresholdsConfig,
    ToleranceConfig,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
SHAW_YAML_PATH = REPO_ROOT / "config" / "e2e" / "sources" / "SHAW.yml"

# Counts confirmed against SHAW.yml at commit-time (16 outputs, 6 inputs).
# Update these constants only when SHAW.yml legitimately changes.
SHAW_EXPECTED_OUTPUT_COUNT = 16
SHAW_EXPECTED_INPUT_COUNT = 6


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_defaults_when_omitted() -> None:
    """A bare ``OutputFileConfig`` validates with all implicit defaults."""
    cfg = OutputFileConfig(
        file_type="ATOCTRAN",
        glob="atoctran_*.txt",
        mapping="config/mappings/SHAW_ATOCTRAN.yaml",
    )
    assert cfg.rules == ""
    assert cfg.multi_record is False
    assert cfg.discriminator_field == ""
    assert cfg.strict_fixed_width is True
    assert cfg.strict_level == "all"
    assert isinstance(cfg.tolerance, ToleranceConfig)
    assert cfg.tolerance.ignore_fields == []
    assert cfg.tolerance.max_errors == 0
    assert cfg.tolerance.max_error_pct == 0.0


def test_defaults_match_legacy_explicit() -> None:
    """The legacy explicit-boilerplate dict and a bare dict produce equal
    models -- proving EA-S3 can safely strip the boilerplate from committed
    YAMLs without changing behaviour.
    """
    explicit = OutputFileConfig.model_validate(
        {
            "file_type": "CDSTRANS_EFB",
            "glob": "cdstrans_efb_*.txt",
            "mapping": "config/mappings/SHAW_CDSTRANS_EFB.json",
            "rules": "config/rules/SHAW_CDSTRANS_EFB.json",
            "multi_record": False,
            "strict_fixed_width": True,
            "strict_level": "all",
            "tolerance": {
                "ignore_fields": [],
                "max_error_pct": 0,
                "max_errors": 0,
            },
        }
    )
    minimal = OutputFileConfig.model_validate(
        {
            "file_type": "CDSTRANS_EFB",
            "glob": "cdstrans_efb_*.txt",
            "mapping": "config/mappings/SHAW_CDSTRANS_EFB.json",
            "rules": "config/rules/SHAW_CDSTRANS_EFB.json",
        }
    )
    assert explicit == minimal


def test_explicit_override_strict_level() -> None:
    """An explicit ``strict_level`` value survives validation.

    ``"format"`` is the non-default Literal value (default is ``"all"``);
    it is also the CLI default for ``valdo validate --strict-level``.
    """
    cfg = OutputFileConfig(
        file_type="P327",
        glob="p327_*.txt",
        mapping="config/mappings/SHAW_P327.json",
        strict_level="format",
    )
    assert cfg.strict_level == "format"
    # Other defaults still apply.
    assert cfg.strict_fixed_width is True
    assert cfg.tolerance.max_errors == 0


def test_explicit_override_tolerance_max_errors() -> None:
    """An explicit ``tolerance.max_errors`` value survives; other tolerance
    fields stay at defaults.
    """
    cfg = OutputFileConfig.model_validate(
        {
            "file_type": "TRANERT",
            "glob": "tranert_*.txt",
            "mapping": "config/mappings/SHAW_TRANERT.yaml",
            "tolerance": {"max_errors": 5},
        }
    )
    assert cfg.tolerance.max_errors == 5
    assert cfg.tolerance.ignore_fields == []
    assert cfg.tolerance.max_error_pct == 0.0


def test_input_file_defaults() -> None:
    """A bare ``InputFileConfig`` validates with ``thresholds.max_errors == 0``."""
    cfg = InputFileConfig(
        file_type="COLLATERAL_MASTER",
        glob="collateral-master_*.txt",
        mapping="config/mappings/SHAW_COLLATERAL_MASTER.json",
        target_staging_table="SHAW_COLLATERAL",
    )
    assert isinstance(cfg.thresholds, ThresholdsConfig)
    assert cfg.thresholds.max_errors == 0


def test_input_file_explicit_threshold() -> None:
    """An explicit ``thresholds.max_errors`` value survives validation."""
    cfg = InputFileConfig.model_validate(
        {
            "file_type": "FEE_MASTER",
            "glob": "fee-master_*.txt",
            "mapping": "config/mappings/SHAW_FEE_MASTER.json",
            "target_staging_table": "SHAW_FEE_MASTER",
            "thresholds": {"max_errors": 3},
        }
    )
    assert cfg.thresholds.max_errors == 3


def test_shaw_yaml_still_loads() -> None:
    """The committed SHAW.yml passes through ``SourceConfig.model_validate``
    unchanged. Regression guard for EA-S3 (boilerplate strip).
    """
    raw = yaml.safe_load(SHAW_YAML_PATH.read_text(encoding="utf-8"))
    cfg = SourceConfig.model_validate(raw)
    assert cfg.source == "SHAW"
    assert len(cfg.output_files) == SHAW_EXPECTED_OUTPUT_COUNT
    assert len(cfg.input_files) == SHAW_EXPECTED_INPUT_COUNT
    # Spot-check that explicit values from SHAW.yml are preserved.
    atoctran = next(o for o in cfg.output_files if o.file_type == "ATOCTRAN")
    assert atoctran.multi_record is True
    assert atoctran.discriminator_field == "TRANSACTION-CODE"
    assert atoctran.strict_level == "all"
    # Spot-check that extra top-level keys survive under extra="allow"
    # (e.g. SHAW.yml has 'gates', which is part of the model, but the
    # invariant we care about is that validation didn't reject the YAML).
    assert cfg.staging_schema == "APP_INT"


def test_bfin_minimal_loads() -> None:
    """An in-memory minimal BFIN-style YAML (``source: BFIN`` + one minimal
    output entry, no boilerplate) validates without exception.
    """
    minimal_dict = {
        "source": "BFIN",
        "output_files": [
            {
                "file_type": "BFIN_TRANS",
                "glob": "bfin_trans_*.txt",
                "mapping": "config/mappings/BFIN_TRANS.json",
            }
        ],
    }
    cfg = SourceConfig.model_validate(minimal_dict)
    assert cfg.source == "BFIN"
    assert cfg.schema_version == 1
    assert cfg.input_files == []
    assert len(cfg.output_files) == 1

    out = cfg.output_files[0]
    assert out.file_type == "BFIN_TRANS"
    # All implicit defaults applied.
    assert out.strict_fixed_width is True
    assert out.strict_level == "all"
    assert out.multi_record is False
    assert out.tolerance.max_errors == 0
    assert out.tolerance.max_error_pct == 0.0
    assert out.tolerance.ignore_fields == []
