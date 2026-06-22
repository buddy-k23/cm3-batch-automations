"""Output-parity regression tests for chunked strict validation.

These tests pin the EXACT validation output (error dicts, row numbers,
ordering, stats) of the chunked strict-validation path. They were written
against the original ``iterrows``-based implementation and must continue to
pass byte-for-byte after the path is vectorized. Any change to error shape,
message text, row-number math, or error ordering will fail these tests.
"""

import os
import tempfile

import pandas as pd

from src.parsers.chunked_validator import ChunkedFileValidator


def _write(rows: list[str]) -> str:
    """Write *rows* (already newline-free) to a temp file, return its path."""
    fh = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt')
    for r in rows:
        fh.write(r + '\n')
    fh.close()
    return fh.name


# Field definitions exercising every strict check kind:
#   - REQ (required, empty)
#   - VAL (valid_values mismatch)
#   - FMT (format mismatch, COBOL codes)
#   - DT_INT (integer data_type)
#   - DT_FLT (float data_type)
STRICT_FIELDS = [
    {'name': 'CODE', 'required': True, 'valid_values': ['AA', 'BB', 'CC']},
    {'name': 'DATE', 'required': False, 'format': 'CCYYMMDD'},
    {'name': 'NUM', 'required': False, 'data_type': 'integer', 'format': '9(3)'},
    {'name': 'AMT', 'required': False, 'data_type': 'float'},
    {'name': 'ALPHA', 'required': False, 'format': 'XXX'},
]

HEADER = 'CODE|DATE|NUM|AMT|ALPHA'

# Mix of valid rows and each violation kind, designed so violations span
# multiple chunks when chunk_size is small (e.g. 3).
DATA_ROWS = [
    'AA|20240101|123|10.5|abc',   # row 1: all valid
    'XX|20240101|123|10.5|abc',   # row 2: CODE invalid value (FW_VAL)
    'AA|2024010 |123|10.5|abc',   # row 3: DATE bad format (FW_FMT) -> 7 chars after strip
    'AA|20240101|12X|10.5|abc',   # row 4: NUM not integer (DT_INT) AND bad 9(3) format (FW_FMT)
    'AA|20240101|123|abc|abc',    # row 5: AMT not float (DT_FLT)
    'AA|20240101|123|10.5|ab1',   # row 6: ALPHA bad XXX format (FW_FMT)
    ' |20240101|123|10.5|abc',    # row 7: CODE required empty (FW_REQ) -> blank
    'AA|20240101|99|10.5|abc',    # row 8: NUM '99' is 2 digits -> 9(3) FMT violation
    'BB|20240101|123|10.5|abc',   # row 9: all valid
]


def _run(chunk_size: int, strict_level: str = 'format'):
    """Run chunked strict validation over the fixture and return the result."""
    path = _write([HEADER] + DATA_ROWS)
    try:
        validator = ChunkedFileValidator(
            file_path=path,
            delimiter='|',
            chunk_size=chunk_size,
            strict_fixed_width=True,
            strict_level=strict_level,
            strict_fields=STRICT_FIELDS,
            has_header=True,
        )
        return validator.validate(show_progress=False)
    finally:
        os.unlink(path)


# The full expected error list, in the EXACT order the row-then-phase-then-field
# nested loop emits them. Phase order per row: all data_type checks (field order)
# first, then all strict_fixed_width checks (field order). Row numbers are 1-based
# absolute over the whole file (independent of chunk boundaries).
def _expected_errors():
    return [
        # row 2: CODE invalid value
        {'severity': 'error', 'category': 'strict_fixed_width', 'code': 'FW_VAL_001',
         'message': "Field 'CODE' has invalid value 'XX'", 'row': 2, 'field': 'CODE'},
        # row 3: DATE bad format ('2024010' is 7 chars after strip)
        {'severity': 'error', 'category': 'strict_fixed_width', 'code': 'FW_FMT_001',
         'message': "Field 'DATE' has invalid format for value '2024010'", 'row': 3, 'field': 'DATE'},
        # row 4: NUM data_type integer fails (DT first), then NUM format fails
        {'severity': 'error', 'category': 'data_type', 'code': 'DT_INT_001',
         'message': "Field 'NUM' expects integer but got '12X'", 'row': 4, 'field': 'NUM'},
        {'severity': 'error', 'category': 'strict_fixed_width', 'code': 'FW_FMT_001',
         'message': "Field 'NUM' has invalid format for value '12X'", 'row': 4, 'field': 'NUM'},
        # row 5: AMT data_type float fails
        {'severity': 'error', 'category': 'data_type', 'code': 'DT_FLT_001',
         'message': "Field 'AMT' expects float but got 'abc'", 'row': 5, 'field': 'AMT'},
        # row 6: ALPHA bad XXX format
        {'severity': 'error', 'category': 'strict_fixed_width', 'code': 'FW_FMT_001',
         'message': "Field 'ALPHA' has invalid format for value 'ab1'", 'row': 6, 'field': 'ALPHA'},
        # row 7: CODE required empty
        {'severity': 'error', 'category': 'strict_fixed_width', 'code': 'FW_REQ_001',
         'message': "Required field 'CODE' is empty", 'row': 7, 'field': 'CODE'},
        # row 8: NUM '99' is 2 digits -> 9(3) format violation
        {'severity': 'error', 'category': 'strict_fixed_width', 'code': 'FW_FMT_001',
         'message': "Field 'NUM' has invalid format for value '99'", 'row': 8, 'field': 'NUM'},
    ]


def _strict_errors_only(result):
    """Return only the per-row strict/data_type error dicts (drop the aggregate
    FW_REQ row=None fail-safe and any non-dict warnings)."""
    out = []
    for e in result.get('errors', []):
        if isinstance(e, dict) and e.get('row') is not None and e.get('code') in {
            'DT_INT_001', 'DT_FLT_001', 'FW_REQ_001', 'FW_VAL_001', 'FW_FMT_001',
        }:
            out.append(e)
    return out


def test_parity_single_chunk():
    """All rows in one chunk: exact error list, shape, order, row numbers."""
    result = _run(chunk_size=100)
    assert _strict_errors_only(result) == _expected_errors()


def test_parity_multi_chunk_size_3():
    """Violations span chunk boundaries (chunk_size=3) — row math must hold."""
    result = _run(chunk_size=3)
    assert _strict_errors_only(result) == _expected_errors()


def test_parity_multi_chunk_size_4():
    """Different chunk boundary alignment (chunk_size=4)."""
    result = _run(chunk_size=4)
    assert _strict_errors_only(result) == _expected_errors()


def test_parity_chunk_size_equals_rowcount_boundary():
    """Chunk boundary exactly between rows (chunk_size=5)."""
    result = _run(chunk_size=5)
    assert _strict_errors_only(result) == _expected_errors()


def test_parity_aggregate_required_failsafe_stable():
    """The aggregate FW_REQ_001 (row=None) fail-safe behavior is identical
    across chunk sizes. (In the pipe-delimited fixture the blank CODE strips
    to neither a recorded null nor empty-string in stats, so no aggregate
    fail-safe error is emitted — only the per-row FW_REQ_001 fires. This test
    pins whatever the current behavior is, identically across chunk sizes.)"""
    r1 = _run(chunk_size=100)
    r2 = _run(chunk_size=3)

    def failsafe(result):
        return [
            e for e in result.get('errors', [])
            if isinstance(e, dict) and e.get('code') == 'FW_REQ_001'
            and e.get('row') is None
        ]

    assert failsafe(r1) == failsafe(r2)


def test_parity_statistics_stable():
    """Null/empty stats and overall validity are stable across chunk sizes."""
    r1 = _run(chunk_size=100)
    r2 = _run(chunk_size=3)
    assert r1['valid'] == r2['valid'] is False
    assert r1['total_rows'] == r2['total_rows'] == len(DATA_ROWS)
    assert r1['statistics']['empty_string_counts'] == r2['statistics']['empty_string_counts']
    assert r1['statistics']['null_counts'] == r2['statistics']['null_counts']


def test_parity_duplicate_detection():
    """Duplicate rows produce a duplicate count regardless of chunk size."""
    path = _write([HEADER, 'AA|20240101|123|10.5|abc', 'AA|20240101|123|10.5|abc', 'BB|20240101|123|10.5|abc'])
    try:
        v1 = ChunkedFileValidator(file_path=path, delimiter='|', chunk_size=100,
                                  strict_fixed_width=True, strict_level='format',
                                  strict_fields=STRICT_FIELDS, has_header=True)
        r1 = v1.validate(show_progress=False)
        v2 = ChunkedFileValidator(file_path=path, delimiter='|', chunk_size=1,
                                  strict_fixed_width=True, strict_level='format',
                                  strict_fields=STRICT_FIELDS, has_header=True)
        r2 = v2.validate(show_progress=False)
        assert r1['statistics']['duplicate_count'] == r2['statistics']['duplicate_count'] == 1
    finally:
        os.unlink(path)


def test_parity_worker_path_matches_sequential():
    """The parallel worker strict path emits the same per-row strict errors
    (modulo ordering) as the sequential path."""
    path = _write([HEADER] + DATA_ROWS)
    try:
        seq = ChunkedFileValidator(file_path=path, delimiter='|', chunk_size=3,
                                   strict_fixed_width=True, strict_level='format',
                                   strict_fields=STRICT_FIELDS, has_header=True, workers=1)
        seq_res = seq.validate(show_progress=False)
        par = ChunkedFileValidator(file_path=path, delimiter='|', chunk_size=3,
                                   strict_fixed_width=True, strict_level='format',
                                   strict_fields=STRICT_FIELDS, has_header=True, workers=2)
        par_res = par.validate(show_progress=False)

        def key(e):
            return (e['row'], e['code'], e['field'])

        seq_errs = sorted(_strict_errors_only(seq_res), key=key)
        par_errs = sorted(_strict_errors_only(par_res), key=key)
        assert seq_errs == par_errs
    finally:
        os.unlink(path)
