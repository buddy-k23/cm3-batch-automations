"""Multi-record file parser: line-level dispatch + per-type field slicing.

Purpose
-------
The L2b SQL-Truth gate (issue #17) needs to iterate a Valdo output file
row-by-row, with **per-field values** extracted, so it can compare them
against SQL truth columns. The reader primitive at
:mod:`src.validators.multi_record_reader` (extracted in issue #21) handles
line-level discriminator dispatch but leaves field slicing to its consumer.

This module is that consumer. It is a **thin wrapper** around
:func:`src.validators.multi_record_reader.read_multi_record_file`:

* For each :class:`~src.validators.multi_record_reader.ParsedRow` it
  loads (and caches) the per-record-type mapping JSON via
  :class:`~src.config.universal_mapping_parser.UniversalMappingParser`, then
  slices the raw line into a ``dict[field_name, trimmed_value]``.
* For each
  :class:`~src.validators.multi_record_reader.UnknownRecordTypeRow` it
  consults the umbrella's ``default_action`` and either raises or yields a
  row with ``record_type=None`` and empty fields.

Design notes
------------
* **Eager input validation, lazy mapping load.** All ``mapping_paths``
  entries are checked at call time (presence + existence on disk) before
  the generator starts, so missing-mapping errors surface synchronously
  rather than mid-iteration. The mapping JSON itself is loaded lazily on
  first encounter and cached per invocation, so a 30-record-type umbrella
  does not pay 30 file-open + JSON-parse costs upfront.
* **Per-invocation cache, not module-global.** The cache is local to
  :func:`iter_parsed_rows` so different harness runs (e.g. SIT then AIT)
  cannot contaminate each other.
* **No logging side-effects.** Matches the reader primitive's contract;
  callers (the L2b comparator) attach their own JSONL audit sinks.

Public API
----------
* :class:`ParsedFileRow`            — frozen value type, one per yielded row.
* :class:`MultiRecordFileParserError` — single exception type.
* :func:`iter_parsed_rows`          — file → iterator of :class:`ParsedFileRow`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Dict, Iterator, Mapping, Optional, Union

from src.config.multi_record_config import MultiRecordConfig
from src.config.universal_mapping_parser import UniversalMappingParser
from src.validators.multi_record_reader import (
    MultiRecordReaderError,
    ParsedRow,
    UnknownRecordTypeRow,
    read_multi_record_file,
)

# --------------------------------------------------------------------------- #
# Exception type
# --------------------------------------------------------------------------- #


class MultiRecordFileParserError(ValueError):
    """Raised for any multi-record file parsing failure.

    Single exception type matches the house style of other
    ``scripts/e2e_lib/`` modules. Covers: missing mapping for a configured
    record type, missing mapping file on disk, unknown discriminator under
    ``default_action='error'``, and underlying reader errors.
    """


# --------------------------------------------------------------------------- #
# Value type (frozen)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ParsedFileRow:
    """A single line with its record type identified and fields sliced.

    Attributes:
        record_type: Logical record-type name from the umbrella configuration
            (e.g. ``"32010"``). ``None`` for rows whose discriminator did not
            match any configured type and whose umbrella ``default_action``
            permits emission (``warn`` or ``skip``).
        fields: Mapping from field name (as declared in the per-record-type
            mapping JSON) to its trimmed string value. Empty dict for rows
            with ``record_type=None``. Read-only.
        line_number: 1-indexed line number in the source file. Blank lines
            are counted; this is the true file line number.
        raw_line: The line text with line terminators stripped.
    """

    record_type: Optional[str]
    fields: Mapping[str, str]
    line_number: int
    raw_line: str


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def iter_parsed_rows(
    file_path: Path,
    umbrella_config: MultiRecordConfig,
    mapping_paths: Mapping[str, Path],
) -> Iterator[ParsedFileRow]:
    """Iterate ``file_path``, yielding one :class:`ParsedFileRow` per line.

    Eagerly validates that every record type declared in ``umbrella_config``
    has a corresponding entry in ``mapping_paths`` and that each mapping
    file exists on disk. Missing-mapping errors raise
    :class:`MultiRecordFileParserError` at call time, before any iteration.

    For each row from the underlying reader primitive:

    * A :class:`~src.validators.multi_record_reader.ParsedRow` is sliced into
      fields using the record type's mapping JSON, which is loaded and cached
      on first encounter.
    * An :class:`~src.validators.multi_record_reader.UnknownRecordTypeRow`
      triggers behaviour based on ``umbrella_config.default_action``:

      - ``"error"`` raises :class:`MultiRecordFileParserError`.
      - ``"warn"`` or ``"skip"`` yields a :class:`ParsedFileRow` with
        ``record_type=None`` and empty ``fields``. The consumer decides
        whether to log, count, or discard.

    Args:
        file_path: Path to the multi-record data file.
        umbrella_config: The umbrella configuration that drives line-level
            discriminator dispatch in the reader primitive.
        mapping_paths: Mapping from record-type name (matching the keys of
            ``umbrella_config.record_types``) to the per-record-type mapping
            JSON file path. Every configured record type must have an entry.

    Yields:
        :class:`ParsedFileRow` instances in file order.

    Raises:
        MultiRecordFileParserError: If a record type is missing from
            ``mapping_paths``, if a mapping file does not exist, if the
            underlying reader fails (e.g. data file missing), or if an
            unknown discriminator is encountered under
            ``default_action='error'``.
    """
    # Eager input validation: presence + on-disk existence of every mapping.
    _validate_mapping_paths(umbrella_config, mapping_paths)

    # Translate the reader's eager-file-open errors into our own type so
    # callers only need to catch MultiRecordFileParserError.
    try:
        row_iter = read_multi_record_file(file_path, umbrella_config)
    except MultiRecordReaderError as exc:
        raise MultiRecordFileParserError(str(exc)) from exc

    return _iter_rows(
        row_iter=row_iter,
        umbrella_config=umbrella_config,
        mapping_paths=mapping_paths,
    )


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #


def _validate_mapping_paths(
    umbrella_config: MultiRecordConfig,
    mapping_paths: Mapping[str, Path],
) -> None:
    """Verify every configured record type has an extant mapping file.

    Raises :class:`MultiRecordFileParserError` on the first defect found.
    Runs at call time to make missing-mapping errors synchronous with the
    caller, rather than surfacing mid-iteration deep inside the L2b gate.
    """
    missing_entries = [
        type_name
        for type_name in umbrella_config.record_types
        if type_name not in mapping_paths
    ]
    if missing_entries:
        raise MultiRecordFileParserError(
            "mapping_paths missing entries for configured record type(s): "
            f"{', '.join(repr(t) for t in sorted(missing_entries))}"
        )

    for type_name, mapping_path in mapping_paths.items():
        # Only check files for record types the umbrella actually declares.
        # Extra entries in mapping_paths are tolerated (caller may share a
        # mapping_paths dict across multiple umbrellas).
        if type_name not in umbrella_config.record_types:
            continue
        path = Path(mapping_path)
        if not path.is_file():
            raise MultiRecordFileParserError(
                f"mapping file for record type {type_name!r} not found: {path}"
            )


def _iter_rows(
    *,
    row_iter: Iterator[Union[ParsedRow, UnknownRecordTypeRow]],
    umbrella_config: MultiRecordConfig,
    mapping_paths: Mapping[str, Path],
) -> Iterator[ParsedFileRow]:
    """Generator body for :func:`iter_parsed_rows`."""
    # Per-invocation mapping cache. Local to this generator so multiple
    # harness runs in the same process cannot leak state between each other.
    parser_cache: Dict[str, UniversalMappingParser] = {}

    try:
        for row in row_iter:
            if isinstance(row, ParsedRow):
                parser = _get_or_load_parser(
                    record_type=row.record_type,
                    mapping_paths=mapping_paths,
                    cache=parser_cache,
                )
                fields = _slice_fields(row.raw_line, parser)
                yield ParsedFileRow(
                    record_type=row.record_type,
                    fields=MappingProxyType(fields),
                    line_number=row.line_number,
                    raw_line=row.raw_line,
                )
            else:
                # UnknownRecordTypeRow — honour the umbrella default_action.
                action = umbrella_config.default_action
                if action == "error":
                    raise MultiRecordFileParserError(
                        f"unknown record type at line {row.line_number}: "
                        f"discriminator={row.discriminator_value!r}"
                    )
                # 'warn' or 'skip' (or any other future tolerant value):
                # surface the row to the consumer with no fields.
                yield ParsedFileRow(
                    record_type=None,
                    fields=MappingProxyType({}),
                    line_number=row.line_number,
                    raw_line=row.raw_line,
                )
    except MultiRecordReaderError as exc:
        raise MultiRecordFileParserError(str(exc)) from exc


def _get_or_load_parser(
    *,
    record_type: str,
    mapping_paths: Mapping[str, Path],
    cache: Dict[str, UniversalMappingParser],
) -> UniversalMappingParser:
    """Return a cached :class:`UniversalMappingParser`, loading if absent.

    Loading is wrapped so any failure (missing file, malformed JSON) is
    translated to :class:`MultiRecordFileParserError`. Eager validation has
    already confirmed file existence, so a failure here means the file
    became unreadable or malformed between the eager check and the lazy
    load — surface it through the module's own error type either way.
    """
    cached = cache.get(record_type)
    if cached is not None:
        return cached
    mapping_path = mapping_paths[record_type]
    try:
        parser = UniversalMappingParser(mapping_path=str(mapping_path))
    except (OSError, ValueError) as exc:
        raise MultiRecordFileParserError(
            f"could not load mapping for record type {record_type!r} "
            f"from {mapping_path}: {exc}"
        ) from exc
    cache[record_type] = parser
    return parser


def _slice_fields(
    raw_line: str,
    parser: UniversalMappingParser,
) -> Dict[str, str]:
    """Slice ``raw_line`` into a dict of field-name → trimmed value.

    Uses the parser's declared field positions for the fixed-width case;
    for other formats it falls back to per-field ``position``/``length``
    attributes, which all multi-record umbrella mappings carry by
    convention (the umbrella file is fixed-width record-by-record even
    when the wider mapping framework also supports delimited inputs).

    A field whose slice begins past the end of ``raw_line`` yields an
    empty string rather than raising, matching the reader primitive's
    permissive ``_extract_discriminator`` behaviour. This makes
    short-line handling consistent across the line-dispatch and
    field-slice layers.
    """
    result: Dict[str, str] = {}
    for field_spec in parser.fields:
        position = field_spec.position
        length = field_spec.length
        if position is None or length is None:
            # Mapping does not declare a fixed-width slice for this field;
            # skip rather than guess.
            continue
        start = position - 1  # 1-indexed to 0-indexed
        end = start + length
        if start >= len(raw_line):
            result[field_spec.name] = ""
            continue
        result[field_spec.name] = raw_line[start:end].strip()
    return result
