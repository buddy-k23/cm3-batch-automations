"""Unit tests for UniversalMappingParser (S17-4, #432)."""

import json

import pytest

from src.config.universal_mapping_parser import (
    FieldSpec,
    UniversalMappingParser,
)


def _fw_mapping():
    return {
        "mapping_name": "fw_map",
        "version": "1.0",
        "source": {"format": "fixed_width"},
        "key_columns": ["id"],
        "fields": [
            {
                "name": "id",
                "data_type": "string",
                "position": 0,
                "length": 5,
                "required": True,
            },
            {
                "name": "amount",
                "data_type": "numeric",
                "position": 5,
                "length": 10,
                "transformations": [{"type": "trim"}],
                "validation_rules": [{"type": "numeric"}],
            },
        ],
    }


def _delim_mapping():
    return {
        "mapping_name": "csv_map",
        "version": "1.0",
        "source": {"format": "csv", "delimiter": ",", "has_header": True},
        "fields": [
            {"name": "a", "source_name": "src_a", "target_name": "DB_A", "data_type": "string"},
            {"name": "b", "data_type": "string"},
        ],
    }


class TestConstruction:
    def test_from_dict(self):
        p = UniversalMappingParser(mapping_dict=_fw_mapping())
        assert len(p.fields) == 2
        assert isinstance(p.fields[0], FieldSpec)

    def test_from_file(self, tmp_path):
        f = tmp_path / "m.json"
        f.write_text(json.dumps(_delim_mapping()))
        p = UniversalMappingParser(mapping_path=str(f))
        assert p.get_format() == "csv"

    def test_requires_one_source(self):
        with pytest.raises(ValueError, match="Either mapping_path or mapping_dict"):
            UniversalMappingParser()


class TestAccessors:
    def test_format_delimiter_header(self):
        p = UniversalMappingParser(mapping_dict=_delim_mapping())
        assert p.get_format() == "csv"
        assert p.get_delimiter() == ","
        assert p.has_header() is True

    def test_delimiter_default(self):
        m = _delim_mapping()
        del m["source"]["delimiter"]
        p = UniversalMappingParser(mapping_dict=m)
        assert p.get_delimiter() == "|"

    def test_column_names_default_to_field_name(self):
        p = UniversalMappingParser(mapping_dict=_delim_mapping())
        # field 'a' has source_name 'src_a'; field 'b' defaults to 'b'
        assert p.get_column_names() == ["src_a", "b"]
        assert p.get_target_column_names() == ["DB_A", "b"]

    def test_column_mapping(self):
        p = UniversalMappingParser(mapping_dict=_delim_mapping())
        assert p.get_column_mapping() == {"src_a": "DB_A", "b": "b"}

    def test_required_and_key_columns(self):
        p = UniversalMappingParser(mapping_dict=_fw_mapping())
        assert p.get_required_fields() == ["id"]
        assert p.get_key_columns() == ["id"]


class TestFixedWidth:
    def test_field_positions(self):
        p = UniversalMappingParser(mapping_dict=_fw_mapping())
        assert p.get_field_positions() == [("id", 0, 5), ("amount", 5, 15)]

    def test_field_positions_requires_fixed_width(self):
        p = UniversalMappingParser(mapping_dict=_delim_mapping())
        with pytest.raises(ValueError, match="fixed-width"):
            p.get_field_positions()

    def test_field_positions_missing_length_raises(self):
        m = _fw_mapping()
        del m["fields"][0]["length"]
        p = UniversalMappingParser(mapping_dict=m)
        with pytest.raises(ValueError, match="missing position or length"):
            p.get_field_positions()

    def test_total_record_length(self):
        p = UniversalMappingParser(mapping_dict=_fw_mapping())
        assert p.get_total_record_length() == 15

    def test_total_record_length_requires_fixed_width(self):
        p = UniversalMappingParser(mapping_dict=_delim_mapping())
        with pytest.raises(ValueError, match="fixed-width"):
            p.get_total_record_length()


class TestFieldLookup:
    def test_get_field_spec_found_and_missing(self):
        p = UniversalMappingParser(mapping_dict=_fw_mapping())
        assert p.get_field_spec("amount").name == "amount"
        assert p.get_field_spec("nope") is None

    def test_transformations_and_validations(self):
        p = UniversalMappingParser(mapping_dict=_fw_mapping())
        assert p.get_transformations("amount") == [{"type": "trim"}]
        assert p.get_validations("amount") == [{"type": "numeric"}]
        # missing field -> empty
        assert p.get_transformations("nope") == []
        assert p.get_validations("nope") == []


class TestValidateSchema:
    def test_valid(self):
        p = UniversalMappingParser(mapping_dict=_fw_mapping())
        result = p.validate_schema()
        assert result["valid"] is True
        assert result["errors"] == []

    def test_missing_top_level_field(self):
        m = _fw_mapping()
        del m["version"]
        p = UniversalMappingParser(mapping_dict=m)
        result = p.validate_schema()
        assert result["valid"] is False
        assert any("version" in e for e in result["errors"])

    def test_fixed_width_missing_position(self):
        m = _fw_mapping()
        del m["fields"][0]["position"]
        p = UniversalMappingParser(mapping_dict=m)
        result = p.validate_schema()
        assert any("missing position" in e for e in result["errors"])

    def test_duplicate_field_names(self):
        m = _fw_mapping()
        m["fields"][1]["name"] = "id"
        p = UniversalMappingParser(mapping_dict=m)
        result = p.validate_schema()
        assert any("Duplicate field names" in e for e in result["errors"])

    def test_key_column_not_in_fields(self):
        m = _fw_mapping()
        m["key_columns"] = ["ghost"]
        p = UniversalMappingParser(mapping_dict=m)
        result = p.validate_schema()
        assert any("ghost" in e for e in result["errors"])


class TestSerialization:
    def test_to_dict_returns_mapping(self):
        m = _fw_mapping()
        p = UniversalMappingParser(mapping_dict=m)
        assert p.to_dict() == m

    def test_save_writes_json(self, tmp_path):
        p = UniversalMappingParser(mapping_dict=_fw_mapping())
        out = tmp_path / "nested" / "out.json"
        p.save(str(out))
        assert out.exists()
        assert json.loads(out.read_text())["mapping_name"] == "fw_map"

    def test_repr(self):
        p = UniversalMappingParser(mapping_dict=_fw_mapping())
        r = repr(p)
        assert "fw_map" in r and "fixed_width" in r
