"""Tests for the multi_record_reader primitive — issue #21.

Covers the pure file-reading + discriminator-dispatch primitive extracted from
``MultiRecordValidator``. The behavioural-equivalence guarantee with the
validator is exercised separately by ``test_multi_record_validator.py``, which
must continue to pass unchanged after the validator is rewired onto this
primitive.

Test cases:

- Empty file → no rows yielded.
- File with only blank lines → no rows yielded.
- Single record-type dispatch by ``match`` value.
- Multi-record dispatch (header + detail + trailer).
- ``position="first"`` semantics: the first non-empty line is dispatched to the
  position-based type even when its discriminator slot would match another
  type.
- ``position="last"`` semantics: the final non-empty line is dispatched to the
  position-based type. Look-ahead must not yield it as a detail row.
- Unknown discriminator value → ``UnknownRecordTypeRow`` yielded.
- Line shorter than discriminator end → ``UnknownRecordTypeRow`` yielded.
- UTF-8 BOM at start of file stripped.
- CRLF line endings normalised (no trailing ``\\r`` in ``raw_line``).
- 1-indexed ``line_number`` honours blank lines.
- File-not-found raises ``MultiRecordReaderError``.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from typing import List

import pytest

from src.config.multi_record_config import (
    DiscriminatorConfig,
    MultiRecordConfig,
    RecordTypeConfig,
)
from src.validators.multi_record_reader import (
    DelimitedFieldStrategy,
    FixedWidthFieldStrategy,
    MultiRecordReaderError,
    ParsedRow,
    RecordFieldStrategy,
    UnknownRecordTypeRow,
    _extract_discriminator,
    field_strategy_for,
    read_multi_record_file,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    record_types: dict | None = None,
    position: int = 1,
    length: int = 3,
) -> MultiRecordConfig:
    """Return a ``MultiRecordConfig`` with sensible defaults for these tests."""
    if record_types is None:
        record_types = {
            "header": RecordTypeConfig(match="HDR", mapping=""),
            "detail": RecordTypeConfig(match="DTL", mapping=""),
            "trailer": RecordTypeConfig(match="TRL", mapping=""),
        }
    return MultiRecordConfig(
        discriminator=DiscriminatorConfig(
            field="REC_TYPE", position=position, length=length
        ),
        record_types=record_types,
    )


def _write_file(content: str, *, suffix: str = ".txt", encoding: str = "utf-8") -> Path:
    """Write ``content`` to a temp file verbatim and return the path.

    Uses binary write so callers can encode CRLF / BOM bytes deterministically
    without Python's newline-translation interfering.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp.write(content.encode(encoding))
    tmp.close()
    return Path(tmp.name)


def _write_bytes(content: bytes, suffix: str = ".txt") -> Path:
    """Write raw bytes to a temp file and return the path."""
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp.write(content)
    tmp.close()
    return Path(tmp.name)


# ---------------------------------------------------------------------------
# Empty / blank-only files
# ---------------------------------------------------------------------------


def test_empty_file_yields_nothing() -> None:
    """An empty file yields zero rows."""
    path = _write_file("")
    config = _make_config()

    rows = list(read_multi_record_file(path, config))

    assert rows == []


def test_blank_lines_only_yields_nothing() -> None:
    """A file containing only blank lines yields zero rows."""
    path = _write_file("\n\n   \n\t\n")
    config = _make_config()

    rows = list(read_multi_record_file(path, config))

    assert rows == []


# ---------------------------------------------------------------------------
# Single-type dispatch
# ---------------------------------------------------------------------------


def test_single_record_type_dispatch_by_match_value() -> None:
    """Every non-empty line whose discriminator equals ``match`` becomes a ParsedRow."""
    config = _make_config(
        record_types={"detail": RecordTypeConfig(match="DTL", mapping="")}
    )
    path = _write_file("DTL row 1\nDTL row 2\nDTL row 3\n")

    rows = list(read_multi_record_file(path, config))

    assert len(rows) == 3
    assert all(isinstance(r, ParsedRow) for r in rows)
    assert [r.record_type for r in rows] == ["detail", "detail", "detail"]
    assert [r.discriminator_value for r in rows] == ["DTL", "DTL", "DTL"]


# ---------------------------------------------------------------------------
# Multi-type dispatch (header + detail + trailer)
# ---------------------------------------------------------------------------


def test_multi_record_dispatch_header_detail_trailer() -> None:
    """Header, two details, and trailer routed to the correct types in order."""
    config = _make_config()
    path = _write_file(
        "HDR batch header line\n"
        "DTL detail line one\n"
        "DTL detail line two\n"
        "TRL trailer line\n"
    )

    rows = list(read_multi_record_file(path, config))

    assert [r.record_type for r in rows] == [
        "header",
        "detail",
        "detail",
        "trailer",
    ]
    assert all(isinstance(r, ParsedRow) for r in rows)
    assert [r.line_number for r in rows] == [1, 2, 3, 4]


# ---------------------------------------------------------------------------
# Position-based dispatch
# ---------------------------------------------------------------------------


def test_position_first_overrides_match() -> None:
    """A ``position="first"`` record type wins on row 0 even if the slot matches another type."""
    # The first row's discriminator slice would say "DTL", but the header
    # record type uses position="first" so it must win.
    config = _make_config(
        record_types={
            "header": RecordTypeConfig(position="first", mapping=""),
            "detail": RecordTypeConfig(match="DTL", mapping=""),
        }
    )
    path = _write_file("DTL this is actually the header\nDTL real detail\n")

    rows = list(read_multi_record_file(path, config))

    assert isinstance(rows[0], ParsedRow)
    assert rows[0].record_type == "header"
    # discriminator value is still extracted and surfaced — only the dispatch
    # changes.
    assert rows[0].discriminator_value == "DTL"
    assert rows[1].record_type == "detail"


def test_position_last_overrides_match() -> None:
    """A ``position="last"`` record type wins on the last non-empty row."""
    config = _make_config(
        record_types={
            "detail": RecordTypeConfig(match="DTL", mapping=""),
            "trailer": RecordTypeConfig(position="last", mapping=""),
        }
    )
    path = _write_file(
        "DTL row 1\n" "DTL row 2\n" "DTL this last line is the trailer\n"
    )

    rows = list(read_multi_record_file(path, config))

    assert [r.record_type for r in rows] == ["detail", "detail", "trailer"]
    # The last row's discriminator slice is still extracted.
    assert rows[-1].discriminator_value == "DTL"


def test_position_last_with_single_row_file() -> None:
    """A single-row file with ``position="last"`` configured: the sole row is the trailer.

    This is the edge case where the same row is both first non-empty and last
    non-empty. ``position="first"`` should win because the validator's
    positional check applies in first-then-last order.
    """
    config = _make_config(
        record_types={
            "header": RecordTypeConfig(position="first", mapping=""),
            "trailer": RecordTypeConfig(position="last", mapping=""),
        }
    )
    path = _write_file("XYZ only line\n")

    rows = list(read_multi_record_file(path, config))

    assert len(rows) == 1
    assert rows[0].record_type == "header"  # first wins over last


# ---------------------------------------------------------------------------
# Unknown / malformed rows
# ---------------------------------------------------------------------------


def test_unknown_discriminator_yields_unknown_row() -> None:
    """A line whose discriminator matches no configured type yields ``UnknownRecordTypeRow``."""
    config = _make_config()
    path = _write_file("HDR header\n" "ZZZ what is this\n" "TRL trailer\n")

    rows = list(read_multi_record_file(path, config))

    assert isinstance(rows[0], ParsedRow)
    assert isinstance(rows[1], UnknownRecordTypeRow)
    assert isinstance(rows[2], ParsedRow)
    assert rows[1].discriminator_value == "ZZZ"
    assert rows[1].line_number == 2


def test_line_shorter_than_discriminator_yields_unknown_row() -> None:
    """A line too short for the discriminator slice yields ``UnknownRecordTypeRow`` with an empty discriminator."""
    config = _make_config(position=5, length=3)  # discriminator starts at col 5
    path = _write_file("HDR\nDTL row\n")  # first line is only 3 chars

    rows = list(read_multi_record_file(path, config))

    assert isinstance(rows[0], UnknownRecordTypeRow)
    assert rows[0].discriminator_value == ""
    # Second line is long enough but its slice (cols 5-7) is "row" — also unknown.
    assert isinstance(rows[1], UnknownRecordTypeRow)


# ---------------------------------------------------------------------------
# Encoding / line-ending handling
# ---------------------------------------------------------------------------


def test_utf8_bom_stripped_from_first_line() -> None:
    """A leading UTF-8 BOM on line 1 is stripped before discriminator extraction."""
    # Without BOM-stripping the discriminator slice on line 1 would include
    # the BOM character and fail to match "HDR".
    config = _make_config()
    bom_bytes = "\ufeff".encode("utf-8")
    body = "HDR header\nDTL detail\n".encode("utf-8")
    path = _write_bytes(bom_bytes + body)

    rows = list(read_multi_record_file(path, config))

    assert [r.record_type for r in rows] == ["header", "detail"]
    assert rows[0].raw_line == "HDR header"
    assert not rows[0].raw_line.startswith("\ufeff")


def test_crlf_line_endings_normalised() -> None:
    """CRLF terminators are stripped — ``raw_line`` has no trailing ``\\r``."""
    config = _make_config()
    # CRLF terminators.
    path = _write_bytes(b"HDR header\r\nDTL detail\r\nTRL trailer\r\n")

    rows = list(read_multi_record_file(path, config))

    assert [r.record_type for r in rows] == ["header", "detail", "trailer"]
    for r in rows:
        assert not r.raw_line.endswith("\r")
        assert not r.raw_line.endswith("\n")


# ---------------------------------------------------------------------------
# Line numbering
# ---------------------------------------------------------------------------


def test_line_numbers_count_blank_lines() -> None:
    """``line_number`` is 1-indexed against the source file, blank lines counted."""
    config = _make_config()
    # Layout:
    #   line 1: blank
    #   line 2: HDR
    #   line 3: blank
    #   line 4: blank
    #   line 5: DTL
    #   line 6: TRL
    path = _write_file("\nHDR header\n\n\nDTL detail\nTRL trailer\n")

    rows = list(read_multi_record_file(path, config))

    assert [r.line_number for r in rows] == [2, 5, 6]
    assert [r.record_type for r in rows] == ["header", "detail", "trailer"]


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_missing_file_raises_reader_error() -> None:
    """A non-existent path raises ``MultiRecordReaderError``."""
    config = _make_config()

    # ``read_multi_record_file`` is a generator factory; the open happens
    # immediately on the function call, before any iteration. Confirm by NOT
    # iterating in the ``with raises`` block.
    with pytest.raises(MultiRecordReaderError):
        read_multi_record_file(Path("does_not_exist_xyz_123.txt"), config)


# ---------------------------------------------------------------------------
# Generator semantics
# ---------------------------------------------------------------------------


def test_reader_is_a_generator_not_a_list() -> None:
    """The reader yields incrementally — it does not materialise the file into memory."""
    config = _make_config()
    path = _write_file("HDR h\nDTL d\nTRL t\n")

    iterator = read_multi_record_file(path, config)

    # Pull rows one at a time.
    first = next(iterator)
    assert isinstance(first, ParsedRow)
    assert first.record_type == "header"

    second = next(iterator)
    assert second.record_type == "detail"

    third = next(iterator)
    assert third.record_type == "trailer"

    with pytest.raises(StopIteration):
        next(iterator)


# ---------------------------------------------------------------------------
# Record-field strategy seam (R-06a)
# ---------------------------------------------------------------------------


def _row_fingerprint(rows: List[object]) -> str:
    """Return a stable SHA-256 over a reader's full output.

    Captures record_type / line_number / raw_line / discriminator (and the
    concrete row class) for every yielded row, so any behaviour change in
    dispatch or extraction would change the digest. This is the byte-identical
    equivalence check the ADR 0008 SHA approach calls for, applied to the
    strategy seam.
    """
    h = hashlib.sha256()
    for r in rows:
        h.update(type(r).__name__.encode("utf-8"))
        h.update(b"\x1f")
        h.update(repr(getattr(r, "record_type", None)).encode("utf-8"))
        h.update(b"\x1f")
        h.update(str(r.line_number).encode("utf-8"))
        h.update(b"\x1f")
        h.update(r.raw_line.encode("utf-8"))
        h.update(b"\x1f")
        h.update(str(r.discriminator_value).encode("utf-8"))
        h.update(b"\x1e")
    return h.hexdigest()


def test_fixed_width_strategy_is_default_byte_identical() -> None:
    """Passing FixedWidthFieldStrategy explicitly yields byte-identical output.

    With the default strategy and an explicit FixedWidthFieldStrategy() the
    reader must produce an identical row stream (SHA-256 over the full output),
    proving the seam is a pure refactor with no behaviour change.
    """
    config = _make_config()
    path = _write_file(
        "HDR batch header line\n"
        "DTL detail line one\n"
        "ZZZ unknown row\n"
        "DTL detail line two\n"
        "TRL trailer line\n"
    )

    default_rows = list(read_multi_record_file(path, config))
    explicit_rows = list(
        read_multi_record_file(path, config, field_strategy=FixedWidthFieldStrategy())
    )

    assert _row_fingerprint(default_rows) == _row_fingerprint(explicit_rows)


def test_none_field_strategy_falls_back_to_default() -> None:
    """Explicitly passing field_strategy=None uses the fixed-width default."""
    config = _make_config()
    path = _write_file("HDR h\nDTL d\nTRL t\n")

    rows = list(read_multi_record_file(path, config, field_strategy=None))

    assert [r.record_type for r in rows] == ["header", "detail", "trailer"]


def test_fixed_width_strategy_extract_matches_legacy_helper() -> None:
    """FixedWidthFieldStrategy.extract equals the legacy _extract_discriminator."""
    config = _make_config(position=4, length=3)
    strategy = FixedWidthFieldStrategy()

    for line in ["HDRABCDEF", "xx", "", "   DTL extra", "TR"]:
        assert strategy.extract(line, config.discriminator) == _extract_discriminator(
            line, config.discriminator
        )


def test_fixed_width_strategy_short_line_returns_empty() -> None:
    """A line shorter than the discriminator position yields '' (no raise)."""
    config = _make_config(position=10, length=3)
    strategy = FixedWidthFieldStrategy()

    assert strategy.extract("short", config.discriminator) == ""


def test_custom_strategy_drives_dispatch() -> None:
    """A custom RecordFieldStrategy changes dispatch without touching the reader.

    Proves the seam is real: a trivial strategy that ignores position/length and
    returns the line's first token routes rows by that token instead of the
    fixed-width slice. This is the shape R-06b (delimited) will take.
    """

    class FirstTokenStrategy:
        """Toy delimited-style strategy: discriminator = first whitespace token."""

        def extract(self, line: str, disc: DiscriminatorConfig) -> str:
            parts = line.split()
            return parts[0] if parts else ""

    # Discriminator config is deliberately a slice that would NOT match here;
    # the custom strategy overrides how the value is located.
    config = _make_config(position=40, length=3)
    path = _write_file("HDR header\nDTL detail\nTRL trailer\n")

    rows = list(
        read_multi_record_file(path, config, field_strategy=FirstTokenStrategy())
    )

    assert [r.record_type for r in rows] == ["header", "detail", "trailer"]


def test_fixed_width_strategy_satisfies_protocol() -> None:
    """FixedWidthFieldStrategy is a structural RecordFieldStrategy."""
    strategy: RecordFieldStrategy = FixedWidthFieldStrategy()
    assert hasattr(strategy, "extract")


def test_shaw_fixture_reader_output_stable() -> None:
    """Reader output over a real SHAW fixture is identical default vs explicit.

    Uses the checked-in TRANERT SHAW sample if present. Skips cleanly when the
    fixture is unavailable so the offline subset stays green everywhere.
    """
    fixture = Path("data/samples/tranert_shaw_20260422.txt")
    if not fixture.is_file():
        pytest.skip("SHAW TRANERT fixture not present")

    # A position/length discriminator typical of the SHAW umbrella's first
    # columns; exact matches are irrelevant — we only assert default == explicit.
    config = _make_config(position=1, length=5)

    default_rows = list(read_multi_record_file(fixture, config))
    explicit_rows = list(
        read_multi_record_file(
            fixture, config, field_strategy=FixedWidthFieldStrategy()
        )
    )

    assert default_rows  # fixture is non-empty
    assert _row_fingerprint(default_rows) == _row_fingerprint(explicit_rows)


# ---------------------------------------------------------------------------
# Delimited reader strategy (R-06b)
# ---------------------------------------------------------------------------


def _make_delimited_config(
    *,
    delimiter: str = ",",
    column=1,
    columns=None,
    record_types: dict | None = None,
) -> MultiRecordConfig:
    """Return a delimited ``MultiRecordConfig`` for the R-06b tests."""
    if record_types is None:
        record_types = {
            "header": RecordTypeConfig(match="HDR", mapping=""),
            "detail": RecordTypeConfig(match="DTL", mapping=""),
            "trailer": RecordTypeConfig(match="TRL", mapping=""),
        }
    return MultiRecordConfig(
        discriminator=DiscriminatorConfig(
            field="REC_TYPE",
            delimiter=delimiter,
            column=column,
            columns=columns or [],
        ),
        record_types=record_types,
    )


def test_delimited_dispatch_by_integer_column() -> None:
    """A delimited file dispatches by a 1-indexed integer column, config-only."""
    config = _make_delimited_config(delimiter=",", column=1)
    path = _write_file("HDR,batch,1\nDTL,alice,100\nDTL,bob,200\nTRL,2,300\n")

    rows = list(read_multi_record_file(path, config))

    assert [r.record_type for r in rows] == [
        "header",
        "detail",
        "detail",
        "trailer",
    ]
    assert all(isinstance(r, ParsedRow) for r in rows)
    assert [r.discriminator_value for r in rows] == ["HDR", "DTL", "DTL", "TRL"]


def test_delimited_dispatch_pipe_delimiter() -> None:
    """A pipe-delimited file dispatches identically to a comma-delimited one."""
    config = _make_delimited_config(delimiter="|", column=1)
    path = _write_file("HDR|batch\nDTL|alice\nTRL|1\n")

    rows = list(read_multi_record_file(path, config))

    assert [r.record_type for r in rows] == ["header", "detail", "trailer"]


def test_delimited_dispatch_by_named_column() -> None:
    """A named column resolves against ``columns`` to its position."""
    config = _make_delimited_config(
        delimiter="|",
        column="rec_type",
        columns=["id", "rec_type", "amount"],
        record_types={
            "header": RecordTypeConfig(match="HDR", mapping=""),
            "detail": RecordTypeConfig(match="DTL", mapping=""),
        },
    )
    path = _write_file("1|HDR|0\n2|DTL|100\n3|DTL|200\n")

    rows = list(read_multi_record_file(path, config))

    assert [r.record_type for r in rows] == ["header", "detail", "detail"]
    assert [r.discriminator_value for r in rows] == ["HDR", "DTL", "DTL"]


def test_delimited_value_is_trimmed() -> None:
    """Surrounding whitespace around the delimited discriminator is stripped."""
    config = _make_delimited_config(delimiter=",", column=2)
    path = _write_file("1,  HDR  ,x\n2, DTL ,y\n")

    rows = list(read_multi_record_file(path, config))

    assert [r.discriminator_value for r in rows] == ["HDR", "DTL"]
    assert [r.record_type for r in rows] == ["header", "detail"]


def test_delimited_unknown_value_yields_unknown_row() -> None:
    """A delimited line whose column value matches no type yields an unknown row."""
    config = _make_delimited_config(delimiter=",", column=1)
    path = _write_file("HDR,a\nZZZ,b\nTRL,c\n")

    rows = list(read_multi_record_file(path, config))

    assert isinstance(rows[0], ParsedRow)
    assert isinstance(rows[1], UnknownRecordTypeRow)
    assert isinstance(rows[2], ParsedRow)
    assert rows[1].discriminator_value == "ZZZ"
    assert rows[1].line_number == 2


def test_delimited_short_line_yields_empty_discriminator() -> None:
    """A line with too few fields for the column yields '' (no raise)."""
    config = _make_delimited_config(delimiter=",", column=3)
    path = _write_file("HDR,only,two\nDTL,one\n")  # 2nd line has 2 fields

    rows = list(read_multi_record_file(path, config))

    # Row 1: 3rd field is "two" -> unknown (no type matches "two").
    assert isinstance(rows[0], UnknownRecordTypeRow)
    assert rows[0].discriminator_value == "two"
    # Row 2: only 2 fields, column 3 absent -> empty -> unknown.
    assert isinstance(rows[1], UnknownRecordTypeRow)
    assert rows[1].discriminator_value == ""


def test_delimited_position_first_and_last_still_apply() -> None:
    """Positional first/last dispatch works in delimited mode too.

    The dispatch priority (first, then last, then value-match) is owned by the
    reader and is format-agnostic; only the discriminator extraction differs.
    """
    config = _make_delimited_config(
        delimiter=",",
        column=1,
        record_types={
            "header": RecordTypeConfig(position="first", mapping=""),
            "detail": RecordTypeConfig(match="DTL", mapping=""),
            "trailer": RecordTypeConfig(position="last", mapping=""),
        },
    )
    path = _write_file("DTL,a\nDTL,real\nDTL,z\n")

    rows = list(read_multi_record_file(path, config))

    assert [r.record_type for r in rows] == ["header", "detail", "trailer"]


def test_delimited_crlf_and_bom_handled() -> None:
    """CRLF terminators and a leading BOM are handled in delimited mode."""
    config = _make_delimited_config(delimiter=",", column=1)
    bom = "\ufeff".encode("utf-8")
    body = b"HDR,a\r\nDTL,b\r\nTRL,c\r\n"
    path = _write_bytes(bom + body)

    rows = list(read_multi_record_file(path, config))

    assert [r.record_type for r in rows] == ["header", "detail", "trailer"]
    assert rows[0].raw_line == "HDR,a"
    assert not rows[0].raw_line.startswith("\ufeff")


# ---------------------------------------------------------------------------
# DelimitedFieldStrategy unit behaviour
# ---------------------------------------------------------------------------


def test_delimited_strategy_satisfies_protocol() -> None:
    """DelimitedFieldStrategy is a structural RecordFieldStrategy."""
    strategy: RecordFieldStrategy = DelimitedFieldStrategy(
        delimiter=",", column_index=0
    )
    assert hasattr(strategy, "extract")


def test_delimited_strategy_extract_selects_column() -> None:
    """extract() returns the trimmed value at the configured 0-indexed column."""
    strategy = DelimitedFieldStrategy(delimiter="|", column_index=1)
    disc = DiscriminatorConfig(
        field="x", delimiter="|", column="b", columns=["a", "b", "c"]
    )
    assert strategy.extract("1| HDR |x", disc) == "HDR"


def test_delimited_strategy_extract_short_line_returns_empty() -> None:
    """extract() returns '' when the line has too few fields (no raise)."""
    strategy = DelimitedFieldStrategy(delimiter=",", column_index=5)
    disc = DiscriminatorConfig(field="x", delimiter=",", column=6)
    assert strategy.extract("a,b,c", disc) == ""


# ---------------------------------------------------------------------------
# field_strategy_for factory (config-only selection)
# ---------------------------------------------------------------------------


def test_factory_returns_fixed_width_for_fixed_width_config() -> None:
    """A fixed-width config yields the shared FixedWidthFieldStrategy."""
    config = _make_config()
    strategy = field_strategy_for(config)
    assert isinstance(strategy, FixedWidthFieldStrategy)


def test_factory_returns_delimited_for_delimited_config() -> None:
    """A delimited config yields a DelimitedFieldStrategy."""
    config = _make_delimited_config(delimiter=",", column=2)
    strategy = field_strategy_for(config)
    assert isinstance(strategy, DelimitedFieldStrategy)


def test_factory_resolves_named_column_to_index() -> None:
    """A named column is resolved to its 0-indexed position by the factory."""
    config = _make_delimited_config(
        delimiter=",", column="rec", columns=["id", "rec", "amt"]
    )
    strategy = field_strategy_for(config)
    assert isinstance(strategy, DelimitedFieldStrategy)
    # Column "rec" is index 1; extract should read the 2nd field.
    disc = config.discriminator
    assert strategy.extract("9,DTL,100", disc) == "DTL"


def test_explicit_strategy_overrides_config_selection() -> None:
    """An explicit field_strategy overrides the config-driven factory choice."""
    # Config says delimited, but caller forces fixed-width. The delimited config
    # has no position/length, so the fixed-width strategy returns "" -> unknown.
    config = _make_delimited_config(delimiter=",", column=1)
    path = _write_file("HDRxx,ignored\n")

    rows = list(
        read_multi_record_file(
            path,
            config,
            field_strategy=FixedWidthFieldStrategy(),
        )
    )
    assert isinstance(rows[0], UnknownRecordTypeRow)


# ---------------------------------------------------------------------------
# Config validation for the delimited shape (R-06b)
# ---------------------------------------------------------------------------


def test_delimited_config_requires_column() -> None:
    """A delimiter without a column is rejected at config-build time."""
    with pytest.raises(Exception):
        DiscriminatorConfig(field="x", delimiter=",")


def test_fixed_width_config_requires_position_and_length() -> None:
    """A non-delimited config without position/length is rejected."""
    with pytest.raises(Exception):
        DiscriminatorConfig(field="x")


def test_named_column_requires_columns_list() -> None:
    """A named column without a columns list is rejected."""
    with pytest.raises(Exception):
        DiscriminatorConfig(field="x", delimiter=",", column="rec")


def test_named_column_must_be_in_columns() -> None:
    """A named column missing from the columns list is rejected."""
    with pytest.raises(Exception):
        DiscriminatorConfig(field="x", delimiter=",", column="nope", columns=["a", "b"])


def test_integer_column_must_be_one_indexed() -> None:
    """A zero/negative integer column is rejected."""
    with pytest.raises(Exception):
        DiscriminatorConfig(field="x", delimiter=",", column=0)


def test_empty_delimiter_rejected() -> None:
    """An empty-string delimiter is rejected."""
    with pytest.raises(Exception):
        DiscriminatorConfig(field="x", delimiter="", column=1)


def test_fixed_width_byte_identical_with_delimited_seam_present() -> None:
    """Fixed-width output is unchanged now that the delimited seam exists.

    Regression guard for acceptance criterion 2: adding the delimited strategy
    must not alter the fixed-width path. The default (auto-selected) reader and
    an explicit FixedWidthFieldStrategy must still produce an identical stream.
    """
    config = _make_config()
    path = _write_file(
        "HDR batch header line\n"
        "DTL detail line one\n"
        "ZZZ unknown row\n"
        "DTL detail line two\n"
        "TRL trailer line\n"
    )

    default_rows = list(read_multi_record_file(path, config))
    explicit_rows = list(
        read_multi_record_file(path, config, field_strategy=FixedWidthFieldStrategy())
    )

    assert _row_fingerprint(default_rows) == _row_fingerprint(explicit_rows)
