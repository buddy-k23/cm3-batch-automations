"""Unit tests for the per-source YAML Pydantic models in
:mod:`src.pipeline.etl_config`.

EA-S1 contract:
  * Boilerplate fields (``strict_fixed_width``, ``strict_level``,
    ``tolerance.*``, ``thresholds.max_errors``) carry sensible implicit
    defaults so source YAMLs can omit them.
  * Explicit values supplied in YAML survive validation unchanged.
  * The committed SHAW.yml still loads through ``SourceConfig`` after the
    EB-S1 inline migration (regression guard for EA-S3 boilerplate strip).
  * A minimal BFIN-style YAML (single output entry, no boilerplate)
    validates without exception.

EB-S1 contract (multi-record dispatch inference, ADR 0005):
  * ``OutputFileConfig.is_multi_record`` is a computed property derived from
    the ``mapping`` file extension: ``.yaml`` / ``.yml`` -> ``True``;
    anything else (typically ``.json``) -> ``False``.
  * Legacy ``multi_record`` and ``discriminator_field`` keys on
    ``output_files[]`` entries are rejected with an actionable
    ``ValidationError`` citing ADR 0005.

The models under test are schema-only and not yet wired into
``PathResolver.source_config()``; these tests pin the contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

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
    """A bare ``OutputFileConfig`` validates with all implicit defaults.

    Also covers EB-S1: a ``.yaml`` mapping infers ``is_multi_record == True``
    without any explicit flag.
    """
    cfg = OutputFileConfig(
        file_type="ATOCTRAN",
        glob="atoctran_*.txt",
        mapping="config/mappings/SHAW_ATOCTRAN.yaml",
    )
    assert cfg.rules == ""
    # EB-S1: .yaml mapping -> multi-record inferred True.
    assert cfg.is_multi_record is True
    assert cfg.strict_fixed_width is True
    assert cfg.strict_level == "all"
    assert isinstance(cfg.tolerance, ToleranceConfig)
    assert cfg.tolerance.ignore_fields == []
    assert cfg.tolerance.max_errors == 0
    assert cfg.tolerance.max_error_pct == 0.0


def test_defaults_match_legacy_explicit() -> None:
    """The legacy explicit-boilerplate dict (sans EB-S1 rejected keys) and
    a bare dict produce equal models -- proving EA-S3 can safely strip the
    boilerplate from committed YAMLs without changing behaviour.
    """
    explicit = OutputFileConfig.model_validate(
        {
            "file_type": "CDSTRANS_EFB",
            "glob": "cdstrans_efb_*.txt",
            "mapping": "config/mappings/SHAW_CDSTRANS_EFB.json",
            "rules": "config/rules/SHAW_CDSTRANS_EFB.json",
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
    # EB-S1: .json mapping -> is_multi_record inferred False.
    assert explicit.is_multi_record is False
    assert minimal.is_multi_record is False


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


def test_shaw_yaml_post_migration_loads() -> None:
    """The committed SHAW.yml (post EB-S1 inline migration) passes through
    ``SourceConfig.model_validate``. Spot-checks that ``is_multi_record`` is
    inferred correctly from the mapping extension on every output entry.

    Regression guard for both EA-S3 (boilerplate strip) and EB-S1 (multi-
    record inference). The legacy ``multi_record:`` /
    ``discriminator_field:`` keys must NOT appear in SHAW.yml -- the
    OutputFileConfig validator would reject them.
    """
    raw = yaml.safe_load(SHAW_YAML_PATH.read_text(encoding="utf-8"))
    cfg = SourceConfig.model_validate(raw)
    assert cfg.source == "SHAW"
    assert len(cfg.output_files) == SHAW_EXPECTED_OUTPUT_COUNT
    assert len(cfg.input_files) == SHAW_EXPECTED_INPUT_COUNT

    # EB-S1: inferred multi-record dispatch by mapping extension.
    multi_record_file_types = {
        out.file_type
        for out in cfg.output_files
        if out.is_multi_record
    }
    # Only TRANERT and ATOCTRAN ship umbrella .yaml mappings today.
    assert multi_record_file_types == {"ATOCTRAN", "TRANERT"}

    # Spot-check explicit values + computed flags on a multi-record entry.
    atoctran = next(o for o in cfg.output_files if o.file_type == "ATOCTRAN")
    assert atoctran.is_multi_record is True
    assert atoctran.mapping.endswith(".yaml")
    assert atoctran.strict_level == "all"

    # Spot-check a flat-mapping entry is inferred single-record.
    p327 = next(o for o in cfg.output_files if o.file_type == "P327")
    assert p327.is_multi_record is False
    assert p327.mapping.endswith(".json")

    # CONTACT currently flat .json -> inferred single-record until the
    # umbrella YAML lands (then mapping flips to *.yaml).
    contact = next(o for o in cfg.output_files if o.file_type == "CONTACT")
    assert contact.is_multi_record is False

    # Source-level overrides survive validation.
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
    # EB-S1: .json -> inferred single-record.
    assert out.is_multi_record is False
    assert out.tolerance.max_errors == 0
    assert out.tolerance.max_error_pct == 0.0
    assert out.tolerance.ignore_fields == []


# ---------------------------------------------------------------------------
# EB-S1: multi-record dispatch inferred from mapping file extension.
# ---------------------------------------------------------------------------


def test_yaml_mapping_is_multi_record() -> None:
    """``mapping: "*.yaml"`` -> ``is_multi_record == True`` (umbrella)."""
    cfg = OutputFileConfig(
        file_type="TRANERT",
        glob="tranert_shaw_*.txt",
        mapping="config/mappings/SHAW_TRANERT.yaml",
    )
    assert cfg.is_multi_record is True


def test_yml_mapping_is_multi_record() -> None:
    """``mapping: "*.yml"`` also counts as an umbrella (case-insensitive)."""
    cfg = OutputFileConfig(
        file_type="EXAMPLE",
        glob="example_*.txt",
        mapping="config/mappings/EXAMPLE.YML",
    )
    assert cfg.is_multi_record is True


def test_json_mapping_is_flat() -> None:
    """``mapping: "*.json"`` -> ``is_multi_record == False`` (flat)."""
    cfg = OutputFileConfig(
        file_type="P327",
        glob="p327_*.txt",
        mapping="config/mappings/SHAW_P327.json",
    )
    assert cfg.is_multi_record is False


def test_legacy_multi_record_key_rejected() -> None:
    """Declaring ``multi_record`` raises a ``ValidationError`` with a
    message naming the deprecated key and citing ADR 0005.
    """
    with pytest.raises(ValidationError) as exc_info:
        OutputFileConfig.model_validate(
            {
                "file_type": "ATOCTRAN",
                "glob": "atoctran_*.txt",
                "mapping": "config/mappings/SHAW_ATOCTRAN.yaml",
                "multi_record": True,
            }
        )
    message = str(exc_info.value)
    assert "multi_record" in message
    assert "ADR 0005" in message
    assert "mapping file extension" in message


def test_legacy_discriminator_field_key_rejected() -> None:
    """Declaring ``discriminator_field`` raises a ``ValidationError`` with
    a message naming the deprecated key and citing ADR 0005.
    """
    with pytest.raises(ValidationError) as exc_info:
        OutputFileConfig.model_validate(
            {
                "file_type": "TRANERT",
                "glob": "tranert_*.txt",
                "mapping": "config/mappings/SHAW_TRANERT.yaml",
                "discriminator_field": "TRN-COD-ERT",
            }
        )
    message = str(exc_info.value)
    assert "discriminator_field" in message
    assert "ADR 0005" in message
    assert "mapping file extension" in message
