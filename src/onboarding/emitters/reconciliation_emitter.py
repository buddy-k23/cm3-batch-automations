"""Reconciliation YAML emitter (ED-S1).

Convert the ``Reconciliation_<FILETYPE>`` sheets carried by a parsed
:class:`~src.onboarding.models.OnboardingWorkbook` into per-file
reconciliation YAML artefacts written under
``config/e2e/sources/<SOURCE>/reconciliation/<filetype>.yml``.

Public API
----------
    >>> from src.onboarding.workbook_reader import read_workbook
    >>> from src.onboarding.emitters.reconciliation_emitter import (
    ...     emit_reconciliation_artefacts,
    ... )
    >>> wb = read_workbook("templates/SHAW_onboarding.xlsx")
    >>> arts = emit_reconciliation_artefacts(wb, source_code="SHAW")
    >>> arts[0].path
    'config/e2e/sources/SHAW/reconciliation/tranert.yml'

Schema produced
---------------
The emitted YAML matches the schema validated by
:mod:`scripts.e2e_lib.reconciliation_spec`. Top-level keys are emitted
in the same order as the committed reference
(``config/e2e/sources/SHAW/reconciliation/tranert.yml``):

* ``schema_version`` (constant ``1`` — bumping is a breaking change).
* ``source`` — copied from :attr:`OnboardingWorkbook.source.source_code`.
* ``file_type`` — the ``Reconciliation_<FILETYPE>`` suffix.
* ``umbrella_mapping`` — derived by convention as
  ``config/mappings/<SOURCE>_<FILETYPE>.yaml`` for multi-record outputs
  (the workbook flags this via ``OutputFileSpec.is_multi_record``);
  falls back to ``config/mappings/<SOURCE>_<FILETYPE>.json`` for flat
  reconciled outputs.
* ``bootstrap_dir`` / ``load_dir`` / ``query_dir`` — derived from the
  SHAW convention ``config/e2e/sources/<SOURCE>/sql/<file_type_lower>/
  {00_bootstrap, 10_load, 20_query}``.
* ``record_types`` — a mapping keyed by ``record_type_name`` (one entry
  per :class:`ReconciliationRow`). Each value preserves the
  ``key``, optional ``predicate``, optional ``ignored_fields``,
  ``cardinality`` (looked up from the matching
  ``MultiRecord_<FILETYPE>`` row), ``expected_sql`` (see convention
  below), and the ``fields`` array (derived from the per-record-type
  mapping sheet).
* ``assertions`` — emitted from
  :attr:`ReconciliationSheet.file_wide_assertions` when non-empty,
  one entry per assertion expression. Names are auto-generated
  (``assertion_1``, ``assertion_2``, ...). The first SHAW assertion
  retains its conventional ``batch_header_count`` name when the
  expression matches the published shape, preserving the committed
  YAML's BA-readable name.

``expected_sql: auto`` marker convention
----------------------------------------
ED-S2 (next story in the Move track) will auto-derive ``expected_*.sql``
files from the umbrella mapping. ED-S1 reserves the marker token
``auto`` for that hand-off:

* If :attr:`ReconciliationRow.expected_sql_override` is non-empty, the
  emitter emits ``expected_sql: <override>`` verbatim. The BA has
  already declared a hand-authored SQL filename.
* If :attr:`ReconciliationRow.expected_sql_override` is empty, the
  emitter emits ``expected_sql: auto``. ED-S2's auto-derivation pass
  scans for this marker and replaces it with the generated filename.

The marker is a valid string per the
:mod:`scripts.e2e_lib.reconciliation_spec` loader's ``.sql`` filename
check — historically the loader rejected non-``.sql`` strings; ED-S2
must teach the loader to accept ``auto`` as a sentinel or rewrite the
file before validation. ED-S1 leaves the marker as a documented hand-
off; the emitter does NOT attempt to validate the emitted spec through
the loader (the loader is engine-side, not workbook-side).

``file_wide_assertions`` top-level key
--------------------------------------
The committed ``tranert.yml`` uses the top-level key ``assertions:``
(plural) for cross-record-type / whole-file assertions. The workbook's
:attr:`ReconciliationSheet.file_wide_assertions` is emitted under
this key as a list of ``{name, expr}`` mappings, matching the
:class:`AssertionSpec` shape the loader expects.

YAML emission style
-------------------
The emitter uses ``yaml.safe_dump(..., sort_keys=False,
default_flow_style=False)`` for block style. The per-field entries
inside ``record_types[*].fields`` are emitted as flow-style mappings
(``{ file_field: ..., expected_column: ... }``) via the
:class:`_FlowDict` pattern reused from EC-S3
(:mod:`src.onboarding.emitters.source_yaml_emitter`) — matches the
committed YAML's one-field-per-line style.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml

from src.onboarding.emitters import EmitterError
from src.onboarding.models import (
    MappingFieldRow,
    MappingSheet,
    MultiRecordRow,
    OnboardingWorkbook,
    OutputFileSpec,
    ReconciliationRow,
    ReconciliationSheet,
)

# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------

#: Schema version emitted at the top of every reconciliation YAML. The
#: :mod:`scripts.e2e_lib.reconciliation_spec` loader rejects any other
#: value (bumping this is a breaking change for the engine).
_SCHEMA_VERSION: int = 1

#: Marker token emitted in place of a real ``expected_*.sql`` filename
#: when the workbook left ``expected_sql_override`` blank. ED-S2's SQL
#: auto-derivation pass scans for this token and replaces it with the
#: generated filename.
_EXPECTED_SQL_AUTO_MARKER: str = "auto"

#: Default cardinality used when the matching ``MultiRecord_<FILETYPE>``
#: row cannot be located. The committed ``tranert.yml`` only uses
#: ``one_per_driver_row`` / ``many_per_driver_row`` /
#: ``zero_or_one_per_driver_row``; the safest default for a "I don't
#: know" case is ``one_per_driver_row`` (most permissive on the count
#: side — the engine validates against the actual rowset).
_DEFAULT_CARDINALITY: str = "one_per_driver_row"

#: Sentinel marking a flat (non-multi-record) reconciliation entry on
#: the workbook side. Matches the :class:`ReconciliationRow` convention
#: documented in :mod:`src.onboarding.models`.
_FLAT_RECORD_TYPE_TOKEN: str = "(flat)"

#: The conventional file-wide assertion name on the committed
#: ``tranert.yml`` (``batch_header_count``). When the workbook's first
#: assertion expression matches the published header-count shape, the
#: emitter preserves this name; otherwise it falls back to
#: ``assertion_1``.
_HEADER_COUNT_ASSERTION_NAME: str = "batch_header_count"
_HEADER_COUNT_ASSERTION_EXPR_TOKENS: tuple[str, ...] = (
    "ITM-CNT-BRT",
    "sum(detail_row_counts)",
)


# ---------------------------------------------------------------------------
# Flow-style sentinel + dumper — reused pattern from EC-S3.
# ---------------------------------------------------------------------------


class _FlowDict(dict):
    """Marker subclass for dicts that must emit flow-style.

    Used exclusively for the per-field ``{file_field: ..., expected_column: ...}``
    sub-mappings inside ``record_types[*].fields`` so the emitted YAML
    matches the committed ``tranert.yml`` shape (one field per line,
    flow-style mapping).
    """


class _ReconciliationYamlDumper(yaml.SafeDumper):
    """``SafeDumper`` subclass that honours :class:`_FlowDict` instances."""


def _represent_flow_dict(
    dumper: yaml.SafeDumper, data: _FlowDict
) -> yaml.MappingNode:
    """Represent a :class:`_FlowDict` as a flow-style YAML mapping.

    Args:
        dumper: The active ``SafeDumper`` instance.
        data: The flow-style dict to emit.

    Returns:
        A ``yaml.MappingNode`` configured with ``flow_style=True``.
    """
    return dumper.represent_mapping(
        "tag:yaml.org,2002:map", data.items(), flow_style=True
    )


_ReconciliationYamlDumper.add_representer(_FlowDict, _represent_flow_dict)


# ---------------------------------------------------------------------------
# Public dataclass returned by the emitter.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EmittedReconciliationArtefact:
    """One emitted reconciliation YAML artefact.

    Attributes:
        path: Repo-relative path the artefact should be written to
            (e.g. ``config/e2e/sources/SHAW/reconciliation/tranert.yml``).
            The emitter never writes to disk; the orchestrator (CLI)
            owns filesystem I/O.
        content: The YAML text in block style.
    """

    path: str
    content: str


# ---------------------------------------------------------------------------
# Derivation helpers.
# ---------------------------------------------------------------------------


def _file_type_lower(file_type: str) -> str:
    """Lower-case the ``file_type`` for path derivation.

    Workbook sheet suffixes carry the uppercase form (``TRANERT``); the
    emitted path uses the lowercase form (``tranert``) per the
    committed convention.

    Args:
        file_type: The uppercase file type from the workbook
            (e.g. ``"TRANERT"``).

    Returns:
        The lowercase form (e.g. ``"tranert"``).
    """
    return file_type.lower()


def _derive_output_path(
    source_code: str, file_type: str, output_dir: str
) -> str:
    """Compute the on-disk path for a reconciliation YAML.

    Args:
        source_code: The source code (e.g. ``"SHAW"``).
        file_type: The uppercase file type from the workbook
            (e.g. ``"TRANERT"``).
        output_dir: The base directory under which the path is rooted
            (defaults to ``config/e2e/sources``).

    Returns:
        The repo-relative path
        ``<output_dir>/<SOURCE>/reconciliation/<filetype_lower>.yml``.
    """
    return (
        f"{output_dir.rstrip('/')}/{source_code}/reconciliation/"
        f"{_file_type_lower(file_type)}.yml"
    )


def _derive_umbrella_mapping(
    source_code: str, file_type: str, output_spec: OutputFileSpec | None
) -> str:
    """Derive the ``umbrella_mapping`` value by convention.

    For multi-record outputs the umbrella mapping is the per-source
    YAML; for flat outputs the umbrella is the per-source mapping JSON.
    Mirrors the EC-S3 ``_output_mapping_path`` convention.

    Args:
        source_code: The source code (e.g. ``"SHAW"``).
        file_type: The uppercase file type (e.g. ``"TRANERT"``).
        output_spec: The matching output-file spec, or ``None`` when
            the workbook has no ``OutputFiles`` row for the file type
            (degenerate case — assume multi-record by default to match
            the committed SHAW shape).

    Returns:
        The repo-relative ``umbrella_mapping`` path.
    """
    is_multi_record = (
        output_spec.is_multi_record if output_spec is not None else True
    )
    extension = "yaml" if is_multi_record else "json"
    return f"config/mappings/{source_code}_{file_type}.{extension}"


def _derive_sql_dir(
    source_code: str, file_type: str, leaf: str
) -> str:
    """Derive a ``<bootstrap|load|query>_dir`` path by convention.

    Args:
        source_code: The source code (e.g. ``"SHAW"``).
        file_type: The uppercase file type (e.g. ``"TRANERT"``).
        leaf: The leaf directory (``"00_bootstrap"`` / ``"10_load"`` /
            ``"20_query"``).

    Returns:
        The repo-relative SQL directory path.
    """
    return (
        f"config/e2e/sources/{source_code}/sql/"
        f"{_file_type_lower(file_type)}/{leaf}"
    )


def _find_output_spec(
    workbook: OnboardingWorkbook, file_type: str
) -> OutputFileSpec | None:
    """Locate the ``OutputFileSpec`` matching a reconciliation file type.

    Args:
        workbook: The parsed onboarding workbook.
        file_type: The uppercase file type to match.

    Returns:
        The matching :class:`OutputFileSpec`, or ``None`` if no row
        matches (degenerate workbook).
    """
    for spec in workbook.output_files:
        if spec.file_type == file_type:
            return spec
    return None


def _index_multi_record_rows(
    workbook: OnboardingWorkbook, file_type: str
) -> dict[str, MultiRecordRow]:
    """Index a ``MultiRecord_<FILETYPE>`` sheet's rows by record-type name.

    Args:
        workbook: The parsed onboarding workbook.
        file_type: The uppercase file type to look up.

    Returns:
        A dict keyed by :attr:`MultiRecordRow.record_type_name`, or
        empty dict when the workbook has no matching multi-record
        sheet (i.e. the reconciled output is flat).
    """
    sheet = workbook.multi_record_sheets.get(file_type)
    if sheet is None:
        return {}
    return {row.record_type_name: row for row in sheet.rows}


# ---------------------------------------------------------------------------
# Field-derivation helpers.
# ---------------------------------------------------------------------------


def _expected_column_for_field(row: MappingFieldRow) -> str:
    """Compute the ``expected_column`` SQL name for a mapping field row.

    The committed ``tranert.yml`` uses the uppercase underscore form
    (``BK_NUM_BRT``) for ``expected_column`` values. The mapping JSON's
    ``target_name`` is the lowercase snake form (``bk_num_brt``);
    upper-casing it is the project convention for the staged column
    name (Oracle returns columns upper-case by default).

    When the BA omitted the ``target_name`` cell (``None``), the
    emitter falls back to upper-casing the DASH-form field name with
    dashes converted to underscores (``LN-NUM-ERT`` ->
    ``LN_NUM_ERT``). Matches the
    :mod:`src.config.template_converter` fallback.

    Args:
        row: The mapping field row from the workbook.

    Returns:
        The ``expected_column`` SQL name in upper-case underscore form.
    """
    if row.target_name and row.target_name.strip():
        return row.target_name.strip().upper()
    return row.field_name.replace("-", "_").upper()


def _build_fields_list(
    mapping_sheet: MappingSheet | None,
) -> list[_FlowDict]:
    """Build the ``fields:`` array for one record type's mapping.

    Each entry is a flow-style ``{file_field, expected_column}``
    mapping, matching the committed ``tranert.yml`` shape. When the
    mapping sheet is missing the function returns an empty list — the
    reconciliation spec loader rejects this on validation, surfacing
    the workbook gap to the BA.

    Args:
        mapping_sheet: The matching mapping sheet from the workbook,
            or ``None`` when the cross-reference cannot be resolved.

    Returns:
        A list of :class:`_FlowDict` instances, one per mapping field.
    """
    if mapping_sheet is None:
        return []
    fields: list[_FlowDict] = []
    for field_row in mapping_sheet.rows:
        if not field_row.field_name:
            continue
        fields.append(
            _FlowDict(
                file_field=field_row.field_name,
                expected_column=_expected_column_for_field(field_row),
            )
        )
    return fields


# ---------------------------------------------------------------------------
# Per-record-type block builder.
# ---------------------------------------------------------------------------


def _build_record_type_block(
    recon_row: ReconciliationRow,
    multi_record_row: MultiRecordRow | None,
    mapping_sheet: MappingSheet | None,
) -> dict[str, Any]:
    """Assemble one entry under the top-level ``record_types:`` key.

    Key order matches the committed ``tranert.yml``:
    ``expected_sql, cardinality, [predicate], key, fields[, ignored_fields]``.

    Args:
        recon_row: The reconciliation row from the workbook.
        multi_record_row: The matching ``MultiRecord_<FILETYPE>`` row,
            looked up by ``record_type_name``. ``None`` for flat
            reconciliation entries (``record_type_name == "(flat)"``)
            or when the cross-reference cannot be resolved — in which
            case :data:`_DEFAULT_CARDINALITY` is used.
        mapping_sheet: The matching ``*_Mapping`` sheet from the
            workbook. ``None`` when no mapping is available; the
            ``fields:`` list is then empty.

    Returns:
        An ordered dict ready for ``yaml.dump``.
    """
    expected_sql = (
        recon_row.expected_sql_override.strip()
        if recon_row.expected_sql_override
        else _EXPECTED_SQL_AUTO_MARKER
    )
    cardinality = (
        multi_record_row.cardinality
        if multi_record_row is not None
        else _DEFAULT_CARDINALITY
    )

    entry: dict[str, Any] = {
        "expected_sql": expected_sql,
        "cardinality": cardinality,
    }
    if recon_row.predicate:
        entry["predicate"] = recon_row.predicate
    entry["key"] = list(recon_row.key_columns)
    entry["fields"] = _build_fields_list(mapping_sheet)
    if recon_row.ignored_fields:
        entry["ignored_fields"] = list(recon_row.ignored_fields)
    return entry


# ---------------------------------------------------------------------------
# Assertions block builder.
# ---------------------------------------------------------------------------


def _build_assertions_block(
    file_wide_assertions: list[str],
) -> list[dict[str, str]]:
    """Build the ``assertions:`` list block from sheet-level assertions.

    Each emitted entry is ``{name, expr}`` matching the
    :class:`AssertionSpec` schema. The first assertion expression is
    given the published name ``batch_header_count`` when its tokens
    match the header-count convention; otherwise auto-generated names
    ``assertion_<i>`` are used.

    Args:
        file_wide_assertions: Ordered list of pipe-separated assertion
            expressions lifted from the first reconciliation row.

    Returns:
        A list of ``{name, expr}`` dicts (block-style on emission).
    """
    assertions: list[dict[str, str]] = []
    for idx, expr in enumerate(file_wide_assertions, start=1):
        name = _derive_assertion_name(expr, idx)
        assertions.append({"name": name, "expr": expr})
    return assertions


def _derive_assertion_name(expr: str, idx: int) -> str:
    """Pick a stable name for an assertion expression.

    Args:
        expr: The assertion expression text.
        idx: 1-based position of the assertion in the sheet's
            assertion list.

    Returns:
        The conventional name when the expression matches the
        published header-count shape; otherwise ``assertion_<idx>``.
    """
    if all(token in expr for token in _HEADER_COUNT_ASSERTION_EXPR_TOKENS):
        return _HEADER_COUNT_ASSERTION_NAME
    return f"assertion_{idx}"


# ---------------------------------------------------------------------------
# Top-level document assembly.
# ---------------------------------------------------------------------------


def _build_document(
    workbook: OnboardingWorkbook,
    sheet: ReconciliationSheet,
    source_code: str,
) -> dict[str, Any]:
    """Assemble the top-level YAML document for one reconciliation sheet.

    Top-level key order matches the committed ``tranert.yml``:

        schema_version, source, file_type, umbrella_mapping,
        bootstrap_dir, load_dir, query_dir, record_types[, assertions]

    Args:
        workbook: The parsed onboarding workbook (used to look up the
            output-file spec for ``is_multi_record`` and the
            ``MultiRecord_<FILETYPE>`` rows for cardinality).
        sheet: The reconciliation sheet being emitted.
        source_code: The source code from
            :attr:`OnboardingWorkbook.source.source_code`.

    Returns:
        An ordered dict ready for ``yaml.dump``.
    """
    file_type = sheet.file_type
    output_spec = _find_output_spec(workbook, file_type)
    multi_record_index = _index_multi_record_rows(workbook, file_type)

    record_types: dict[str, dict[str, Any]] = {}
    for recon_row in sheet.rows:
        rt_name = recon_row.record_type_name
        if not rt_name:
            continue
        mr_row = (
            multi_record_index.get(rt_name)
            if rt_name != _FLAT_RECORD_TYPE_TOKEN
            else None
        )
        mapping_sheet_name = (
            mr_row.mapping_sheet if mr_row is not None else None
        )
        mapping_sheet = (
            workbook.mapping_sheets.get(mapping_sheet_name)
            if mapping_sheet_name
            else None
        )
        record_types[rt_name] = _build_record_type_block(
            recon_row, mr_row, mapping_sheet
        )

    document: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "source": source_code,
        "file_type": file_type,
        "umbrella_mapping": _derive_umbrella_mapping(
            source_code, file_type, output_spec
        ),
        "bootstrap_dir": _derive_sql_dir(source_code, file_type, "00_bootstrap"),
        "load_dir": _derive_sql_dir(source_code, file_type, "10_load"),
        "query_dir": _derive_sql_dir(source_code, file_type, "20_query"),
        "record_types": record_types,
    }

    assertions = _build_assertions_block(sheet.file_wide_assertions)
    if assertions:
        document["assertions"] = assertions
    return document


# ---------------------------------------------------------------------------
# Public emitter class + module-level convenience function.
# ---------------------------------------------------------------------------


class ReconciliationEmitter:
    """Emit reconciliation YAML artefacts from a parsed onboarding workbook.

    Stateless across calls. The emitter never writes to disk — it
    returns a list of :class:`EmittedReconciliationArtefact` instances
    so the orchestrator (``valdo onboard-source`` CLI) owns filesystem
    I/O and the ``--dry-run`` / ``--check`` modes can intercept.

    Usage:

        >>> from src.onboarding.workbook_reader import read_workbook
        >>> from src.onboarding.emitters.reconciliation_emitter import (
        ...     ReconciliationEmitter,
        ... )
        >>> wb = read_workbook("templates/SHAW_onboarding.xlsx")
        >>> arts = ReconciliationEmitter("SHAW").emit_all(wb)
        >>> arts[0].path
        'config/e2e/sources/SHAW/reconciliation/tranert.yml'
    """

    def __init__(
        self,
        source_code: str,
        output_dir: str = "config/e2e/sources",
    ) -> None:
        """Construct a reconciliation emitter.

        Args:
            source_code: The source code (e.g. ``"SHAW"``). Used to
                derive both the on-disk path prefix and the
                ``source:`` field inside every emitted document.
                Required because the emitter is sometimes called
                with a synthetic workbook for unit-test fixtures
                whose ``OnboardingWorkbook.source.source_code``
                does not match the expected on-disk location.
            output_dir: The base directory for emitted artefacts.
                Defaults to ``config/e2e/sources`` matching the
                committed convention.

        Raises:
            EmitterError: When ``source_code`` is empty or whitespace.
        """
        if not source_code or not source_code.strip():
            raise EmitterError(
                "ReconciliationEmitter requires a non-empty source_code"
            )
        self._source_code = source_code.strip()
        self._output_dir = output_dir

    def emit_all(
        self, workbook: OnboardingWorkbook
    ) -> list[EmittedReconciliationArtefact]:
        """Emit one artefact per reconciliation sheet in the workbook.

        Args:
            workbook: The parsed ``OnboardingWorkbook`` tree from
                EC-S2. The emitter walks
                :attr:`OnboardingWorkbook.reconciliation_sheets` in
                workbook order (Python 3.7+ dict order).

        Returns:
            Ordered list of :class:`EmittedReconciliationArtefact` —
            empty when the workbook carries no reconciliation sheets.

        Raises:
            EmitterError: If any sheet's YAML emission fails.
        """
        artefacts: list[EmittedReconciliationArtefact] = []
        for file_type, sheet in workbook.reconciliation_sheets.items():
            document = _build_document(workbook, sheet, self._source_code)
            try:
                content = yaml.dump(
                    document,
                    Dumper=_ReconciliationYamlDumper,
                    sort_keys=False,
                    default_flow_style=False,
                    allow_unicode=True,
                    width=4096,
                )
            except yaml.YAMLError as exc:
                raise EmitterError(
                    f"Failed to emit reconciliation YAML for {file_type}: {exc}"
                ) from exc
            artefacts.append(
                EmittedReconciliationArtefact(
                    path=_derive_output_path(
                        self._source_code, file_type, self._output_dir
                    ),
                    content=content,
                )
            )
        return artefacts


def emit_reconciliation_artefacts(
    workbook: OnboardingWorkbook,
    source_code: str | None = None,
    output_dir: str = "config/e2e/sources",
) -> list[EmittedReconciliationArtefact]:
    """Module-level convenience wrapper around :meth:`ReconciliationEmitter.emit_all`.

    Args:
        workbook: The parsed ``OnboardingWorkbook`` tree from EC-S2.
        source_code: Override for the source code. When ``None`` (the
            common case), the value is read from
            :attr:`OnboardingWorkbook.source.source_code`.
        output_dir: The base directory for emitted artefacts. Defaults
            to ``config/e2e/sources``.

    Returns:
        Ordered list of :class:`EmittedReconciliationArtefact`.

    Raises:
        EmitterError: If the workbook carries no source-info row and
            ``source_code`` was not supplied, or any sheet emission
            fails.
    """
    effective_source_code = (
        source_code if source_code is not None else workbook.source.source_code
    )
    return ReconciliationEmitter(
        source_code=effective_source_code,
        output_dir=output_dir,
    ).emit_all(workbook)


__all__ = [
    "EmittedReconciliationArtefact",
    "ReconciliationEmitter",
    "emit_reconciliation_artefacts",
]
