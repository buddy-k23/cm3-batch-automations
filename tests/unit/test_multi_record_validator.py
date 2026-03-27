"""Tests for MultiRecordValidator and CrossTypeValidator — issues #161/#162.

Covers:
- Discriminator extraction (various positions/lengths)
- Record type routing (multiple types correctly grouped)
- Header/trailer detection by value AND by position (first/last)
- Unknown record type handling (warn/error/skip)
- expect: exactly_one enforcement
- Cross-type: required_companion
- Cross-type: header_trailer_count
- Cross-type: header_trailer_sum
- Cross-type: header_detail_consistent
- Cross-type: header_trailer_match
- Cross-type: type_sequence
- Cross-type: expect_count
- Empty file, single-row file
- Integration: end-to-end validate with multi-record config
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import List

import pytest

from src.config.multi_record_config import (
    CrossTypeRule,
    DiscriminatorConfig,
    MultiRecordConfig,
    RecordTypeConfig,
)
from src.validators.cross_type_validator import CrossTypeValidator
from src.validators.multi_record_validator import MultiRecordValidator
from src.validators.rule_engine import RuleViolation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_disc(position: int = 1, length: int = 3) -> DiscriminatorConfig:
    """Return a DiscriminatorConfig for tests."""
    return DiscriminatorConfig(field="REC_TYPE", position=position, length=length)


def _make_config(
    disc: DiscriminatorConfig | None = None,
    record_types: dict | None = None,
    cross_type_rules: list | None = None,
    default_action: str = "warn",
) -> MultiRecordConfig:
    """Return a minimal MultiRecordConfig for tests."""
    if disc is None:
        disc = _make_disc()
    if record_types is None:
        record_types = {
            "header": RecordTypeConfig(match="HDR", mapping=""),
            "detail": RecordTypeConfig(match="DTL", mapping=""),
            "trailer": RecordTypeConfig(match="TRL", mapping=""),
        }
    return MultiRecordConfig(
        discriminator=disc,
        record_types=record_types,
        cross_type_rules=cross_type_rules or [],
        default_action=default_action,
    )


def _write_lines(lines: list[str], suffix: str = ".txt") -> Path:
    """Write lines to a temp file and return the Path."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=suffix, delete=False, encoding="utf-8"
    )
    tmp.write("\n".join(lines))
    tmp.close()
    return Path(tmp.name)


def _write_mapping(fields: list[dict]) -> Path:
    """Write a minimal mapping JSON to a temp file and return the Path."""
    mapping = {
        "source": {"format": "pipe_delimited", "delimiter": "|", "has_header": False},
        "fields": fields,
    }
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    )
    json.dump(mapping, tmp)
    tmp.close()
    return Path(tmp.name)


# ---------------------------------------------------------------------------
# DiscriminatorConfig
# ---------------------------------------------------------------------------


class TestDiscriminatorConfig:
    """Tests for the DiscriminatorConfig Pydantic model."""

    def test_valid_config_created(self):
        """Model is created with expected field, position, and length."""
        disc = DiscriminatorConfig(field="REC_TYPE", position=1, length=3)
        assert disc.field == "REC_TYPE"
        assert disc.position == 1
        assert disc.length == 3

    def test_position_must_be_positive(self):
        """Position must be >= 1."""
        with pytest.raises(Exception):
            DiscriminatorConfig(field="X", position=0, length=3)

    def test_length_must_be_positive(self):
        """Length must be >= 1."""
        with pytest.raises(Exception):
            DiscriminatorConfig(field="X", position=1, length=0)


# ---------------------------------------------------------------------------
# _extract_discriminator
# ---------------------------------------------------------------------------


class TestExtractDiscriminator:
    """Tests for MultiRecordValidator._extract_discriminator."""

    def setup_method(self):
        self.validator = MultiRecordValidator()

    def test_extracts_from_start_of_line(self):
        """Extracts correctly when position=1, length=3."""
        disc = DiscriminatorConfig(field="T", position=1, length=3)
        assert self.validator._extract_discriminator("HDR12345", disc) == "HDR"

    def test_extracts_from_middle_of_line(self):
        """Extracts from position=4, length=3 (0-indexed slice [3:6])."""
        disc = DiscriminatorConfig(field="T", position=4, length=3)
        assert self.validator._extract_discriminator("AAADTLBBB", disc) == "DTL"

    def test_strips_trailing_whitespace(self):
        """Extracted value is stripped."""
        disc = DiscriminatorConfig(field="T", position=1, length=5)
        assert self.validator._extract_discriminator("HDR  XYZ", disc) == "HDR"

    def test_short_line_returns_empty_string(self):
        """Returns '' when line is shorter than position+length."""
        disc = DiscriminatorConfig(field="T", position=10, length=3)
        assert self.validator._extract_discriminator("SHORTLINE", disc) == ""

    def test_empty_line_returns_empty_string(self):
        """Returns '' for an empty line."""
        disc = DiscriminatorConfig(field="T", position=1, length=3)
        assert self.validator._extract_discriminator("", disc) == ""


# ---------------------------------------------------------------------------
# _identify_record_type
# ---------------------------------------------------------------------------


class TestIdentifyRecordType:
    """Tests for MultiRecordValidator._identify_record_type."""

    def setup_method(self):
        self.validator = MultiRecordValidator()
        self.config = _make_config()

    def test_matches_by_value(self):
        """Returns record type key when discriminator value matches."""
        result = self.validator._identify_record_type("HDR", 0, 10, self.config)
        assert result == "header"

    def test_matches_detail(self):
        """Returns detail key for 'DTL'."""
        result = self.validator._identify_record_type("DTL", 3, 10, self.config)
        assert result == "detail"

    def test_matches_trailer_by_value(self):
        """Returns trailer key for 'TRL'."""
        result = self.validator._identify_record_type("TRL", 9, 10, self.config)
        assert result == "trailer"

    def test_first_row_matches_position_first(self):
        """Row 0 matches a type configured with position='first' even if value differs."""
        config = _make_config(
            record_types={
                "header": RecordTypeConfig(match="", position="first", mapping=""),
                "detail": RecordTypeConfig(match="DTL", mapping=""),
            }
        )
        result = self.validator._identify_record_type("DTL", 0, 5, config)
        assert result == "header"

    def test_last_row_matches_position_last(self):
        """Last row matches a type configured with position='last' even if value differs."""
        config = _make_config(
            record_types={
                "detail": RecordTypeConfig(match="DTL", mapping=""),
                "trailer": RecordTypeConfig(match="", position="last", mapping=""),
            }
        )
        result = self.validator._identify_record_type("DTL", 4, 5, config)
        assert result == "trailer"

    def test_unknown_value_returns_none(self):
        """Returns None when no type matches the discriminator value."""
        result = self.validator._identify_record_type("ZZZ", 3, 10, self.config)
        assert result is None


# ---------------------------------------------------------------------------
# Record grouping
# ---------------------------------------------------------------------------


class TestRecordGrouping:
    """Tests that rows are grouped into the correct record types."""

    def test_groups_three_record_types(self):
        """Three different record type lines are split into three groups."""
        lines = [
            "HDR000001",
            "DTL000002",
            "DTL000003",
            "TRL000004",
        ]
        file_path = _write_lines(lines)
        config = _make_config()
        validator = MultiRecordValidator()
        groups = validator._group_rows(str(file_path), config)

        assert len(groups["header"]) == 1
        assert len(groups["detail"]) == 2
        assert len(groups["trailer"]) == 1

    def test_only_detail_rows(self):
        """File with only detail rows produces one group."""
        lines = ["DTL000001", "DTL000002"]
        file_path = _write_lines(lines)
        config = _make_config()
        validator = MultiRecordValidator()
        groups = validator._group_rows(str(file_path), config)

        assert len(groups.get("detail", [])) == 2
        assert len(groups.get("header", [])) == 0

    def test_header_by_position_first(self):
        """First row assigned to header even when it has a DTL discriminator value."""
        lines = ["DTL000001", "DTL000002", "DTL000003"]
        file_path = _write_lines(lines)
        config = _make_config(
            record_types={
                "header": RecordTypeConfig(match="", position="first", mapping=""),
                "detail": RecordTypeConfig(match="DTL", mapping=""),
            }
        )
        validator = MultiRecordValidator()
        groups = validator._group_rows(str(file_path), config)

        assert len(groups.get("header", [])) == 1
        assert len(groups.get("detail", [])) == 2

    def test_trailer_by_position_last(self):
        """Last row assigned to trailer even when it has a DTL discriminator value."""
        lines = ["DTL000001", "DTL000002", "DTL000003"]
        file_path = _write_lines(lines)
        config = _make_config(
            record_types={
                "detail": RecordTypeConfig(match="DTL", mapping=""),
                "trailer": RecordTypeConfig(match="", position="last", mapping=""),
            }
        )
        validator = MultiRecordValidator()
        groups = validator._group_rows(str(file_path), config)

        assert len(groups.get("trailer", [])) == 1
        assert len(groups.get("detail", [])) == 2

    def test_empty_file_returns_empty_groups(self):
        """Empty file produces empty groups (no violations from grouping itself)."""
        file_path = _write_lines([])
        config = _make_config()
        validator = MultiRecordValidator()
        groups = validator._group_rows(str(file_path), config)
        assert all(len(v) == 0 for v in groups.values())

    def test_single_row_file(self):
        """Single-row file is grouped correctly."""
        lines = ["HDR000001"]
        file_path = _write_lines(lines)
        config = _make_config()
        validator = MultiRecordValidator()
        groups = validator._group_rows(str(file_path), config)
        assert len(groups.get("header", [])) == 1


# ---------------------------------------------------------------------------
# Unknown record type handling
# ---------------------------------------------------------------------------


class TestUnknownRecordType:
    """Tests for default_action: warn, error, skip."""

    def test_unknown_warn_produces_warning_violation(self):
        """default_action=warn produces a warning-severity violation for unknown rows."""
        lines = ["HDR000001", "UNK000002"]
        file_path = _write_lines(lines)
        config = _make_config(
            record_types={"header": RecordTypeConfig(match="HDR", mapping="")},
            default_action="warn",
        )
        validator = MultiRecordValidator()
        result = validator.validate(str(file_path), config)
        unknown_violations = [
            v for v in result.get("cross_type_violations", [])
            if "unknown" in v.get("message", "").lower() or "unrecognized" in v.get("message", "").lower()
        ]
        assert len(unknown_violations) >= 1
        assert unknown_violations[0]["severity"] == "warning"

    def test_unknown_error_produces_error_violation(self):
        """default_action=error produces an error-severity violation for unknown rows."""
        lines = ["HDR000001", "UNK000002"]
        file_path = _write_lines(lines)
        config = _make_config(
            record_types={"header": RecordTypeConfig(match="HDR", mapping="")},
            default_action="error",
        )
        validator = MultiRecordValidator()
        result = validator.validate(str(file_path), config)
        unknown_violations = [
            v for v in result.get("cross_type_violations", [])
            if "unknown" in v.get("message", "").lower() or "unrecognized" in v.get("message", "").lower()
        ]
        assert len(unknown_violations) >= 1
        assert unknown_violations[0]["severity"] == "error"

    def test_unknown_skip_produces_no_violations(self):
        """default_action=skip silently discards unknown rows without a violation."""
        lines = ["HDR000001", "UNK000002"]
        file_path = _write_lines(lines)
        config = _make_config(
            record_types={"header": RecordTypeConfig(match="HDR", mapping="")},
            default_action="skip",
        )
        validator = MultiRecordValidator()
        result = validator.validate(str(file_path), config)
        unknown_violations = [
            v for v in result.get("cross_type_violations", [])
            if "unknown" in v.get("message", "").lower() or "unrecognized" in v.get("message", "").lower()
        ]
        assert len(unknown_violations) == 0


# ---------------------------------------------------------------------------
# expect enforcement
# ---------------------------------------------------------------------------


class TestExpectEnforcement:
    """Tests for RecordTypeConfig.expect field."""

    def test_exactly_one_passes_when_one_row(self):
        """expect=exactly_one does not produce a violation when one row exists."""
        lines = ["HDR000001", "DTL000002"]
        file_path = _write_lines(lines)
        config = _make_config(
            record_types={
                "header": RecordTypeConfig(match="HDR", mapping="", expect="exactly_one"),
                "detail": RecordTypeConfig(match="DTL", mapping="", expect="any"),
            }
        )
        validator = MultiRecordValidator()
        result = validator.validate(str(file_path), config)
        expect_violations = [
            v for v in result.get("cross_type_violations", [])
            if "expect" in v.get("message", "").lower() or "exactly" in v.get("message", "").lower()
        ]
        assert len(expect_violations) == 0

    def test_exactly_one_fails_when_zero_rows(self):
        """expect=exactly_one produces a violation when record type is absent."""
        lines = ["DTL000002"]
        file_path = _write_lines(lines)
        config = _make_config(
            record_types={
                "header": RecordTypeConfig(match="HDR", mapping="", expect="exactly_one"),
                "detail": RecordTypeConfig(match="DTL", mapping="", expect="any"),
            }
        )
        validator = MultiRecordValidator()
        result = validator.validate(str(file_path), config)
        expect_violations = [
            v for v in result.get("cross_type_violations", [])
            if "header" in v.get("message", "").lower()
        ]
        assert len(expect_violations) >= 1

    def test_exactly_one_fails_when_two_rows(self):
        """expect=exactly_one produces a violation when two rows match."""
        lines = ["HDR000001", "HDR000002", "DTL000003"]
        file_path = _write_lines(lines)
        config = _make_config(
            record_types={
                "header": RecordTypeConfig(match="HDR", mapping="", expect="exactly_one"),
                "detail": RecordTypeConfig(match="DTL", mapping="", expect="any"),
            }
        )
        validator = MultiRecordValidator()
        result = validator.validate(str(file_path), config)
        expect_violations = [
            v for v in result.get("cross_type_violations", [])
            if "header" in v.get("message", "").lower()
        ]
        assert len(expect_violations) >= 1

    def test_at_least_one_passes_when_one_row(self):
        """expect=at_least_one passes when one or more rows exist."""
        lines = ["DTL000001"]
        file_path = _write_lines(lines)
        config = _make_config(
            record_types={
                "detail": RecordTypeConfig(match="DTL", mapping="", expect="at_least_one"),
            }
        )
        validator = MultiRecordValidator()
        result = validator.validate(str(file_path), config)
        violations = result.get("cross_type_violations", [])
        assert len(violations) == 0

    def test_at_least_one_fails_when_absent(self):
        """expect=at_least_one produces a violation when record type is absent."""
        lines = ["HDR000001"]
        file_path = _write_lines(lines)
        config = _make_config(
            record_types={
                "header": RecordTypeConfig(match="HDR", mapping="", expect="any"),
                "detail": RecordTypeConfig(match="DTL", mapping="", expect="at_least_one"),
            }
        )
        validator = MultiRecordValidator()
        result = validator.validate(str(file_path), config)
        expect_violations = [
            v for v in result.get("cross_type_violations", [])
            if "detail" in v.get("message", "").lower()
        ]
        assert len(expect_violations) >= 1


# ---------------------------------------------------------------------------
# CrossTypeValidator — unit tests
# ---------------------------------------------------------------------------


class TestCrossTypeValidator:
    """Unit tests for CrossTypeValidator check methods."""

    def setup_method(self):
        self.validator = CrossTypeValidator()

    # ---- required_companion ------------------------------------------------

    def test_required_companion_passes_when_both_present(self):
        """No violation when both when_type and requires_type are present."""
        groups = {"header": ["HDR|1"], "detail": ["DTL|1"]}
        rule = CrossTypeRule(
            check="required_companion",
            when_type="header",
            requires_type="detail",
            message="detail required when header present",
        )
        violations = self.validator.validate(groups, [rule], [])
        assert violations == []

    def test_required_companion_fails_when_requires_absent(self):
        """Violation when when_type is present but requires_type is absent."""
        groups = {"header": ["HDR|1"]}
        rule = CrossTypeRule(
            check="required_companion",
            when_type="header",
            requires_type="detail",
            message="detail required when header present",
        )
        violations = self.validator.validate(groups, [rule], [])
        assert len(violations) == 1
        assert violations[0].severity == "error"

    def test_required_companion_no_violation_when_when_type_absent(self):
        """No violation when the when_type itself is absent (nothing to require)."""
        groups = {"detail": ["DTL|1"]}
        rule = CrossTypeRule(
            check="required_companion",
            when_type="header",
            requires_type="detail",
        )
        violations = self.validator.validate(groups, [rule], [])
        assert violations == []

    # ---- header_trailer_count ----------------------------------------------

    def test_header_trailer_count_passes(self):
        """No violation when trailer count field matches number of detail rows."""
        # Trailer row: "TRL|3" — count field at index 1 (value "3"), 3 detail rows
        groups = {
            "detail": ["DTL|a", "DTL|b", "DTL|c"],
            "trailer": ["TRL|3"],
        }
        rule = CrossTypeRule(
            check="header_trailer_count",
            record_type="trailer",
            trailer_field="1",
            count_of="detail",
            message="trailer count mismatch",
        )
        # Provide rows as list of dicts for count extraction
        groups_dict = {
            "detail": [{"REC_TYPE": "DTL", "VAL": "a"},
                        {"REC_TYPE": "DTL", "VAL": "b"},
                        {"REC_TYPE": "DTL", "VAL": "c"}],
            "trailer": [{"REC_TYPE": "TRL", "COUNT": "3"}],
        }
        rule2 = CrossTypeRule(
            check="header_trailer_count",
            record_type="trailer",
            trailer_field="COUNT",
            count_of="detail",
            message="trailer count mismatch",
        )
        violations = self.validator.validate(groups_dict, [rule2], [])
        assert violations == []

    def test_header_trailer_count_fails(self):
        """Violation when trailer count field does not match number of detail rows."""
        groups = {
            "detail": [{"REC_TYPE": "DTL", "VAL": "a"}, {"REC_TYPE": "DTL", "VAL": "b"}],
            "trailer": [{"REC_TYPE": "TRL", "COUNT": "5"}],
        }
        rule = CrossTypeRule(
            check="header_trailer_count",
            record_type="trailer",
            trailer_field="COUNT",
            count_of="detail",
            message="trailer count mismatch",
        )
        violations = self.validator.validate(groups, [rule], [])
        assert len(violations) >= 1

    # ---- header_trailer_sum ------------------------------------------------

    def test_header_trailer_sum_passes(self):
        """No violation when sum of detail field matches trailer sum field."""
        groups = {
            "detail": [
                {"REC_TYPE": "DTL", "AMOUNT": "100"},
                {"REC_TYPE": "DTL", "AMOUNT": "200"},
            ],
            "trailer": [{"REC_TYPE": "TRL", "TOTAL": "300"}],
        }
        rule = CrossTypeRule(
            check="header_trailer_sum",
            record_type="trailer",
            trailer_field="TOTAL",
            sum_of=["AMOUNT"],
            count_of="detail",
        )
        violations = self.validator.validate(groups, [rule], [])
        assert violations == []

    def test_header_trailer_sum_fails(self):
        """Violation when sum of detail field does not match trailer sum field."""
        groups = {
            "detail": [
                {"REC_TYPE": "DTL", "AMOUNT": "100"},
                {"REC_TYPE": "DTL", "AMOUNT": "200"},
            ],
            "trailer": [{"REC_TYPE": "TRL", "TOTAL": "999"}],
        }
        rule = CrossTypeRule(
            check="header_trailer_sum",
            record_type="trailer",
            trailer_field="TOTAL",
            sum_of=["AMOUNT"],
            count_of="detail",
        )
        violations = self.validator.validate(groups, [rule], [])
        assert len(violations) >= 1

    # ---- header_detail_consistent ------------------------------------------

    def test_header_detail_consistent_passes(self):
        """No violation when header field value matches all detail field values."""
        groups = {
            "header": [{"REC_TYPE": "HDR", "BATCH_ID": "B001"}],
            "detail": [
                {"REC_TYPE": "DTL", "BATCH_ID": "B001"},
                {"REC_TYPE": "DTL", "BATCH_ID": "B001"},
            ],
        }
        rule = CrossTypeRule(
            check="header_detail_consistent",
            header_field="BATCH_ID",
            detail_field="BATCH_ID",
        )
        violations = self.validator.validate(groups, [rule], [])
        assert violations == []

    def test_header_detail_consistent_fails(self):
        """Violation when a detail row has a different value than the header field."""
        groups = {
            "header": [{"REC_TYPE": "HDR", "BATCH_ID": "B001"}],
            "detail": [
                {"REC_TYPE": "DTL", "BATCH_ID": "B001"},
                {"REC_TYPE": "DTL", "BATCH_ID": "B999"},
            ],
        }
        rule = CrossTypeRule(
            check="header_detail_consistent",
            header_field="BATCH_ID",
            detail_field="BATCH_ID",
        )
        violations = self.validator.validate(groups, [rule], [])
        assert len(violations) >= 1

    # ---- header_trailer_match ----------------------------------------------

    def test_header_trailer_match_passes(self):
        """No violation when header and trailer fields have matching values."""
        groups = {
            "header": [{"REC_TYPE": "HDR", "BATCH_ID": "B001"}],
            "trailer": [{"REC_TYPE": "TRL", "BATCH_ID": "B001"}],
        }
        rule = CrossTypeRule(
            check="header_trailer_match",
            header_field="BATCH_ID",
            trailer_field="BATCH_ID",
        )
        violations = self.validator.validate(groups, [rule], [])
        assert violations == []

    def test_header_trailer_match_fails(self):
        """Violation when header and trailer field values differ."""
        groups = {
            "header": [{"REC_TYPE": "HDR", "BATCH_ID": "B001"}],
            "trailer": [{"REC_TYPE": "TRL", "BATCH_ID": "B999"}],
        }
        rule = CrossTypeRule(
            check="header_trailer_match",
            header_field="BATCH_ID",
            trailer_field="BATCH_ID",
        )
        violations = self.validator.validate(groups, [rule], [])
        assert len(violations) >= 1

    # ---- type_sequence -----------------------------------------------------

    def test_type_sequence_passes(self):
        """No violation when record types appear in expected order."""
        all_rows_types = ["header", "detail", "detail", "trailer"]
        groups = {
            "header": [{"REC_TYPE": "HDR"}],
            "detail": [{"REC_TYPE": "DTL"}, {"REC_TYPE": "DTL"}],
            "trailer": [{"REC_TYPE": "TRL"}],
        }
        rule = CrossTypeRule(
            check="type_sequence",
            expected_order=["header", "detail", "trailer"],
        )
        violations = self.validator.validate(groups, [rule], all_rows_types)
        assert violations == []

    def test_type_sequence_fails_when_trailer_before_detail(self):
        """Violation when trailer appears before detail rows."""
        all_rows_types = ["header", "trailer", "detail"]
        groups = {
            "header": [{"REC_TYPE": "HDR"}],
            "detail": [{"REC_TYPE": "DTL"}],
            "trailer": [{"REC_TYPE": "TRL"}],
        }
        rule = CrossTypeRule(
            check="type_sequence",
            expected_order=["header", "detail", "trailer"],
        )
        violations = self.validator.validate(groups, [rule], all_rows_types)
        assert len(violations) >= 1

    # ---- expect_count ------------------------------------------------------

    def test_expect_count_exactly_passes(self):
        """No violation when group row count matches exactly."""
        groups = {
            "header": [{"REC_TYPE": "HDR"}],
        }
        rule = CrossTypeRule(
            check="expect_count",
            record_type="header",
            exactly=1,
        )
        violations = self.validator.validate(groups, [rule], [])
        assert violations == []

    def test_expect_count_exactly_fails(self):
        """Violation when group has wrong number of rows for exactly check."""
        groups = {
            "header": [{"REC_TYPE": "HDR"}, {"REC_TYPE": "HDR"}],
        }
        rule = CrossTypeRule(
            check="expect_count",
            record_type="header",
            exactly=1,
        )
        violations = self.validator.validate(groups, [rule], [])
        assert len(violations) >= 1


# ---------------------------------------------------------------------------
# MultiRecordValidator.validate — integration
# ---------------------------------------------------------------------------


class TestMultiRecordValidatorIntegration:
    """End-to-end integration tests for MultiRecordValidator.validate."""

    def test_returns_aggregate_result_keys(self):
        """Result dict always contains required top-level keys."""
        lines = ["HDR000001", "DTL000002"]
        file_path = _write_lines(lines)
        config = _make_config()
        validator = MultiRecordValidator()
        result = validator.validate(str(file_path), config)

        assert "record_type_results" in result
        assert "cross_type_violations" in result
        assert "total_rows" in result
        assert "valid" in result

    def test_total_rows_counts_all_lines(self):
        """total_rows equals the number of non-empty lines in the file."""
        lines = ["HDR000001", "DTL000002", "DTL000003", "TRL000004"]
        file_path = _write_lines(lines)
        config = _make_config()
        validator = MultiRecordValidator()
        result = validator.validate(str(file_path), config)

        assert result["total_rows"] == 4

    def test_valid_is_false_when_cross_type_errors_present(self):
        """valid=False when cross-type error violations are found."""
        # expect=exactly_one for header, but provide two headers
        lines = ["HDR000001", "HDR000002", "DTL000003"]
        file_path = _write_lines(lines)
        config = _make_config(
            record_types={
                "header": RecordTypeConfig(match="HDR", mapping="", expect="exactly_one"),
                "detail": RecordTypeConfig(match="DTL", mapping="", expect="any"),
            }
        )
        validator = MultiRecordValidator()
        result = validator.validate(str(file_path), config)
        assert result["valid"] is False

    def test_valid_is_true_with_no_violations(self):
        """valid=True when file has clean structure and no cross-type rules."""
        lines = ["HDR000001", "DTL000002", "DTL000003", "TRL000004"]
        file_path = _write_lines(lines)
        config = _make_config()
        validator = MultiRecordValidator()
        result = validator.validate(str(file_path), config)
        # No cross-type rules, no per-type mappings — should have no violations
        assert result["valid"] is True

    def test_empty_file_is_valid_with_any_expect(self):
        """Empty file with all record types expecting 'any' produces valid=True."""
        file_path = _write_lines([])
        config = _make_config()
        validator = MultiRecordValidator()
        result = validator.validate(str(file_path), config)
        assert result["total_rows"] == 0
        assert result["valid"] is True
