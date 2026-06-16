"""Shared delimited-file writer for adapter ``extract_to_file`` paths (S16-2, #426).

Before S16-2 each adapter joined a row's values with the configured delimiter
and a bare ``str(val)``.  That manual join was lossy: a value that itself
contained the delimiter split into a spurious extra column when read back, and a
value containing a newline split into a spurious extra row — corrupting the
extract and (worse) making ``db-compare`` flag false diffs.

This module centralises the write so every backend (Oracle, PostgreSQL, SQLite)
and the orchestrating :class:`~src.database.extractor.DataExtractor` share one
correct implementation built on the stdlib :mod:`csv` module:

* :func:`csv.writer` with the configured ``delimiter`` and
  :data:`csv.QUOTE_MINIMAL` — values containing the delimiter, the quote
  character, or a newline are automatically quoted/escaped, and simple values
  are written bare (no spurious quoting).  This is **symmetric** with the read
  path (:func:`pandas.read_csv` with ``sep=delimiter``, which applies the same
  standard CSV quoting), so extracted files round-trip.
* :func:`_format_value` gives a **stable** textual rendering of each value —
  in particular it normalises :class:`~decimal.Decimal` trailing zeros so a
  ``Decimal('100.50')`` from one backend renders the same token as a ``float``
  ``100.5`` from another, removing the float-trailing-zero render that the
  Sprint-15 PostgreSQL smoke (``demo/pg_fullstack_smoke.sh``) saw db-compare
  report as a spurious diff.

NUMERIC scope note (S16-2): this is the minimal *extract-side* fix — it makes
the **written representation self-consistent and stable**.  Full cross-engine
numeric equivalence (e.g. honouring a column's declared scale, or treating
``100`` == ``100.0`` across backends) is a *compare-time* normalisation concern
and is intentionally left to the comparator rather than over-reaching here.
"""

from __future__ import annotations

import csv
from decimal import Decimal
from typing import Any, Iterable, List, Sequence


# ``newline=""`` is required by the csv module so it controls line termination
# itself (otherwise quoted fields containing "\n" can be mangled on some
# platforms). We pin the terminator to "\n" for cross-platform stable output.
_LINE_TERMINATOR = "\n"


def _format_value(val: Any) -> str:
    """Render a single cell value to a stable string for delimited output.

    ``None`` becomes an empty field (the long-standing NULL contract).
    :class:`~decimal.Decimal` values are normalised so trailing zeros do not
    produce a representation that differs from an equal ``float`` on another
    backend (the PG-smoke ``100.50`` vs ``100.5`` false-diff): the Decimal is
    normalised and, when integral, rendered as a plain integer token (``100``,
    never ``1E+2``).  All other values fall back to ``str``.

    Residual scope note (S16-2): this stabilises the **extract-side** rendering
    so it is self-consistent.  Cross-type equivalence that the extract side
    cannot see — e.g. a float ``100.0`` (rendered ``'100.0'`` by Python's
    ``str``) versus a Decimal ``100`` (rendered ``'100'``) — is a compare-time
    normalisation concern and is intentionally not forced here (normalising
    arbitrary floats risks silently changing precision).

    Args:
        val: The raw cell value from the driver (str, int, float, Decimal,
            bytes, ``None``, …).

    Returns:
        The stable string representation to write into the field.
    """
    if val is None:
        return ""
    if isinstance(val, Decimal):
        # normalize() strips trailing zeros (100.50 -> 1.0050E+2 / 100.5) but
        # can yield scientific notation for integral values (100.00 -> 1E+2);
        # quantize back to a plain fixed-point string. Matching str(float)
        # behaviour keeps Decimal- and float-backed columns consistent.
        normalized = val.normalize()
        # to_integral_value when there is no fractional part avoids "1E+2".
        if normalized == normalized.to_integral_value():
            return str(normalized.quantize(Decimal(1)))
        return format(normalized, "f")
    return str(val)


def _write_delimited(
    output_path: str,
    delimiter: str,
    col_names: Sequence[str],
    row_batches: Iterable[Sequence[Sequence[Any]]],
) -> int:
    """Write a header + data rows to *output_path* using ``csv.writer``.

    Centralises the delimited write for every ``extract_to_file`` path so the
    quoting/escaping is correct and identical across backends (S16-2, #426).
    Values containing the delimiter, the quote character, or a newline are
    quoted via :data:`csv.QUOTE_MINIMAL`; simple values are written bare.

    Args:
        output_path: Path of the file to create or overwrite.
        delimiter: Single-character column separator (e.g. ``"|"``).
        col_names: Ordered column names for the header line.
        row_batches: An iterable of row *batches* (each batch a sequence of
            rows; each row a sequence of cell values).  Supplying batches lets
            chunk-fetching backends (Oracle/PostgreSQL ``fetchmany``) stream
            without materialising the whole result set; a single-shot backend
            (SQLite ``fetchall``) simply passes one batch.

    Returns:
        Total number of data rows written (excluding the header).
    """
    total = 0
    with open(output_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(
            fh,
            delimiter=delimiter,
            quoting=csv.QUOTE_MINIMAL,
            lineterminator=_LINE_TERMINATOR,
        )
        writer.writerow(list(col_names))
        for batch in row_batches:
            for row in batch:
                writer.writerow([_format_value(val) for val in row])
                total += 1
    return total
