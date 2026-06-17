"""Unit tests for MappingParser / MappingDocument / MappingProcessor (S17-4, #432)."""

import pandas as pd
import pytest

from src.config.mapping_parser import (
    ColumnMapping,
    MappingDocument,
    MappingParser,
    MappingProcessor,
)


def _mapping_dict(**overrides):
    base = {
        "mapping_name": "m",
        "version": "1.0",
        "description": "desc",
        "source": {"format": "csv"},
        "target": {"type": "file"},
        "key_columns": ["id"],
        "mappings": [
            {
                "source_column": "raw_id",
                "target_column": "ID",
                "data_type": "string",
                "required": True,
            },
            {
                "source_column": "raw_amt",
                "target_column": "AMT",
                "data_type": "numeric",
                "transformations": [{"type": "trim"}],
                "validation_rules": [{"type": "not_null"}],
            },
        ],
    }
    base.update(overrides)
    return base


def _doc():
    return MappingParser().parse(_mapping_dict())


class TestMappingParser:
    def test_parse_builds_document(self):
        doc = _doc()
        assert isinstance(doc, MappingDocument)
        assert doc.mapping_name == "m"
        assert len(doc.mappings) == 2
        assert isinstance(doc.mappings[0], ColumnMapping)

    def test_missing_field_raises(self):
        d = _mapping_dict()
        del d["version"]
        with pytest.raises(ValueError, match="Missing required field: version"):
            MappingParser().parse(d)

    def test_mappings_must_be_list(self):
        d = _mapping_dict(mappings={"not": "a list"})
        with pytest.raises(ValueError, match="must be a list"):
            MappingParser().parse(d)

    def test_mappings_cannot_be_empty(self):
        d = _mapping_dict(mappings=[])
        with pytest.raises(ValueError, match="cannot be empty"):
            MappingParser().parse(d)


class TestMappingDocument:
    def test_get_column_mapping(self):
        doc = _doc()
        assert doc.get_column_mapping() == {"raw_id": "ID", "raw_amt": "AMT"}

    def test_get_required_columns(self):
        doc = _doc()
        assert doc.get_required_columns() == ["raw_id"]


class TestApplyTransformations:
    def test_trim_applied(self):
        doc = _doc()
        proc = MappingProcessor(doc)
        df = pd.DataFrame({"raw_id": ["x"], "raw_amt": ["  9  "]})
        out = proc.apply_transformations(df)
        assert out["raw_amt"].tolist() == ["9"]

    def test_missing_source_column_skipped(self):
        doc = _doc()
        proc = MappingProcessor(doc)
        df = pd.DataFrame({"raw_id": ["x"]})  # raw_amt absent
        out = proc.apply_transformations(df)
        assert "raw_id" in out.columns

    def test_apply_each_transformation_type(self):
        doc = _doc()
        proc = MappingProcessor(doc)
        s = pd.Series(["  Hello  "])
        assert proc._apply_transformation(s, "trim", {}).tolist() == ["Hello"]
        assert proc._apply_transformation(s, "upper", {}).tolist() == ["  HELLO  "]
        assert proc._apply_transformation(s, "lower", {}).tolist() == ["  hello  "]
        sub = proc._apply_transformation(pd.Series(["abcdef"]), "substring", {"start": 1, "length": 2})
        assert sub.tolist() == ["bc"]
        sub2 = proc._apply_transformation(pd.Series(["abcdef"]), "substring", {"start": 2})
        assert sub2.tolist() == ["cdef"]
        rep = proc._apply_transformation(pd.Series(["a-b"]), "replace", {"old": "-", "new": "_"})
        assert rep.tolist() == ["a_b"]
        num = proc._apply_transformation(pd.Series(["12"]), "cast", {"to_type": "number"})
        assert num.tolist() == [12]
        # unknown transformation returns the series unchanged
        assert proc._apply_transformation(s, "bogus", {}).tolist() == s.tolist()


class TestValidateData:
    def test_required_column_missing_is_error(self):
        doc = _doc()
        proc = MappingProcessor(doc)
        df = pd.DataFrame({"raw_amt": ["1"]})  # raw_id required, missing
        result = proc.validate_data(df)
        assert result["valid"] is False
        assert any("Required column missing" in e for e in result["errors"])

    def test_optional_column_missing_is_warning(self):
        doc = _doc()
        proc = MappingProcessor(doc)
        df = pd.DataFrame({"raw_id": ["a"]})  # raw_amt optional, missing
        result = proc.validate_data(df)
        assert any("Optional column missing" in w for w in result["warnings"])

    def test_validation_rule_violation_recorded(self):
        doc = _doc()
        proc = MappingProcessor(doc)
        # raw_amt has not_null rule; include a null
        df = pd.DataFrame({"raw_id": ["a", "b"], "raw_amt": ["1", None]})
        result = proc.validate_data(df)
        assert result["valid"] is False
        assert any("validation failed" in e for e in result["errors"])

    def test_validate_rule_types(self):
        doc = _doc()
        proc = MappingProcessor(doc)
        assert proc._validate_rule(pd.Series(["a", None]), "not_null", {}) == 1
        assert proc._validate_rule(pd.Series(["ab", "a"]), "min_length", {"length": 2}) == 1
        assert proc._validate_rule(pd.Series(["abc", "a"]), "max_length", {"length": 2}) == 1
        assert proc._validate_rule(pd.Series(["12", "xx"]), "regex", {"pattern": r"\d+"}) == 1
        assert proc._validate_rule(pd.Series(["5", "50"]), "range", {"min": 0, "max": 9}) == 1
        # unknown rule type -> 0
        assert proc._validate_rule(pd.Series(["a"]), "bogus", {}) == 0


class TestTransformAndMap:
    def test_renames_and_selects_target_columns(self):
        doc = _doc()
        proc = MappingProcessor(doc)
        df = pd.DataFrame({"raw_id": ["x"], "raw_amt": ["  9  "], "extra": [1]})
        out = proc.transform_and_map(df)
        assert list(out.columns) == ["ID", "AMT"]
        assert out["AMT"].tolist() == ["9"]
