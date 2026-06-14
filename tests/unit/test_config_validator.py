"""Unit tests for src/utils/config_validator.py."""
import json
from pathlib import Path

import pytest

from src.utils.config_validator import (
    validate_mapping_file,
    validate_rules_file,
    validate_suite_file,
)


# ---------------------------------------------------------------------------
# Fixtures — write temp config files for validation
# ---------------------------------------------------------------------------

@pytest.fixture()
def valid_mapping(tmp_path: Path) -> Path:
    """Create a minimal valid mapping JSON file."""
    data = {
        "mapping_name": "test_mapping",
        "version": "1.0.0",
        "source": {"type": "file", "format": "pipe_delimited"},
        "target": {"type": "database"},
        "fields": [
            {
                "name": "account_id",
                "data_type": "string",
                "target_name": "ACCOUNT_ID",
            }
        ],
    }
    p = tmp_path / "valid_mapping.json"
    p.write_text(json.dumps(data))
    return p


@pytest.fixture()
def invalid_mapping_missing_fields(tmp_path: Path) -> Path:
    """Mapping JSON missing required top-level keys."""
    data = {"mapping_name": "incomplete"}
    p = tmp_path / "bad_mapping.json"
    p.write_text(json.dumps(data))
    return p


@pytest.fixture()
def invalid_mapping_bad_json(tmp_path: Path) -> Path:
    """File that is not valid JSON."""
    p = tmp_path / "broken.json"
    p.write_text("{not valid json!!")
    return p


@pytest.fixture()
def invalid_mapping_empty_fields(tmp_path: Path) -> Path:
    """Mapping with empty fields array."""
    data = {
        "mapping_name": "test",
        "version": "1.0.0",
        "source": {"type": "file"},
        "target": {"type": "database"},
        "fields": [],
    }
    p = tmp_path / "empty_fields.json"
    p.write_text(json.dumps(data))
    return p


@pytest.fixture()
def invalid_mapping_field_missing_name(tmp_path: Path) -> Path:
    """Mapping with a field entry missing required sub-keys."""
    data = {
        "mapping_name": "test",
        "version": "1.0.0",
        "source": {"type": "file"},
        "target": {"type": "database"},
        "fields": [{"data_type": "string"}],
    }
    p = tmp_path / "field_no_name.json"
    p.write_text(json.dumps(data))
    return p


@pytest.fixture()
def valid_rules(tmp_path: Path) -> Path:
    """Create a minimal valid rules JSON file."""
    data = {
        "metadata": {
            "name": "test_rules",
            "description": "Test rules file",
        },
        "rules": [
            {
                "id": "r001",
                "name": "Some Rule",
                "type": "field_validation",
                "severity": "error",
                "field": "ACCT",
                "operator": "not_null",
                "enabled": True,
            }
        ],
    }
    p = tmp_path / "valid_rules.json"
    p.write_text(json.dumps(data))
    return p


@pytest.fixture()
def invalid_rules_missing_metadata(tmp_path: Path) -> Path:
    """Rules JSON missing metadata section."""
    data = {"rules": []}
    p = tmp_path / "no_meta.json"
    p.write_text(json.dumps(data))
    return p


@pytest.fixture()
def invalid_rules_bad_rule(tmp_path: Path) -> Path:
    """Rules JSON with a rule missing required keys."""
    data = {
        "metadata": {"name": "x", "description": "x"},
        "rules": [{"id": "r001"}],
    }
    p = tmp_path / "bad_rule.json"
    p.write_text(json.dumps(data))
    return p


@pytest.fixture()
def valid_suite(tmp_path: Path) -> Path:
    """Create a minimal valid suite YAML file."""
    content = (
        "name: test-suite\n"
        "description: A test suite\n"
        "steps:\n"
        "  - name: step1\n"
        "    type: validate\n"
        "    file_pattern: data/test.txt\n"
        "    mapping: config/mappings/test.json\n"
    )
    p = tmp_path / "valid_suite.yaml"
    p.write_text(content)
    return p


@pytest.fixture()
def invalid_suite_bad_yaml(tmp_path: Path) -> Path:
    """File that is not valid YAML."""
    p = tmp_path / "bad.yaml"
    p.write_text("name: [\ninvalid yaml {{{}}")
    return p


@pytest.fixture()
def invalid_suite_missing_keys(tmp_path: Path) -> Path:
    """Suite YAML missing required keys."""
    content = "description: no name or steps\n"
    p = tmp_path / "missing.yaml"
    p.write_text(content)
    return p


@pytest.fixture()
def invalid_suite_step_missing_keys(tmp_path: Path) -> Path:
    """Suite YAML with a step missing required keys."""
    content = (
        "name: test-suite\n"
        "steps:\n"
        "  - name: step1\n"
    )
    p = tmp_path / "bad_step.yaml"
    p.write_text(content)
    return p


# ---------------------------------------------------------------------------
# Mapping validation tests
# ---------------------------------------------------------------------------

class TestValidateMapping:
    """Tests for validate_mapping_file."""

    def test_valid_mapping_passes(self, valid_mapping: Path) -> None:
        errors = validate_mapping_file(valid_mapping)
        assert errors == []

    def test_invalid_json_reports_parse_error(self, invalid_mapping_bad_json: Path) -> None:
        errors = validate_mapping_file(invalid_mapping_bad_json)
        assert len(errors) == 1
        assert "parse" in errors[0].lower() or "json" in errors[0].lower()

    def test_missing_required_fields(self, invalid_mapping_missing_fields: Path) -> None:
        errors = validate_mapping_file(invalid_mapping_missing_fields)
        assert len(errors) >= 1
        # Should mention at least one missing key
        combined = " ".join(errors).lower()
        assert "source" in combined or "fields" in combined or "version" in combined

    def test_empty_fields_array(self, invalid_mapping_empty_fields: Path) -> None:
        errors = validate_mapping_file(invalid_mapping_empty_fields)
        assert any("fields" in e.lower() and "empty" in e.lower() for e in errors)

    def test_field_missing_name(self, invalid_mapping_field_missing_name: Path) -> None:
        errors = validate_mapping_file(invalid_mapping_field_missing_name)
        assert any("name" in e.lower() for e in errors)

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        errors = validate_mapping_file(tmp_path / "does_not_exist.json")
        assert len(errors) == 1
        assert "not found" in errors[0].lower() or "does not exist" in errors[0].lower()


# ---------------------------------------------------------------------------
# Rules validation tests
# ---------------------------------------------------------------------------

class TestValidateRules:
    """Tests for validate_rules_file."""

    def test_valid_rules_passes(self, valid_rules: Path) -> None:
        errors = validate_rules_file(valid_rules)
        assert errors == []

    def test_invalid_json(self, invalid_mapping_bad_json: Path) -> None:
        errors = validate_rules_file(invalid_mapping_bad_json)
        assert len(errors) == 1

    def test_missing_metadata(self, invalid_rules_missing_metadata: Path) -> None:
        errors = validate_rules_file(invalid_rules_missing_metadata)
        assert any("metadata" in e.lower() for e in errors)

    def test_rule_missing_required_keys(self, invalid_rules_bad_rule: Path) -> None:
        errors = validate_rules_file(invalid_rules_bad_rule)
        assert len(errors) >= 1
        combined = " ".join(errors).lower()
        assert "name" in combined or "type" in combined or "severity" in combined


# ---------------------------------------------------------------------------
# Suite validation tests
# ---------------------------------------------------------------------------

class TestValidateSuite:
    """Tests for validate_suite_file."""

    def test_valid_suite_passes(self, valid_suite: Path) -> None:
        errors = validate_suite_file(valid_suite)
        assert errors == []

    def test_invalid_yaml(self, invalid_suite_bad_yaml: Path) -> None:
        errors = validate_suite_file(invalid_suite_bad_yaml)
        assert len(errors) == 1
        assert "yaml" in errors[0].lower() or "parse" in errors[0].lower()

    def test_missing_required_keys(self, invalid_suite_missing_keys: Path) -> None:
        errors = validate_suite_file(invalid_suite_missing_keys)
        combined = " ".join(errors).lower()
        assert "name" in combined or "steps" in combined

    def test_step_missing_required_keys(self, invalid_suite_step_missing_keys: Path) -> None:
        errors = validate_suite_file(invalid_suite_step_missing_keys)
        assert len(errors) >= 1
        combined = " ".join(errors).lower()
        assert "type" in combined or "file_pattern" in combined or "mapping" in combined

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        errors = validate_suite_file(tmp_path / "nope.yaml")
        assert len(errors) == 1
        assert "not found" in errors[0].lower()
