"""Reconciliation spec: declarative driver for the L2b SQL-Truth gate.

Purpose
-------
The L2b SQL-Truth gate (issue #17 onwards) reconciles a generated output file
against the same Oracle staging data the Java code reads from. To avoid
embedding source-specific business logic in Python, the gate is driven by a
per-source YAML manifest under
``config/e2e/sources/<SOURCE>/reconciliation/<file_type>.yml``.

This module defines:

* The schema for those YAML files (frozen dataclasses, mirroring the
  ``scripts/e2e_lib/path_resolver.py`` house style).
* A loader that validates the YAML on read, rejecting unknown keys, bad enum
  values, malformed cardinality, and other shape defects with a single
  ``ReconciliationSpecError`` exception type.

No Python file in this module imports Pydantic on purpose: the rest of
``scripts/e2e_lib/`` uses standard-library dataclasses, and the harness
should not pull in an additional dependency.

Why declarative
---------------
All TRANERT-specific business logic (cost-merge, APPS/APP_INT contact dedup,
bankruptcy chapter cases, property-file lookups) lives in versioned
``.sql`` files referenced from the spec. The comparator engine that consumes
this spec (see ``scripts/e2e_lib/db_truth_comparator.py``) is generic — new
sources or file types are added by checking in a YAML manifest + SQL, never
by editing Python.

Spec shape (reference)
----------------------
::

    schema_version: 1
    source: SHAW
    file_type: TRANERT
    umbrella_mapping: config/mappings/SHAW_TRANERT.yaml
    bootstrap_dir:    config/e2e/sources/SHAW/sql/tranert/00_bootstrap
    load_dir:         config/e2e/sources/SHAW/sql/tranert/10_load
    query_dir:        config/e2e/sources/SHAW/sql/tranert/20_query

    record_types:
      batch_header:
        expected_sql: expected_batch_header.sql
        cardinality: one_per_driver_row
        key: [BK-NUM-BRT]
        fields:
          - file_field: BK-NUM-BRT
            expected_column: BK_NUM_BRT
          - file_field: ITM-CNT-BRT
            expected_column: ITM_CNT_BRT
      rt_32000:
        expected_sql: expected_32000.sql
        cardinality: one_per_driver_row
        key: [LN-NUM-ERT]
        fields:
          - file_field: LN-NUM-ERT
            expected_column: LN_NUM_ERT
          - file_field: EFF-DAT-ERT
            expected_column: EFF_DAT_ERT
      rt_32010:
        expected_sql: expected_32010.sql
        cardinality: zero_or_one_per_driver_row
        predicate: "CHG_OFF_CD = '1'"
        key: [LN-NUM-ERT]
        fields:
          - file_field: OGL-NTE-DAT-ORI
            expected_column: OGL_NTE_DAT_ORI
        ignored_fields: [LN-TYP-ORI, REP-TYP-ORI]

    assertions:
      - name: batch_header_count
        expr: "header.ITM-CNT-BRT == sum(detail_row_counts)"

Public API
----------
* :class:`Cardinality`            — string enum: one / zero_or_one / many.
* :class:`FieldSpec`              — one row × one column mapping, frozen.
* :class:`RecordTypeSpec`         — one detail record type, frozen.
* :class:`AssertionSpec`          — cross-type assertion (e.g. header count).
* :class:`ReconciliationSpec`     — top-level spec, frozen.
* :class:`ReconciliationSpecError` — single exception type.
* :func:`load_spec`               — file → :class:`ReconciliationSpec`.

This module is the schema author. The :class:`ReconciliationSpec` class is
referenced from issue #19 (the SHAW TRANERT reconciliation YAML lands then)
and from :mod:`scripts.e2e_lib.db_truth_comparator` (the engine that consumes
it, also delivered in issue #17).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import yaml


# --------------------------------------------------------------------------- #
# Schema constants
# --------------------------------------------------------------------------- #

#: The single supported schema version. Bumping this is a breaking change.
SCHEMA_VERSION: int = 1

#: Keys allowed at the top level of a reconciliation spec YAML. Anything else
#: is a typo and must fail fast (typo-defence is one of the main reasons to
#: validate manually rather than ``yaml.safe_load`` and pray).
_ALLOWED_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "source",
        "file_type",
        "umbrella_mapping",
        "bootstrap_dir",
        "load_dir",
        "query_dir",
        "record_types",
        "assertions",
    }
)

#: Required top-level keys. ``assertions`` is optional (some sources may not
#: have cross-type rules); everything else must be present.
_REQUIRED_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "source",
        "file_type",
        "umbrella_mapping",
        "bootstrap_dir",
        "load_dir",
        "query_dir",
        "record_types",
    }
)

#: Keys allowed inside a single record-type block.
_ALLOWED_RECORD_TYPE_KEYS = frozenset(
    {
        "expected_sql",
        "cardinality",
        "key",
        "predicate",
        "fields",
        "ignored_fields",
    }
)

#: Required keys inside a single record-type block.
_REQUIRED_RECORD_TYPE_KEYS = frozenset(
    {"expected_sql", "cardinality", "key", "fields"}
)

#: Keys allowed inside a single field-spec block.
_ALLOWED_FIELD_SPEC_KEYS = frozenset(
    {
        "file_field",
        "expected_column",
        "predicate",
        "regression_only",
    }
)

#: Required keys inside a single field-spec block.
_REQUIRED_FIELD_SPEC_KEYS = frozenset({"file_field", "expected_column"})

#: Keys allowed inside a single assertion block.
_ALLOWED_ASSERTION_KEYS = frozenset({"name", "expr"})
_REQUIRED_ASSERTION_KEYS = _ALLOWED_ASSERTION_KEYS


# --------------------------------------------------------------------------- #
# Exception type
# --------------------------------------------------------------------------- #


class ReconciliationSpecError(ValueError):
    """Raised for any reconciliation-spec misuse or config defect.

    The single exception type mirrors :class:`PathResolverError` —
    callers should match on this one class for every failure mode.
    """


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #


class Cardinality(str, Enum):
    """How many expected rows correspond to each driver-query row.

    The values are string-valued so YAML round-trips cleanly:
    ``cardinality: one_per_driver_row``.
    """

    ONE_PER_DRIVER_ROW = "one_per_driver_row"
    ZERO_OR_ONE_PER_DRIVER_ROW = "zero_or_one_per_driver_row"
    MANY_PER_DRIVER_ROW = "many_per_driver_row"

    @classmethod
    def parse(cls, raw: Any, *, where: str) -> "Cardinality":
        """Parse a YAML scalar into a :class:`Cardinality`.

        Args:
            raw: The raw YAML value (should be a string).
            where: Human-readable location for error messages, e.g.
                ``"record_types['32010'].cardinality"``.

        Raises:
            ReconciliationSpecError: If ``raw`` is not a known value.
        """
        if not isinstance(raw, str):
            raise ReconciliationSpecError(
                f"{where}: must be a string, got {type(raw).__name__}"
            )
        try:
            return cls(raw)
        except ValueError as exc:
            valid = ", ".join(sorted(c.value for c in cls))
            raise ReconciliationSpecError(
                f"{where}: unknown cardinality '{raw}'. Valid values: {valid}"
            ) from exc


# --------------------------------------------------------------------------- #
# Value types (frozen)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FieldSpec:
    """One reconciled field: one file column compared to one SQL column.

    Attributes:
        file_field: The field name as it appears in the per-record-type
            mapping JSON (e.g. ``"LN-NUM-ERT"``). Case-sensitive; punctuation
            is preserved exactly as the mapping declares it.
        expected_column: The column name in the ``expected_*.sql`` view
            (e.g. ``"LN_NUM_ERT"``). Oracle returns columns upper-case by
            default; the spec stores them upper-case for stable matching.
        predicate: Optional SQL-like predicate expressed against the driver
            row. When set, the field is only compared on rows where the
            predicate is true. Used for conditionally-populated fields such
            as ``OGL-NTE-DAT-ORI`` (only when ``CHG_OFF_CD = '1'``).
        regression_only: When ``True``, the L2b gate skips this field
            entirely and only the L3 baseline diff covers it. Used for
            fields whose value depends on stateful Java behaviour the SQL
            cannot reproduce.
    """

    file_field: str
    expected_column: str
    predicate: Optional[str] = None
    regression_only: bool = False


@dataclass(frozen=True)
class RecordTypeSpec:
    """One detail record type within a multi-record output file.

    Attributes:
        record_type: The discriminator value (e.g. ``"32010"``). Always a
            string, never coerced from int — YAML happily parses ``32010``
            as an integer otherwise.
        expected_sql: Filename of the SQL file (under ``query_dir``) that
            produces the expected rows for this record type. Must end
            ``.sql``.
        cardinality: How many expected rows per driver-query row. See
            :class:`Cardinality`.
        key: Ordered tuple of column names that uniquely identify a row
            within this record type. Single-column for most types; composite
            for 32005 ``(ACCT_NUM, CONTACT_ID)``.
        predicate: Optional SQL-like predicate against the driver row.
            When set, the record type is only expected for driver rows
            where the predicate is true. Used for 32010 (only when
            ``CHG_OFF_CD = '1'``).
        fields: Ordered tuple of :class:`FieldSpec` — every column this
            record type reconciles. Empty tuple is rejected at load time.
        ignored_fields: Tuple of file-field names known to be present but
            deliberately not reconciled. Tracked so the BA can see what's
            *not* covered. The diff report surfaces them.
    """

    record_type: str
    expected_sql: str
    cardinality: Cardinality
    key: Tuple[str, ...]
    fields: Tuple[FieldSpec, ...]
    predicate: Optional[str] = None
    ignored_fields: Tuple[str, ...] = ()


@dataclass(frozen=True)
class AssertionSpec:
    """A cross-record-type or whole-file assertion (e.g. header counts).

    Attributes:
        name: Stable identifier for the assertion, used in failure reports.
        expr: A simple expression evaluated by the comparator engine. The
            engine recognises a small grammar (e.g.
            ``header.ITM-CNT-BRT == sum(detail_row_counts)``); arbitrary
            Python expressions are NOT evaluated.
    """

    name: str
    expr: str


@dataclass(frozen=True)
class ReconciliationSpec:
    """Top-level reconciliation spec for one source × one file type.

    Attributes:
        schema_version: Always ``1`` today. Bumping this is a breaking
            change.
        source: The source system (e.g. ``"SHAW"``).
        file_type: The file type (e.g. ``"TRANERT"``).
        umbrella_mapping: Repo-relative path to the multi-record umbrella
            YAML that drives parsing of the actual file (e.g.
            ``config/mappings/SHAW_TRANERT.yaml``).
        bootstrap_dir: Repo-relative path to the SQL files that
            create-or-replace views, create-if-not-exists lookup tables,
            etc. Executed first on every run.
        load_dir: Repo-relative path to the SQL files that load lookup
            data (e.g. ``MERGE`` from CSV). Executed second.
        query_dir: Repo-relative path to the per-record-type
            ``expected_*.sql`` files plus any driver / helper queries.
        record_types: Mapping from discriminator value to
            :class:`RecordTypeSpec`. Order is preserved (Python 3.7+ dict).
        assertions: Tuple of cross-type assertions. May be empty.
    """

    schema_version: int
    source: str
    file_type: str
    umbrella_mapping: str
    bootstrap_dir: str
    load_dir: str
    query_dir: str
    record_types: Mapping[str, RecordTypeSpec]
    assertions: Tuple[AssertionSpec, ...] = ()


# --------------------------------------------------------------------------- #
# Public loader
# --------------------------------------------------------------------------- #


def load_spec(path: Path) -> ReconciliationSpec:
    """Load and validate a reconciliation spec YAML file.

    Args:
        path: Filesystem path to the YAML file.

    Returns:
        A fully validated :class:`ReconciliationSpec`.

    Raises:
        ReconciliationSpecError: If the file is missing, not valid YAML,
            or fails any structural / semantic check.
    """
    path = Path(path)
    if not path.is_file():
        raise ReconciliationSpecError(f"spec file not found: {path}")
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReconciliationSpecError(
            f"could not read spec file {path}: {exc}"
        ) from exc
    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise ReconciliationSpecError(
            f"failed to parse {path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise ReconciliationSpecError(
            f"{path}: top-level YAML must be a mapping, got "
            f"{type(data).__name__}"
        )
    return _build_spec(data, where=str(path))


def load_spec_from_dict(data: Mapping[str, Any], *, where: str) -> ReconciliationSpec:
    """Build a :class:`ReconciliationSpec` from an already-parsed mapping.

    Exposed for tests so they can avoid round-tripping through a tmp file
    for every assertion. ``where`` is a free-form location string used in
    error messages.
    """
    if not isinstance(data, Mapping):
        raise ReconciliationSpecError(
            f"{where}: top-level value must be a mapping, got "
            f"{type(data).__name__}"
        )
    return _build_spec(dict(data), where=where)


# --------------------------------------------------------------------------- #
# Internal validation helpers
# --------------------------------------------------------------------------- #


def _build_spec(data: Dict[str, Any], *, where: str) -> ReconciliationSpec:
    """Validate a raw dict and assemble a :class:`ReconciliationSpec`."""
    _check_keys(
        data,
        allowed=_ALLOWED_TOP_LEVEL_KEYS,
        required=_REQUIRED_TOP_LEVEL_KEYS,
        where=where,
    )

    schema_version = data["schema_version"]
    if not isinstance(schema_version, int) or schema_version != SCHEMA_VERSION:
        raise ReconciliationSpecError(
            f"{where}.schema_version: must be {SCHEMA_VERSION}, "
            f"got {schema_version!r}"
        )

    source = _require_nonempty_str(data, "source", where=where)
    file_type = _require_nonempty_str(data, "file_type", where=where)
    umbrella_mapping = _require_nonempty_str(
        data, "umbrella_mapping", where=where
    )
    bootstrap_dir = _require_nonempty_str(data, "bootstrap_dir", where=where)
    load_dir = _require_nonempty_str(data, "load_dir", where=where)
    query_dir = _require_nonempty_str(data, "query_dir", where=where)

    raw_record_types = data["record_types"]
    if not isinstance(raw_record_types, Mapping):
        raise ReconciliationSpecError(
            f"{where}.record_types: must be a mapping, got "
            f"{type(raw_record_types).__name__}"
        )
    if not raw_record_types:
        raise ReconciliationSpecError(
            f"{where}.record_types: at least one record type is required"
        )

    record_types: Dict[str, RecordTypeSpec] = {}
    for code, raw_rt in raw_record_types.items():
        rt_where = f"{where}.record_types[{code!r}]"
        # YAML happily parses 32010 as int — force string for stable dispatch.
        code_str = str(code)
        if not code_str:
            raise ReconciliationSpecError(
                f"{rt_where}: record-type code may not be empty"
            )
        record_types[code_str] = _build_record_type(
            code_str, raw_rt, where=rt_where
        )

    raw_assertions = data.get("assertions", [])
    if not isinstance(raw_assertions, list):
        raise ReconciliationSpecError(
            f"{where}.assertions: must be a list, got "
            f"{type(raw_assertions).__name__}"
        )
    assertions = tuple(
        _build_assertion(a, where=f"{where}.assertions[{i}]")
        for i, a in enumerate(raw_assertions)
    )

    return ReconciliationSpec(
        schema_version=schema_version,
        source=source,
        file_type=file_type,
        umbrella_mapping=umbrella_mapping,
        bootstrap_dir=bootstrap_dir,
        load_dir=load_dir,
        query_dir=query_dir,
        record_types=record_types,
        assertions=assertions,
    )


def _build_record_type(
    code: str, raw: Any, *, where: str
) -> RecordTypeSpec:
    if not isinstance(raw, Mapping):
        raise ReconciliationSpecError(
            f"{where}: must be a mapping, got {type(raw).__name__}"
        )
    _check_keys(
        raw,
        allowed=_ALLOWED_RECORD_TYPE_KEYS,
        required=_REQUIRED_RECORD_TYPE_KEYS,
        where=where,
    )

    expected_sql = _require_nonempty_str(raw, "expected_sql", where=where)
    if not expected_sql.endswith(".sql"):
        raise ReconciliationSpecError(
            f"{where}.expected_sql: must end with '.sql', got "
            f"{expected_sql!r}"
        )

    cardinality = Cardinality.parse(
        raw["cardinality"], where=f"{where}.cardinality"
    )

    raw_key = raw["key"]
    if not isinstance(raw_key, list) or not raw_key:
        raise ReconciliationSpecError(
            f"{where}.key: must be a non-empty list of column names"
        )
    for i, k in enumerate(raw_key):
        if not isinstance(k, str) or not k:
            raise ReconciliationSpecError(
                f"{where}.key[{i}]: must be a non-empty string, got {k!r}"
            )
    key = tuple(raw_key)

    raw_fields = raw["fields"]
    if not isinstance(raw_fields, list) or not raw_fields:
        raise ReconciliationSpecError(
            f"{where}.fields: must be a non-empty list of field specs"
        )
    fields = tuple(
        _build_field_spec(f, where=f"{where}.fields[{i}]")
        for i, f in enumerate(raw_fields)
    )

    predicate = raw.get("predicate")
    if predicate is not None and (
        not isinstance(predicate, str) or not predicate.strip()
    ):
        raise ReconciliationSpecError(
            f"{where}.predicate: must be a non-empty string when present, "
            f"got {predicate!r}"
        )

    raw_ignored = raw.get("ignored_fields", [])
    if not isinstance(raw_ignored, list):
        raise ReconciliationSpecError(
            f"{where}.ignored_fields: must be a list, got "
            f"{type(raw_ignored).__name__}"
        )
    for i, f in enumerate(raw_ignored):
        if not isinstance(f, str) or not f:
            raise ReconciliationSpecError(
                f"{where}.ignored_fields[{i}]: must be a non-empty string, "
                f"got {f!r}"
            )
    ignored_fields = tuple(raw_ignored)

    return RecordTypeSpec(
        record_type=code,
        expected_sql=expected_sql,
        cardinality=cardinality,
        key=key,
        fields=fields,
        predicate=predicate,
        ignored_fields=ignored_fields,
    )


def _build_field_spec(raw: Any, *, where: str) -> FieldSpec:
    if not isinstance(raw, Mapping):
        raise ReconciliationSpecError(
            f"{where}: must be a mapping, got {type(raw).__name__}"
        )
    _check_keys(
        raw,
        allowed=_ALLOWED_FIELD_SPEC_KEYS,
        required=_REQUIRED_FIELD_SPEC_KEYS,
        where=where,
    )
    file_field = _require_nonempty_str(raw, "file_field", where=where)
    expected_column = _require_nonempty_str(
        raw, "expected_column", where=where
    )
    predicate = raw.get("predicate")
    if predicate is not None and (
        not isinstance(predicate, str) or not predicate.strip()
    ):
        raise ReconciliationSpecError(
            f"{where}.predicate: must be a non-empty string when present, "
            f"got {predicate!r}"
        )
    regression_only = raw.get("regression_only", False)
    if not isinstance(regression_only, bool):
        raise ReconciliationSpecError(
            f"{where}.regression_only: must be a bool, got "
            f"{type(regression_only).__name__}"
        )
    return FieldSpec(
        file_field=file_field,
        expected_column=expected_column,
        predicate=predicate,
        regression_only=regression_only,
    )


def _build_assertion(raw: Any, *, where: str) -> AssertionSpec:
    if not isinstance(raw, Mapping):
        raise ReconciliationSpecError(
            f"{where}: must be a mapping, got {type(raw).__name__}"
        )
    _check_keys(
        raw,
        allowed=_ALLOWED_ASSERTION_KEYS,
        required=_REQUIRED_ASSERTION_KEYS,
        where=where,
    )
    name = _require_nonempty_str(raw, "name", where=where)
    expr = _require_nonempty_str(raw, "expr", where=where)
    return AssertionSpec(name=name, expr=expr)


# --------------------------------------------------------------------------- #
# Tiny helpers used by every builder
# --------------------------------------------------------------------------- #


def _check_keys(
    data: Mapping[str, Any],
    *,
    allowed: frozenset,
    required: frozenset,
    where: str,
) -> None:
    """Reject unknown and missing keys with a single error each.

    Typo defence: if a YAML author writes ``predcate:`` instead of
    ``predicate:`` we want to point at the offending key, not silently
    drop it on the floor.
    """
    keys = set(data.keys())
    unknown = sorted(keys - allowed)
    if unknown:
        raise ReconciliationSpecError(
            f"{where}: unknown key(s): {', '.join(repr(k) for k in unknown)}. "
            f"Allowed: {', '.join(sorted(allowed))}"
        )
    missing = sorted(required - keys)
    if missing:
        raise ReconciliationSpecError(
            f"{where}: missing required key(s): "
            f"{', '.join(repr(k) for k in missing)}"
        )


def _require_nonempty_str(
    data: Mapping[str, Any], key: str, *, where: str
) -> str:
    """Pull ``key`` from ``data`` and require a non-empty string value."""
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ReconciliationSpecError(
            f"{where}.{key}: must be a non-empty string, got {value!r}"
        )
    return value
