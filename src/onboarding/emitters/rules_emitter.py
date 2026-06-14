"""Rules JSON emitter (EC-S5).

Convert a parsed :class:`~src.onboarding.models.OnboardingWorkbook` into
the per-file rules JSON artefacts the engine consumes:

    * Per flat output file -> ``config/rules/<SOURCE>_<FILETYPE>.json``
      (cross-row + cross-field + per-field rules for a single-record
      output file).
    * Per multi-record record-type layout ->
      ``config/rules/<SOURCE>_<FILETYPE>_<LAYOUT>_rules.json``. The
      ``<LAYOUT>`` token is derived via the same algorithm EC-S4 uses
      for mapping sheets, so two discriminator values sharing a layout
      (``rt_32000`` + ``rt_32001`` both pointing at
      ``TRANERT_NEW1_Rules``) collapse to a single emitted JSON.

Public API
----------
    >>> from src.onboarding.workbook_reader import read_workbook
    >>> from src.onboarding.emitters.rules_emitter import emit_rules_artefacts
    >>> wb = read_workbook("templates/SHAW_onboarding.xlsx")
    >>> artefacts = emit_rules_artefacts(wb)
    >>> {a.kind for a in artefacts}
    {'flat_rules_json', 'per_type_rules_json'}

Design contract
---------------
* **Delegates rule conversion to the existing
  :class:`src.config.ba_rules_template_converter.BARulesTemplateConverter`.**
  No re-implementation of ``_is_descriptive_text``, severity coercion,
  rule-type vocabulary mapping, etc. The emitter wraps ``RulesRow``
  lists into a ``pandas.DataFrame`` whose column names match the
  snake_case alias set the converter already accepts
  (``rule_id``, ``rule_name``, ``field``, ``rule_type``, ``severity``,
  ``enabled``, ``message``, ``expected_values``) plus the two columns
  the converter reads by their canonical BA labels
  (``Condition (optional)``, ``Notes``). The
  :class:`~src.onboarding.models.RulesRow` dataclass carries the
  snake_case ``condition`` / ``notes`` attributes; the emitter renames
  those two columns when building the DataFrame so the converter picks
  them up.
* **Layout-tag de-duplication shared with EC-S4.** Both emitters
  derive the rules artefact path via
  :func:`src.onboarding.emitters.derive_rules_artefact_path` (EC-S7
  shared helper); EC-S4 uses it to populate ``record_types.<name>.rules``
  in the umbrella YAML and EC-S5 uses it to choose the on-disk JSON
  filename. The single source of truth means the umbrella's
  ``rules:`` paths are guaranteed to resolve to artefacts EC-S5 emits.
* **No disk writes.** ``emit_all`` returns
  :class:`EmittedRulesArtefact` instances; EC-S6's
  ``valdo onboard-source`` CLI is responsible for writing them to the
  filesystem.
* **Empty / missing rules sheets are silent.** When
  ``OutputFileSpec.rules_sheet == ""`` (BA explicitly left blank) OR
  ``MultiRecordRow.rules_sheet == ""`` (this record type has no rules),
  the emitter skips that file/record-type. When the rules sheet exists
  but has zero rows, the emitter still emits a valid empty-rules JSON
  (``{"metadata": {...}, "rules": []}``) so the source YAML's
  ``output_files[].rules`` cross-reference resolves to a real file.
* **DASH-style field names preserved verbatim.** The emitter never
  snake_cases the ``field`` cell -- ``LN-NUM-ERT`` propagates from
  the workbook to the emitted JSON unchanged.
* **Umbrella rules emit nothing at the umbrella level.** When
  ``OutputFileSpec.rules_sheet == "(umbrella)"``, the per-record-type
  ``MultiRecordRow.rules_sheet`` references drive the artefacts; the
  emitter never emits a single "umbrella rules JSON" because the
  committed convention has each record type carry its own rules file
  (e.g. ``SHAW_TRANERT_BATCH_HEADER_rules.json``).

Reused-not-rebuilt
------------------
* :class:`src.config.ba_rules_template_converter.BARulesTemplateConverter`
  -- the BA-friendly rules converter. EC-S5 calls
  ``BARulesTemplateConverter._convert_dataframe`` directly; the public
  ``from_csv`` / ``from_excel`` methods are I/O wrappers we do not
  need because the rows already live in memory.

Cross-reference contract with EC-S3
-----------------------------------
The source YAML EC-S3 emits already references
``config/rules/<SOURCE>_<FILETYPE>.json`` paths for non-umbrella output
files. EC-S5's flat-output emission path matches that exact convention
so every non-empty ``output_files[].rules`` entry in the source YAML
resolves to an artefact emitted in the same workbook-onboarding run.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from src.config.ba_rules_template_converter import BARulesTemplateConverter
from src.onboarding.emitters import (
    EmitterError,
    derive_rules_artefact_path,
)
from src.onboarding.models import (
    MultiRecordSheet,
    OnboardingWorkbook,
    OutputFileSpec,
    RulesRow,
    RulesSheet,
)

# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------

_UMBRELLA_SHEET_TOKEN = "(umbrella)"

# Map the snake_case ``RulesRow`` attribute names that the converter does
# NOT alias to their canonical BA-friendly column labels. The converter's
# ``_convert_row`` reads these by exact label (``row.get('Condition (optional)')``,
# ``row.get('Notes')``), so the emitter renames the DataFrame columns to
# match. Every other ``RulesRow`` attribute is already aliased by the
# converter (see ``BARulesTemplateConverter._convert_dataframe.col_aliases``).
_RULES_COLUMN_RENAMES: dict[str, str] = {
    "condition": "Condition (optional)",
    "notes": "Notes",
}


# ---------------------------------------------------------------------------
# Public artefact dataclass.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EmittedRulesArtefact:
    """One emitted rules artefact.

    Returned by :meth:`RulesEmitter.emit_all`; the EC-S6 CLI writes
    them to disk. The emitter itself never touches the filesystem so
    artefacts can be previewed (e.g. in the future BA UI) before being
    persisted.

    Attributes:
        path: Repo-relative path where the artefact will be written
            (e.g. ``"config/rules/SHAW_TRANERT_BATCH_HEADER_rules.json"``).
        content: The serialised JSON text. Trailing newline included so
            the artefact is POSIX-clean on disk.
        kind: Discriminator the EC-S6 CLI uses to log emitted artefacts
            by category, and the test suite uses to assert counts:

                * ``"flat_rules_json"`` -- the rules JSON for a single-
                  record output file (``config/rules/SHAW_CDSTRANS_EFB.json``).
                * ``"per_type_rules_json"`` -- the rules JSON for one
                  record-type layout inside a multi-record output file
                  (``config/rules/SHAW_TRANERT_BATCH_HEADER_rules.json``).
    """

    path: str
    content: str
    kind: Literal["flat_rules_json", "per_type_rules_json"]


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _resolve_rules_sheet(
    workbook: OnboardingWorkbook, target_name: str
) -> RulesSheet:
    """Look up a rules sheet by its (possibly shortened) name.

    Uses the same exact-then-tilde-fallback algorithm as
    :func:`src.onboarding.emitters.mapping_emitter._resolve_mapping_sheet`
    so a workbook that names a tilde-shortened tab in the
    ``rules_sheet`` cell still resolves. The fallback only triggers
    when the cell value's prefix + suffix match the tab's prefix-before-
    ``~`` + suffix-after-``~``; ambiguous fallbacks raise rather than
    silently picking one.

    Args:
        workbook: The parsed workbook tree from EC-S2.
        target_name: The ``rules_sheet`` cell value to resolve.

    Returns:
        The resolved :class:`RulesSheet`.

    Raises:
        EmitterError: When neither exact match nor the ``~``-fallback
            resolves, or when the fallback matches multiple tabs.
    """
    if target_name in workbook.rules_sheets:
        return workbook.rules_sheets[target_name]

    candidates: list[str] = []
    for name in workbook.rules_sheets:
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
        return workbook.rules_sheets[candidates[0]]

    if len(candidates) > 1:
        raise EmitterError(
            f"Rules sheet cell '{target_name}' is ambiguous: it matches "
            f"multiple ~-shortened tabs ({candidates}). Use the exact "
            "(possibly ~-shortened) tab name in the cell."
        )

    raise EmitterError(
        f"Rules sheet '{target_name}' not found in workbook. "
        f"Available sheets: {sorted(workbook.rules_sheets)}"
    )


def _rules_rows_to_dataframe(rows: list[RulesRow]) -> pd.DataFrame:
    """Convert a list of :class:`RulesRow` into a ``pandas.DataFrame``.

    Column names match the snake_case alias set the existing
    :class:`BARulesTemplateConverter` already accepts (``rule_id`` ->
    ``Rule ID`` etc.), with two manual renames for the columns the
    converter reads by their canonical BA-friendly labels
    (``condition`` -> ``Condition (optional)``,
    ``notes`` -> ``Notes``) -- see :data:`_RULES_COLUMN_RENAMES`.

    All values pass through as-is. ``None`` cells stay ``None`` (pandas
    surfaces them as ``NaN`` which the converter's ``pd.isna`` checks
    treat as blank, matching the BA's blank-cell intent).

    Args:
        rows: The rules-sheet rows from the parsed workbook.

    Returns:
        A DataFrame ready to feed to
        ``BARulesTemplateConverter._convert_dataframe``. Empty inputs
        produce a DataFrame with the expected columns so the converter
        does not KeyError on the required-column check.
    """
    records = [dataclasses.asdict(row) for row in rows]
    df = pd.DataFrame(records)
    if df.empty:
        df = pd.DataFrame(columns=list(RulesRow.__dataclass_fields__))
    df = df.rename(columns=_RULES_COLUMN_RENAMES)
    return df


def _convert_rules_sheet_to_dict(
    sheet: RulesSheet, rules_id: str
) -> dict[str, Any]:
    """Run a parsed rules sheet through the existing
    :class:`BARulesTemplateConverter` and return the resulting dict.

    The converter's ``_convert_dataframe`` is the right entry point --
    the public ``from_csv`` / ``from_excel`` wrappers are I/O shims we
    do not need because the rows already live in memory.

    The synthetic ``template_path`` we pass (``f"{rules_id}.csv"``) is
    what the converter embeds in the generated artefact's
    ``metadata.name`` (``Path(template_path).stem``),
    ``metadata.description`` (``"Generated from BA-friendly template:
    <stem>.csv"``), and ``metadata.template_path`` so the historic
    CSV-driven artefacts and the new workbook-driven artefacts have
    the same ``description`` text -- eases the EC-S5 regression test
    against committed JSONs.

    Args:
        sheet: The :class:`RulesSheet` to convert.
        rules_id: The rules name -- becomes the basename for the
            ``metadata.name`` field (e.g.
            ``"SHAW_TRANERT_BATCH_HEADER_rules"``).

    Returns:
        The dict the converter produced (``metadata`` + ``rules`` keys).

    Raises:
        EmitterError: When the converter rejects the input (e.g.
            missing required columns or unsupported rule type); the
            original ``ValueError`` is chained as ``__cause__``.
    """
    df = _rules_rows_to_dataframe(sheet.rows)
    converter = BARulesTemplateConverter()
    try:
        return converter._convert_dataframe(  # noqa: SLF001 -- documented entry point
            df, template_path=f"{rules_id}.csv"
        )
    except ValueError as exc:
        raise EmitterError(
            f"BARulesTemplateConverter rejected rules sheet "
            f"'{sheet.sheet_name}' (rules_id={rules_id!r}): {exc}"
        ) from exc


def _serialise_json(data: dict[str, Any]) -> str:
    """Serialise a converter dict to JSON text with a trailing newline.

    Indentation = 2 and ``sort_keys=False`` to preserve the converter's
    natural key order (``metadata`` -> ``rules``; inside each rule the
    converter assembles ``id`` -> ``name`` -> ``description`` -> ...).
    Matches the shape of the committed ``config/rules/*.json`` files
    so the regression diff is minimal.

    Args:
        data: The converter output dict.

    Returns:
        JSON text terminated with ``"\\n"``.
    """
    return json.dumps(data, indent=2, sort_keys=False, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# Public emitter class + module-level convenience function.
# ---------------------------------------------------------------------------


class RulesEmitter:
    """Emit per-output-file rules JSON artefacts.

    Stateless across calls. The class form exists so callers can swap
    in subclasses for testing (e.g. injecting a fake
    :class:`BARulesTemplateConverter`) without monkey-patching module-
    level functions.

    Usage:

        >>> from src.onboarding.workbook_reader import read_workbook
        >>> from src.onboarding.emitters.rules_emitter import RulesEmitter
        >>> wb = read_workbook("templates/SHAW_onboarding.xlsx")
        >>> artefacts = RulesEmitter("SHAW").emit_all(wb)
        >>> sorted({a.kind for a in artefacts})
        ['flat_rules_json', 'per_type_rules_json']

    Args:
        source_code: The source code (e.g. ``"SHAW"``). Becomes the
            filename prefix on every emitted artefact.
        output_dir: Repo-relative directory the emitted artefacts will
            live under. Defaults to ``"config/rules"`` to match the
            committed convention.
    """

    def __init__(self, source_code: str, output_dir: str = "config/rules"):
        self.source_code = source_code
        self.output_dir = output_dir.rstrip("/")

    # -- public ----------------------------------------------------------

    def emit_all(self, workbook: OnboardingWorkbook) -> list[EmittedRulesArtefact]:
        """Emit every rules artefact for ``workbook``.

        Walks the workbook in this order:

            1. Per flat output file (``rules_sheet`` is a real sheet
               name, not ``""`` and not ``"(umbrella)"``) ->
               ``flat_rules_json``.
            2. Per multi-record output file: per record-type layout
               (deduped by layout tag) -> ``per_type_rules_json``.

        Output files with ``rules_sheet == ""`` are skipped (BA opted
        out of rules for that file). Multi-record record-types with
        ``rules_sheet == ""`` are likewise skipped (this layout has
        no rules).

        Does NOT write to disk. EC-S6's ``valdo onboard-source`` CLI
        is responsible for persistence.

        Args:
            workbook: The parsed onboarding workbook from EC-S2.

        Returns:
            A list of :class:`EmittedRulesArtefact`. Order matches the
            walk above so a caller can stream artefacts to disk in
            workbook order.

        Raises:
            EmitterError: When a referenced rules sheet cannot be
                resolved, when the converter rejects a sheet's rows,
                or when a per-record-type sheet name does not follow
                the ``<FILE_TYPE>_<LAYOUT>_Rules`` convention.
        """
        artefacts: list[EmittedRulesArtefact] = []

        # 1. Output files -- flat vs multi-record dispatch.
        for spec in workbook.output_files:
            if spec.is_multi_record:
                artefacts.extend(self._emit_multi_record_artefacts(workbook, spec))
            else:
                flat = self._emit_flat_output_artefact(workbook, spec)
                if flat is not None:
                    artefacts.append(flat)

        return artefacts

    # -- flat output -----------------------------------------------------

    def _emit_flat_output_artefact(
        self, workbook: OnboardingWorkbook, spec: OutputFileSpec
    ) -> EmittedRulesArtefact | None:
        """Emit the flat rules JSON for one non-multi-record ``OutputFileSpec``.

        Returns ``None`` when the BA explicitly left the ``rules_sheet``
        cell blank -- no artefact is emitted for that file (matching
        the EC-S3 source-YAML convention that emits ``rules: ""`` in
        the corresponding ``output_files[]`` entry).

        Delegates path derivation to
        :func:`src.onboarding.emitters.derive_rules_artefact_path` so
        the EC-S4 mapping emitter's umbrella ``rules:`` paths and the
        actual emitted JSON paths are guaranteed-equal (EC-S7
        cross-emitter coordination).
        """
        artefact_path = derive_rules_artefact_path(
            self.source_code,
            spec.file_type,
            spec.rules_sheet,
            rules_dir=self.output_dir,
        )
        if artefact_path is None:
            return None
        sheet = _resolve_rules_sheet(workbook, spec.rules_sheet)
        rules_id = Path(artefact_path).stem  # e.g. "SHAW_CDSTRANS_EFB"
        data = _convert_rules_sheet_to_dict(sheet, rules_id)
        return EmittedRulesArtefact(
            path=artefact_path,
            content=_serialise_json(data),
            kind="flat_rules_json",
        )

    # -- multi-record ---------------------------------------------------

    def _emit_multi_record_artefacts(
        self, workbook: OnboardingWorkbook, spec: OutputFileSpec
    ) -> list[EmittedRulesArtefact]:
        """Emit every per-record-type rules JSON for one multi-record output.

        The umbrella token (``OutputFileSpec.rules_sheet == "(umbrella)"``)
        is NOT itself emitted -- per the committed convention, each
        record type carries its own rules JSON (e.g.
        ``SHAW_TRANERT_BATCH_HEADER_rules.json``) and the umbrella YAML
        references them per-record-type. The emitter walks the
        corresponding ``MultiRecordSheet`` and emits one artefact per
        unique layout tag (deduped so ``rt_32000`` + ``rt_32001``
        sharing ``TRANERT_NEW1_Rules`` collapse to a single
        ``SHAW_TRANERT_NEW1_rules.json``).

        Record-types whose ``rules_sheet`` is blank are silently
        skipped (no rules apply to that layout).
        """
        mr_sheet: MultiRecordSheet | None = workbook.multi_record_sheets.get(
            spec.file_type
        )
        if mr_sheet is None:
            raise EmitterError(
                f"Output file '{spec.file_type}' is marked multi-record "
                f"(rules_sheet='(umbrella)') but the workbook has no "
                f"'MultiRecord_{spec.file_type}' sheet."
            )

        artefacts: list[EmittedRulesArtefact] = []
        seen_paths: set[str] = set()

        for row in mr_sheet.rows:
            # Both the blank-rules and the canonical-path derivation
            # are owned by the shared helper so EC-S4's umbrella YAML
            # ``rules:`` paths and the emitted JSON paths cannot drift
            # (EC-S7 cross-emitter coordination).
            artefact_path = derive_rules_artefact_path(
                self.source_code,
                spec.file_type,
                row.rules_sheet,
                rules_dir=self.output_dir,
            )
            if artefact_path is None:
                # This record type has no rules; nothing to emit. Other
                # record types in the same MR file may still emit.
                continue
            if artefact_path in seen_paths:
                # Two record types share a rules layout (e.g. rt_32000
                # + rt_32001 -> TRANERT_NEW1_Rules) -- emit once.
                continue
            seen_paths.add(artefact_path)

            sheet = _resolve_rules_sheet(workbook, row.rules_sheet)
            rules_id = Path(artefact_path).stem  # e.g. "SHAW_TRANERT_NEW1_rules"
            data = _convert_rules_sheet_to_dict(sheet, rules_id)
            artefacts.append(
                EmittedRulesArtefact(
                    path=artefact_path,
                    content=_serialise_json(data),
                    kind="per_type_rules_json",
                )
            )

        return artefacts


def emit_rules_artefacts(
    workbook: OnboardingWorkbook, output_dir: str = "config/rules"
) -> list[EmittedRulesArtefact]:
    """Module-level convenience wrapper around :meth:`RulesEmitter.emit_all`.

    Derives ``source_code`` from ``workbook.source.source_code`` so the
    most common caller (the EC-S6 CLI orchestrator) does not have to
    pass it separately.

    Args:
        workbook: The parsed onboarding workbook from EC-S2.
        output_dir: Repo-relative directory the emitted artefacts will
            live under. Defaults to ``"config/rules"``.

    Returns:
        A list of :class:`EmittedRulesArtefact`.

    Raises:
        EmitterError: When a referenced rules sheet cannot be resolved,
            when the converter rejects a sheet's rows, or when a
            per-record-type sheet name does not follow the
            ``<FILE_TYPE>_<LAYOUT>_Rules`` convention.
    """
    return RulesEmitter(
        source_code=workbook.source.source_code, output_dir=output_dir
    ).emit_all(workbook)


__all__ = [
    "EmittedRulesArtefact",
    "RulesEmitter",
    "emit_rules_artefacts",
]
