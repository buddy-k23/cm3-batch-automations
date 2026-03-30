"""Unit tests for MultiRecordTransformEngine (Phase 5d).

Tests cover:
- Engine instantiated per record type with correct mapping
- Record type discrimination routes row to correct engine
- Record type without transforms uses noop passthrough
- Mixed record types each get their own transforms
- Missing discriminator field falls back to noop
- apply() returns transformed row plus injected record_type key
"""

from __future__ import annotations

import pytest

from src.transforms.multi_record_transform_engine import MultiRecordTransformEngine


# ---------------------------------------------------------------------------
# Fixtures — minimal multi-record config
# ---------------------------------------------------------------------------

def _make_config(types: list[dict]) -> dict:
    """Build a minimal multi-record config with a discriminator + record types."""
    return {
        "discriminator": {
            "field": "REC_TYPE",
        },
        "record_types": types,
    }


HEADER_TYPE = {
    "type_name": "HEADER",
    "discriminator_value": "H",
    "mapping": {
        "fields": [
            {"target_name": "REC_TYPE", "transformation": "Pass 'H'"},
            {"target_name": "TITLE",    "transformation": "Pass 'HDR'"},
        ]
    },
}

DETAIL_TYPE = {
    "type_name": "DETAIL",
    "discriminator_value": "D",
    "mapping": {
        "fields": [
            {"target_name": "REC_TYPE", "transformation": "Pass 'D'"},
            {"target_name": "AMOUNT",   "transformation": "Pass as is"},
        ]
    },
}

TRAILER_TYPE = {
    "type_name": "TRAILER",
    "discriminator_value": "T",
    "mapping": None,  # no transforms defined
}


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestMultiRecordTransformEngineInit:
    def test_accepts_valid_config(self):
        config = _make_config([HEADER_TYPE, DETAIL_TYPE])
        engine = MultiRecordTransformEngine(config)
        assert engine is not None

    def test_accepts_empty_record_types(self):
        config = _make_config([])
        engine = MultiRecordTransformEngine(config)
        assert engine is not None


# ---------------------------------------------------------------------------
# apply() — routing by discriminator
# ---------------------------------------------------------------------------

class TestMultiRecordApplyRouting:
    def test_header_row_gets_header_transforms(self):
        config = _make_config([HEADER_TYPE, DETAIL_TYPE])
        engine = MultiRecordTransformEngine(config)
        row = {"REC_TYPE": "H", "TITLE": "OLD", "AMOUNT": "999"}
        result = engine.apply(row)
        assert result["TITLE"] == "HDR"

    def test_detail_row_gets_detail_transforms(self):
        config = _make_config([HEADER_TYPE, DETAIL_TYPE])
        engine = MultiRecordTransformEngine(config)
        row = {"REC_TYPE": "D", "TITLE": "OLD", "AMOUNT": "42.00"}
        result = engine.apply(row)
        assert result["AMOUNT"] == "42.00"

    def test_two_different_rows_routed_independently(self):
        config = _make_config([HEADER_TYPE, DETAIL_TYPE])
        engine = MultiRecordTransformEngine(config)
        h_result = engine.apply({"REC_TYPE": "H", "TITLE": "X"})
        d_result = engine.apply({"REC_TYPE": "D", "AMOUNT": "99"})
        assert h_result["TITLE"] == "HDR"
        assert d_result["AMOUNT"] == "99"


# ---------------------------------------------------------------------------
# apply() — record type without transforms (None mapping)
# ---------------------------------------------------------------------------

class TestMultiRecordNoTransform:
    def test_type_with_none_mapping_passes_row_through(self):
        config = _make_config([TRAILER_TYPE])
        engine = MultiRecordTransformEngine(config)
        row = {"REC_TYPE": "T", "TOTAL": "100"}
        result = engine.apply(row)
        assert result["REC_TYPE"] == "T"
        assert result["TOTAL"] == "100"

    def test_unknown_discriminator_value_passes_row_through(self):
        config = _make_config([HEADER_TYPE])
        engine = MultiRecordTransformEngine(config)
        row = {"REC_TYPE": "X", "DATA": "abc"}
        result = engine.apply(row)
        # Unknown type — row returned unchanged
        assert result["DATA"] == "abc"

    def test_missing_discriminator_field_passes_row_through(self):
        config = _make_config([HEADER_TYPE])
        engine = MultiRecordTransformEngine(config)
        row = {"DATA": "abc"}  # no REC_TYPE
        result = engine.apply(row)
        assert result["DATA"] == "abc"


# ---------------------------------------------------------------------------
# apply() — record_type annotation in result
# ---------------------------------------------------------------------------

class TestMultiRecordResultAnnotation:
    def test_result_includes_record_type_annotation(self):
        config = _make_config([HEADER_TYPE])
        engine = MultiRecordTransformEngine(config)
        row = {"REC_TYPE": "H", "TITLE": "X"}
        result = engine.apply(row)
        assert result.get("_record_type") == "HEADER"

    def test_unknown_type_annotated_as_unknown(self):
        config = _make_config([HEADER_TYPE])
        engine = MultiRecordTransformEngine(config)
        row = {"REC_TYPE": "Z", "DATA": "x"}
        result = engine.apply(row)
        assert result.get("_record_type") == "UNKNOWN"


# ---------------------------------------------------------------------------
# apply_batch() — apply to a list of rows
# ---------------------------------------------------------------------------

class TestMultiRecordApplyBatch:
    def test_apply_batch_transforms_all_rows(self):
        config = _make_config([HEADER_TYPE, DETAIL_TYPE])
        engine = MultiRecordTransformEngine(config)
        rows = [
            {"REC_TYPE": "H", "TITLE": "X"},
            {"REC_TYPE": "D", "AMOUNT": "5"},
            {"REC_TYPE": "D", "AMOUNT": "10"},
        ]
        results = engine.apply_batch(rows)
        assert len(results) == 3
        assert results[0]["TITLE"] == "HDR"
        assert results[1]["AMOUNT"] == "5"
        assert results[2]["AMOUNT"] == "10"

    def test_apply_batch_empty_list(self):
        config = _make_config([HEADER_TYPE])
        engine = MultiRecordTransformEngine(config)
        assert engine.apply_batch([]) == []
