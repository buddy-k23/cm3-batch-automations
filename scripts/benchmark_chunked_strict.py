"""Benchmark the chunked strict-validation hot path.

Generates a large pipe-delimited file with several strict fields (including a
COBOL format field and a valid_values field) and times
:meth:`ChunkedFileValidator.validate`. Intended for manual before/after
performance comparison of the chunked strict path — NOT collected by pytest
(lives under ``scripts/`` and is gated behind ``__main__``).

Usage::

    .venv/bin/python scripts/benchmark_chunked_strict.py [num_rows] [chunk_size]

Defaults: 300000 rows, chunk_size 50000. Prints wall-clock seconds and
rows/sec along with the error count so before/after runs can confirm the
output volume is unchanged.
"""

import os
import sys
import tempfile
import time

# Allow running from repo root without installing the package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.parsers.chunked_validator import ChunkedFileValidator  # noqa: E402

STRICT_FIELDS = [
    {'name': 'CODE', 'required': True, 'valid_values': ['AA', 'BB', 'CC']},
    {'name': 'DATE', 'required': False, 'format': 'CCYYMMDD'},
    {'name': 'NUM', 'required': False, 'data_type': 'integer', 'format': '9(3)'},
    {'name': 'AMT', 'required': False, 'data_type': 'float'},
    {'name': 'ALPHA', 'required': False, 'format': 'XXX'},
]
HEADER = 'CODE|DATE|NUM|AMT|ALPHA'


def _generate_file(num_rows: int) -> str:
    """Write *num_rows* rows (1 in 10 carrying a violation) to a temp file."""
    fh = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt')
    fh.write(HEADER + '\n')
    valid = 'AA|20240101|123|10.5|abc'
    # A rotating set of violation rows so every strict check kind is exercised.
    bad = [
        'XX|20240101|123|10.5|abc',   # invalid value
        'AA|2024010X|123|10.5|abc',   # bad date format
        'AA|20240101|12X|10.5|abc',   # non-integer + bad 9(3)
        'AA|20240101|123|xyz|abc',    # non-float
        'AA|20240101|123|10.5|ab1',   # bad XXX
    ]
    for i in range(num_rows):
        if i % 10 == 0:
            fh.write(bad[(i // 10) % len(bad)] + '\n')
        else:
            fh.write(valid + '\n')
    fh.close()
    return fh.name


def main() -> None:
    """Generate a large file, time chunked strict validation, print results."""
    num_rows = int(sys.argv[1]) if len(sys.argv) > 1 else 300_000
    chunk_size = int(sys.argv[2]) if len(sys.argv) > 2 else 50_000

    path = _generate_file(num_rows)
    try:
        validator = ChunkedFileValidator(
            file_path=path,
            delimiter='|',
            chunk_size=chunk_size,
            strict_fixed_width=True,
            strict_level='format',
            strict_fields=STRICT_FIELDS,
            has_header=True,
        )
        start = time.time()
        result = validator.validate(show_progress=False)
        elapsed = time.time() - start

        n_errors = sum(
            1 for e in result.get('errors', [])
            if isinstance(e, dict) and e.get('row') is not None
        )
        rps = num_rows / elapsed if elapsed > 0 else 0.0
        print(f"rows={num_rows:,} chunk_size={chunk_size:,}")
        print(f"wall_clock={elapsed:.3f}s  rows_per_sec={rps:,.0f}")
        print(f"per_row_strict_errors={n_errors:,}  valid={result['valid']}")
    finally:
        os.unlink(path)


if __name__ == '__main__':
    main()
