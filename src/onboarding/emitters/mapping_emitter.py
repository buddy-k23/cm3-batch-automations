"""Mapping JSON + umbrella YAML emitter (EC-S4).

Convert a parsed :class:`~src.onboarding.models.OnboardingWorkbook` into
the per-file mapping artefacts the engine consumes:

    * Per input file -> ``config/mappings/<SOURCE>_<FILETYPE>.json``
      (flat fixed-width / delimited mapping).
    * Per flat output file -> ``config/mappings/<SOURCE>_<FILETYPE>.json``.
    * Per multi-record output file:
        - One umbrella YAML at
          ``config/mappings/<SOURCE>_<FILETYPE>.yaml`` consumed by
          :class:`src.config.multi_record_config.MultiRecordConfig`.
        - One per-record-type JSON at
          ``config/mappings/<SOURCE>_<FILETYPE>_<LAYOUT>_mapping.json``.
          ``<LAYOUT>`` is the suffix of the per-type mapping sheet
          (``TRANERT_NEW1_Mapping`` -> ``NEW1``) so two record-types that
          share a layout (``rt_32000`` + ``rt_32001`` both pointing at
          ``TRANERT_NEW1_Mapping``) collapse to a single emitted JSON
          and the umbrella YAML references it from both
          ``record_types[].mapping`` entries.

Public API
----------
    >>> from src.onboarding.workbook_reader import read_workbook
    >>> from src.onboarding.emitters.mapping_emitter import emit_mapping_artefacts
    >>> wb = read_workbook("templates/SHAW_onboarding.xlsx")
    >>> artefacts = emit_mapping_artefacts(wb)
    >>> {a.kind for a in artefacts}
    {'flat_json', 'umbrella_yaml', 'per_type_json'}

Design contract
---------------
* **Delegates field-mapping conversion to the existing
  :class:`src.config.template_converter.TemplateConverter`.** No
  re-implementation of ``_is_descriptive_text``, default-value
  extraction, fixed-width length warnings, etc. The emitter wraps
  ``MappingFieldRow`` lists into a ``pandas.DataFrame`` whose column
  names exactly match the converter's accepted snake_case set
  (``field_name``, ``data_type``, ``position``, ``length``,
  ``target_name``, ``required``, ``format``, ``transformation``,
  ``valid_values``, ``description``) and hands the DataFrame to
  ``TemplateConverter._convert_dataframe``.
* **No disk writes.** ``emit_all`` returns :class:`EmittedMappingArtefact`
  instances; EC-S6's ``valdo onboard-source`` CLI is responsible for
  writing them to the filesystem. This keeps the emitter pure and
  testable, and keeps the "BA preview" use-case (render in the UI
  without touching disk) viable.
* **Umbrella YAML shape** matches the committed
  ``config/mappings/SHAW_TRANERT.yaml`` and
  ``config/mappings/SHAW_ATOCTRAN.yaml`` -- i.e. the shape consumed by
  :class:`src.config.multi_record_config.MultiRecordConfig`:

    .. code-block:: yaml

        discriminator:
          field: <discriminator_field>
          position: <discriminator_position>
          length: <discriminator_length>

        record_types:
          <record_type_name>:
            match: "<value>"          # for discriminator_equals
            # OR
            position: "first"         # for position_first
            mapping: config/mappings/<SOURCE>_<FT>_<LAYOUT>_mapping.json
            rules:   config/rules/<SOURCE>_<FT>_<LAYOUT>_rules.json
            expect:  any | at_least_one | exactly_one

        cross_type_rules: []
        default_action: error

  Cardinality vocabulary translation (workbook -> umbrella):

      ``one_per_driver_row``         -> ``at_least_one``
      ``many_per_driver_row``        -> ``any``
      ``zero_or_one_per_driver_row`` -> ``any``

  The umbrella never writes ``cross_type_rules`` content -- the
  workbook does not (yet) carry a cross-type-rules sheet, and the
  committed SHAW umbrellas embed those rules as hand-edited overlays.
  The emitter emits an empty ``cross_type_rules: []`` so the
  ``MultiRecordConfig`` shape is well-formed; operators add cross-type
  rules to the umbrella YAML after EC-S6 writes it (the EC-S6 CLI
  preserves an existing umbrella's ``cross_type_rules`` block when
  re-emitting -- see EC-S6).
* **DASH-style field names preserved verbatim.** The emitter never
  snake_cases the ``field_name`` cell. The downstream converter
  derives the ``target_name`` (snake_case SQL alias) only when the
  BA leaves the ``target_name`` cell blank.
* **Tolerant sheet-name lookup.** The BA-facing convention is that
  ``mapping_sheet`` cells carry the exact (possibly ``~``-shortened)
  sheet tab name. Real-world SHAW workbook has the unshortened name
  in the cell paired with a ``~``-shortened tab (EC-S1 gap noted in
  the SHAW workbook). The emitter does an exact-match lookup first,
  then falls back to a ``~`` wildcard match where ``~`` in the tab
  name expands to "any non-empty span in the cell". This keeps the
  emitter resilient without silently swallowing real misconfigurations
  -- the fallback only triggers when the cell value's prefix AND
  suffix match the tab's prefix-before-``~`` and suffix-after-``~``.

Reused-not-rebuilt
------------------
* :class:`src.config.template_converter.TemplateConverter` -- the
  field-mapping converter. EC-S4 calls
  ``TemplateConverter._convert_dataframe`` directly; the public
  ``from_csv``/``from_excel`` methods are I/O wrappers we don't need
  because the DataFrame comes from the parsed workbook tree, not from
  disk.

Surfacing gaps in the converter
-------------------------------
Per the EC-S4 guardrails: if the converter is missing a feature the
emitter needs, the emitter STOPS rather than silently extending the
converter. Today there is no such gap -- the converter's snake_case
column normaliser already accepts the exact column set
:class:`~src.onboarding.models.MappingFieldRow` carries.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Literal

import pandas as pd
import yaml

from src.config.template_converter import TemplateConverter
from src.onboarding.emitters import EmitterError, derive_layout_tag
from src.onboarding.models import (
    InputFileSpec,
    MappingFieldRow,
    MappingSheet,
    MultiRecordRow,
    MultiRecordSheet,
    OnboardingWorkbook,
    OutputFileSpec,
)

# ---------------------------------------------------------------------------
# Constants -- vocabulary translation tables and sentinel tokens.
# ---------------------------------------------------------------------------

_UMBRELLA_SHEET_TOKEN = "(umbrella)"

# Map the workbook's cardinality vocabulary onto the
# ``MultiRecordConfig.RecordTypeConfig.expect`` vocabulary. The umbrella
# YAML's ``expect`` field accepts ``any`` / ``at_least_one`` / ``exactly_one``.
# The workbook's three values translate as:
#
#     one_per_driver_row         -> at_least_one  (must appear at least once)
#     many_per_driver_row        -> any           (zero or more)
#     zero_or_one_per_driver_row -> any           (zero or one; ``any`` is
#                                                  the least-restrictive
#                                                  match the umbrella schema
#                                                  supports)
#
# Operators tighten ``any`` -> ``exactly_one`` etc. by hand after EC-S6 emits
# the umbrella; the emitter never assumes a stricter cardinality than the
# workbook explicitly carries.
_CARDINALITY_TO_EXPECT: dict[str, str] = {
    "one_per_driver_row": "at_least_one",
    "many_per_driver_row": "any",
    "zero_or_one_per_driver_row": "any",
}

# ``MultiRecordConfig.default_action`` accepted values are warn/error/skip.
# EC-S4 always emits ``error`` -- matches both committed SHAW umbrellas.
_DEFAULT_ACTION = "error"


# ---------------------------------------------------------------------------
# Public artefact dataclass.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EmittedMappingArtefact:
    """One emitted mapping artefact (mapping JSON or umbrella YAML).

    Returned by :meth:`MappingEmitter.emit_all`; the EC-S6 CLI writes
    them to disk. The emitter itself never touches the filesystem so
    artefacts can be previewed (e.g. in the future BA UI) before being
    persisted.

    Attributes:
        path: Repo-relative path where the artefact will be written
            (e.g. ``"config/mappings/SHAW_TRANERT.yaml"``).
        content: The serialised file content -- JSON text for
            ``flat_json`` / ``per_type_json`` kinds, YAML text for
            ``umbrella_yaml``. Trailing newline included so the
            artefact is POSIX-clean on disk.
        kind: Discriminator the EC-S6 CLI uses to log emitted artefacts
            by category, and the test suite uses to assert counts.
    """

    path: str
    content: str
    kind: Literal["flat_json", "umbrella_yaml", "per_type_json"]


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _resolve_mapping_sheet(
    workbook: OnboardingWorkbook, target_name: str
) -> MappingSheet:
    """Look up a mapping sheet by its (possibly shortened) name.

    Tries an exact match first. If that fails, walks every sheet name
    containing ``~`` and checks whether the cell value matches the
    tab's prefix-before-``~`` + suffix-after-``~`` envelope. This
    accommodates the real-world SHAW workbook where the BA wrote the
    unshortened name in the ``mapping_sheet`` cell but the tab was
    shortened to fit Excel's 31-char limit (EC-S1 gap; see the
    convention discussed in
    ``templates/source_onboarding_template_README.md``).

    Args:
        workbook: The parsed workbook tree from EC-S2.
        target_name: The ``mapping_sheet`` cell value to resolve.

    Returns:
        The resolved :class:`MappingSheet`.

    Raises:
        EmitterError: When neither exact match nor the ``~``-fallback
            resolves; the error message lists the closest candidates
            so the BA can fix the cell value.
    """
    if target_name in workbook.mapping_sheets:
        return workbook.mapping_sheets[target_name]

    candidates: list[str] = []
    for name in workbook.mapping_sheets:
        if "~" not in name:
            continue
        prefix, _, suffix = name.partition("~")
        if (
            prefix
            and suffix
            and target_name.startswith(prefix)
            and target_name.endswith(suffix)
            and len(target_name) >= len(prefix) + len(suffix)
        ):
            candidates.append(name)

    if len(candidates) == 1:
        return workbook.mapping_sheets[candidates[0]]

    if len(candidates) > 1:
        raise EmitterError(
            f"Mapping sheet cell '{target_name}' is ambiguous: it matches "
            f"multiple ~-shortened tabs ({candidates}). Use the exact "
            "(possibly ~-shortened) tab name in the cell."
        )

    raise EmitterError(
        f"Mapping sheet '{target_name}' not found in workbook. "
        f"Available sheets: {sorted(workbook.mapping_sheets)}"
    )


def _mapping_rows_to_dataframe(rows: list[MappingFieldRow]) -> pd.DataFrame:
    """Convert a list of :class:`MappingFieldRow` into a ``pandas.DataFrame``.

    The DataFrame's column names match the snake_case set the existing
    :class:`TemplateConverter` already accepts (it normalises
    ``field_name`` -> ``Field Name`` etc. internally), so the emitter
    needs no adapter code.

    All values are passed through as-is. ``None`` cells stay ``None``
    (pandas surfaces them as ``NaN`` which the converter's ``pd.notna``
    checks treat as blank, matching the BA's blank-cell intent).

    Args:
        rows: The mapping-sheet rows from the parsed workbook.

    Returns:
        A DataFrame ready to feed to
        ``TemplateConverter._convert_dataframe``.
    """
    records = [dataclasses.asdict(row) for row in rows]
    # Force string dtype so the converter's ``int()``/``str()`` coercions
    # see literals exactly as the BA typed them (matches ADR 0007: pandas
    # auto-infers numeric columns otherwise and ``valid_values`` would
    # drift between "100030" and "100030.0").
    df = pd.DataFrame(records)
    # Empty input -> still produce a DataFrame with the expected columns
    # so downstream coercion does not KeyError. The converter validates
    # required columns regardless.
    if df.empty:
        df = pd.DataFrame(columns=list(MappingFieldRow.__dataclass_fields__))
    return df


def _convert_mapping_sheet_to_dict(
    sheet: MappingSheet, mapping_id: str
) -> dict[str, Any]:
    """Run a parsed mapping sheet through the existing
    :class:`TemplateConverter` and return the resulting dict.

    The converter's ``_convert_dataframe`` is the right entry point --
    the public ``from_csv`` / ``from_excel`` wrappers are I/O shims we
    do not need because the rows already live in memory.

    The synthetic ``template_path`` we pass (``f"{mapping_id}.csv"``)
    is what the converter embeds in the generated artefact's
    ``description`` (``"Generated from template: <stem>.csv"``) and
    ``metadata.source_template`` so the historic CSV-driven artefacts
    and the new workbook-driven artefacts have the same ``description``
    text (eases the EC-S4 regression test against committed JSONs).

    Args:
        sheet: The :class:`MappingSheet` to convert.
        mapping_id: The mapping name -- becomes the ``mapping_name``
            field of the emitted dict (e.g.
            ``"SHAW_TRANERT_BATCH_HEADER_mapping"``).

    Returns:
        The dict the converter produced.

    Raises:
        EmitterError: When the converter rejects the input (e.g.
            missing required columns); the original ``ValueError`` is
            chained.
    """
    df = _mapping_rows_to_dataframe(sheet.rows)
    converter = TemplateConverter()
    try:
        return converter._convert_dataframe(  # noqa: SLF001 -- documented entry point
            df,
            template_path=f"{mapping_id}.csv",
            mapping_name=mapping_id,
        )
    except ValueError as exc:
        raise EmitterError(
            f"TemplateConverter rejected mapping sheet '{sheet.sheet_name}' "
            f"(mapping_id={mapping_id!r}): {exc}"
        ) from exc


def _derive_layout_tag(file_type: str, sheet_name: str) -> str:
    """Derive the layout tag for a per-record-type *mapping* sheet.

    Thin shim around :func:`src.onboarding.emitters.derive_layout_tag`
    that pins the EC-S4 suffix (``"_Mapping"``). Preserved as a
    module-level name so existing imports inside this file (and any
    tests referencing it) keep working after the shared helper was
    extracted into the package ``__init__`` for reuse by EC-S5.

    Args:
        file_type: The output-file file type (e.g. ``"TRANERT"``).
        sheet_name: The mapping sheet name (e.g.
            ``"TRANERT_NEW1_Mapping"``).

    Returns:
        The layout tag (e.g. ``"NEW1"``).

    Raises:
        EmitterError: When the sheet name does not follow the
            ``<FILE_TYPE>_<LAYOUT>_Mapping`` convention.
    """
    return derive_layout_tag(file_type, sheet_name, suffix="_Mapping")


def _build_record_type_entry(
    source_code: str,
    file_type: str,
    row: MultiRecordRow,
    layout_tag: str,
) -> dict[str, Any]:
    """Build one ``record_types.<name>`` entry for the umbrella YAML.

    The shape matches :class:`src.config.multi_record_config.RecordTypeConfig`
    and the committed SHAW umbrellas:

        * ``match_kind == "position_first"`` -> emit ``position: "first"``.
        * ``match_kind == "discriminator_equals"`` -> emit ``match: "<value>"``.
        * ``match_kind == "discriminator_in"`` -> emit ``match: "<value>"``;
          the comma-list is preserved verbatim. (The committed umbrellas
          encode each value as its own ``rt_<value>`` entry so the
          single-string ``match`` field is sufficient for that pattern.
          Sources that truly need an in-list ``match`` should split into
          separate ``record_types`` keys in the workbook.)

    Mapping path: derived from the layout tag, not the record_type_name,
    so two discriminator values sharing a layout (e.g. ``rt_32000`` +
    ``rt_32001`` both -> ``TRANERT_NEW1_Mapping``) end up pointing at
    the same emitted JSON.

    Rules path: blank string for now (per-record-type rules emission
    lands in EC-S5). Operators add the rules path by hand if needed
    until EC-S5 wires it in.

    Expect: translated from the workbook's cardinality vocabulary via
    :data:`_CARDINALITY_TO_EXPECT`.

    Args:
        source_code: The source code (drives mapping path prefix).
        file_type: The output-file file type.
        row: The multi-record row from the workbook.
        layout_tag: The layout tag derived from the per-type mapping
            sheet name.

    Returns:
        The block-style dict for this record-type entry.
    """
    entry: dict[str, Any] = {}
    if row.match_kind == "position_first":
        entry["position"] = "first"
    else:
        entry["match"] = row.match_value
    entry["mapping"] = (
        f"config/mappings/{source_code}_{file_type}_{layout_tag}_mapping.json"
    )
    entry["rules"] = ""  # EC-S5 will populate when a rules_sheet is present.
    entry["expect"] = _CARDINALITY_TO_EXPECT[row.cardinality]
    return entry


def _build_umbrella_document(
    source_code: str,
    spec: OutputFileSpec,
    mr_sheet: MultiRecordSheet,
) -> dict[str, Any]:
    """Build the top-level umbrella YAML document.

    Shape matches :class:`src.config.multi_record_config.MultiRecordConfig`
    so the emitted YAML loads cleanly via
    ``MultiRecordConfig.model_validate``.

    The discriminator block uses the FIRST row's ``discriminator_field``
    / ``discriminator_position`` / ``discriminator_length`` cells. The
    workbook reader does not enforce that all rows in a multi-record
    sheet share the same discriminator (each row carries its own cells),
    so the emitter trusts the BA's intent that the first row's values
    are canonical for the file. If divergence ever becomes a real
    problem, EC-S1 should add a schema-level check.

    Args:
        source_code: The source code.
        spec: The output-file spec (for ``file_type``).
        mr_sheet: The parsed ``MultiRecord_<FILETYPE>`` sheet.

    Returns:
        The block-style umbrella document ready for ``yaml.safe_dump``.

    Raises:
        EmitterError: When the multi-record sheet has zero rows (the
            engine cannot dispatch with no record types).
    """
    if not mr_sheet.rows:
        raise EmitterError(
            f"Multi-record sheet 'MultiRecord_{spec.file_type}' has no rows; "
            "cannot emit umbrella YAML."
        )

    first = mr_sheet.rows[0]
    discriminator = {
        "field": first.discriminator_field,
        "position": first.discriminator_position,
        "length": first.discriminator_length,
    }

    record_types: dict[str, Any] = {}
    for row in mr_sheet.rows:
        layout_tag = _derive_layout_tag(spec.file_type, row.mapping_sheet)
        record_types[row.record_type_name] = _build_record_type_entry(
            source_code, spec.file_type, row, layout_tag
        )

    return {
        "discriminator": discriminator,
        "record_types": record_types,
        "cross_type_rules": [],
        "default_action": _DEFAULT_ACTION,
    }


def _serialise_json(data: dict[str, Any]) -> str:
    """Serialise a converter dict to JSON text with a trailing newline.

    Indentation = 2, ``sort_keys=False`` so the converter's natural key
    order (``mapping_name`` -> ``version`` -> ``description`` -> ...)
    survives, matching the committed JSON files' shape.

    Args:
        data: The converter output dict.

    Returns:
        JSON text terminated with ``\\n``.
    """
    import json

    return json.dumps(data, indent=2, sort_keys=False, ensure_ascii=False) + "\n"


def _serialise_yaml(data: dict[str, Any]) -> str:
    """Serialise an umbrella document to YAML text with a trailing newline.

    ``sort_keys=False`` preserves the assembled key order
    (``discriminator`` -> ``record_types`` -> ``cross_type_rules`` ->
    ``default_action``) so the emitted file reads top-down naturally.

    Args:
        data: The umbrella document dict.

    Returns:
        YAML text terminated with ``\\n``.
    """
    text = yaml.safe_dump(
        data,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=4096,
    )
    if not text.endswith("\n"):
        text += "\n"
    return text


# ---------------------------------------------------------------------------
# Public emitter class + module-level convenience function.
# ---------------------------------------------------------------------------


class MappingEmitter:
    """Emit per-file mapping JSON + umbrella YAML artefacts.

    Stateless across calls. The class form exists so callers can swap
    in subclasses for testing (e.g. injecting a fake
    :class:`TemplateConverter`) without monkey-patching module-level
    functions.

    Usage:

        >>> from src.onboarding.workbook_reader import read_workbook
        >>> from src.onboarding.emitters.mapping_emitter import MappingEmitter
        >>> wb = read_workbook("templates/SHAW_onboarding.xlsx")
        >>> artefacts = MappingEmitter("SHAW").emit_all(wb)
        >>> sorted({a.kind for a in artefacts})
        ['flat_json', 'per_type_json', 'umbrella_yaml']

    Args:
        source_code: The source code (e.g. ``"SHAW"``). Becomes the
            filename prefix on every emitted artefact.
        output_dir: Repo-relative directory the emitted artefacts will
            live under. Defaults to ``"config/mappings"`` to match the
            committed convention.
    """

    def __init__(self, source_code: str, output_dir: str = "config/mappings"):
        self.source_code = source_code
        self.output_dir = output_dir.rstrip("/")

    # -- public ----------------------------------------------------------

    def emit_all(self, workbook: OnboardingWorkbook) -> list[EmittedMappingArtefact]:
        """Emit every mapping artefact for ``workbook``.

        Walks the workbook in this order so the umbrella YAML's
        ``record_types[].mapping`` references resolve to artefacts
        emitted earlier in the same call:

            1. Per input file -> ``flat_json``.
            2. Per flat output file -> ``flat_json``.
            3. Per multi-record output file:
                a. Per layout (dedup'd) -> ``per_type_json``.
                b. The umbrella -> ``umbrella_yaml``.

        Does NOT write to disk. EC-S6's ``valdo onboard-source`` CLI
        is responsible for persistence.

        Args:
            workbook: The parsed onboarding workbook from EC-S2.

        Returns:
            A list of :class:`EmittedMappingArtefact`. Order matches
            the walk above so a caller can stream artefacts to disk
            in dependency-safe order.

        Raises:
            EmitterError: When a mapping sheet cannot be resolved,
                when the converter rejects a sheet's rows, or when an
                umbrella YAML cannot be assembled.
        """
        artefacts: list[EmittedMappingArtefact] = []

        # 1. Input files (always flat).
        for spec in workbook.input_files:
            artefacts.append(self._emit_input_artefact(workbook, spec))

        # 2. Output files -- partition into flat vs multi-record.
        for spec in workbook.output_files:
            if spec.is_multi_record:
                artefacts.extend(self._emit_multi_record_artefacts(workbook, spec))
            else:
                artefacts.append(self._emit_flat_output_artefact(workbook, spec))

        return artefacts

    # -- input/output flat -----------------------------------------------

    def _emit_input_artefact(
        self, workbook: OnboardingWorkbook, spec: InputFileSpec
    ) -> EmittedMappingArtefact:
        """Emit the flat mapping JSON for one ``InputFileSpec``."""
        sheet = _resolve_mapping_sheet(workbook, spec.mapping_sheet)
        mapping_id = f"{self.source_code}_{spec.file_type}"
        data = _convert_mapping_sheet_to_dict(sheet, mapping_id)
        return EmittedMappingArtefact(
            path=f"{self.output_dir}/{mapping_id}.json",
            content=_serialise_json(data),
            kind="flat_json",
        )

    def _emit_flat_output_artefact(
        self, workbook: OnboardingWorkbook, spec: OutputFileSpec
    ) -> EmittedMappingArtefact:
        """Emit the flat mapping JSON for one non-multi-record ``OutputFileSpec``."""
        sheet = _resolve_mapping_sheet(workbook, spec.mapping_sheet)
        mapping_id = f"{self.source_code}_{spec.file_type}"
        data = _convert_mapping_sheet_to_dict(sheet, mapping_id)
        return EmittedMappingArtefact(
            path=f"{self.output_dir}/{mapping_id}.json",
            content=_serialise_json(data),
            kind="flat_json",
        )

    # -- multi-record ---------------------------------------------------

    def _emit_multi_record_artefacts(
        self, workbook: OnboardingWorkbook, spec: OutputFileSpec
    ) -> list[EmittedMappingArtefact]:
        """Emit every per-record-type JSON + the umbrella YAML for one multi-record output."""
        mr_sheet = workbook.multi_record_sheets.get(spec.file_type)
        if mr_sheet is None:
            raise EmitterError(
                f"Output file '{spec.file_type}' is marked multi-record "
                f"(mapping_sheet='(umbrella)') but the workbook has no "
                f"'MultiRecord_{spec.file_type}' sheet."
            )

        artefacts: list[EmittedMappingArtefact] = []

        # 3a. Per-record-type JSONs (dedup'd by layout tag).
        seen_layouts: set[str] = set()
        for row in mr_sheet.rows:
            layout_tag = _derive_layout_tag(spec.file_type, row.mapping_sheet)
            if layout_tag in seen_layouts:
                # Two record types share a layout (e.g. rt_32000 + rt_32001
                # -> NEW1) -- emit once.
                continue
            seen_layouts.add(layout_tag)

            sheet = _resolve_mapping_sheet(workbook, row.mapping_sheet)
            mapping_id = (
                f"{self.source_code}_{spec.file_type}_{layout_tag}_mapping"
            )
            data = _convert_mapping_sheet_to_dict(sheet, mapping_id)
            artefacts.append(
                EmittedMappingArtefact(
                    path=f"{self.output_dir}/{mapping_id}.json",
                    content=_serialise_json(data),
                    kind="per_type_json",
                )
            )

        # 3b. Umbrella YAML.
        document = _build_umbrella_document(self.source_code, spec, mr_sheet)
        artefacts.append(
            EmittedMappingArtefact(
                path=f"{self.output_dir}/{self.source_code}_{spec.file_type}.yaml",
                content=_serialise_yaml(document),
                kind="umbrella_yaml",
            )
        )

        return artefacts


def emit_mapping_artefacts(
    workbook: OnboardingWorkbook, output_dir: str = "config/mappings"
) -> list[EmittedMappingArtefact]:
    """Module-level convenience wrapper around :meth:`MappingEmitter.emit_all`.

    Derives ``source_code`` from ``workbook.source.source_code`` so the
    most common caller (the EC-S6 CLI orchestrator) does not have to
    pass it separately.

    Args:
        workbook: The parsed onboarding workbook from EC-S2.
        output_dir: Repo-relative directory the emitted artefacts will
            live under. Defaults to ``"config/mappings"``.

    Returns:
        A list of :class:`EmittedMappingArtefact`.

    Raises:
        EmitterError: When a mapping sheet cannot be resolved, when
            the converter rejects a sheet's rows, or when an umbrella
            YAML cannot be assembled.
    """
    return MappingEmitter(
        source_code=workbook.source.source_code, output_dir=output_dir
    ).emit_all(workbook)


__all__ = [
    "EmittedMappingArtefact",
    "MappingEmitter",
    "emit_mapping_artefacts",
]
