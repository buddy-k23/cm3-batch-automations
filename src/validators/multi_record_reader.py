"""Pure file-reading primitive for multi-record-type files — issue #21.

Extracts the file-reading and discriminator-dispatch logic that previously lived
inside :class:`~src.validators.multi_record_validator.MultiRecordValidator` so
that future consumers (notably the L2b SQL-Truth gate in issue #17) can iterate
multi-record files without dragging in per-type validation, cross-type rules, or
reporting.

This module is intentionally **pure**:

* No logging side-effects beyond a single ``getLogger(__name__)`` instance that
  the caller may attach handlers to.
* No file-system side-effects beyond reading the input file.
* No audit-log writes.
* No DataFrame buffering — iteration is row-at-a-time via a generator.

The validator's existing public surface (``MultiRecordValidator.validate``) is
re-built on top of this primitive without behaviour change for its callers; see
ADR ``docs/adr/0008-extract-multi-record-reader-primitive.md``.

Record-field strategy seam (R-06a)
----------------------------------
The line-to-fields slicing that turns a raw line into a discriminator value is
isolated behind the :class:`RecordFieldStrategy` protocol. The default is
:class:`FixedWidthFieldStrategy`, which preserves the historical 1-indexed
``position``/``length`` slice exactly. Passing an alternative strategy (e.g. a
future delimited reader, R-06b) lets the same dispatch logic drive a
non-fixed-width file without touching this module. The seam is a pure refactor:
with the default strategy, reader output is byte-identical to the pre-seam
behaviour. See ADR ``docs/adr/0014-record-reader-strategy-seam.md``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional, Protocol, Union

from src.config.multi_record_config import DiscriminatorConfig, MultiRecordConfig

_logger = logging.getLogger(__name__)

# UTF-8 byte-order-mark, decoded.
_UTF8_BOM = "\ufeff"


class MultiRecordReaderError(ValueError):
    """Raised when the primitive is misused (e.g. file not found)."""


class RecordFieldStrategy(Protocol):
    """Strategy for extracting a field substring from a raw record line.

    The reader dispatches each line to a record type by inspecting a
    *discriminator* substring. How that substring is located is the only
    format-specific concern in the reader; isolating it here lets the same
    dispatch logic serve fixed-width files (the default) and, in a future
    slice (R-06b), delimited files — without changing
    :func:`read_multi_record_file`.

    Implementations must be pure (no I/O, no mutation of inputs) and must
    return the empty string rather than raising when the requested field is
    absent from a short line, matching the historical permissive behaviour
    of the fixed-width reader.
    """

    def extract(self, line: str, disc: DiscriminatorConfig) -> str:
        """Return the trimmed discriminator value for ``line``.

        Args:
            line: The raw line (CR/LF stripped, BOM stripped if applicable).
            disc: Discriminator configuration describing where the value
                lives within the line.

        Returns:
            The trimmed discriminator value, or the empty string when the
            line does not carry one (e.g. it is too short).
        """
        ...


class FixedWidthFieldStrategy:
    """Default :class:`RecordFieldStrategy`: a 1-indexed position/length slice.

    This preserves the reader's historical behaviour exactly. ``position`` is
    1-indexed and ``length`` is the number of characters to read; the slice is
    trimmed of surrounding whitespace. A line shorter than ``position`` yields
    the empty string rather than raising.
    """

    def extract(self, line: str, disc: DiscriminatorConfig) -> str:
        """Slice ``line`` by the discriminator's 1-indexed position/length.

        Args:
            line: The raw line (CR/LF stripped, BOM stripped if applicable).
            disc: Discriminator configuration carrying ``position`` and
                ``length``.

        Returns:
            Stripped discriminator value, or the empty string when the line
            is shorter than ``position``.
        """
        if disc.position is None or disc.length is None:
            # Defensive: a fixed-width strategy was handed a delimited config.
            # The model validator prevents this for loaded configs; treat it as
            # a non-extractable line rather than raising on the hot path.
            return ""
        start = disc.position - 1  # to 0-indexed
        end = start + disc.length
        if len(line) < start + 1:
            return ""
        return line[start:end].strip()


class DelimitedFieldStrategy:
    """:class:`RecordFieldStrategy` for delimited (CSV/pipe) files (R-06b).

    Splits each line on a configured ``delimiter`` and returns the trimmed value
    of the discriminator field selected by ``column``. ``column`` may be a
    1-indexed integer (the Nth field) or a string column name resolved against
    the ordered ``columns`` header list. A line with too few fields for the
    selected column yields the empty string rather than raising, matching the
    permissive short-line contract of :class:`FixedWidthFieldStrategy`.

    The strategy is constructed once per reader invocation (the column name is
    resolved to a 0-indexed position at construction time) and is then stateless
    and side-effect free, so the per-line :meth:`extract` does no allocation
    beyond the unavoidable ``str.split``.
    """

    def __init__(self, delimiter: str, column_index: int) -> None:
        """Build a delimited strategy.

        Args:
            delimiter: Field delimiter (e.g. ``","``, ``"|"``, ``"\\t"``).
            column_index: 0-indexed position of the discriminator field within
                each split line.
        """
        self._delimiter = delimiter
        self._column_index = column_index

    def extract(self, line: str, disc: DiscriminatorConfig) -> str:
        """Return the trimmed discriminator value for a delimited ``line``.

        Args:
            line: The raw line (CR/LF stripped, BOM stripped if applicable).
            disc: Discriminator configuration (unused here; the delimiter and
                column position are captured at construction time).

        Returns:
            The trimmed value of the configured column, or the empty string when
            the line has too few fields.
        """
        fields = line.split(self._delimiter)
        if self._column_index >= len(fields):
            return ""
        return fields[self._column_index].strip()


# The default fixed-width strategy instance. Stateless and side-effect free, so a
# single module-level instance is safe to share across all reader invocations.
_DEFAULT_FIELD_STRATEGY: RecordFieldStrategy = FixedWidthFieldStrategy()


def field_strategy_for(config: MultiRecordConfig) -> RecordFieldStrategy:
    """Select the :class:`RecordFieldStrategy` implied by a config (R-06b).

    This is the config-only selection point: a fixed-width discriminator yields
    the shared :class:`FixedWidthFieldStrategy` (byte-identical to the historical
    behaviour), while a delimited discriminator yields a
    :class:`DelimitedFieldStrategy` bound to the configured delimiter and
    resolved column position. Callers that do not pass an explicit
    ``field_strategy`` to :func:`read_multi_record_file` get this automatically.

    Args:
        config: The multi-record configuration whose ``discriminator`` selects
            the strategy.

    Returns:
        A :class:`FixedWidthFieldStrategy` (the shared default) for fixed-width
        configs, or a freshly-built :class:`DelimitedFieldStrategy` for delimited
        configs.

    Raises:
        MultiRecordReaderError: When a delimited config is internally
            inconsistent (this should not happen for configs loaded through
            :class:`~src.config.multi_record_config.DiscriminatorConfig`, whose
            model validator enforces consistency, but is guarded here for
            programmatically-constructed configs).
    """
    disc = config.discriminator
    if not disc.is_delimited:
        return _DEFAULT_FIELD_STRATEGY

    if disc.delimiter is None or disc.column is None:
        raise MultiRecordReaderError(
            "delimited discriminator requires 'delimiter' and 'column'"
        )

    if isinstance(disc.column, int):
        column_index = disc.column - 1  # 1-indexed -> 0-indexed
    else:
        try:
            column_index = disc.columns.index(disc.column)
        except ValueError as exc:
            raise MultiRecordReaderError(
                f"column '{disc.column}' is not present in 'columns' " f"{disc.columns}"
            ) from exc

    if column_index < 0:
        raise MultiRecordReaderError("resolved column index must be >= 0")

    return DelimitedFieldStrategy(delimiter=disc.delimiter, column_index=column_index)


@dataclass(frozen=True)
class ParsedRow:
    """A single line whose record type was identified.

    Attributes:
        record_type: Logical record-type name from the configuration (e.g.
            ``"header"``).
        line_number: 1-indexed line number in the source file. Blank lines are
            counted; this is the true file line number, not a non-blank index.
        raw_line: The line text with line terminators stripped (CRLF and LF both
            normalised away). A leading UTF-8 BOM is stripped from the first
            line if present.
        discriminator_value: The trimmed substring extracted from the
            configured discriminator position. May be the empty string when the
            row was dispatched by ``position`` rather than by ``match`` (e.g.
            the first-line header in a file whose header has no value to match
            against).
    """

    record_type: str
    line_number: int
    raw_line: str
    discriminator_value: Optional[str]


@dataclass(frozen=True)
class UnknownRecordTypeRow:
    """A single non-empty line whose record type could not be identified.

    Yielded for lines whose extracted discriminator value does not match any
    configured ``RecordTypeConfig.match`` and which do not fall on a
    ``position``-controlled row. Also yielded for lines that are too short for
    the discriminator slice to be extracted.

    The consumer decides how to react (skip, warn, error). The validator wraps
    these into ``RuleViolation`` records via its ``default_action`` policy; the
    L2b comparator treats them as hard failures.

    Attributes:
        line_number: 1-indexed line number in the source file (blank lines
            counted).
        raw_line: The line text with line terminators stripped.
        discriminator_value: The extracted discriminator string (possibly
            empty if the line was too short to slice).
    """

    line_number: int
    raw_line: str
    discriminator_value: str


# Type alias for the two yieldable row shapes.
ReaderRow = Union[ParsedRow, UnknownRecordTypeRow]


def read_multi_record_file(
    file_path: Union[str, Path],
    config: MultiRecordConfig,
    field_strategy: Optional[RecordFieldStrategy] = None,
) -> Iterator[ReaderRow]:
    """Iterate over a multi-record file, yielding one row per non-empty line.

    The reader:

    * Opens the file as UTF-8 with ``errors="replace"`` (matches the validator's
      historical behaviour for unmappable bytes).
    * Strips a leading UTF-8 BOM from the first line if present.
    * Skips empty lines (lines that are empty after stripping CR/LF and
      whitespace). Blank lines still count toward ``line_number``.
    * Extracts the discriminator substring using the configured 1-indexed
      ``position`` and ``length``.
    * Dispatches each line to a record type using the same priority order as
      ``MultiRecordValidator``:

      1. The first non-empty line goes to the record type with
         ``position="first"`` if one is configured.
      2. The last non-empty line goes to the record type with
         ``position="last"`` if one is configured.
      3. Otherwise, the first ``RecordTypeConfig`` whose ``match`` equals the
         extracted discriminator value wins.

    Because step 2 requires knowing which line is *last*, the reader buffers a
    single row of look-ahead. Memory usage is therefore O(1) in the file size.

    The file is opened eagerly so that missing-file errors surface at call time
    rather than at first-iteration time. The returned object is then a lazy
    generator over the file's contents.

    Args:
        file_path: Path to the multi-record data file.
        config: :class:`~src.config.multi_record_config.MultiRecordConfig`
            describing the file structure.
        field_strategy: Optional :class:`RecordFieldStrategy` used to extract
            the discriminator substring from each line. When omitted (the
            default), the strategy is selected from ``config`` via
            :func:`field_strategy_for`: a fixed-width discriminator uses the
            shared :class:`FixedWidthFieldStrategy` (byte-identical to the
            historical 1-indexed position/length slice), and a delimited
            discriminator (``config.discriminator.delimiter`` set) uses a
            :class:`DelimitedFieldStrategy`. Pass an explicit strategy to
            override this config-driven selection.

    Returns:
        An iterator yielding :class:`ParsedRow` for each non-empty line whose
        record type was identified, or :class:`UnknownRecordTypeRow` for each
        non-empty line that did not match any configured type.

    Raises:
        MultiRecordReaderError: When the file cannot be opened.
    """
    strategy = (
        field_strategy if field_strategy is not None else field_strategy_for(config)
    )
    path = Path(file_path)
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise MultiRecordReaderError(f"Cannot open file '{file_path}': {exc}") from exc

    # Find positional-dispatch types up front. ``dict`` preserves insertion order,
    # so the "first/last matching type wins" tie-break matches the validator.
    first_type: Optional[str] = None
    last_type: Optional[str] = None
    for type_name, type_config in config.record_types.items():
        if type_config.position == "first" and first_type is None:
            first_type = type_name
        elif type_config.position == "last" and last_type is None:
            last_type = type_name

    return _iter_rows(
        handle=handle,
        file_path=file_path,
        config=config,
        first_type=first_type,
        last_type=last_type,
        field_strategy=strategy,
    )


def _iter_rows(
    *,
    handle,  # type: ignore[no-untyped-def]
    file_path: Union[str, Path],
    config: MultiRecordConfig,
    first_type: Optional[str],
    last_type: Optional[str],
    field_strategy: RecordFieldStrategy,
) -> Iterator[ReaderRow]:
    """Generator body for :func:`read_multi_record_file`.

    Split out so that the public function can open the file eagerly and raise
    :class:`MultiRecordReaderError` at call time on missing files, rather than
    on first ``next()``.
    """
    # Single-row look-ahead so we know which non-empty row is the last one.
    # ``pending`` holds (line_number, raw_line) waiting to be yielded.
    pending: Optional[tuple[int, str]] = None
    non_empty_index = 0  # 0-indexed position among non-empty lines

    try:
        with handle:
            for one_indexed_line_number, raw in enumerate(handle, start=1):
                # Normalise CR/LF terminators. Strip BOM from the very first
                # character of the very first line only — a BOM anywhere else
                # in a UTF-8 file would be an error in the file, not our
                # problem to silently swallow.
                stripped = raw.rstrip("\r\n")
                if one_indexed_line_number == 1 and stripped.startswith(_UTF8_BOM):
                    stripped = stripped[len(_UTF8_BOM) :]

                if not stripped.strip():
                    # Blank line — still counts toward line_number but is not
                    # yielded and does not advance the non-empty index.
                    continue

                if pending is not None:
                    # Emit the previously-buffered row knowing it is NOT the last.
                    p_line_no, p_raw = pending
                    yield _dispatch_row(
                        raw_line=p_raw,
                        line_number=p_line_no,
                        non_empty_index=non_empty_index,
                        is_last=False,
                        first_type=first_type,
                        last_type=last_type,
                        config=config,
                        field_strategy=field_strategy,
                    )
                    non_empty_index += 1

                pending = (one_indexed_line_number, stripped)

            # Flush the final buffered row, if any. It is the last non-empty line.
            if pending is not None:
                p_line_no, p_raw = pending
                yield _dispatch_row(
                    raw_line=p_raw,
                    line_number=p_line_no,
                    non_empty_index=non_empty_index,
                    is_last=True,
                    first_type=first_type,
                    last_type=last_type,
                    config=config,
                    field_strategy=field_strategy,
                )
    except OSError as exc:
        # An OSError mid-iteration (e.g. network drive disappearing) is a misuse
        # of the primitive from the caller's perspective — surface it as the
        # module's own error type for consistency with the file-open path.
        raise MultiRecordReaderError(
            f"Error reading file '{file_path}': {exc}"
        ) from exc


def _dispatch_row(
    *,
    raw_line: str,
    line_number: int,
    non_empty_index: int,
    is_last: bool,
    first_type: Optional[str],
    last_type: Optional[str],
    config: MultiRecordConfig,
    field_strategy: RecordFieldStrategy,
) -> ReaderRow:
    """Decide whether ``raw_line`` is a ParsedRow or an UnknownRecordTypeRow.

    Args:
        raw_line: The stripped line content.
        line_number: 1-indexed source-file line number.
        non_empty_index: 0-indexed position among non-empty lines.
        is_last: True if this is the final non-empty line in the file.
        first_type: Record-type name with ``position="first"``, or None.
        last_type: Record-type name with ``position="last"``, or None.
        config: Multi-record configuration.
        field_strategy: Strategy used to extract the discriminator value.

    Returns:
        A :class:`ParsedRow` or :class:`UnknownRecordTypeRow`.
    """
    disc_value = field_strategy.extract(raw_line, config.discriminator)

    # Positional dispatch wins over value-based match (preserves validator order).
    if non_empty_index == 0 and first_type is not None:
        return ParsedRow(
            record_type=first_type,
            line_number=line_number,
            raw_line=raw_line,
            discriminator_value=disc_value,
        )

    if is_last and last_type is not None:
        return ParsedRow(
            record_type=last_type,
            line_number=line_number,
            raw_line=raw_line,
            discriminator_value=disc_value,
        )

    # Value-based match.
    if disc_value:
        for type_name, type_config in config.record_types.items():
            if type_config.match and type_config.match == disc_value:
                return ParsedRow(
                    record_type=type_name,
                    line_number=line_number,
                    raw_line=raw_line,
                    discriminator_value=disc_value,
                )

    return UnknownRecordTypeRow(
        line_number=line_number,
        raw_line=raw_line,
        discriminator_value=disc_value,
    )


def _extract_discriminator(line: str, disc: DiscriminatorConfig) -> str:
    """Extract the discriminator substring from ``line`` (fixed-width).

    Retained as a thin, backward-compatible shim over the default
    :class:`FixedWidthFieldStrategy` so existing imports keep working. New
    code should depend on the :class:`RecordFieldStrategy` seam instead.

    Args:
        line: The raw line (CR/LF stripped, BOM stripped if applicable).
        disc: Discriminator configuration.

    Returns:
        Stripped discriminator value, or the empty string when the line is
        shorter than ``position``.
    """
    return _DEFAULT_FIELD_STRATEGY.extract(line, disc)
