"""Unit tests for run_multi_record_validate_service (S17-4, #432)."""

from unittest import mock

import pytest

from src.services import multi_record_validate_service as svc


def test_invalid_yaml_raises_value_error():
    with pytest.raises(ValueError, match="Invalid YAML"):
        svc.run_multi_record_validate_service("/tmp/f", "key: : :\n  - [")


def test_non_mapping_yaml_raises():
    with pytest.raises(ValueError, match="must be a YAML mapping"):
        svc.run_multi_record_validate_service("/tmp/f", "- just\n- a\n- list")


def test_invalid_config_structure_raises():
    # valid YAML mapping but not a valid MultiRecordConfig
    with pytest.raises(ValueError, match="Invalid multi-record config structure"):
        svc.run_multi_record_validate_service("/tmp/f", "totally: bogus")


def test_valid_config_delegates_to_validator():
    yaml_text = (
        "discriminator:\n"
        "  field: rec_type\n"
        "  position: [0, 1]\n"
        "record_types:\n"
        "  H:\n"
        "    name: header\n"
        "    mapping_file: m.json\n"
    )
    fake_result = {"valid": True, "total_rows": 0}
    with mock.patch.object(svc, "__name__", svc.__name__):
        with mock.patch(
            "src.config.multi_record_config.MultiRecordConfig"
        ) as cfg_cls, mock.patch(
            "src.validators.multi_record_validator.MultiRecordValidator"
        ) as val_cls:
            cfg_cls.return_value = mock.Mock()
            val_cls.return_value.validate.return_value = fake_result
            out = svc.run_multi_record_validate_service("/data/f.txt", yaml_text)
    assert out == fake_result
    val_cls.return_value.validate.assert_called_once()
