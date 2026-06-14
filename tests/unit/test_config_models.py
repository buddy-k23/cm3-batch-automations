"""Unit tests for Pydantic config models (issue #89).

Tests are written BEFORE the implementation (TDD red phase).
They validate that src.config.models provides typed models for
mapping, rules, and workflow configs — with fail-fast validation
on bad input.
"""

from __future__ import annotations

import json
import pytest
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _customer_batch_mapping() -> dict:
    """Return a valid MappingConfig-compatible dict matching
    config/mappings/customer_batch_universal.json."""
    return {
        "mapping_name": "customer_batch_universal",
        "version": "1.0.0",
        "description": "Test mapping",
        "source": {
            "format": "pipe_delimited",
            "delimiter": "|",
            "has_header": False,
            "encoding": "UTF-8",
        },
        "fields": [
            {
                "name": "customer_id",
                "data_type": "string",
                "required": True,
                "position": None,
                "length": None,
            },
            {
                "name": "account_balance",
                "data_type": "decimal",
                "required": True,
                "position": None,
                "length": None,
            },
        ],
        "key_columns": ["customer_id"],
    }


def _fixed_width_mapping() -> dict:
    """Return a valid fixed-width MappingConfig-compatible dict."""
    return {
        "mapping_name": "p327_fixed",
        "version": "2.0",
        "source": {
            "format": "fixed_width",
            "has_header": False,
            "encoding": "UTF-8",
        },
        "fields": [
            {"name": "ACCT_NUM", "data_type": "string", "required": True, "position": 1, "length": 10},
            {"name": "BALANCE", "data_type": "decimal", "required": False, "position": 11, "length": 15},
        ],
        "key_columns": ["ACCT_NUM"],
    }


def _valid_rules_config() -> dict:
    """Return a valid RulesConfig-compatible dict."""
    return {
        "metadata": {
            "name": "p327_rules",
            "description": "Test rules",
            "created_by": "test",
            "created_date": "2026-03-01T00:00:00Z",
        },
        "rules": [
            {
                "id": "R001",
                "name": "Not null check",
                "type": "field_validation",
                "severity": "error",
                "operator": "not_null",
                "field": "ACCT_NUM",
                "enabled": True,
            },
            {
                "id": "R002",
                "name": "Range check",
                "type": "field_validation",
                "severity": "warning",
                "operator": "range",
                "field": "BALANCE",
                "enabled": True,
            },
        ],
    }


# ===========================================================================
# SourceConfig tests
# ===========================================================================

class TestSourceConfig:
    """Tests for SourceConfig Pydantic model."""

    def test_valid_pipe_delimited(self):
        from src.config.models import SourceConfig
        cfg = SourceConfig(format="pipe_delimited", delimiter="|", has_header=False, encoding="UTF-8")
        assert cfg.format == "pipe_delimited"
        assert cfg.delimiter == "|"
        assert cfg.has_header is False

    def test_valid_fixed_width(self):
        from src.config.models import SourceConfig
        cfg = SourceConfig(format="fixed_width", has_header=False, encoding="UTF-8")
        assert cfg.format == "fixed_width"
        # delimiter is optional for fixed_width
        assert cfg.delimiter is None

    def test_valid_csv_format(self):
        from src.config.models import SourceConfig
        cfg = SourceConfig(format="csv", delimiter=",", has_header=True)
        assert cfg.format == "csv"

    def test_missing_format_raises(self):
        from src.config.models import SourceConfig
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            SourceConfig()

    def test_defaults_applied(self):
        from src.config.models import SourceConfig
        cfg = SourceConfig(format="pipe_delimited")
        # has_header defaults to False, encoding defaults to UTF-8
        assert cfg.has_header is False
        assert cfg.encoding == "UTF-8"

    def test_model_dump_roundtrip(self):
        from src.config.models import SourceConfig
        data = {"format": "pipe_delimited", "delimiter": "|", "has_header": True, "encoding": "UTF-8"}
        cfg = SourceConfig(**data)
        dumped = cfg.model_dump()
        assert dumped["format"] == "pipe_delimited"
        assert dumped["delimiter"] == "|"


# ===========================================================================
# FieldConfig tests
# ===========================================================================

class TestFieldConfig:
    """Tests for FieldConfig Pydantic model."""

    def test_minimal_valid_field(self):
        from src.config.models import FieldConfig
        f = FieldConfig(name="customer_id", data_type="string")
        assert f.name == "customer_id"
        assert f.data_type == "string"
        assert f.required is False
        assert f.position is None
        assert f.length is None

    def test_full_fixed_width_field(self):
        from src.config.models import FieldConfig
        f = FieldConfig(
            name="ACCT_NUM",
            data_type="string",
            required=True,
            position=1,
            length=10,
            description="Account number",
            source_name="ACCT_NUM",
            target_name="account_number",
        )
        assert f.position == 1
        assert f.length == 10
        assert f.target_name == "account_number"

    def test_missing_name_raises(self):
        from src.config.models import FieldConfig
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            FieldConfig(data_type="string")

    def test_missing_data_type_raises(self):
        from src.config.models import FieldConfig
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            FieldConfig(name="col1")

    def test_transformations_default_empty_list(self):
        from src.config.models import FieldConfig
        f = FieldConfig(name="x", data_type="string")
        assert f.transformations == []
        assert f.validation_rules == []

    def test_model_dump_is_dict(self):
        from src.config.models import FieldConfig
        f = FieldConfig(name="col1", data_type="string", required=True)
        d = f.model_dump()
        assert isinstance(d, dict)
        assert d["name"] == "col1"

    def test_extra_keys_ignored(self):
        """Unknown keys in input dict should not raise — they are silently ignored."""
        from src.config.models import FieldConfig
        # This matches the pattern from real mapping JSON files which may have
        # extra fields like 'source_name', 'target_name', 'description'.
        f = FieldConfig(name="x", data_type="string", unknown_future_key="value")
        assert f.name == "x"


# ===========================================================================
# MappingConfig tests
# ===========================================================================

class TestMappingConfig:
    """Tests for MappingConfig Pydantic model."""

    def test_valid_pipe_delimited_mapping(self):
        from src.config.models import MappingConfig
        data = _customer_batch_mapping()
        cfg = MappingConfig(**data)
        assert cfg.mapping_name == "customer_batch_universal"
        assert cfg.version == "1.0.0"
        assert len(cfg.fields) == 2

    def test_valid_fixed_width_mapping(self):
        from src.config.models import MappingConfig
        data = _fixed_width_mapping()
        cfg = MappingConfig(**data)
        assert cfg.mapping_name == "p327_fixed"
        assert cfg.source.format == "fixed_width"
        assert cfg.fields[0].position == 1
        assert cfg.fields[0].length == 10

    def test_missing_mapping_name_raises(self):
        from src.config.models import MappingConfig
        from pydantic import ValidationError
        data = _customer_batch_mapping()
        del data["mapping_name"]
        with pytest.raises(ValidationError):
            MappingConfig(**data)

    def test_missing_source_raises(self):
        from src.config.models import MappingConfig
        from pydantic import ValidationError
        data = _customer_batch_mapping()
        del data["source"]
        with pytest.raises(ValidationError):
            MappingConfig(**data)

    def test_empty_fields_list_raises(self):
        from src.config.models import MappingConfig
        from pydantic import ValidationError
        data = _customer_batch_mapping()
        data["fields"] = []
        with pytest.raises(ValidationError):
            MappingConfig(**data)

    def test_missing_fields_raises(self):
        from src.config.models import MappingConfig
        from pydantic import ValidationError
        data = _customer_batch_mapping()
        del data["fields"]
        with pytest.raises(ValidationError):
            MappingConfig(**data)

    def test_version_defaults_to_unknown(self):
        from src.config.models import MappingConfig
        data = _customer_batch_mapping()
        del data["version"]
        cfg = MappingConfig(**data)
        assert cfg.version == "unknown"

    def test_key_columns_defaults_empty(self):
        from src.config.models import MappingConfig
        data = _customer_batch_mapping()
        del data["key_columns"]
        cfg = MappingConfig(**data)
        assert cfg.key_columns == []

    def test_model_dump_is_dict_with_nested(self):
        from src.config.models import MappingConfig
        data = _customer_batch_mapping()
        cfg = MappingConfig(**data)
        d = cfg.model_dump()
        assert isinstance(d, dict)
        # source should be a dict (for backward-compat with existing code that
        # does mapping_config["source"]["format"])
        assert isinstance(d["source"], dict)
        assert isinstance(d["fields"], list)

    def test_from_dict_classmethod(self):
        from src.config.models import MappingConfig
        data = _customer_batch_mapping()
        cfg = MappingConfig.from_dict(data)
        assert cfg.mapping_name == "customer_batch_universal"

    def test_from_json_string(self):
        from src.config.models import MappingConfig
        data = _customer_batch_mapping()
        cfg = MappingConfig.from_json(json.dumps(data))
        assert cfg.mapping_name == "customer_batch_universal"

    def test_load_from_file(self, tmp_path: Path):
        from src.config.models import MappingConfig
        data = _customer_batch_mapping()
        f = tmp_path / "mapping.json"
        f.write_text(json.dumps(data))
        cfg = MappingConfig.from_file(str(f))
        assert cfg.mapping_name == "customer_batch_universal"

    def test_load_from_nonexistent_file_raises(self):
        from src.config.models import MappingConfig
        with pytest.raises(FileNotFoundError):
            MappingConfig.from_file("/nonexistent/path/mapping.json")

    def test_source_is_sourceconfig_instance(self):
        from src.config.models import MappingConfig, SourceConfig
        cfg = MappingConfig(**_customer_batch_mapping())
        assert isinstance(cfg.source, SourceConfig)

    def test_fields_are_fieldconfig_instances(self):
        from src.config.models import MappingConfig, FieldConfig
        cfg = MappingConfig(**_customer_batch_mapping())
        for field in cfg.fields:
            assert isinstance(field, FieldConfig)

    def test_real_customer_batch_json(self):
        """Load the real config/mappings/customer_batch_universal.json."""
        from src.config.models import MappingConfig
        mapping_path = (
            Path(__file__).parent.parent.parent
            / "config" / "mappings" / "customer_batch_universal.json"
        )
        if mapping_path.exists():
            cfg = MappingConfig.from_file(str(mapping_path))
            assert cfg.mapping_name == "customer_batch_universal"
            assert len(cfg.fields) > 0


# ===========================================================================
# RuleConfig tests
# ===========================================================================

class TestRuleConfig:
    """Tests for RuleConfig Pydantic model."""

    def test_minimal_valid_rule(self):
        from src.config.models import RuleConfig
        r = RuleConfig(id="R001", name="Not null", type="field_validation",
                       severity="error", operator="not_null")
        assert r.id == "R001"
        assert r.enabled is True  # default

    def test_missing_id_raises(self):
        from src.config.models import RuleConfig
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            RuleConfig(name="x", type="field_validation", severity="error", operator="not_null")

    def test_missing_severity_raises(self):
        from src.config.models import RuleConfig
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            RuleConfig(id="R001", name="x", type="field_validation", operator="not_null")

    def test_enabled_defaults_true(self):
        from src.config.models import RuleConfig
        r = RuleConfig(id="R001", name="x", type="field_validation",
                       severity="error", operator="not_null")
        assert r.enabled is True

    def test_disabled_rule(self):
        from src.config.models import RuleConfig
        r = RuleConfig(id="R001", name="x", type="field_validation",
                       severity="error", operator="not_null", enabled=False)
        assert r.enabled is False

    def test_extra_operator_fields_allowed(self):
        """RuleConfig must accept extra operator-specific fields (pattern, value, etc.)."""
        from src.config.models import RuleConfig
        r = RuleConfig(
            id="R001", name="regex", type="field_validation",
            severity="error", operator="regex",
            field="ACCT_NUM", pattern="^[0-9]{10}$",
        )
        # Pattern is an extra field stored as model extra or via a dedicated attribute
        assert r.id == "R001"

    def test_model_dump_roundtrip(self):
        from src.config.models import RuleConfig
        r = RuleConfig(id="R002", name="range", type="field_validation",
                       severity="warning", operator="range", field="BALANCE")
        d = r.model_dump()
        assert d["id"] == "R002"
        assert d["operator"] == "range"


# ===========================================================================
# RulesConfig tests
# ===========================================================================

class TestRulesConfig:
    """Tests for RulesConfig Pydantic model."""

    def test_valid_rules_config(self):
        from src.config.models import RulesConfig
        data = _valid_rules_config()
        cfg = RulesConfig(**data)
        assert len(cfg.rules) == 2

    def test_missing_rules_key_raises(self):
        from src.config.models import RulesConfig
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            RulesConfig(metadata={"name": "x"})

    def test_empty_rules_list_allowed(self):
        from src.config.models import RulesConfig
        cfg = RulesConfig(rules=[])
        assert cfg.rules == []

    def test_rules_are_ruleconfig_instances(self):
        from src.config.models import RulesConfig, RuleConfig
        data = _valid_rules_config()
        cfg = RulesConfig(**data)
        for rule in cfg.rules:
            assert isinstance(rule, RuleConfig)

    def test_from_dict_classmethod(self):
        from src.config.models import RulesConfig
        data = _valid_rules_config()
        cfg = RulesConfig.from_dict(data)
        assert len(cfg.rules) == 2

    def test_from_json_string(self):
        from src.config.models import RulesConfig
        data = _valid_rules_config()
        cfg = RulesConfig.from_json(json.dumps(data))
        assert len(cfg.rules) == 2

    def test_from_file(self, tmp_path: Path):
        from src.config.models import RulesConfig
        data = _valid_rules_config()
        f = tmp_path / "rules.json"
        f.write_text(json.dumps(data))
        cfg = RulesConfig.from_file(str(f))
        assert len(cfg.rules) == 2

    def test_from_nonexistent_file_raises(self):
        from src.config.models import RulesConfig
        with pytest.raises(FileNotFoundError):
            RulesConfig.from_file("/nonexistent/rules.json")

    def test_model_dump_is_serializable(self):
        from src.config.models import RulesConfig
        data = _valid_rules_config()
        cfg = RulesConfig(**data)
        d = cfg.model_dump()
        # Ensure it serializes cleanly to JSON (no non-serializable types)
        assert json.dumps(d)

    def test_real_p327_business_rules(self):
        """Load the real config/rules/p327_business_rules.json."""
        from src.config.models import RulesConfig
        rules_path = (
            Path(__file__).parent.parent.parent
            / "config" / "rules" / "p327_business_rules.json"
        )
        if rules_path.exists():
            cfg = RulesConfig.from_file(str(rules_path))
            assert len(cfg.rules) > 0


# ===========================================================================
# Integration: MappingConfig backward-compat with existing dict consumers
# ===========================================================================

class TestMappingConfigBackwardCompat:
    """Verify model_dump() output is compatible with existing dict-access patterns."""

    def test_source_dict_format_access(self):
        """Existing code does mapping_config['source']['format']."""
        from src.config.models import MappingConfig
        cfg = MappingConfig(**_customer_batch_mapping())
        d = cfg.model_dump()
        assert d["source"]["format"] == "pipe_delimited"

    def test_fields_list_name_access(self):
        """Existing code does [f['name'] for f in mapping_config['fields']]."""
        from src.config.models import MappingConfig
        cfg = MappingConfig(**_customer_batch_mapping())
        d = cfg.model_dump()
        names = [f["name"] for f in d["fields"]]
        assert "customer_id" in names

    def test_has_header_access(self):
        """Existing code does mapping_config['source']['has_header']."""
        from src.config.models import MappingConfig
        cfg = MappingConfig(**_customer_batch_mapping())
        d = cfg.model_dump()
        assert d["source"]["has_header"] is False

    def test_key_columns_access(self):
        """Existing code does mapping_config.get('key_columns', [])."""
        from src.config.models import MappingConfig
        cfg = MappingConfig(**_customer_batch_mapping())
        d = cfg.model_dump()
        assert d["key_columns"] == ["customer_id"]
