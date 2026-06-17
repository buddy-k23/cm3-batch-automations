"""Unit tests for MappingValidator (S17-4, #432)."""

import pandas as pd
import pytest

from src.validators.mapping_validator import MappingValidator


@pytest.fixture
def mapping():
    return {"file_a": "DB_A", "file_b": "DB_B"}


def test_validate_file_columns_reports_missing(mapping):
    v = MappingValidator(mapping)
    df = pd.DataFrame({"file_a": [1]})
    missing = v.validate_file_columns(df)
    assert missing == ["file_b"]


def test_validate_file_columns_none_missing(mapping):
    v = MappingValidator(mapping)
    df = pd.DataFrame({"file_a": [1], "file_b": [2]})
    assert v.validate_file_columns(df) == []


def test_validate_db_columns_reports_missing(mapping):
    v = MappingValidator(mapping)
    missing = v.validate_db_columns(["DB_A"])
    assert missing == ["DB_B"]


def test_validate_db_columns_none_missing(mapping):
    v = MappingValidator(mapping)
    assert v.validate_db_columns(["DB_A", "DB_B"]) == []


def test_get_unmapped_columns(mapping):
    v = MappingValidator(mapping)
    df = pd.DataFrame({"file_a": [1], "file_b": [2], "extra": [3]})
    assert v.get_unmapped_columns(df) == ["extra"]


def test_apply_mapping_renames_columns(mapping):
    v = MappingValidator(mapping)
    df = pd.DataFrame({"file_a": [1], "file_b": [2], "extra": [3]})
    out = v.apply_mapping(df)
    assert list(out.columns) == ["DB_A", "DB_B"]
    assert out["DB_A"].tolist() == [1]


def test_apply_mapping_raises_on_missing(mapping):
    v = MappingValidator(mapping)
    df = pd.DataFrame({"file_a": [1]})
    with pytest.raises(ValueError, match="Missing columns"):
        v.apply_mapping(df)
