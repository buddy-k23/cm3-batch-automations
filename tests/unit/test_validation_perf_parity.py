"""Output-parity regression guards for the non-chunked validation perf fixes.

These tests pin the FULL validation result (errors + warnings + info +
field_analysis + date_analysis + strict_fixed_width + quality stats) produced
by :class:`~src.parsers.enhanced_validator.EnhancedFileValidator` so that the
two performance optimisations applied to that class cannot change observable
behaviour:

  * Fix #1 — gating ``_analyze_date_fields`` so non-date columns skip the
    expensive ``pd.to_datetime`` coercion. The reported ``date_analysis`` and
    the date-related warnings/info must be byte-for-byte identical.
  * Fix #2 — vectorising the ``_validate_strict_fixed_width`` error
    construction (removing per-violation ``df.loc[idx, name]`` lookups). The
    emitted ``FW_REQ_001`` / ``FW_VAL_001`` / ``FW_FMT_001`` error dicts, their
    row numbers, messages and emission order must be identical.

The two scenarios deliberately exercise a mix of column kinds:

  (a) DELIMITED: real-date columns, date-like-but-not-date columns, numeric,
      free text, and a valid_values code column.
  (b) FIXED-WIDTH: clean rows plus rows that trigger each strict violation
      (required-empty, invalid valid_value, format mismatch, and length), with
      enough rows to exercise the ``int(idx) + 1`` row-number math.

The expectations are captured as literals from the CURRENT implementation; the
tests pass against today's code (pinning behaviour) and must still pass after
the refactor.
"""

import os
import tempfile

import pandas as pd

from src.parsers.fixed_width_parser import FixedWidthParser
from src.parsers.pipe_delimited_parser import PipeDelimitedParser
from src.parsers.enhanced_validator import EnhancedFileValidator


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _write(text: str, suffix: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _date_keys():
    """The exact key set every date_analysis entry must contain."""
    return {
        "earliest_date",
        "latest_date",
        "date_range_days",
        "valid_date_count",
        "valid_date_pct",
        "invalid_date_count",
        "invalid_date_pct",
        "future_date_count",
        "future_date_pct",
        "null_date_count",
        "null_date_pct",
        "detected_formats",
    }


# --------------------------------------------------------------------------- #
# Scenario A — delimited, mixed column kinds (date gate)
# --------------------------------------------------------------------------- #

def _delimited_setup():
    # Columns:
    #   REAL_DATE  - real ISO dates (date column)
    #   OPEN_DATE  - YYYYMMDD date-named column (date column)
    #   FAKE_DATE  - date-LIKE but mostly non-date text (NOT a date column)
    #   AMOUNT     - numeric strings (NOT a date column)
    #   CODE       - small valid_values domain (NOT a date column)
    #   NOTE       - free text (NOT a date column)
    header = "REAL_DATE|OPEN_DATE|FAKE_DATE|AMOUNT|CODE|NOTE"
    rows = [
        "2021-01-15|20210115|HELLO|100|A|free text one",
        "2021-06-30|20210630|WORLD|200|B|free text two",
        "2022-03-01|20220301|FOOBAR|300|A|another note",
        "2022-12-25|20221225|ZZZZZZ|400|B|yet another",
        "2023-07-04|20230704|QWERTY|500|A|last note here",
    ]
    content = header + "\n" + "\n".join(rows) + "\n"
    path = _write(content, ".psv")
    mapping = {
        "file_path": "delim_mapping.json",
        "fields": [
            {"name": "REAL_DATE", "data_type": "String"},
            {"name": "OPEN_DATE", "data_type": "String", "format": "YYYYMMDD"},
            {"name": "FAKE_DATE", "data_type": "String"},
            {"name": "AMOUNT", "data_type": "String"},
            {"name": "CODE", "data_type": "String", "valid_values": ["A", "B"]},
            {"name": "NOTE", "data_type": "String"},
        ],
    }
    parser = PipeDelimitedParser(
        path,
        columns=["REAL_DATE", "OPEN_DATE", "FAKE_DATE", "AMOUNT", "CODE", "NOTE"],
        delimiter="|",
        has_header=True,
    )
    return path, parser, mapping


def test_delimited_date_analysis_parity():
    path, parser, mapping = _delimited_setup()
    try:
        validator = EnhancedFileValidator(parser, mapping)
        result = validator.validate(detailed=True)
    finally:
        os.unlink(path)

    da = result["date_analysis"]

    # Which columns ARE reported as dates: the two genuine date columns plus
    # __source_row__ (an int column that pd.to_datetime coerces 100%). The
    # non-date object columns (FAKE_DATE, AMOUNT, CODE, NOTE) must be ABSENT.
    assert set(da.keys()) == {"__source_row__", "REAL_DATE", "OPEN_DATE"}
    assert "FAKE_DATE" not in da
    assert "AMOUNT" not in da
    assert "CODE" not in da
    assert "NOTE" not in da

    # Every reported entry has the full key set.
    for col, entry in da.items():
        assert set(entry.keys()) == _date_keys(), col

    # Pin the genuine ISO date column values exactly.
    real = da["REAL_DATE"]
    assert real["earliest_date"] == "2021-01-15T00:00:00"
    assert real["latest_date"] == "2023-07-04T00:00:00"
    assert real["valid_date_count"] == 5
    assert real["valid_date_pct"] == 100.0
    assert real["invalid_date_count"] == 0
    assert real["null_date_count"] == 0
    assert real["detected_formats"] == ["YYYY-MM-DD"]

    # OPEN_DATE (YYYYMMDD) — pandas parses 8-digit ints/strings as dates.
    od = da["OPEN_DATE"]
    assert od["valid_date_count"] == 5
    assert od["valid_date_pct"] == 100.0
    assert od["detected_formats"] == ["YYYYMMDD"]


def test_delimited_field_analysis_inferred_types_parity():
    path, parser, mapping = _delimited_setup()
    try:
        validator = EnhancedFileValidator(parser, mapping)
        result = validator.validate(detailed=True)
    finally:
        os.unlink(path)

    fa = result["field_analysis"]
    # inferred_type is computed independently of the date gate; pin it so a
    # gate change cannot accidentally perturb _infer_data_type.
    assert fa["REAL_DATE"]["inferred_type"] == "datetime"
    assert fa["OPEN_DATE"]["inferred_type"] == "datetime"
    assert fa["FAKE_DATE"]["inferred_type"] == "string"
    assert fa["AMOUNT"]["inferred_type"] == "numeric"
    assert fa["NOTE"]["inferred_type"] == "string"


def test_delimited_no_spurious_date_warnings():
    path, parser, mapping = _delimited_setup()
    try:
        validator = EnhancedFileValidator(parser, mapping)
        result = validator.validate(detailed=True)
    finally:
        os.unlink(path)

    # No invalid-date warnings should fire for any column in this clean set.
    date_warnings = [
        w for w in result["warnings"]
        if "invalid date values" in str(w.get("message", ""))
    ]
    assert date_warnings == []


# --------------------------------------------------------------------------- #
# Scenario A2 — a date-named column that is mostly NOT dates
# --------------------------------------------------------------------------- #

def test_date_named_but_not_date_column_excluded():
    # A column literally named *_DATE whose contents are NOT dates (< 50%
    # parseable) must NOT appear in date_analysis — the gate must not flip it.
    header = "TXN_DATE|VAL"
    rows = [
        "NOTADATE|1",
        "ALSONOPE|2",
        "STILLNO_|3",
        "20210101|4",  # one real-ish date, but < 50% overall
        "RANDOMXX|5",
    ]
    content = header + "\n" + "\n".join(rows) + "\n"
    path = _write(content, ".psv")
    mapping = {"file_path": "m.json", "fields": [
        {"name": "TXN_DATE", "data_type": "String"},
        {"name": "VAL", "data_type": "String"},
    ]}
    parser = PipeDelimitedParser(path, columns=["TXN_DATE", "VAL"],
                                 delimiter="|", has_header=True)
    try:
        validator = EnhancedFileValidator(parser, mapping)
        result = validator.validate(detailed=True)
    finally:
        os.unlink(path)

    da = result["date_analysis"]
    assert "TXN_DATE" not in da
    # __source_row__ (int) still parses as a date today.
    assert "__source_row__" in da


# --------------------------------------------------------------------------- #
# Scenario B — fixed-width, strict violations (error construction)
# --------------------------------------------------------------------------- #

def _fixed_width_setup():
    # Layout: ACCT(4) STATUS(1) AMT(4)  -> record length 9
    #   ACCT   required, format 9(4) (4 digits)
    #   STATUS valid_values [A, B]
    #   AMT    format 9(4)
    # Rows engineered to trigger each strict violation across distinct rows so
    # row-number math (int(idx)+1) is exercised.
    lines = [
        "1234A0010",   # row 1: clean
        "5678B0020",   # row 2: clean
        "    A0030",   # row 3: ACCT required-empty (FW_REQ_001)
        "9012Z0040",   # row 4: STATUS invalid value 'Z' (FW_VAL_001)
        "34X6A00X0",   # row 5: ACCT '34X6' bad format + AMT '00X0' bad format
        "7777B0050",   # row 6: clean
        "8888Q0060",   # row 7: STATUS invalid value 'Q' (FW_VAL_001)
    ]
    content = "\n".join(lines) + "\n"
    path = _write(content, ".dat")
    mapping = {
        "file_path": "fw_mapping.json",
        "fields": [
            {"name": "ACCT", "position": 1, "length": 4,
             "required": True, "format": "9(4)"},
            {"name": "STATUS", "position": 5, "length": 1,
             "required": False, "valid_values": ["A", "B"]},
            {"name": "AMT", "position": 6, "length": 4,
             "required": False, "format": "9(4)"},
        ],
        "total_record_length": 9,
    }
    parser = FixedWidthParser(
        path, [("ACCT", 0, 4), ("STATUS", 4, 5), ("AMT", 5, 9)]
    )
    return path, parser, mapping


def _strict_errors(result):
    return [e for e in result["errors"]
            if e.get("category") == "strict_fixed_width"]


def test_fixed_width_strict_errors_parity():
    path, parser, mapping = _fixed_width_setup()
    try:
        validator = EnhancedFileValidator(parser, mapping)
        result = validator.validate(
            detailed=False, strict_fixed_width=True, strict_level="format"
        )
    finally:
        os.unlink(path)

    errs = _strict_errors(result)

    # Capture the exact emitted strict-FW error dicts, in emission order.
    # Emission order follows the source loop: per field (ACCT, STATUS, AMT),
    # required-checks first then valid_values then format, scanning rows in
    # index order within each check.
    expected = [
        # ACCT required-empty (row 3)
        {"severity": "error", "category": "strict_fixed_width", "code": "FW_REQ_001",
         "message": "Required field 'ACCT' is empty", "row": 3, "field": "ACCT"},
        # ACCT format mismatch '34X6' (row 5)
        {"severity": "error", "category": "strict_fixed_width", "code": "FW_FMT_001",
         "message": "Field 'ACCT' has invalid format for value '34X6'",
         "row": 5, "field": "ACCT"},
        # STATUS invalid value 'Z' (row 4)
        {"severity": "error", "category": "strict_fixed_width", "code": "FW_VAL_001",
         "message": "Field 'STATUS' has invalid value 'Z'",
         "row": 4, "field": "STATUS"},
        # STATUS invalid value 'Q' (row 7)
        {"severity": "error", "category": "strict_fixed_width", "code": "FW_VAL_001",
         "message": "Field 'STATUS' has invalid value 'Q'",
         "row": 7, "field": "STATUS"},
        # AMT format mismatch '00X0' (row 5)
        {"severity": "error", "category": "strict_fixed_width", "code": "FW_FMT_001",
         "message": "Field 'AMT' has invalid format for value '00X0'",
         "row": 5, "field": "AMT"},
    ]
    assert errs == expected


def test_fixed_width_strict_result_summary_parity():
    path, parser, mapping = _fixed_width_setup()
    try:
        validator = EnhancedFileValidator(parser, mapping)
        result = validator.validate(
            detailed=False, strict_fixed_width=True, strict_level="format"
        )
    finally:
        os.unlink(path)

    strict = result["strict_fixed_width"]
    assert strict["enabled"] is True
    assert strict["strict_level"] == "format"
    # Invalid rows: 3 (req), 4 (val), 5 (two fmt), 7 (val) -> {3,4,5,7}
    assert strict["invalid_row_numbers"] == [3, 4, 5, 7]
    assert strict["invalid_records"] == 4
    # format_errors counts VAL + FMT (not REQ): Z, Q, ACCT-fmt, AMT-fmt = 4
    assert strict["format_errors"] == 4


def _gate_validator():
    """A validator whose parser/mapping are irrelevant for gate unit tests."""
    path = _write("x\n", ".psv")
    parser = PipeDelimitedParser(path, columns=["x"], delimiter="|", has_header=True)
    try:
        return EnhancedFileValidator(parser, None), path
    finally:
        pass


def test_gate_empty_dataframe_returns_true():
    v, path = _gate_validator()
    try:
        df = pd.DataFrame({"A": []})
        assert v._is_date_candidate(df, "A") is True
    finally:
        os.unlink(path)


def test_gate_non_string_dtype_passthrough():
    # Integer columns are never gated out (they parse as dates today).
    v, path = _gate_validator()
    try:
        df = pd.DataFrame({"N": pd.Series(range(1, 6001))})
        assert df["N"].dtype != object
        assert v._is_date_candidate(df, "N") is True
    finally:
        os.unlink(path)


def test_gate_null_dominated_column_skipped():
    # > 50% null -> can never reach 50% valid dates -> skip without parsing.
    v, path = _gate_validator()
    try:
        col = [None] * 7000 + ["2021-01-01"] * 3000  # 30% non-null
        df = pd.DataFrame({"D": col})
        assert v._is_date_candidate(df, "D") is False
    finally:
        os.unlink(path)


def test_gate_large_non_date_column_skipped_matches_full_coercion():
    # Large object column of non-date text: gate must skip AND the legacy full
    # coercion must also classify it as not-a-date (verdict parity).
    import warnings as _w
    v, path = _gate_validator()
    try:
        vals = [f"TEXT{i:05d}XY" for i in range(6000)]
        df = pd.DataFrame({"T": pd.Series(vals, dtype=object)})
        # Gate decision.
        gate = v._is_date_candidate(df, "T")
        # Authoritative legacy verdict.
        with _w.catch_warnings():
            _w.simplefilter("ignore")
            full = pd.to_datetime(df["T"], errors="coerce")
        full_is_date = bool(full.notna().sum() / len(df) * 100 >= 50)
        assert gate is False
        assert full_is_date is False  # parity: both say "not a date"
    finally:
        os.unlink(path)


def test_gate_large_date_column_kept_matches_full_coercion():
    # Large object column of genuine dates: gate keeps it for full coercion.
    import warnings as _w
    v, path = _gate_validator()
    try:
        vals = [f"20210{(i % 9) + 1:01d}15" for i in range(6000)]  # YYYYMMDD
        df = pd.DataFrame({"D": pd.Series(vals, dtype=object)})
        gate = v._is_date_candidate(df, "D")
        with _w.catch_warnings():
            _w.simplefilter("ignore")
            full = pd.to_datetime(df["D"], errors="coerce")
        full_is_date = bool(full.notna().sum() / len(df) * 100 >= 50)
        assert gate is True
        assert full_is_date is True
    finally:
        os.unlink(path)


def test_fixed_width_strict_basic_level_parity():
    # basic level: only required checks fire, no VAL/FMT.
    path, parser, mapping = _fixed_width_setup()
    try:
        validator = EnhancedFileValidator(parser, mapping)
        result = validator.validate(
            detailed=False, strict_fixed_width=True, strict_level="basic"
        )
    finally:
        os.unlink(path)

    errs = _strict_errors(result)
    assert errs == [
        {"severity": "error", "category": "strict_fixed_width", "code": "FW_REQ_001",
         "message": "Required field 'ACCT' is empty", "row": 3, "field": "ACCT"},
    ]
    assert result["strict_fixed_width"]["format_errors"] == 0
