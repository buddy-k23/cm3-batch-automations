"""Reusable Excel *data* reader for the Excel<->DB comparison path (S24-1).

This is a foundational reader that loads a sheet of **data** (rows of records,
not a BA-authored spec template) from an ``.xlsx`` / ``.xls`` workbook into a
string-coerced :class:`pandas.DataFrame`.  The output is shaped so it can be
joined row-by-row against a database extract in S24-2 (the Excel<->DB compare
service) without value drift.

Why a dedicated reader (layering)
---------------------------------
The reader lives in ``src/parsers`` — the lowest layer of the stack
(CLI/API -> Commands/Routers -> Services -> Parsing).  It sits alongside the
existing format readers (:mod:`src.parsers.pipe_delimited_parser`,
:mod:`src.parsers.fixed_width_parser`): like them it turns a file on disk into a
comparable DataFrame and has no orchestration responsibilities.  S24-2's compare
service (the *services* layer) will import and call it, honouring clean layer
separation — a service may depend on a parser, never the reverse.  It is
deliberately **net-new and additive**: it does not touch the spec-ingestion
converters (:mod:`src.config.template_converter`,
:mod:`src.onboarding.workbook_reader`), which read *templates* into mapping
objects, a different concern.

String-coercion contract (matches the comparison side)
------------------------------------------------------
The DB side and the file side of a comparison are both read as **strings** so a
row-by-row diff lines up: :func:`src.services.compare_service.run_compare_service`
and :class:`src.parsers.pipe_delimited_parser.PipeDelimitedParser` use
``pandas.read_csv(..., dtype=str, keep_default_na=False)``.  This reader mirrors
that with ``pandas.read_excel(..., dtype=str)`` plus deterministic fixes for the
two ways Excel silently mangles values that ``dtype=str`` alone cannot fix:

* **Integer-key float drift.**  Excel has no integer type — a whole number like
  the key ``12345`` is stored as the float ``12345.0`` and read back as the
  string ``"12345.0"``.  A DB ``NUMBER``/``CHAR`` key extracts as ``"12345"``,
  so the join would miss every key.  The reader normalises a trailing ``.0`` on
  whole numbers: ``"12345.0" -> "12345"`` and ``"0.0" -> "0"``, while genuine
  decimals are preserved (``"100.5"`` stays ``"100.5"``).
* **Date Timestamp drift.**  Excel date cells read as ``pandas.Timestamp`` /
  ``datetime`` objects whose ``str()`` form carries a time component
  (``"2024-03-17 00:00:00"``).  The reader coerces them to a stable ISO
  ``YYYY-MM-DD`` string so they compare predictably against a DB ``DATE`` column
  rendered the same way.  (The format is fixed and documented here; S24-2 must
  render its DB date column to the same ISO form to stay consistent — see the
  module note at the bottom.)
* **Blank cells -> ""** (not ``NaN``), matching ``keep_default_na=False`` so an
  empty Excel cell and an empty DB string compare equal rather than as ``NaN``.

Values are otherwise preserved verbatim — no stripping by default — matching the
comparison side, which does not strip cell contents.

Header normalisation
--------------------
By default the reader normalises column headers with
:func:`src.utils.column_names.normalize_column_name` (UPPER case, ``-`` -> ``_``).
Raw Oracle cursor column names come back upper-cased with underscores (hyphens
are illegal SQL identifiers; see :mod:`src.utils.column_names` and
``DataExtractor.get_table_stats`` which upper-cases cursor columns), so aligning
the Excel headers to the same canonical form *now* means the S24-2 join finds its
key columns instead of silently producing zero matches — the same class of bug
fixed in the run-tests path.  Callers that need the raw header strings (e.g. for
display) can opt out with ``normalize_headers=False``.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from src.utils.column_names import normalize_column_name

__all__ = ["read_excel_data"]


def _is_iso_date(text: str) -> bool:
    """Return True when *text* is exactly a ``YYYY-MM-DD`` calendar date.

    Args:
        text: Candidate date string (the date portion split from a
            ``"YYYY-MM-DD HH:MM:SS"`` cell rendered by pandas).

    Returns:
        True if *text* parses as an ISO calendar date, False otherwise.
    """
    try:
        date.fromisoformat(text)
        return True
    except ValueError:
        return False


def _coerce_cell(value: object) -> str:
    """Coerce a single Excel cell to its stable comparison string.

    Applies the three deterministic fixes documented at the module level, in
    order: blank -> ``""``, date/Timestamp -> ISO ``YYYY-MM-DD``, whole-number
    float string -> integer string (trailing ``.0`` stripped).  Any other value
    is returned as its plain ``str`` form.

    Args:
        value: The raw cell value as produced by ``pandas.read_excel`` with
            ``dtype=str`` — a ``str`` for populated cells (date cells already
            rendered as ``"YYYY-MM-DD HH:MM:SS"`` strings) or ``NaN`` (float)
            for a blank cell.

    Returns:
        The cell rendered as a stable string suitable for row-by-row comparison
        against a DB extract.
    """
    # Blank cell -> "" (keep_default_na=False semantics). pandas surfaces a
    # blank as a float NaN even under dtype=str; pd.isna handles NaN/NaT/None.
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ""

    text = str(value)

    # Date Timestamp drift via dtype=str: pandas.read_excel(dtype=str) renders a
    # date cell as the string "YYYY-MM-DD HH:MM:SS" (a midnight time component
    # it appended itself), never as a Timestamp. Trim a pure-midnight time so a
    # date compares against a DB DATE rendered as ISO YYYY-MM-DD. A non-midnight
    # time is a genuine datetime value and is preserved verbatim.
    if text.endswith(" 00:00:00") and len(text) == len("YYYY-MM-DD 00:00:00"):
        date_part = text[: -len(" 00:00:00")]
        if _is_iso_date(date_part):
            return date_part

    # Integer-key float drift: a whole-number float prints as "<int>.0".
    # Normalise so an Excel key matches a DB CHAR/NUMBER key. Only the exact
    # "<digits>.0" shape is touched — genuine decimals keep their fraction.
    if text.endswith(".0"):
        head = text[:-2]
        sign = ""
        if head.startswith("-"):
            sign, head = "-", head[1:]
        if head.isdigit():
            # "0.0" -> "0", "12345.0" -> "12345", "-7.0" -> "-7".
            return f"{sign}{head}"

    return text


def read_excel_data(
    file_path: str | Path,
    sheet: str | int | None = None,
    header_row: int = 0,
    columns: list[str] | None = None,
    normalize_headers: bool = True,
) -> pd.DataFrame:
    """Read a sheet of data from an Excel workbook into a string DataFrame.

    Loads one worksheet of *data* (not a spec template) and returns a
    DataFrame whose every cell is a stable string, ready to be joined against a
    DB extract by the S24-2 compare service.  See the module docstring for the
    full string-coercion and header-normalisation contract.

    Args:
        file_path: Path to the ``.xlsx`` (or ``.xls``) workbook.
        sheet: Which sheet to read.  A ``str`` selects by sheet name, an ``int``
            selects by zero-based position.  ``None`` (the default) reads the
            first sheet.
        header_row: Zero-based index of the row used for column names.  Rows
            above it (e.g. a title banner) are skipped and not returned as data.
            Defaults to ``0`` (the first row is the header).
        columns: Optional list of column names to keep, in the requested order.
            Each name is matched after header normalisation (so both
            ``"ACCT-NUM"`` and ``"ACCT_NUM"`` resolve when
            ``normalize_headers`` is ``True``).  A name absent from the sheet
            raises ``ValueError``.  ``None`` keeps all columns.
        normalize_headers: When ``True`` (the default) column headers are
            normalised via
            :func:`src.utils.column_names.normalize_column_name` (UPPER case,
            ``-`` -> ``_``) so they line up with raw DB cursor column names for
            the S24-2 join.  Set ``False`` to preserve the raw header strings.

    Returns:
        A :class:`pandas.DataFrame` whose columns are all ``object``-dtype
        strings; blank cells are ``""`` (never ``NaN``), whole-number keys have
        no trailing ``.0``, and date cells are ISO ``YYYY-MM-DD`` strings.

    Raises:
        FileNotFoundError: If *file_path* does not exist.
        ValueError: If *sheet* names/indexes a worksheet that does not exist
            (the message lists the available sheet names), or if a name in
            *columns* is not present in the resolved sheet.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Excel file not found: {file_path}")

    # Enumerate sheet names up front so sheet-selection errors can list the
    # available names and so an index can be validated before the read.
    with pd.ExcelFile(path) as xls:
        available = list(xls.sheet_names)

        target = _resolve_sheet(sheet, available)

        # dtype=str mirrors the comparison side's read_csv(dtype=str, ...).
        # header=header_row consumes the header row and drops everything above
        # it. We deliberately do NOT pass keep_default_na (read_excel has no
        # such kwarg) — blanks are normalised to "" in _coerce_cell instead.
        df = pd.read_excel(xls, sheet_name=target, header=header_row, dtype=str)

    # Per-cell coercion: blanks -> "", dates -> ISO, ".0" float keys -> int.
    # applymap is the simplest element-wise pass; the frames here are
    # comparison-sized, not streaming-large.
    df = df.apply(lambda col: col.map(_coerce_cell))

    if normalize_headers:
        df.columns = [normalize_column_name(c) for c in df.columns]

    if columns is not None:
        wanted = (
            [normalize_column_name(c) for c in columns]
            if normalize_headers
            else list(columns)
        )
        missing = [c for c in wanted if c not in df.columns]
        if missing:
            raise ValueError(
                f"Requested column(s) not found in sheet: {missing}. "
                f"Available columns: {list(df.columns)}"
            )
        df = df[wanted]

    return df


def _resolve_sheet(sheet: str | int | None, available: list[str]) -> str | int:
    """Resolve the *sheet* selector to a concrete name or validated index.

    Args:
        sheet: The caller's selector — a sheet name (``str``), a zero-based
            position (``int``), or ``None`` for the first sheet.
        available: The workbook's sheet names, in workbook order.

    Returns:
        The selector to hand to ``pandas.read_excel``: the resolved sheet name
        for a ``str``/``None`` selector, or the validated integer index.

    Raises:
        ValueError: If a named sheet is absent (message lists available names)
            or an integer index is out of range.
    """
    if sheet is None:
        return 0

    if isinstance(sheet, int):
        if sheet < 0 or sheet >= len(available):
            raise ValueError(
                f"Sheet index {sheet} is out of range; the workbook has "
                f"{len(available)} sheet(s): {available}"
            )
        return sheet

    if sheet not in available:
        raise ValueError(
            f"Sheet '{sheet}' not found. Available sheets: {available}"
        )
    return sheet
