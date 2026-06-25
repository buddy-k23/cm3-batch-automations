"""Unit tests for the reusable Excel data reader (S24-1).

These tests drive the design of :mod:`src.parsers.excel_reader`, the
foundational reader that loads a sheet of *data* (not a spec template) into a
string-coerced :class:`pandas.DataFrame` ready to be compared against a DB
extract in S24-2.

Fixtures are generated in-test with openpyxl into ``tmp_path`` so the suite has
no binary committed artefacts and each case isolates one coercion concern.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest
from openpyxl import Workbook

from src.parsers.excel_reader import _coerce_cell, read_excel_data


# ---------------------------------------------------------------------------
# Fixture builders.
# ---------------------------------------------------------------------------


def _write_workbook(path: Path, sheets: dict[str, list[list]]) -> None:
    """Write a multi-sheet ``.xlsx`` workbook from raw row lists.

    Args:
        path: Destination ``.xlsx`` path.
        sheets: Mapping of sheet name -> list of rows (each row a list of
            native Python cell values, written verbatim so Excel stores the
            native type — int, float, datetime, str, or None for blank).
    """
    wb = Workbook()
    # Remove the default sheet so only the requested sheets exist (in order).
    default = wb.active
    wb.remove(default)
    for sheet_name, rows in sheets.items():
        ws = wb.create_sheet(title=sheet_name)
        for row in rows:
            ws.append(row)
    wb.save(str(path))


@pytest.fixture
def simple_xlsx(tmp_path: Path) -> Path:
    """A single-sheet workbook with mixed native cell types.

    Header row: ACCT-NUM | AMOUNT | TXN-DATE | NAME
    One integer key stored as int, one float, one datetime, one blank cell.
    """
    path = tmp_path / "simple.xlsx"
    _write_workbook(
        path,
        {
            "Sheet1": [
                ["ACCT-NUM", "AMOUNT", "TXN-DATE", "NAME"],
                [12345, 100.50, datetime(2024, 3, 17), "ALICE"],
                [67890, 0.0, datetime(2024, 12, 1), None],
            ]
        },
    )
    return path


@pytest.fixture
def multi_sheet_xlsx(tmp_path: Path) -> Path:
    """A workbook with three sheets carrying distinguishable data."""
    path = tmp_path / "multi.xlsx"
    _write_workbook(
        path,
        {
            "First": [["COL"], ["first-val"]],
            "Second": [["COL"], ["second-val"]],
            "Third": [["COL"], ["third-val"]],
        },
    )
    return path


@pytest.fixture
def title_row_xlsx(tmp_path: Path) -> Path:
    """A workbook whose real header sits on the 3rd row (index 2)."""
    path = tmp_path / "title.xlsx"
    _write_workbook(
        path,
        {
            "Sheet1": [
                ["Quarterly Extract Report", None, None],
                [None, None, None],
                ["ID", "STATUS", "BALANCE"],
                [1001, "OPEN", 250.0],
                [1002, "CLOSED", 0.0],
            ]
        },
    )
    return path


# ---------------------------------------------------------------------------
# Sheet selection.
# ---------------------------------------------------------------------------


def test_default_reads_first_sheet(multi_sheet_xlsx: Path) -> None:
    df = read_excel_data(multi_sheet_xlsx)
    assert list(df.columns) == ["COL"]
    assert df["COL"].tolist() == ["first-val"]


def test_select_sheet_by_name(multi_sheet_xlsx: Path) -> None:
    df = read_excel_data(multi_sheet_xlsx, sheet="Second")
    assert df["COL"].tolist() == ["second-val"]


def test_select_sheet_by_index(multi_sheet_xlsx: Path) -> None:
    df = read_excel_data(multi_sheet_xlsx, sheet=2)
    assert df["COL"].tolist() == ["third-val"]


def test_missing_sheet_name_raises_valueerror_listing_available(
    multi_sheet_xlsx: Path,
) -> None:
    with pytest.raises(ValueError) as exc:
        read_excel_data(multi_sheet_xlsx, sheet="Nope")
    msg = str(exc.value)
    assert "Nope" in msg
    # Available names must be surfaced so the caller can self-correct.
    assert "First" in msg and "Second" in msg and "Third" in msg


def test_out_of_range_sheet_index_raises_valueerror(multi_sheet_xlsx: Path) -> None:
    with pytest.raises(ValueError) as exc:
        read_excel_data(multi_sheet_xlsx, sheet=99)
    assert "99" in str(exc.value)


# ---------------------------------------------------------------------------
# Header row.
# ---------------------------------------------------------------------------


def test_header_row_above_zero_skips_title_rows(title_row_xlsx: Path) -> None:
    df = read_excel_data(title_row_xlsx, header_row=2)
    assert list(df.columns) == ["ID", "STATUS", "BALANCE"]
    assert df["STATUS"].tolist() == ["OPEN", "CLOSED"]
    # The two title rows above the header must not appear as data rows.
    assert len(df) == 2


# ---------------------------------------------------------------------------
# Type coercion — the core S24-1 concern.
# ---------------------------------------------------------------------------


def test_all_columns_are_string_typed(simple_xlsx: Path) -> None:
    # Match the comparison side's dtype exactly: read_csv(dtype=str) and
    # read_excel(dtype=str) both yield a string dtype (object on pandas < 2.x,
    # the dedicated string dtype on pandas 3.x). Assert string-ness in a
    # version-agnostic way: pandas reports it as a string dtype, and — the
    # property the row-by-row compare actually relies on — every cell is a
    # Python str (never int / float / Timestamp / NaN).
    df = read_excel_data(simple_xlsx)
    for col in df.columns:
        assert pd.api.types.is_string_dtype(df[col]), (col, df[col].dtype)
        for val in df[col]:
            assert isinstance(val, str), (col, val, type(val))


def test_integer_key_float_drift_is_normalized(simple_xlsx: Path) -> None:
    # Excel stores 12345 as float -> pandas reads "12345.0"; reader must
    # strip the trailing ".0" so the key matches a DB CHAR/NUMBER value.
    df = read_excel_data(simple_xlsx)
    assert df["ACCT_NUM"].tolist() == ["12345", "67890"]


def test_non_whole_float_keeps_decimal(simple_xlsx: Path) -> None:
    # A genuine decimal must NOT lose its fraction — only whole-number ".0"
    # is normalized.
    df = read_excel_data(simple_xlsx)
    assert df["AMOUNT"].iloc[0] == "100.5"
    # 0.0 is a whole number -> normalized to "0".
    assert df["AMOUNT"].iloc[1] == "0"


def test_date_cell_coerced_to_iso_string(simple_xlsx: Path) -> None:
    df = read_excel_data(simple_xlsx)
    assert df["TXN_DATE"].tolist() == ["2024-03-17", "2024-12-01"]


def test_blank_cell_is_empty_string_not_nan(simple_xlsx: Path) -> None:
    df = read_excel_data(simple_xlsx)
    # NAME on row 2 was None -> "" (keep_default_na=False semantics).
    assert df["NAME"].iloc[1] == ""
    assert not df["NAME"].isna().any()


def test_negative_whole_float_normalized(tmp_path: Path) -> None:
    # A negative whole number (-7.0) must drop ".0" while keeping the sign.
    path = tmp_path / "neg.xlsx"
    _write_workbook(path, {"S": [["DELTA"], [-7.0], [-3.5]]})
    df = read_excel_data(path)
    assert df["DELTA"].tolist() == ["-7", "-3.5"]


def test_dotzero_text_value_is_not_mangled(tmp_path: Path) -> None:
    # A non-numeric string that merely ends in ".0" (e.g. a version label)
    # must be preserved verbatim — only "<digits>.0" is normalized.
    path = tmp_path / "ver.xlsx"
    _write_workbook(path, {"S": [["VER"], ["v2.0"], ["A.0"]]})
    df = read_excel_data(path)
    assert df["VER"].tolist() == ["v2.0", "A.0"]


def test_datetime_with_time_component_preserved(tmp_path: Path) -> None:
    # A genuine datetime (non-midnight) is NOT a pure date — keep it verbatim
    # so it is never silently truncated to a date.
    path = tmp_path / "dt.xlsx"
    _write_workbook(path, {"S": [["TS"], [datetime(2024, 3, 17, 9, 30, 0)]]})
    df = read_excel_data(path)
    assert df["TS"].iloc[0] == "2024-03-17 09:30:00"


def test_midnight_string_with_bad_date_part_preserved(tmp_path: Path) -> None:
    # A literal string that looks like "<text> 00:00:00" but whose head is not
    # a valid ISO date must be left untouched (exercises the _is_iso_date guard).
    path = tmp_path / "odd.xlsx"
    _write_workbook(path, {"S": [["RAW"], ["9999-99-99 00:00:00"]]})
    df = read_excel_data(path)
    assert df["RAW"].iloc[0] == "9999-99-99 00:00:00"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("12345.0", "12345"),   # positive whole-number key
        ("0.0", "0"),           # zero
        ("-7.0", "-7"),         # negative whole number (sign preserved)
        ("100.5", "100.5"),     # genuine decimal — untouched
        ("v2.0", "v2.0"),       # non-numeric ".0" tail — untouched
        ("2024-03-17 00:00:00", "2024-03-17"),  # midnight date string -> ISO
        ("", ""),               # already-empty
    ],
)
def test_coerce_cell_rules(raw: str, expected: str) -> None:
    # Directly exercise the documented coercion helper so every branch (incl.
    # the negative-sign normalization) is covered deterministically, since some
    # forms (e.g. literal "-7.0") never reach it via pandas' own rendering.
    assert _coerce_cell(raw) == expected


def test_pure_date_object_coerced_to_iso(tmp_path: Path) -> None:
    # openpyxl can store a date (no time component) — must also be ISO.
    path = tmp_path / "d.xlsx"
    _write_workbook(
        path,
        {"S": [["WHEN"], [date(2025, 1, 5)]]},
    )
    df = read_excel_data(path)
    assert df["WHEN"].tolist() == ["2025-01-05"]


# ---------------------------------------------------------------------------
# Column subset.
# ---------------------------------------------------------------------------


def test_column_subset_keeps_only_requested(simple_xlsx: Path) -> None:
    df = read_excel_data(simple_xlsx, columns=["ACCT_NUM", "NAME"])
    assert list(df.columns) == ["ACCT_NUM", "NAME"]


def test_column_subset_missing_column_raises(simple_xlsx: Path) -> None:
    with pytest.raises(ValueError) as exc:
        read_excel_data(simple_xlsx, columns=["ACCT_NUM", "DOES_NOT_EXIST"])
    msg = str(exc.value)
    assert "DOES_NOT_EXIST" in msg


def test_column_subset_uses_normalized_names(simple_xlsx: Path) -> None:
    # Caller may pass the raw header form; it is normalized before matching
    # so ACCT-NUM and ACCT_NUM both resolve.
    df = read_excel_data(simple_xlsx, columns=["ACCT-NUM"])
    assert list(df.columns) == ["ACCT_NUM"]


# ---------------------------------------------------------------------------
# Header normalization (aligns with DB cursor column names for the S24-2 join).
# ---------------------------------------------------------------------------


def test_headers_normalized_by_default(simple_xlsx: Path) -> None:
    df = read_excel_data(simple_xlsx)
    # ACCT-NUM -> ACCT_NUM, TXN-DATE -> TXN_DATE, lower stays upper, etc.
    assert list(df.columns) == ["ACCT_NUM", "AMOUNT", "TXN_DATE", "NAME"]


def test_normalize_headers_can_be_disabled(simple_xlsx: Path) -> None:
    df = read_excel_data(simple_xlsx, normalize_headers=False)
    # Raw header strings preserved verbatim.
    assert list(df.columns) == ["ACCT-NUM", "AMOUNT", "TXN-DATE", "NAME"]


# ---------------------------------------------------------------------------
# Error handling.
# ---------------------------------------------------------------------------


def test_missing_file_raises_filenotfound(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_excel_data(tmp_path / "nope.xlsx")


def test_returns_dataframe_instance(simple_xlsx: Path) -> None:
    assert isinstance(read_excel_data(simple_xlsx), pd.DataFrame)
