"""Unit tests for ``scripts.e2e_lib.multi_record_file_parser``."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.config.multi_record_config import (  # noqa: E402
    DiscriminatorConfig,
    MultiRecordConfig,
    RecordTypeConfig,
)
from scripts.e2e_lib.multi_record_file_parser import (  # noqa: E402
    MultiRecordFileParserError,
    ParsedFileRow,
    iter_parsed_rows,
)

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _write_mapping(
    path: Path,
    mapping_name: str,
    fields: List[Dict[str, Any]],
) -> Path:
    """Write a minimal UniversalMappingParser-compatible mapping JSON."""
    content = {
        "mapping_name": mapping_name,
        "version": "1.0.0",
        "source": {"format": "fixed_width", "encoding": "UTF-8"},
        "target": {"type": "database"},
        "fields": fields,
        "key_columns": [],
    }
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


def _three_type_fixture(
    tmp_path: Path,
) -> tuple[Path, MultiRecordConfig, Dict[str, Path]]:
    """Build a 3-record-type fixture: header (HDR), detail (DET), trailer (TRL).

    Discriminator is at position 1, length 3. Each line is 20 chars wide.

    Layout (all 1-indexed):
      HDR fields: TYPE (1-3), BATCH_NUM (4-9), DATE (10-17)
      DET fields: TYPE (1-3), ACCT_NUM (4-12), AMOUNT (13-20)
      TRL fields: TYPE (1-3), TOTAL_CNT (4-9)
    """
    hdr_mapping = _write_mapping(
        tmp_path / "hdr.json",
        "HDR_mapping",
        [
            {"name": "TYPE", "data_type": "string", "position": 1, "length": 3},
            {"name": "BATCH_NUM", "data_type": "string", "position": 4, "length": 6},
            {"name": "DATE", "data_type": "string", "position": 10, "length": 8},
        ],
    )
    det_mapping = _write_mapping(
        tmp_path / "det.json",
        "DET_mapping",
        [
            {"name": "TYPE", "data_type": "string", "position": 1, "length": 3},
            {"name": "ACCT_NUM", "data_type": "string", "position": 4, "length": 9},
            {"name": "AMOUNT", "data_type": "string", "position": 13, "length": 8},
        ],
    )
    trl_mapping = _write_mapping(
        tmp_path / "trl.json",
        "TRL_mapping",
        [
            {"name": "TYPE", "data_type": "string", "position": 1, "length": 3},
            {"name": "TOTAL_CNT", "data_type": "string", "position": 4, "length": 6},
        ],
    )

    umbrella = MultiRecordConfig(
        discriminator=DiscriminatorConfig(field="record_type", position=1, length=3),
        record_types={
            "header": RecordTypeConfig(match="HDR", mapping=str(hdr_mapping)),
            "detail": RecordTypeConfig(match="DET", mapping=str(det_mapping)),
            "trailer": RecordTypeConfig(match="TRL", mapping=str(trl_mapping)),
        },
        default_action="error",
    )
    mapping_paths = {
        "header": hdr_mapping,
        "detail": det_mapping,
        "trailer": trl_mapping,
    }

    data_file = tmp_path / "data.txt"
    # Layout (column-aligned to declared positions, no separators):
    #   HDR (1-3) + BATCH_NUM (4-9, "B00001") + DATE (10-17, "20260514")
    #   DET (1-3) + ACCT_NUM (4-12, "100000001") + AMOUNT (13-20, "000100.5")
    #   DET (1-3) + ACCT_NUM (4-12, "200000002") + AMOUNT (13-20, "000250.0")
    #   TRL (1-3) + TOTAL_CNT (4-9, "000002")
    data_file.write_text(
        "HDRB0000120260514\n"
        "DET100000001000100.5\n"
        "DET200000002000250.0\n"
        "TRL000002\n",
        encoding="utf-8",
    )
    return data_file, umbrella, mapping_paths


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #


class TestHappyPath:
    """Three-record-type file parses cleanly."""

    def test_yields_one_row_per_non_empty_line(self, tmp_path: Path) -> None:
        data_file, umbrella, mapping_paths = _three_type_fixture(tmp_path)
        rows = list(iter_parsed_rows(data_file, umbrella, mapping_paths))
        assert len(rows) == 4
        assert [r.record_type for r in rows] == [
            "header",
            "detail",
            "detail",
            "trailer",
        ]

    def test_line_numbers_are_true_file_line_numbers(self, tmp_path: Path) -> None:
        data_file, umbrella, mapping_paths = _three_type_fixture(tmp_path)
        rows = list(iter_parsed_rows(data_file, umbrella, mapping_paths))
        assert [r.line_number for r in rows] == [1, 2, 3, 4]

    def test_fields_are_sliced_and_trimmed(self, tmp_path: Path) -> None:
        data_file, umbrella, mapping_paths = _three_type_fixture(tmp_path)
        rows = list(iter_parsed_rows(data_file, umbrella, mapping_paths))
        header = rows[0]
        assert header.fields["TYPE"] == "HDR"
        assert header.fields["BATCH_NUM"] == "B00001"
        assert header.fields["DATE"] == "20260514"

        det1 = rows[1]
        assert det1.fields["TYPE"] == "DET"
        assert det1.fields["ACCT_NUM"] == "100000001"
        assert det1.fields["AMOUNT"] == "000100.5"

        trailer = rows[-1]
        assert trailer.fields["TYPE"] == "TRL"
        assert trailer.fields["TOTAL_CNT"] == "000002"

    def test_parsed_file_row_is_frozen(self, tmp_path: Path) -> None:
        data_file, umbrella, mapping_paths = _three_type_fixture(tmp_path)
        row = next(iter_parsed_rows(data_file, umbrella, mapping_paths))
        with pytest.raises((AttributeError, TypeError)):
            row.record_type = "OTHER"  # type: ignore[misc]

    def test_fields_mapping_is_read_only(self, tmp_path: Path) -> None:
        data_file, umbrella, mapping_paths = _three_type_fixture(tmp_path)
        row = next(iter_parsed_rows(data_file, umbrella, mapping_paths))
        # MappingProxyType raises TypeError on mutation attempts.
        with pytest.raises(TypeError):
            row.fields["TYPE"] = "OTHER"  # type: ignore[index]


# --------------------------------------------------------------------------- #
# Unknown discriminator behaviour
# --------------------------------------------------------------------------- #


class TestUnknownDiscriminator:
    """Behaviour gated by ``umbrella_config.default_action``."""

    def test_default_action_error_raises_mid_iteration(self, tmp_path: Path) -> None:
        data_file, umbrella, mapping_paths = _three_type_fixture(tmp_path)
        # Inject an unknown discriminator on line 3.
        data_file.write_text(
            "HDRB00001 20260514\n"
            "DET100000001000100.5\n"
            "XXXunknownrowcontent\n"
            "TRL000002\n",
            encoding="utf-8",
        )
        # Umbrella already has default_action='error' in the fixture.
        with pytest.raises(MultiRecordFileParserError, match=r"line 3.*'XXX'"):
            list(iter_parsed_rows(data_file, umbrella, mapping_paths))

    def test_default_action_warn_yields_row_with_none_type(
        self, tmp_path: Path
    ) -> None:
        data_file, umbrella, mapping_paths = _three_type_fixture(tmp_path)
        data_file.write_text(
            "HDRB00001 20260514\n" "XXXunknownrowcontent\n" "TRL000002\n",
            encoding="utf-8",
        )
        umbrella_warn = MultiRecordConfig(
            discriminator=umbrella.discriminator,
            record_types=umbrella.record_types,
            default_action="warn",
        )
        rows = list(iter_parsed_rows(data_file, umbrella_warn, mapping_paths))
        assert len(rows) == 3
        assert rows[1].record_type is None
        assert dict(rows[1].fields) == {}
        assert rows[1].line_number == 2
        assert rows[1].raw_line == "XXXunknownrowcontent"

    def test_default_action_skip_yields_row_with_none_type(
        self, tmp_path: Path
    ) -> None:
        # 'skip' is documented as silent-discard, but the parser delegates
        # that policy to the consumer by surfacing the unknown row with
        # record_type=None. The L2b comparator decides whether to count
        # or drop it.
        data_file, umbrella, mapping_paths = _three_type_fixture(tmp_path)
        data_file.write_text(
            "HDRB00001 20260514\n" "XXXunknownrowcontent\n" "TRL000002\n",
            encoding="utf-8",
        )
        umbrella_skip = MultiRecordConfig(
            discriminator=umbrella.discriminator,
            record_types=umbrella.record_types,
            default_action="skip",
        )
        rows = list(iter_parsed_rows(data_file, umbrella_skip, mapping_paths))
        assert len(rows) == 3
        assert rows[1].record_type is None


# --------------------------------------------------------------------------- #
# Eager input validation (call-time fail-fast)
# --------------------------------------------------------------------------- #


class TestEagerValidation:
    """Defects surface at call time, before any iteration."""

    def test_missing_mapping_path_entry_raises_at_call_time(
        self, tmp_path: Path
    ) -> None:
        data_file, umbrella, mapping_paths = _three_type_fixture(tmp_path)
        # Drop the 'trailer' entry — umbrella still declares it.
        partial_paths = {k: v for k, v in mapping_paths.items() if k != "trailer"}
        with pytest.raises(
            MultiRecordFileParserError,
            match=r"mapping_paths missing entries.*'trailer'",
        ):
            iter_parsed_rows(data_file, umbrella, partial_paths)

    def test_missing_mapping_file_on_disk_raises_at_call_time(
        self, tmp_path: Path
    ) -> None:
        data_file, umbrella, mapping_paths = _three_type_fixture(tmp_path)
        # Delete one of the mapping files between fixture build and iter call.
        mapping_paths["detail"].unlink()
        with pytest.raises(
            MultiRecordFileParserError,
            match=r"mapping file for record type 'detail' not found",
        ):
            iter_parsed_rows(data_file, umbrella, mapping_paths)

    def test_extra_mapping_paths_entries_are_tolerated(self, tmp_path: Path) -> None:
        # Caller may share a mapping_paths dict across multiple umbrellas;
        # an extra entry that doesn't appear in this umbrella's record_types
        # must not raise.
        data_file, umbrella, mapping_paths = _three_type_fixture(tmp_path)
        unrelated = _write_mapping(
            tmp_path / "unrelated.json",
            "unrelated",
            [{"name": "X", "data_type": "string", "position": 1, "length": 1}],
        )
        mapping_paths_extra = dict(mapping_paths)
        mapping_paths_extra["other_umbrella_type"] = unrelated
        # Should not raise.
        rows = list(iter_parsed_rows(data_file, umbrella, mapping_paths_extra))
        assert len(rows) == 4

    def test_missing_data_file_raises_through_module_error_type(
        self, tmp_path: Path
    ) -> None:
        _, umbrella, mapping_paths = _three_type_fixture(tmp_path)
        missing = tmp_path / "no-such-file.txt"
        with pytest.raises(MultiRecordFileParserError, match=r"Cannot open file"):
            iter_parsed_rows(missing, umbrella, mapping_paths)


# --------------------------------------------------------------------------- #
# Mapping cache
# --------------------------------------------------------------------------- #


class TestMappingCache:
    """Each record type's mapping JSON is loaded once per invocation."""

    def test_one_load_per_record_type_regardless_of_row_count(
        self, tmp_path: Path
    ) -> None:
        data_file, umbrella, mapping_paths = _three_type_fixture(tmp_path)
        # Build a wider data file with many rows of each type.
        lines = ["HDRB00001 20260514"]
        for i in range(50):
            lines.append(f"DET{i:09d}000100.5")
        lines.append("TRL000050")
        data_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        with patch(
            "scripts.e2e_lib.multi_record_file_parser.UniversalMappingParser",
            wraps=__import__(
                "src.config.universal_mapping_parser",
                fromlist=["UniversalMappingParser"],
            ).UniversalMappingParser,
        ) as wrapped:
            list(iter_parsed_rows(data_file, umbrella, mapping_paths))
        # Exactly three record types -> exactly three loads.
        assert wrapped.call_count == 3

    def test_cache_is_per_invocation_not_module_global(self, tmp_path: Path) -> None:
        data_file, umbrella, mapping_paths = _three_type_fixture(tmp_path)
        with patch(
            "scripts.e2e_lib.multi_record_file_parser.UniversalMappingParser",
            wraps=__import__(
                "src.config.universal_mapping_parser",
                fromlist=["UniversalMappingParser"],
            ).UniversalMappingParser,
        ) as wrapped:
            list(iter_parsed_rows(data_file, umbrella, mapping_paths))
            first_call_count = wrapped.call_count
            list(iter_parsed_rows(data_file, umbrella, mapping_paths))
            # Second invocation reloads — caches don't leak across calls.
            assert wrapped.call_count == first_call_count * 2


# --------------------------------------------------------------------------- #
# Field slicing edge cases
# --------------------------------------------------------------------------- #


class TestFieldSlicing:
    """Edge cases in :func:`_slice_fields`."""

    def test_short_line_yields_empty_string_for_overrun_fields(
        self, tmp_path: Path
    ) -> None:
        # DET record declares ACCT_NUM at position 4 length 9 and AMOUNT at
        # position 13 length 8. A short DET line (only 5 chars after type)
        # should not raise; overrun fields collapse to "".
        _, umbrella, mapping_paths = _three_type_fixture(tmp_path)
        data_file = tmp_path / "short.txt"
        data_file.write_text(
            "HDRB00001 20260514\n"
            "DETab\n"  # only 5 chars total — AMOUNT slice starts past EOL
            "TRL000001\n",
            encoding="utf-8",
        )
        rows = list(iter_parsed_rows(data_file, umbrella, mapping_paths))
        det_row = rows[1]
        # ACCT_NUM (pos 4 len 9): starts at index 3, line length 5, partial slice "ab".
        assert det_row.fields["ACCT_NUM"] == "ab"
        # AMOUNT (pos 13 len 8): starts at index 12, past EOL → empty.
        assert det_row.fields["AMOUNT"] == ""

    def test_field_without_position_or_length_is_skipped(self, tmp_path: Path) -> None:
        # A mapping field that lacks position/length (e.g. a target-only
        # field on a delimited mapping) should be omitted from the sliced
        # dict rather than raising.
        mapping_path = _write_mapping(
            tmp_path / "with_unsliced.json",
            "with_unsliced_mapping",
            [
                {"name": "TYPE", "data_type": "string", "position": 1, "length": 3},
                {"name": "META", "data_type": "string"},  # no position/length
            ],
        )
        umbrella = MultiRecordConfig(
            discriminator=DiscriminatorConfig(field="rt", position=1, length=3),
            record_types={
                "only": RecordTypeConfig(match="ABC", mapping=str(mapping_path)),
            },
            default_action="error",
        )
        data_file = tmp_path / "d.txt"
        data_file.write_text("ABCextra\n", encoding="utf-8")
        rows = list(iter_parsed_rows(data_file, umbrella, {"only": mapping_path}))
        assert rows[0].fields == {"TYPE": "ABC"}
