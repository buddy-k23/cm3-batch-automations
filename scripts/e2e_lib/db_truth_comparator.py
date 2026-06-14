"""L2b SQL-Truth comparator engine: reconcile a Valdo output file vs SQL truth.

Purpose
-------
The L2b SQL-Truth gate (issue #17) reconciles a Valdo output file row-by-row
against a SQL truth source derived from the same Oracle staging data the Java
code reads from. This module is the engine that drives that reconciliation.

It composes three other ``scripts/e2e_lib/`` primitives:

* :func:`scripts.e2e_lib.sql_bootstrap.run_sql_directory` to apply the
  bootstrap and load DDL/DML.
* :func:`scripts.e2e_lib.multi_record_file_parser.iter_parsed_rows` to
  iterate the output file and slice per-record-type fields.
* :class:`scripts.e2e_lib.reconciliation_spec.ReconciliationSpec` as the
  declarative driver.

The engine is generic — new sources or file types are added by checking in
a YAML spec plus the per-record-type ``expected_*.sql`` files. No Python
edits required.

Comparison model
----------------
For each record type declared in the spec:

1. The expected ``SELECT`` is read from ``query_dir / expected_sql`` and
   executed via the PEP-249 cursor. No bind-parameter substitution into
   the expected SQL is performed in this release (the spec does not model
   per-type SQL parameters; a future field + ADR can lift this).
2. The expected rowset is indexed by the spec's ``key`` tuple. Each value
   is normalised via ``str(v).strip()`` with ``None`` mapped to ``""``.
3. The file is iterated once via :func:`iter_parsed_rows`. Each file row is
   indexed by the same ``key`` tuple, drawing field values from the
   primitive's slicing dict.
4. Per-key comparison emits one of:

   * ``field_mismatch``       — file value ≠ expected value for a field.
   * ``missing_expected``     — expected key has no matching file row.
   * ``unexpected_file_row``  — file key has no matching expected row.
   * ``cardinality_violation`` — file count for a key disagrees with the
     record type's :class:`~scripts.e2e_lib.reconciliation_spec.Cardinality`.
   * ``assertion_failed``     — a cross-type assertion (e.g. header count
     equals sum of detail rows) did not hold.
   * ``unknown_record_type``  — a file row whose discriminator did not
     match any configured record type and whose umbrella ``default_action``
     permitted emission.

5. Cross-type assertions are evaluated against the parsed file rows only
   (the SQL side is consulted only through the per-record-type expected
   query). The assertion grammar is narrow on purpose; see
   :func:`_evaluate_assertion`.

The engine collects **every** violation rather than failing fast — the L2b
gate's reporting layer needs the full picture. Determinism: violations are
emitted in a stable sort order.

What this module does NOT do
----------------------------
* Per-field or per-record-type predicate evaluation Python-side. Predicates
  live in the ``expected_*.sql`` views; the SQL returns NULL (or omits the
  row) for predicate-false cases.
* Numeric-tolerance comparison. Strict trimmed-string equality only.
* Authoring the SQL files themselves. That is issue #18 (SHAW TRANERT
  artifacts) and onwards.
* Connection lifecycle / commit policy. The caller hands the engine a
  PEP-249 connection; the engine performs no commits or rollbacks.

Public API
----------
* :class:`PerTypeCount`            — per-record-type counters, frozen.
* :class:`ReconciliationViolation` — one row of diff output, frozen.
* :class:`ReconciliationReport`    — engine output, frozen.
* :class:`DbTruthComparatorError`  — single exception type.
* :func:`reconcile`                — drive a single reconciliation pass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import (
    Any,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

import yaml

from src.config.multi_record_config import MultiRecordConfig
from src.database.truth_source import ORACLE_DIALECT, SqlDialect

from scripts.e2e_lib.multi_record_file_parser import (
    MultiRecordFileParserError,
    ParsedFileRow,
    iter_parsed_rows,
)
from scripts.e2e_lib.reconciliation_spec import (
    AssertionSpec,
    Cardinality,
    FieldSpec,
    ReconciliationSpec,
    RecordTypeSpec,
)
from scripts.e2e_lib.sql_bootstrap import run_sql_directory

# --------------------------------------------------------------------------- #
# Exception type
# --------------------------------------------------------------------------- #


class DbTruthComparatorError(ValueError):
    """Raised for any reconciliation engine misuse or configuration defect.

    Per-row data discrepancies are not raised; they are collected as
    :class:`ReconciliationViolation` entries on the
    :class:`ReconciliationReport`. This exception is reserved for cases
    where the engine cannot meaningfully proceed: an unloadable umbrella
    mapping, an unparseable assertion expression, an expected SQL file
    that does not exist, an unsupported cardinality value, or an
    unrecoverable parser error.
    """


# --------------------------------------------------------------------------- #
# Value types (frozen)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PerTypeCount:
    """Per-record-type counters surfaced on the report.

    Attributes:
        file_rows: Number of file rows dispatched to this record type.
        expected_rows: Number of SQL expected rows returned for this type.
        field_mismatches: Number of ``field_mismatch`` violations emitted
            for this record type.
    """

    file_rows: int
    expected_rows: int
    field_mismatches: int


@dataclass(frozen=True)
class ReconciliationViolation:
    """One row of reconciliation diff output.

    Attributes:
        kind: Violation kind. One of ``"field_mismatch"``,
            ``"missing_expected"``, ``"unexpected_file_row"``,
            ``"cardinality_violation"``, ``"assertion_failed"``,
            ``"unknown_record_type"``.
        record_type: Record type the violation is anchored to. ``None``
            for cross-type assertion failures.
        key_values: Tuple of stringified key column values identifying the
            offending row(s). Empty tuple for assertion failures.
        field: For ``field_mismatch`` only — the file-field name that
            disagreed. ``None`` for other kinds.
        expected: Expected value (stringified). ``None`` when not
            applicable (e.g. cardinality).
        actual: Actual file value (stringified). ``None`` when not
            applicable.
        message: Human-readable summary of the violation.
        line_number: 1-indexed source-file line for file-side violations;
            ``None`` for SQL-only kinds (``missing_expected``,
            ``assertion_failed``).
    """

    kind: str
    record_type: Optional[str]
    key_values: Tuple[str, ...]
    field: Optional[str]
    expected: Optional[str]
    actual: Optional[str]
    message: str
    line_number: Optional[int]


@dataclass(frozen=True)
class ReconciliationReport:
    """Engine output.

    Attributes:
        spec_source: ``ReconciliationSpec.source`` echoed for traceability.
        spec_file_type: ``ReconciliationSpec.file_type`` echoed.
        rows_compared: Total file rows whose record type was identified.
        rows_unknown_type: File rows with no identifiable record type
            (``default_action='warn'`` or ``'skip'`` in the umbrella).
        violations: All collected violations in deterministic sort order
            (see module docstring).
        per_type_counts: Read-only mapping of record-type name to
            :class:`PerTypeCount`.
    """

    spec_source: str
    spec_file_type: str
    rows_compared: int
    rows_unknown_type: int
    violations: Tuple[ReconciliationViolation, ...]
    per_type_counts: Mapping[str, PerTypeCount]


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def reconcile(
    conn: Any,
    spec: ReconciliationSpec,
    file_path: Path,
    *,
    skip_bootstrap: bool = False,
    dialect: Optional[SqlDialect] = None,
) -> ReconciliationReport:
    """Drive one reconciliation pass for ``file_path`` against ``conn``.

    The sequence is:

    1. Pre-validate every assertion expression (so an unparseable
       assertion aborts before any SQL runs).
    2. Run ``spec.bootstrap_dir`` then ``spec.load_dir`` via
       :func:`run_sql_directory` (unless ``skip_bootstrap=True``).
    3. Load ``spec.umbrella_mapping`` into a :class:`MultiRecordConfig`.
    4. Build ``mapping_paths`` for the file parser by reading the
       per-record-type ``mapping`` field on each
       :class:`~src.config.multi_record_config.RecordTypeConfig`.
    5. Execute each record type's ``expected_sql`` and index the rows.
    6. Iterate the file once, building a parallel file-row index keyed
       by the spec's ``key`` tuple per record type.
    7. Run field-level comparison, cardinality checks, and assertion
       evaluation. Collect all violations.
    8. Return a :class:`ReconciliationReport`.

    Args:
        conn: PEP-249 connection. The engine calls ``conn.cursor()`` and
            ``cursor.execute(...)``; it does not commit or rollback.
        spec: Loaded reconciliation spec.
        file_path: Path to the Valdo output file to reconcile.
        skip_bootstrap: When ``True``, the engine skips the
            ``bootstrap_dir`` and ``load_dir`` invocations. Tests use this
            to focus on the comparator alone; production runs leave it
            ``False``.
        dialect: Optional :class:`~src.database.truth_source.SqlDialect`
            hint for the backend supplying ``conn`` (ADR 0010 / R-01c).
            Defaults to ``None``, which the engine treats as the
            historical Oracle assumption (no behaviour change). A future
            non-Oracle backend passes its own dialect so dialect-specific
            handling can be added here without per-call-site branches.

    Returns:
        A :class:`ReconciliationReport`.

    Raises:
        DbTruthComparatorError: For any unrecoverable defect: unparseable
            assertion, missing umbrella mapping, missing expected SQL file,
            unloadable umbrella, parser error, or unknown cardinality.
    """
    # Resolve the effective SQL dialect. ``None`` preserves the historical
    # Oracle assumption so existing callers are unaffected (R-01c seam).
    effective_dialect = dialect or ORACLE_DIALECT
    _ = effective_dialect  # consumed by dialect-specific handling (R-01c+)

    # Step 1 — validate every assertion grammar upfront so we never run
    # SQL only to discover the assertion is unparseable.
    parsed_assertions = tuple(
        (assertion, _parse_assertion(assertion)) for assertion in spec.assertions
    )

    # Step 2 — bootstrap and load.
    if not skip_bootstrap:
        _run_directory_or_raise(conn, Path(spec.bootstrap_dir), purpose="bootstrap")
        _run_directory_or_raise(conn, Path(spec.load_dir), purpose="load")

    # Step 3 — umbrella mapping.
    umbrella_config = _load_umbrella(Path(spec.umbrella_mapping))

    # Step 4 — mapping_paths derived from the umbrella's per-type ``mapping`` fields.
    mapping_paths: Dict[str, Path] = {
        type_name: Path(type_config.mapping)
        for type_name, type_config in umbrella_config.record_types.items()
    }

    # Step 5 — execute each expected SQL and index by spec ``key``.
    query_dir = Path(spec.query_dir)
    expected_index: Dict[str, "_ExpectedIndex"] = {}
    for record_type, rt_spec in spec.record_types.items():
        sql_path = query_dir / rt_spec.expected_sql
        if not sql_path.is_file():
            raise DbTruthComparatorError(
                f"expected SQL file not found for record type {record_type!r}: {sql_path}"
            )
        expected_index[record_type] = _fetch_expected(conn, sql_path, rt_spec)

    # Step 6 — iterate the file.
    file_index: Dict[str, "_FileIndex"] = {rt: _FileIndex() for rt in spec.record_types}
    rows_compared = 0
    rows_unknown_type = 0
    try:
        for row in iter_parsed_rows(file_path, umbrella_config, mapping_paths):
            if row.record_type is None:
                rows_unknown_type += 1
                continue
            rows_compared += 1
            if row.record_type not in spec.record_types:
                # The umbrella knows this record type but the spec doesn't
                # reconcile it. Skip silently — the spec is authoritative
                # about what the gate covers.
                continue
            file_index[row.record_type].add(row, spec.record_types[row.record_type])
    except MultiRecordFileParserError as exc:
        raise DbTruthComparatorError(
            f"file parsing failed for {file_path}: {exc}"
        ) from exc

    # Step 7 — compare each record type.
    violations: List[ReconciliationViolation] = []
    per_type_counts: Dict[str, PerTypeCount] = {}
    for record_type, rt_spec in spec.record_types.items():
        rt_violations, rt_field_mismatches = _compare_record_type(
            record_type=record_type,
            rt_spec=rt_spec,
            expected=expected_index[record_type],
            file=file_index[record_type],
        )
        violations.extend(rt_violations)
        per_type_counts[record_type] = PerTypeCount(
            file_rows=file_index[record_type].row_count,
            expected_rows=expected_index[record_type].row_count,
            field_mismatches=rt_field_mismatches,
        )

    # Step 7b — unknown-record-type violations (one per affected file row).
    # iter_parsed_rows already yielded these in file order via record_type=None;
    # we reconstruct the violations by re-iterating because we discarded line
    # numbers above for simplicity of step 6. Re-iterate cheaply.
    if rows_unknown_type:
        violations.extend(
            _collect_unknown_record_type_violations(
                file_path=file_path,
                umbrella_config=umbrella_config,
                mapping_paths=mapping_paths,
            )
        )

    # Step 7c — assertions.
    for assertion_spec, parsed in parsed_assertions:
        violation = _evaluate_assertion(
            assertion_spec=assertion_spec,
            parsed=parsed,
            umbrella_config=umbrella_config,
            file_index=file_index,
        )
        if violation is not None:
            violations.append(violation)

    # Determinism: stable sort across violation kinds.
    violations.sort(key=_violation_sort_key)

    return ReconciliationReport(
        spec_source=spec.source,
        spec_file_type=spec.file_type,
        rows_compared=rows_compared,
        rows_unknown_type=rows_unknown_type,
        violations=tuple(violations),
        per_type_counts=MappingProxyType(per_type_counts),
    )


# --------------------------------------------------------------------------- #
# Internal: bootstrap / umbrella loading
# --------------------------------------------------------------------------- #


def _run_directory_or_raise(conn: Any, directory: Path, *, purpose: str) -> None:
    """Wrap :func:`run_sql_directory` and translate its error type."""
    from scripts.e2e_lib.sql_bootstrap import SqlBootstrapError

    try:
        run_sql_directory(conn, directory)
    except SqlBootstrapError as exc:
        raise DbTruthComparatorError(
            f"{purpose} SQL directory failed ({directory}): {exc}"
        ) from exc


def _load_umbrella(path: Path) -> MultiRecordConfig:
    """Load the umbrella YAML into a :class:`MultiRecordConfig`."""
    if not path.is_file():
        raise DbTruthComparatorError(f"umbrella mapping not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DbTruthComparatorError(
            f"could not load umbrella mapping {path}: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise DbTruthComparatorError(
            f"umbrella mapping {path}: top-level YAML must be a mapping"
        )
    try:
        return MultiRecordConfig.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 — pydantic raises ValidationError
        raise DbTruthComparatorError(
            f"umbrella mapping {path}: schema validation failed: {exc}"
        ) from exc


# --------------------------------------------------------------------------- #
# Internal: expected and file row indexing
# --------------------------------------------------------------------------- #


class _ExpectedIndex:
    """Indexed expected rows for a single record type.

    Stored as a mapping of key-tuple → list of column dicts (list because
    SQL may legitimately return multiple rows per key for
    ``MANY_PER_DRIVER_ROW``). For ``ONE_PER_DRIVER_ROW`` and
    ``ZERO_OR_ONE_PER_DRIVER_ROW``, the engine treats more-than-one as a
    cardinality violation on the *expected* side (i.e. the SQL is buggy);
    that surfaces during comparison.
    """

    __slots__ = ("by_key", "row_count")

    def __init__(self) -> None:
        self.by_key: Dict[Tuple[str, ...], List[Dict[str, str]]] = {}
        self.row_count: int = 0

    def add(self, key: Tuple[str, ...], row: Dict[str, str]) -> None:
        self.by_key.setdefault(key, []).append(row)
        self.row_count += 1


class _FileIndex:
    """Indexed file rows for a single record type.

    Each entry stores the line number alongside the row so violations can
    surface their source-file location.
    """

    __slots__ = ("by_key", "row_count")

    def __init__(self) -> None:
        self.by_key: Dict[
            Tuple[str, ...], List[Tuple[ParsedFileRow, Tuple[str, ...]]]
        ] = {}
        self.row_count: int = 0

    def add(self, row: ParsedFileRow, rt_spec: RecordTypeSpec) -> None:
        key = _file_key(row, rt_spec)
        self.by_key.setdefault(key, []).append((row, key))
        self.row_count += 1


def _file_key(row: ParsedFileRow, rt_spec: RecordTypeSpec) -> Tuple[str, ...]:
    """Build the spec ``key`` tuple from a file row's sliced fields.

    The spec's ``key`` names are file-field names (matching the mapping
    JSON). When a key field is absent from the parsed row's fields dict
    (e.g. mapping declares no slice for it), the entry is the empty
    string — the resulting comparison naturally surfaces as a
    ``missing_expected`` or ``unexpected_file_row`` depending on which
    side has the value.
    """
    return tuple(row.fields.get(k, "") for k in rt_spec.key)


def _fetch_expected(
    conn: Any,
    sql_path: Path,
    rt_spec: RecordTypeSpec,
) -> _ExpectedIndex:
    """Execute the expected SQL and index by spec ``key``.

    No bind-parameter substitution: the spec does not model per-record-type
    SQL parameters in this release.
    """
    try:
        sql_text = sql_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DbTruthComparatorError(
            f"could not read expected SQL {sql_path}: {exc}"
        ) from exc

    cursor = conn.cursor()
    try:
        cursor.execute(sql_text)
    except Exception as exc:  # noqa: BLE001 — driver decides type
        raise DbTruthComparatorError(
            f"expected SQL failed for record type {rt_spec.record_type!r} "
            f"({sql_path}): {exc}"
        ) from exc

    columns = _column_names(cursor)
    index = _ExpectedIndex()
    for raw_row in cursor.fetchall():
        row_dict = {col: _normalise(val) for col, val in zip(columns, raw_row)}
        key = tuple(row_dict.get(k, "") for k in rt_spec.key)
        index.add(key, row_dict)
    return index


def _column_names(cursor: Any) -> List[str]:
    """Extract the column-name list from a PEP-249 cursor.

    ``cursor.description`` is a sequence of 7-tuples whose first entry is
    the column name. Tests pass mock cursors with a simple
    ``description`` list of strings; both shapes are handled.
    """
    description = getattr(cursor, "description", None)
    if not description:
        return []
    names: List[str] = []
    for entry in description:
        if isinstance(entry, str):
            names.append(entry)
        else:
            # PEP-249 7-tuple: first element is the name.
            names.append(str(entry[0]))
    return names


def _normalise(value: Any) -> str:
    """Coerce a SQL value to its trimmed string form."""
    if value is None:
        return ""
    return str(value).strip()


# --------------------------------------------------------------------------- #
# Internal: per-record-type comparison
# --------------------------------------------------------------------------- #


def _compare_record_type(
    *,
    record_type: str,
    rt_spec: RecordTypeSpec,
    expected: _ExpectedIndex,
    file: _FileIndex,
) -> Tuple[List[ReconciliationViolation], int]:
    """Compare expected vs file rows for a single record type.

    Returns the list of violations and the per-type field-mismatch count.
    """
    violations: List[ReconciliationViolation] = []
    field_mismatch_count = 0

    expected_keys = set(expected.by_key.keys())
    file_keys = set(file.by_key.keys())

    # Cardinality + field comparison on shared keys.
    for key in expected_keys & file_keys:
        exp_rows = expected.by_key[key]
        file_entries = file.by_key[key]

        # Cardinality check (per-key).
        card_violation = _check_cardinality(
            record_type=record_type,
            rt_spec=rt_spec,
            key=key,
            expected_count=len(exp_rows),
            file_count=len(file_entries),
            file_line_number=file_entries[0][0].line_number,
        )
        if card_violation is not None:
            violations.append(card_violation)
            # Cardinality violation does not skip field comparison; both
            # are useful for diagnostics. Continue.

        # Field comparison: pair file rows to expected rows positionally,
        # capped by the shorter side. For ONE_PER_DRIVER_ROW and
        # ZERO_OR_ONE_PER_DRIVER_ROW that's exactly one pair; for
        # MANY_PER_DRIVER_ROW it's min(len(file), len(expected)) — extras
        # already surfaced as the cardinality violation above. ``zip``
        # gives us min-length pairing naturally.
        for (file_row, _), exp_row in zip(file_entries, exp_rows):
            for field_spec in rt_spec.fields:
                if field_spec.regression_only:
                    continue
                actual = file_row.fields.get(field_spec.file_field, "")
                expected_val = exp_row.get(field_spec.expected_column, "")
                if actual != expected_val:
                    field_mismatch_count += 1
                    violations.append(
                        ReconciliationViolation(
                            kind="field_mismatch",
                            record_type=record_type,
                            key_values=key,
                            field=field_spec.file_field,
                            expected=expected_val,
                            actual=actual,
                            message=(
                                f"{record_type} key={key}: "
                                f"{field_spec.file_field} expected={expected_val!r} "
                                f"actual={actual!r}"
                            ),
                            line_number=file_row.line_number,
                        )
                    )

    # Expected-only keys → missing_expected.
    for key in sorted(expected_keys - file_keys):
        violations.append(
            ReconciliationViolation(
                kind="missing_expected",
                record_type=record_type,
                key_values=key,
                field=None,
                expected=None,
                actual=None,
                message=f"{record_type} key={key}: expected row not found in file",
                line_number=None,
            )
        )

    # File-only keys → unexpected_file_row.
    for key in sorted(file_keys - expected_keys):
        # Use the first file row at this key for the line number.
        first_row = file.by_key[key][0][0]
        violations.append(
            ReconciliationViolation(
                kind="unexpected_file_row",
                record_type=record_type,
                key_values=key,
                field=None,
                expected=None,
                actual=None,
                message=f"{record_type} key={key}: file row has no matching expected row",
                line_number=first_row.line_number,
            )
        )

    return violations, field_mismatch_count


def _check_cardinality(
    *,
    record_type: str,
    rt_spec: RecordTypeSpec,
    key: Tuple[str, ...],
    expected_count: int,
    file_count: int,
    file_line_number: int,
) -> Optional[ReconciliationViolation]:
    """Return a cardinality violation if the file/expected counts disagree.

    The expected side is treated as the source of truth for "is the key
    supposed to be present". The file side is what's actually there. The
    violation kind depends on the record type's
    :class:`~scripts.e2e_lib.reconciliation_spec.Cardinality`.
    """
    card = rt_spec.cardinality
    if card == Cardinality.ONE_PER_DRIVER_ROW:
        if file_count == 1 and expected_count == 1:
            return None
        return _cardinality_violation(
            record_type,
            key,
            expected_count,
            file_count,
            file_line_number,
            expected_label="exactly 1",
        )
    if card == Cardinality.ZERO_OR_ONE_PER_DRIVER_ROW:
        if file_count <= 1 and expected_count <= 1:
            return None
        return _cardinality_violation(
            record_type,
            key,
            expected_count,
            file_count,
            file_line_number,
            expected_label="0 or 1",
        )
    if card == Cardinality.MANY_PER_DRIVER_ROW:
        # Any count agrees; only a count mismatch flags. Field comparison
        # below still runs for the shared rows.
        if file_count == expected_count:
            return None
        return _cardinality_violation(
            record_type,
            key,
            expected_count,
            file_count,
            file_line_number,
            expected_label=f"{expected_count} (SQL row count)",
        )
    # Unreachable in practice — Cardinality.parse rejects unknown values
    # at spec-load time. Defensive raise here for forward compatibility.
    raise DbTruthComparatorError(
        f"unknown cardinality {card!r} on record type {record_type!r}"
    )


def _cardinality_violation(
    record_type: str,
    key: Tuple[str, ...],
    expected_count: int,
    file_count: int,
    file_line_number: int,
    *,
    expected_label: str,
) -> ReconciliationViolation:
    return ReconciliationViolation(
        kind="cardinality_violation",
        record_type=record_type,
        key_values=key,
        field=None,
        expected=expected_label,
        actual=str(file_count),
        message=(
            f"{record_type} key={key}: cardinality expected {expected_label}, "
            f"got {file_count} file row(s) against {expected_count} expected row(s)"
        ),
        line_number=file_line_number,
    )


# --------------------------------------------------------------------------- #
# Internal: unknown record types (re-iteration for line numbers)
# --------------------------------------------------------------------------- #


def _collect_unknown_record_type_violations(
    *,
    file_path: Path,
    umbrella_config: MultiRecordConfig,
    mapping_paths: Mapping[str, Path],
) -> Iterable[ReconciliationViolation]:
    """Re-iterate the file to collect unknown-record-type violations.

    Step 6 of :func:`reconcile` already iterated the file once and
    counted ``rows_unknown_type`` without retaining line numbers. This
    second pass is gated on a non-zero count, so a clean file pays
    nothing extra. The pass is O(N) and uses the same primitive.
    """
    try:
        for row in iter_parsed_rows(file_path, umbrella_config, mapping_paths):
            if row.record_type is None:
                yield ReconciliationViolation(
                    kind="unknown_record_type",
                    record_type=None,
                    key_values=(),
                    field=None,
                    expected=None,
                    actual=row.raw_line,
                    message=(
                        f"line {row.line_number}: record type could not be "
                        f"identified from raw line"
                    ),
                    line_number=row.line_number,
                )
    except MultiRecordFileParserError as exc:
        # Should not occur: the first pass already succeeded against the
        # same primitive. If it does, surface as the engine's error type.
        raise DbTruthComparatorError(
            f"file parsing failed during unknown-type collection pass: {exc}"
        ) from exc


# --------------------------------------------------------------------------- #
# Internal: assertion grammar
# --------------------------------------------------------------------------- #


# Assertion grammar (initial, narrow):
#
#   expr := <side> '==' <side>
#   side := <int_literal>
#         | 'header' '.' <field_name>
#         | 'trailer' '.' <field_name>
#         | 'sum' '(' 'detail_row_counts' ')'
#         | 'count' '(' <record_type_name> ')'
#
# Field names match what the parser slices from the file (mapping JSON
# field names, dash-and-letter friendly). Record type names match the
# umbrella's ``record_types`` keys.

_FIELD_REF_PATTERN = re.compile(r"^(header|trailer)\.([A-Za-z0-9_\-]+)$")
_COUNT_PATTERN = re.compile(r"^count\(([A-Za-z0-9_]+)\)$")
_INT_PATTERN = re.compile(r"^-?\d+$")
_SUM_DETAIL_LITERAL = "sum(detail_row_counts)"


@dataclass(frozen=True)
class _AssertionSide:
    """Parsed representation of one side of an assertion expression."""

    kind: str  # "int" | "header_field" | "trailer_field" | "sum_detail" | "count_type"
    payload: str  # for int: the literal; for *_field: the field name; for count_type: record type


@dataclass(frozen=True)
class _ParsedAssertion:
    """A pre-parsed, lhs/rhs assertion ready for evaluation."""

    lhs: _AssertionSide
    rhs: _AssertionSide


def _parse_assertion(assertion: AssertionSpec) -> _ParsedAssertion:
    """Parse ``assertion.expr`` into a :class:`_ParsedAssertion`.

    Raises :class:`DbTruthComparatorError` for any expression outside the
    supported grammar. The intent is that authors get a fast, specific
    error at spec-load time rather than at evaluation time.
    """
    expr = assertion.expr.strip()
    # Only ``==`` is supported. Reject other operators explicitly.
    for forbidden in ("!=", ">=", "<=", "<>", "<", ">"):
        if forbidden in expr:
            raise DbTruthComparatorError(
                f"assertion {assertion.name!r}: operator {forbidden!r} is not "
                f"supported; only '==' is recognised"
            )
    if "==" not in expr:
        raise DbTruthComparatorError(
            f"assertion {assertion.name!r}: expression must contain '=='"
        )
    parts = expr.split("==")
    if len(parts) != 2:
        raise DbTruthComparatorError(
            f"assertion {assertion.name!r}: expected exactly one '==', got "
            f"{len(parts) - 1}"
        )
    lhs = _parse_assertion_side(parts[0].strip(), assertion=assertion)
    rhs = _parse_assertion_side(parts[1].strip(), assertion=assertion)
    return _ParsedAssertion(lhs=lhs, rhs=rhs)


def _parse_assertion_side(text: str, *, assertion: AssertionSpec) -> _AssertionSide:
    """Parse one side of an assertion expression."""
    if _INT_PATTERN.match(text):
        return _AssertionSide(kind="int", payload=text)
    if text == _SUM_DETAIL_LITERAL:
        return _AssertionSide(kind="sum_detail", payload="")
    match = _FIELD_REF_PATTERN.match(text)
    if match:
        anchor, field_name = match.group(1), match.group(2)
        kind = "header_field" if anchor == "header" else "trailer_field"
        return _AssertionSide(kind=kind, payload=field_name)
    match = _COUNT_PATTERN.match(text)
    if match:
        return _AssertionSide(kind="count_type", payload=match.group(1))
    raise DbTruthComparatorError(
        f"assertion {assertion.name!r}: side {text!r} does not match the "
        f"supported grammar (int literal, header.FIELD, trailer.FIELD, "
        f"sum(detail_row_counts), count(<record_type>))"
    )


def _evaluate_assertion(
    *,
    assertion_spec: AssertionSpec,
    parsed: _ParsedAssertion,
    umbrella_config: MultiRecordConfig,
    file_index: Mapping[str, _FileIndex],
) -> Optional[ReconciliationViolation]:
    """Evaluate one parsed assertion. Returns a violation, or ``None`` if it holds."""
    lhs_val = _resolve_side(parsed.lhs, assertion_spec, umbrella_config, file_index)
    rhs_val = _resolve_side(parsed.rhs, assertion_spec, umbrella_config, file_index)
    if lhs_val == rhs_val:
        return None
    return ReconciliationViolation(
        kind="assertion_failed",
        record_type=None,
        key_values=(),
        field=None,
        expected=str(rhs_val),
        actual=str(lhs_val),
        message=(
            f"assertion {assertion_spec.name!r} failed: "
            f"lhs={lhs_val} rhs={rhs_val} (expr={assertion_spec.expr!r})"
        ),
        line_number=None,
    )


def _resolve_side(
    side: _AssertionSide,
    assertion_spec: AssertionSpec,
    umbrella_config: MultiRecordConfig,
    file_index: Mapping[str, _FileIndex],
) -> int:
    """Resolve one parsed side to an int."""
    if side.kind == "int":
        return int(side.payload)
    if side.kind == "header_field":
        return _resolve_positional_field(
            position="first",
            field_name=side.payload,
            assertion_spec=assertion_spec,
            umbrella_config=umbrella_config,
            file_index=file_index,
        )
    if side.kind == "trailer_field":
        return _resolve_positional_field(
            position="last",
            field_name=side.payload,
            assertion_spec=assertion_spec,
            umbrella_config=umbrella_config,
            file_index=file_index,
        )
    if side.kind == "sum_detail":
        # Sum of file-row counts for every record type that is not the
        # first-positional or last-positional type.
        first_type = _positional_type_name(umbrella_config, "first")
        last_type = _positional_type_name(umbrella_config, "last")
        total = 0
        for rt_name, idx in file_index.items():
            if rt_name == first_type or rt_name == last_type:
                continue
            total += idx.row_count
        return total
    if side.kind == "count_type":
        rt_name = side.payload
        if rt_name not in file_index:
            raise DbTruthComparatorError(
                f"assertion {assertion_spec.name!r}: count({rt_name}) references "
                f"a record type not declared in the spec"
            )
        return file_index[rt_name].row_count
    raise DbTruthComparatorError(
        f"assertion {assertion_spec.name!r}: unknown side kind {side.kind!r}"
    )


def _positional_type_name(
    umbrella_config: MultiRecordConfig, position: str
) -> Optional[str]:
    """Return the umbrella record type configured with ``position=first|last``."""
    for type_name, type_config in umbrella_config.record_types.items():
        if type_config.position == position:
            return type_name
    return None


def _resolve_positional_field(
    *,
    position: str,
    field_name: str,
    assertion_spec: AssertionSpec,
    umbrella_config: MultiRecordConfig,
    file_index: Mapping[str, _FileIndex],
) -> int:
    """Pull a named field from the single file row at ``position`` and coerce to int."""
    type_name = _positional_type_name(umbrella_config, position)
    if type_name is None:
        raise DbTruthComparatorError(
            f"assertion {assertion_spec.name!r}: no record type configured with "
            f"position={position!r} in the umbrella"
        )
    idx = file_index.get(type_name)
    if idx is None or idx.row_count == 0:
        raise DbTruthComparatorError(
            f"assertion {assertion_spec.name!r}: no {position}-positional rows in "
            f"the file (record type {type_name!r})"
        )
    # There must be exactly one positional row.
    if idx.row_count != 1:
        raise DbTruthComparatorError(
            f"assertion {assertion_spec.name!r}: expected exactly one "
            f"{position}-positional row, got {idx.row_count}"
        )
    only_entry = next(iter(idx.by_key.values()))[0][0]
    raw_value = only_entry.fields.get(field_name, "")
    try:
        return int(raw_value.strip())
    except (ValueError, AttributeError) as exc:
        raise DbTruthComparatorError(
            f"assertion {assertion_spec.name!r}: field {field_name!r} on "
            f"{position}-positional row is not an integer: {raw_value!r}"
        ) from exc


# --------------------------------------------------------------------------- #
# Internal: violation ordering
# --------------------------------------------------------------------------- #


# Kind ordering for deterministic sort. Per-record-type kinds first
# (grouped together so a record type's violations are contiguous in the
# output), then assertion failures last (a single bucket across the whole
# report), then unknown record types as a tail.
_KIND_ORDER: Dict[str, int] = {
    "field_mismatch": 0,
    "cardinality_violation": 1,
    "missing_expected": 2,
    "unexpected_file_row": 3,
    "assertion_failed": 4,
    "unknown_record_type": 5,
}


def _violation_sort_key(v: ReconciliationViolation) -> Tuple[Any, ...]:
    """Stable sort key for :class:`ReconciliationViolation`.

    Order: (record_type, line_number, kind, field, key_values).
    ``record_type=None`` sorts last to keep cross-cutting assertion and
    unknown-row violations as a footer block.
    """
    return (
        v.record_type is None,  # False (0) sorts before True (1)
        v.record_type or "",
        v.line_number if v.line_number is not None else 0,
        _KIND_ORDER.get(v.kind, 99),
        v.field or "",
        v.key_values,
    )
