from src.config.fixed_width_multitype_normalizer import (
    normalize_fixed_width_mapping_v2,
    validate_fixed_width_mapping_v2,
)


def test_normalize_legacy_fixed_width_to_single_detail_record_type():
    legacy = {
        "mapping_name": "legacy_map",
        "version": "1.0.0",
        "source": {"type": "file", "format": "fixed_width"},
        "fields": [
            {"name": "A", "position": 1, "length": 2, "data_type": "string", "required": True},
            {"name": "B", "position": 3, "length": 3, "data_type": "string", "required": False},
        ],
    }

    out = normalize_fixed_width_mapping_v2(legacy)

    assert out["record_types"][0]["id"] == "DETAIL"
    assert out["record_types"][0]["classification"]["kind"] == "default"
    assert out["record_types"][0]["expected_total_width"] == 5
    assert out["file_rules"]["unknown_record_policy"] == "error"
    assert out["metadata"]["source_mapping_version"] == "legacy"


def test_v2_mapping_gets_defaults_without_destructive_changes():
    v2 = {
        "mapping_name": "multi",
        "record_types": [
            {
                "id": "DETAIL",
                "classification": {"kind": "default"},
                "fields": [{"name": "A", "position": 1, "length": 1, "data_type": "string", "required": True}],
            }
        ],
    }

    out = normalize_fixed_width_mapping_v2(v2)

    assert out["format"] == "fixed_width"
    assert out["version"] == "2.0"
    assert out["file_rules"]["required_record_types"] == []
    assert out["metadata"]["source_mapping_version"] == "v2"
    assert out["record_types"][0]["id"] == "DETAIL"


def test_non_fixed_width_mapping_is_unchanged():
    mapping = {
        "mapping_name": "csv",
        "source": {"type": "file", "format": "pipe_delimited"},
        "fields": [{"name": "A", "data_type": "string"}],
    }

    out = normalize_fixed_width_mapping_v2(mapping)

    assert "record_types" not in out
    assert out["source"]["format"] == "pipe_delimited"


def test_validate_v2_reports_duplicate_ids_and_multiple_defaults():
    bad = {
        "record_types": [
            {"id": "DETAIL", "classification": {"kind": "default"}, "fields": []},
            {"id": "DETAIL", "classification": {"kind": "default"}, "fields": []},
        ]
    }

    valid, errors = validate_fixed_width_mapping_v2(bad)

    assert valid is False
    assert any("duplicate record type ids" in e for e in errors)
    assert any("only one record type may use classification kind 'default'" in e for e in errors)
