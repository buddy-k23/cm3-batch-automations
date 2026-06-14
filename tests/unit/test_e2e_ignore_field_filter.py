"""Unit tests for ``scripts.e2e_lib.ignore_field_filter``."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.e2e_lib.ignore_field_filter import (  # noqa: E402
    IgnoreFieldFilterError,
    apply_filter_to_file,
    blank_line,
    build_filter_plan,
    filter_pair,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _single_type_mapping(*fields):
    """Build a minimal fixed-width mapping JSON with one record type."""
    return {
        "mapping_name": "test",
        "format_type": "fixed_width",
        "fields": [
            {
                "name": name,
                "position": i + 1,
                "length": length,
                "transaction_type": "default",
            }
            for i, (name, length) in enumerate(fields)
        ],
    }


def _multi_type_mapping():
    """Two record types ``H`` and ``D`` sharing a 1-byte discriminator."""
    return {
        "mapping_name": "multi",
        "format_type": "fixed_width",
        "fields": [
            # Record type H
            {"name": "RECORD_TYPE", "position": 1, "length": 1,
             "transaction_type": "H"},
            {"name": "LOC", "position": 2, "length": 5,
             "transaction_type": "H"},
            {"name": "FILE_CREATE_TS", "position": 3, "length": 14,
             "transaction_type": "H"},
            # Record type D
            {"name": "RECORD_TYPE", "position": 1, "length": 1,
             "transaction_type": "D"},
            {"name": "ACCT_NUM", "position": 2, "length": 10,
             "transaction_type": "D"},
            {"name": "BATCH_SEQ_NO", "position": 3, "length": 8,
             "transaction_type": "D"},
            {"name": "AMOUNT", "position": 4, "length": 12,
             "transaction_type": "D"},
        ],
    }


# --------------------------------------------------------------------------- #
# Plan construction
# --------------------------------------------------------------------------- #


class TestBuildFilterPlan:
    def test_single_type_plan_computes_offsets(self) -> None:
        mapping = _single_type_mapping(
            ("A", 3), ("B", 5), ("C", 2),
        )
        plan = build_filter_plan(mapping, ["B"])
        # B is the 2nd field, offset = 3, length = 5
        assert plan.blank_ranges["default"] == [(3, 5)]
        assert plan.located_fields == ["default.B"]
        assert plan.missing_fields == []

    def test_multiple_ignore_fields_sorted(self) -> None:
        mapping = _single_type_mapping(
            ("A", 3), ("B", 5), ("C", 2), ("D", 4),
        )
        plan = build_filter_plan(mapping, ["D", "A"])
        # Offsets: A=0,3 ; D=10,4
        assert plan.blank_ranges["default"] == [(0, 3), (10, 4)]

    def test_missing_field_recorded(self) -> None:
        mapping = _single_type_mapping(("A", 3), ("B", 5))
        plan = build_filter_plan(mapping, ["B", "GHOST"])
        assert "GHOST" in plan.missing_fields
        assert plan.blank_ranges["default"] == [(3, 5)]

    def test_multi_type_plan_per_record_type(self) -> None:
        plan = build_filter_plan(
            _multi_type_mapping(),
            ["FILE_CREATE_TS", "BATCH_SEQ_NO"],
            discriminator_field="RECORD_TYPE",
        )
        # H: RECORD_TYPE(1) + LOC(5) + FILE_CREATE_TS(14) -> blank (6, 14)
        # D: RECORD_TYPE(1) + ACCT_NUM(10) + BATCH_SEQ_NO(8) -> blank (11, 8)
        assert plan.blank_ranges["H"] == [(6, 14)]
        assert plan.blank_ranges["D"] == [(11, 8)]
        assert plan.discriminator is not None
        assert plan.discriminator.start == 0
        assert plan.discriminator.length == 1

    def test_discriminator_required_when_specified(self) -> None:
        mapping = _single_type_mapping(("RECORD_TYPE", 1), ("X", 4))
        # discriminator declared but field absent in mapping → error
        with pytest.raises(IgnoreFieldFilterError, match="not present"):
            build_filter_plan(
                mapping, ["X"], discriminator_field="MISSING",
            )

    def test_inconsistent_discriminator_offset_rejected(self) -> None:
        mapping = {
            "format_type": "fixed_width",
            "fields": [
                {"name": "TYPE", "position": 1, "length": 1,
                 "transaction_type": "H"},
                {"name": "PAD", "position": 1, "length": 3,
                 "transaction_type": "D"},
                {"name": "TYPE", "position": 2, "length": 1,
                 "transaction_type": "D"},
            ],
        }
        with pytest.raises(IgnoreFieldFilterError, match="different offset"):
            build_filter_plan(
                mapping, [], discriminator_field="TYPE",
            )

    def test_non_fixed_width_rejected(self) -> None:
        mapping = {
            "format_type": "delimited",
            "fields": [{"name": "X", "position": 1, "length": 1}],
        }
        with pytest.raises(IgnoreFieldFilterError, match="format_type"):
            build_filter_plan(mapping, ["X"])

    def test_zero_length_field_skipped(self) -> None:
        mapping = {
            "format_type": "fixed_width",
            "fields": [
                {"name": "A", "position": 1, "length": 3},
                {"name": "B", "position": 2, "length": 0},
                {"name": "C", "position": 3, "length": 4},
            ],
        }
        plan = build_filter_plan(mapping, ["A", "C"])
        # B contributes 0 → C lands at offset 3, not 3+0.
        assert plan.blank_ranges["default"] == [(0, 3), (3, 4)]

    def test_is_noop_when_no_ignores(self) -> None:
        plan = build_filter_plan(_single_type_mapping(("A", 5)), [])
        assert plan.is_noop()


# --------------------------------------------------------------------------- #
# Per-line blanking
# --------------------------------------------------------------------------- #


class TestBlankLine:
    def test_blanks_correct_byte_range(self) -> None:
        plan = build_filter_plan(
            _single_type_mapping(("A", 3), ("B", 5), ("C", 2)), ["B"]
        )
        line = "XYZ12345AB"
        out = blank_line(line, record_type="default", plan=plan)
        assert out == "XYZ     AB"

    def test_preserves_lf(self) -> None:
        plan = build_filter_plan(
            _single_type_mapping(("A", 3), ("B", 5)), ["B"]
        )
        out = blank_line("ABC12345\n", record_type="default", plan=plan)
        assert out.endswith("\n") and not out.endswith("\r\n")
        assert out == "ABC     \n"

    def test_preserves_crlf(self) -> None:
        plan = build_filter_plan(
            _single_type_mapping(("A", 3), ("B", 5)), ["B"]
        )
        out = blank_line("ABC12345\r\n", record_type="default", plan=plan)
        assert out.endswith("\r\n")
        assert out == "ABC     \r\n"

    def test_short_line_is_clipped_not_extended(self) -> None:
        plan = build_filter_plan(
            _single_type_mapping(("A", 3), ("B", 5)), ["B"]
        )
        # Line shorter than the planned end of B (offset 3 + length 5 = 8).
        out = blank_line("ABC12\n", record_type="default", plan=plan)
        # Only the bytes that exist get blanked; length is unchanged.
        assert out == "ABC  \n"

    def test_unknown_record_type_returns_input_unchanged(self) -> None:
        plan = build_filter_plan(
            _multi_type_mapping(),
            ["FILE_CREATE_TS"],
            discriminator_field="RECORD_TYPE",
        )
        # 'X' is not in {H, D}; blank_line treats it as no-op for default.
        line = "XLOC12FILE_CREATE_TS"
        out = blank_line(line, record_type="X", plan=plan)
        assert out == line


# --------------------------------------------------------------------------- #
# File-level streaming
# --------------------------------------------------------------------------- #


class TestApplyFilterToFile:
    def test_single_type_file_round_trip(self, tmp_path: Path) -> None:
        mapping = _single_type_mapping(("A", 3), ("B", 5), ("C", 2))
        plan = build_filter_plan(mapping, ["B"])

        src = tmp_path / "src.txt"
        src.write_text("ABCabcdeXY\nDEFfghijZW\n", encoding="utf-8")
        dst = tmp_path / "dst.txt"
        counters = apply_filter_to_file(src_file=src, dst_file=dst, plan=plan)
        assert counters == {
            "lines": 2,
            "lines_blanked": 2,
            "unknown_record_types": 0,
        }
        assert dst.read_text(encoding="utf-8") == "ABC     XY\nDEF     ZW\n"

    def test_multi_type_file_per_record_blanking(self, tmp_path: Path) -> None:
        plan = build_filter_plan(
            _multi_type_mapping(),
            ["FILE_CREATE_TS", "BATCH_SEQ_NO"],
            discriminator_field="RECORD_TYPE",
        )
        src = tmp_path / "src.txt"
        # H-line: 1 + 5 + 14 = 20 chars
        # D-line: 1 + 10 + 8 + 12 = 31 chars
        h_line = "H" + "LOC01" + "20260513T010203"[:14] + "\n"
        d_line = "D" + "ACCT000001" + "SEQ00001" + "AMOUNT000001" + "\n"
        src.write_text(h_line + d_line, encoding="utf-8")
        dst = tmp_path / "dst.txt"
        counters = apply_filter_to_file(src_file=src, dst_file=dst, plan=plan)
        assert counters["lines"] == 2
        assert counters["lines_blanked"] == 2

        out = dst.read_text(encoding="utf-8").splitlines(keepends=True)
        # H: blank bytes 6..20
        assert out[0] == "H" + "LOC01" + (" " * 14) + "\n"
        # D: blank bytes 11..19
        assert out[1] == "D" + "ACCT000001" + (" " * 8) + "AMOUNT000001" + "\n"

    def test_unknown_record_type_passes_through_and_counted(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        plan = build_filter_plan(
            _multi_type_mapping(),
            ["FILE_CREATE_TS"],
            discriminator_field="RECORD_TYPE",
        )
        src = tmp_path / "src.txt"
        src.write_text("XHELLO            \n", encoding="utf-8")
        dst = tmp_path / "dst.txt"
        counters = apply_filter_to_file(src_file=src, dst_file=dst, plan=plan)
        assert counters["unknown_record_types"] == 1
        assert counters["lines_blanked"] == 0
        # The pass-through warning lands on stderr.
        err = capsys.readouterr().err
        assert "unknown record_type" in err

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        mapping = _single_type_mapping(("A", 3))
        plan = build_filter_plan(mapping, [])
        src = tmp_path / "src.txt"
        src.write_text("ABC\n", encoding="utf-8")
        dst = tmp_path / "nested" / "deep" / "dst.txt"
        apply_filter_to_file(src_file=src, dst_file=dst, plan=plan)
        assert dst.is_file()

    def test_noop_plan_copies_lines_verbatim(self, tmp_path: Path) -> None:
        plan = build_filter_plan(_single_type_mapping(("A", 3)), [])
        src = tmp_path / "src.txt"
        # write_bytes avoids Windows' \n → \r\n translation that write_text
        # would apply, so the fixture reflects exactly the bytes a
        # RHEL-generated file would carry.
        src.write_bytes(b"ABC\nDEF\r\nGHI")
        dst = tmp_path / "dst.txt"
        counters = apply_filter_to_file(src_file=src, dst_file=dst, plan=plan)
        # Byte-exact pass-through; mixed line endings preserved.
        assert dst.read_bytes() == b"ABC\nDEF\r\nGHI"
        assert counters["lines_blanked"] == 0


# --------------------------------------------------------------------------- #
# High-level helper
# --------------------------------------------------------------------------- #


class TestFilterPair:
    def test_filter_pair_processes_both_files(self, tmp_path: Path) -> None:
        mapping = _single_type_mapping(("A", 3), ("B", 5))
        mapping_path = tmp_path / "m.json"
        mapping_path.write_text(json.dumps(mapping), encoding="utf-8")

        a = tmp_path / "a.txt"
        a.write_text("ABC12345\n", encoding="utf-8")
        b = tmp_path / "b.txt"
        b.write_text("ABCxxxxx\n", encoding="utf-8")

        out_a = tmp_path / "out" / "a.txt"
        out_b = tmp_path / "out" / "b.txt"
        summary = filter_pair(
            mapping_path=mapping_path,
            ignore_fields=["B"],
            discriminator_field=None,
            file_a=a, file_b=b, out_a=out_a, out_b=out_b,
        )
        assert summary["noop"] is False
        assert summary["fields_located"] == ["default.B"]
        # Both files now blank in the B range → byte-identical.
        assert out_a.read_text(encoding="utf-8") == out_b.read_text(
            encoding="utf-8"
        )
        assert out_a.read_text(encoding="utf-8") == "ABC     \n"

    def test_filter_pair_missing_mapping(self, tmp_path: Path) -> None:
        with pytest.raises(IgnoreFieldFilterError, match="not found"):
            filter_pair(
                mapping_path=tmp_path / "nope.json",
                ignore_fields=["X"],
                discriminator_field=None,
                file_a=tmp_path / "a", file_b=tmp_path / "b",
                out_a=tmp_path / "oa", out_b=tmp_path / "ob",
            )
