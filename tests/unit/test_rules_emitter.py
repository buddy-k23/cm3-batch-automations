"""Unit tests for the EC-S5 rules JSON emitter.

Covers (8 cases):

  * test_shaw_workbook_emits_expected_rules_artefact_count -- SHAW
    round-trip yields the full set of rules artefacts (14 flat output
    + 14 per-record-type, with ``rt_32000`` + ``rt_32001`` deduped to
    a single NEW1 = 28 total).
  * test_emitted_flat_rules_json_matches_committed_for_known_good_file
    -- the SHAW TRANERT BATCH_HEADER per-record-type rules JSON the
    emitter produces is structurally equivalent (modulo timestamped
    ``metadata`` block) to the committed
    ``config/rules/SHAW_TRANERT_BATCH_HEADER_rules.json``.
  * test_per_type_rules_path_matches_layout_tag_convention -- for
    SHAW TRANERT, ``rt_32000`` + ``rt_32001`` (both share NEW1 layout)
    collapse to a single ``SHAW_TRANERT_NEW1_rules.json`` emitted
    artefact, not two.
  * test_empty_rules_sheet_emits_nothing -- synthetic workbook with
    an output entry whose ``rules_sheet`` is blank yields no artefact
    for that file.
  * test_rules_sheet_with_zero_rows_emits_empty_rules_json --
    synthetic with a rules sheet that has only the header row emits
    a valid empty-rules JSON (``"rules": []``).
  * test_dash_field_names_preserved_in_rules_field_references -- pick
    a TRANERT rules entry where ``field == "LN-NUM-ERT"`` (DASH form)
    and assert the emitted JSON preserves it verbatim.
  * test_cross_reference_with_source_yaml -- emit source YAML via
    EC-S3 AND rules via EC-S5 from the same SHAW workbook; for every
    non-empty ``output_files[].rules`` path in the source YAML,
    assert a corresponding emitted rules artefact exists.
  * test_emit_all_returns_no_disk_writes -- the emitter never touches
    the filesystem; ``config/rules/`` mtimes are unchanged across
    an ``emit_all`` call (EC-S6's CLI does the writing).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from openpyxl import Workbook

from src.pipeline.etl_config import SourceConfig
from src.onboarding.emitters import derive_rules_artefact_path
from src.onboarding.emitters.mapping_emitter import emit_mapping_artefacts
from src.onboarding.emitters.rules_emitter import (
    EmittedRulesArtefact,
    RulesEmitter,
    emit_rules_artefacts,
)
from src.onboarding.emitters.source_yaml_emitter import emit_source_yaml
from src.onboarding.workbook_reader import read_workbook
from src.onboarding.workbook_schema import (
    INPUT_FILES_REQUIRED_COLUMNS,
    OUTPUT_FILES_REQUIRED_COLUMNS,
    RULES_SHEET_REQUIRED_COLUMNS,
    SOURCE_SHEET_REQUIRED_COLUMNS,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = REPO_ROOT / "templates"
SHAW_WORKBOOK = TEMPLATES_DIR / "SHAW_onboarding.xlsx"
COMMITTED_RULES_DIR = REPO_ROOT / "config" / "rules"


# ---------------------------------------------------------------------------
# Helpers (mirror the EC-S3 / EC-S4 synthetic-workbook builders).
# ---------------------------------------------------------------------------


def _drop_metadata(data: dict) -> dict:
    """Return ``data`` with the converter-generated ``metadata`` block dropped.

    The converter embeds a fresh ``datetime.utcnow()`` in
    ``metadata.created_date`` and a host-specific
    ``metadata.template_path`` -- both irreducibly variable. Stripping
    ``metadata`` yields a comparable, deterministic shape for
    regression checks.
    """
    return {k: v for k, v in data.items() if k != "metadata"}


_DEFAULT_SOURCE_DATA: dict[str, object] = {
    "source_code": "TEST",
    "schema_version": 1,
    "release_tag": "2026.M06",
    "description": "Synthetic test workbook.",
    "staging_schema": "APP_INT",
    "output_root": "/tmp/test",
    "java_load_script": "/tmp/load.sh",
    "java_generate_script": "",
    "gate_load_blocking": "true",
    "gate_load_invoke_java": "false",
    "gate_f2s_blocking": "true",
    "gate_generate_blocking": "false",
    "gate_generate_invoke_java": "false",
    "gate_l1_blocking": "true",
    "gate_l2b_blocking": "true",
    "gate_l3_blocking": "false",
    "gate_mr_report_blocking": "false",
}


def _write_row(ws, row_number: int, values: list[object]) -> None:
    """Write ``values`` into row ``row_number`` of ``ws`` (1-indexed columns)."""
    for col_idx, value in enumerate(values, start=1):
        ws.cell(row=row_number, column=col_idx, value=value)


def _build_synthetic_workbook(
    tmp_path: Path,
    *,
    input_files_rows: list[dict[str, object]] | None = None,
    output_files_rows: list[dict[str, object]] | None = None,
    mapping_sheets: dict[str, list[dict[str, object]]] | None = None,
    rules_sheets: dict[str, list[dict[str, object]]] | None = None,
    filename: str = "synthetic.xlsx",
) -> Path:
    """Synthesise a minimal valid-shape workbook with arbitrary rules sheets.

    The InputFiles / OutputFiles / mapping-sheet plumbing follows the same
    pattern as ``tests/unit/test_mapping_emitter.py`` so synthetic workbooks
    here look structurally identical to those used by EC-S4 tests.

    Args:
        tmp_path: pytest tmp_path fixture.
        input_files_rows: Optional ``InputFiles`` rows.
        output_files_rows: Optional ``OutputFiles`` rows.
        mapping_sheets: Dict mapping sheet name -> list of mapping-row dicts
            (needed because every InputFiles / OutputFiles row references one).
        rules_sheets: Dict mapping sheet name -> list of rules-row dicts. Each
            row dict's keys must be a subset of the BA-friendly column set
            (``Rule ID``, ``Rule Name``, ``Field``, ``Rule Type``,
            ``Severity``, ``Expected / Values``, ``Enabled``, ``Message``,
            ``Condition (optional)``, ``Notes``).
        filename: Output filename in tmp_path.

    Returns:
        Path to the saved workbook.
    """
    input_files_rows = input_files_rows or []
    output_files_rows = output_files_rows or []
    mapping_sheets = mapping_sheets or {}
    rules_sheets = rules_sheets or {}

    wb = Workbook()
    wb.remove(wb.active)

    ws = wb.create_sheet("Source")
    _write_row(ws, 1, list(SOURCE_SHEET_REQUIRED_COLUMNS))
    _write_row(
        ws, 2, [_DEFAULT_SOURCE_DATA[col] for col in SOURCE_SHEET_REQUIRED_COLUMNS]
    )

    ws = wb.create_sheet("InputFiles")
    _write_row(ws, 1, list(INPUT_FILES_REQUIRED_COLUMNS))
    for idx, row_dict in enumerate(input_files_rows, start=2):
        _write_row(
            ws, idx, [row_dict.get(col) for col in INPUT_FILES_REQUIRED_COLUMNS]
        )

    ws = wb.create_sheet("OutputFiles")
    _write_row(ws, 1, list(OUTPUT_FILES_REQUIRED_COLUMNS))
    for idx, row_dict in enumerate(output_files_rows, start=2):
        _write_row(
            ws, idx, [row_dict.get(col) for col in OUTPUT_FILES_REQUIRED_COLUMNS]
        )

    mapping_columns = [
        "Field Name",
        "Data Type",
        "Position",
        "Length",
        "Target Name",
        "Required",
        "Format",
        "Transformation",
        "Valid Values",
        "Description",
    ]
    for sheet_name, rows in mapping_sheets.items():
        ws = wb.create_sheet(sheet_name)
        _write_row(ws, 1, mapping_columns)
        for idx, row_dict in enumerate(rows, start=2):
            _write_row(ws, idx, [row_dict.get(col) for col in mapping_columns])

    # Full rules-sheet column set the EC-S2 reader honours.
    rules_columns = [
        "Rule ID",
        "Rule Name",
        "Field",
        "Rule Type",
        "Severity",
        "Expected / Values",
        "Enabled",
        "Message",
        "Condition (optional)",
        "Notes",
    ]
    for sheet_name, rows in rules_sheets.items():
        ws = wb.create_sheet(sheet_name)
        _write_row(ws, 1, rules_columns)
        for idx, row_dict in enumerate(rows, start=2):
            _write_row(ws, idx, [row_dict.get(col) for col in rules_columns])

    out_path = tmp_path / filename
    wb.save(str(out_path))
    return out_path


# ---------------------------------------------------------------------------
# 1. SHAW round-trip artefact-count proof.
# ---------------------------------------------------------------------------


def test_shaw_workbook_emits_expected_rules_artefact_count():
    """The SHAW workbook emits the full set of rules artefacts.

    Expected from EC-S1's worked example:
        * 14 flat-output rules JSONs (every CDSTRANS_* + CONTACT +
          CONTACT_ACCOUNT + P327; everything that isn't ATOCTRAN or
          TRANERT).
        * 14 per-record-type rules JSONs (7 ATOCTRAN + 7 TRANERT
          layouts; TRANERT's ``rt_32001`` shares the ``NEW1`` layout
          with ``rt_32000`` so the LAYOUT-keyed dedup collapses to
          7 files for TRANERT).
        * Total: 28.

    Counts here mirror the EC-S4 mapping-emitter test, minus the input
    files (rules are output-side only) and minus the two umbrella YAMLs
    (rules ride per-record-type inside multi-record outputs).
    """
    workbook = read_workbook(SHAW_WORKBOOK)
    artefacts = emit_rules_artefacts(workbook)

    counts: dict[str, int] = {}
    for art in artefacts:
        counts[art.kind] = counts.get(art.kind, 0) + 1

    assert len(artefacts) == 28, (
        f"Expected 28 rules artefacts; got {len(artefacts)} (counts={counts})"
    )
    assert counts == {
        "flat_rules_json": 14,
        "per_type_rules_json": 14,
    }, f"Per-kind breakdown mismatch: {counts}"

    # Paths must be unique -- a duplicate path would silently overwrite
    # in EC-S6's writer.
    paths = [a.path for a in artefacts]
    assert len(paths) == len(set(paths)), (
        f"Emitted rules artefacts have duplicate paths: {sorted(paths)}"
    )


# ---------------------------------------------------------------------------
# 2. BATCH_HEADER rules JSON matches committed (modulo metadata timestamps).
# ---------------------------------------------------------------------------


def test_emitted_flat_rules_json_matches_committed_for_known_good_file():
    """The emitted TRANERT BATCH_HEADER rules JSON matches the committed
    file structurally.

    The committed
    ``config/rules/SHAW_TRANERT_BATCH_HEADER_rules.json`` was originally
    generated by the same
    :class:`src.config.ba_rules_template_converter.BARulesTemplateConverter`
    that EC-S5 delegates to (from the CSV at
    ``mappings/csv/shaw_tranert/SHAW_TRANERT_BATCH_HEADER_rules.csv``).
    The workbook's ``TRANERT_BATCH_HEADER_Rules`` sheet carries the
    same rule rows. Therefore the emitted artefact's content should be
    structurally identical EXCEPT for the converter's ``metadata`` block,
    which embeds a fresh ``datetime.utcnow()`` and a host-specific
    ``template_path``. The regression compares everything else.
    """
    workbook = read_workbook(SHAW_WORKBOOK)
    artefacts = emit_rules_artefacts(workbook)

    emitted = next(
        a
        for a in artefacts
        if a.path == "config/rules/SHAW_TRANERT_BATCH_HEADER_rules.json"
    )
    emitted_data = json.loads(emitted.content)

    committed_path = COMMITTED_RULES_DIR / "SHAW_TRANERT_BATCH_HEADER_rules.json"
    committed_data = json.loads(committed_path.read_text(encoding="utf-8"))

    assert _drop_metadata(emitted_data) == _drop_metadata(committed_data), (
        "Emitted TRANERT BATCH_HEADER rules JSON diverges from committed file "
        "(after stripping the variable metadata block)."
    )


# ---------------------------------------------------------------------------
# 3. Layout-tag dedup: rt_32000 + rt_32001 -> single NEW1 rules artefact.
# ---------------------------------------------------------------------------


def test_per_type_rules_path_matches_layout_tag_convention():
    """SHAW TRANERT ``rt_32000`` and ``rt_32001`` both reference
    ``TRANERT_NEW1_Rules`` -- the EC-S5 emitter must collapse them
    to a single ``SHAW_TRANERT_NEW1_rules.json`` artefact (not two).

    Guards against accidentally keying per-record-type artefacts by
    discriminator value (which would emit two near-identical files
    and break the workbook -> emitter -> umbrella YAML reference chain
    that EC-S4 already wires by layout, not by discriminator).
    """
    workbook = read_workbook(SHAW_WORKBOOK)
    artefacts = emit_rules_artefacts(workbook)

    tranert_paths = [
        a.path for a in artefacts if "TRANERT" in a.path and "NEW1" in a.path
    ]
    assert tranert_paths == ["config/rules/SHAW_TRANERT_NEW1_rules.json"], (
        f"Expected a single NEW1 rules artefact, got: {tranert_paths}"
    )

    # No file named after rt_32000 / rt_32001 should appear.
    bad_paths = [
        a.path for a in artefacts if "32000" in a.path or "32001" in a.path
    ]
    assert not bad_paths, (
        f"Per-type rules JSON named after discriminator value (not layout): "
        f"{bad_paths}"
    )


# ---------------------------------------------------------------------------
# 4. Blank rules_sheet on a flat output emits nothing for that file.
# ---------------------------------------------------------------------------


def test_empty_rules_sheet_emits_nothing(tmp_path):
    """A flat output entry whose ``rules_sheet`` cell is blank produces
    no emitted artefact -- matching the EC-S3 convention that emits
    ``rules: ""`` in the corresponding ``output_files[]`` entry.

    Two flat outputs are wired: one with a rules sheet (``WITH``), one
    without (``WITHOUT``). The emitter must produce exactly one artefact
    and it must be for ``WITH``.
    """
    wb_path = _build_synthetic_workbook(
        tmp_path,
        output_files_rows=[
            {
                "file_type": "WITH",
                "glob": "with_*.txt",
                "mapping_sheet": "WITH_Mapping",
                "rules_sheet": "WITH_Rules",
                "tolerance_max_errors": None,
                "tolerance_max_error_pct": None,
                "tolerance_ignore_fields": None,
            },
            {
                "file_type": "WITHOUT",
                "glob": "without_*.txt",
                "mapping_sheet": "WITHOUT_Mapping",
                "rules_sheet": "",  # explicit opt-out
                "tolerance_max_errors": None,
                "tolerance_max_error_pct": None,
                "tolerance_ignore_fields": None,
            },
        ],
        mapping_sheets={
            "WITH_Mapping": [
                {
                    "Field Name": "FOO",
                    "Data Type": "String",
                    "Position": 1,
                    "Length": 5,
                    "Required": "Yes",
                }
            ],
            "WITHOUT_Mapping": [
                {
                    "Field Name": "BAR",
                    "Data Type": "String",
                    "Position": 1,
                    "Length": 5,
                    "Required": "Yes",
                }
            ],
        },
        rules_sheets={
            "WITH_Rules": [
                {
                    "Rule ID": "R001",
                    "Rule Name": "FOO required",
                    "Field": "FOO",
                    "Rule Type": "not_empty",
                    "Severity": "error",
                    "Enabled": "Yes",
                    "Message": "FOO must not be empty",
                }
            ],
        },
    )

    workbook = read_workbook(wb_path)
    artefacts = emit_rules_artefacts(workbook)

    assert len(artefacts) == 1, (
        f"Expected exactly one artefact (for WITH); got "
        f"{[(a.kind, a.path) for a in artefacts]}"
    )
    assert artefacts[0].path == "config/rules/TEST_WITH.json"
    assert artefacts[0].kind == "flat_rules_json"


# ---------------------------------------------------------------------------
# 5. Rules sheet with zero data rows emits an empty-rules JSON.
# ---------------------------------------------------------------------------


def test_rules_sheet_with_zero_rows_emits_empty_rules_json(tmp_path):
    """A rules sheet that has only the header row (no data rows) emits
    a valid empty-rules JSON.

    The EC-S3 source YAML still references
    ``config/rules/<SOURCE>_<FT>.json`` for any non-blank
    ``rules_sheet`` cell, so the cross-reference would dangle if EC-S5
    emitted nothing here. Instead the emitter produces a well-formed
    ``{"metadata": {...}, "rules": []}`` artefact so the engine can
    load it (and the operator can fill it in later without changing
    the source YAML).
    """
    wb_path = _build_synthetic_workbook(
        tmp_path,
        output_files_rows=[
            {
                "file_type": "EMPTY_RULES",
                "glob": "empty_*.txt",
                "mapping_sheet": "EMPTY_RULES_Mapping",
                "rules_sheet": "EMPTY_RULES_Rules",
                "tolerance_max_errors": None,
                "tolerance_max_error_pct": None,
                "tolerance_ignore_fields": None,
            },
        ],
        mapping_sheets={
            "EMPTY_RULES_Mapping": [
                {
                    "Field Name": "FOO",
                    "Data Type": "String",
                    "Position": 1,
                    "Length": 5,
                    "Required": "Yes",
                }
            ],
        },
        rules_sheets={
            "EMPTY_RULES_Rules": [],  # header-only sheet
        },
    )

    workbook = read_workbook(wb_path)
    artefacts = emit_rules_artefacts(workbook)

    assert len(artefacts) == 1
    art = artefacts[0]
    assert art.path == "config/rules/TEST_EMPTY_RULES.json"
    assert art.kind == "flat_rules_json"

    data = json.loads(art.content)
    assert "metadata" in data
    assert data["rules"] == [], (
        f"Header-only rules sheet must emit an empty rules list; got "
        f"{data['rules']!r}"
    )


# ---------------------------------------------------------------------------
# 6. DASH-style field names preserved end-to-end.
# ---------------------------------------------------------------------------


def test_dash_field_names_preserved_in_rules_field_references():
    """DASH-style canonical field names survive verbatim from a workbook
    rules row's ``Field`` cell to the emitted JSON's ``field`` key.

    The workbook stores field names like ``LN-NUM-ERT`` exactly as the
    BA typed them (no snake_case normalisation in EC-S2 -- per the
    project convention that DASH is the canonical form). The EC-S5
    emitter must preserve this verbatim. Picks the TRANERT NEW1 layout
    (which carries ``LN-NUM-ERT`` rules per the SHAW worked example)
    and confirms at least one of the emitted JSON's rule entries has
    the DASH form.
    """
    workbook = read_workbook(SHAW_WORKBOOK)
    artefacts = emit_rules_artefacts(workbook)

    new1 = next(
        a
        for a in artefacts
        if a.path == "config/rules/SHAW_TRANERT_NEW1_rules.json"
    )
    data = json.loads(new1.content)

    referenced_fields = {r.get("field") for r in data["rules"] if "field" in r}
    assert "LN-NUM-ERT" in referenced_fields, (
        f"Expected DASH-form field 'LN-NUM-ERT' in emitted NEW1 rules JSON; "
        f"got fields: {sorted(f for f in referenced_fields if f)}"
    )


# ---------------------------------------------------------------------------
# 7. Cross-reference contract with the EC-S3 source YAML emitter.
# ---------------------------------------------------------------------------


def test_cross_reference_with_source_yaml():
    """Every non-empty ``output_files[].rules`` path in the source YAML
    EC-S3 emits resolves to an artefact EC-S5 emits from the same
    workbook.

    This guards against silent drift between the two emitters' path
    conventions: if EC-S5 ever changes its filename rule (e.g. inserts
    ``_rules`` for flat outputs) without EC-S3 following suit, the
    engine would fail at load-time when the SourceConfig's reference
    cannot be opened. The check uses the same workbook for both
    emitters so the cross-reference is exercised end-to-end.
    """
    workbook = read_workbook(SHAW_WORKBOOK)
    yaml_text = emit_source_yaml(workbook)
    source_doc = yaml.safe_load(yaml_text)
    rules_artefacts = emit_rules_artefacts(workbook)
    emitted_paths = {a.path for a in rules_artefacts}

    # Sanity: the source YAML must validate through Pydantic so the
    # rules paths we extract below are guaranteed to be the contract
    # the engine sees.
    SourceConfig.model_validate(source_doc)

    referenced_paths = [
        entry["rules"]
        for entry in source_doc["output_files"]
        if entry.get("rules")
    ]
    assert referenced_paths, (
        "Source YAML for SHAW must reference at least one rules JSON; "
        "test fixture has drifted."
    )

    missing = [p for p in referenced_paths if p not in emitted_paths]
    assert not missing, (
        f"Source YAML references rules paths that EC-S5 did not emit: "
        f"{missing}. Emitted paths: {sorted(emitted_paths)}"
    )


# ---------------------------------------------------------------------------
# 8. emit_all is a pure function: no disk writes.
# ---------------------------------------------------------------------------


def test_emit_all_returns_no_disk_writes():
    """``emit_all`` MUST NOT touch the filesystem.

    The EC-S6 CLI is responsible for writing artefacts to disk. The
    emitter staying pure preserves the "preview-before-write" use case
    (e.g. the future BA UI rendering proposed artefacts) and keeps the
    EC-S6 CLI in charge of file-system policy (path validation, dry-run
    mode, write-permission checks).

    Snapshots ``config/rules/`` directory listing + per-file mtimes
    before and after ``emit_all``; the snapshot must be unchanged.
    """
    rules_dir = COMMITTED_RULES_DIR
    assert rules_dir.exists(), (
        f"Test fixture missing: {rules_dir} should exist in the repo."
    )

    def _snapshot() -> dict[str, float]:
        return {
            p.name: p.stat().st_mtime
            for p in rules_dir.iterdir()
            if p.is_file()
        }

    before = _snapshot()
    workbook = read_workbook(SHAW_WORKBOOK)
    emitter = RulesEmitter("SHAW")
    artefacts = emitter.emit_all(workbook)
    after = _snapshot()

    assert before == after, (
        f"emit_all() modified config/rules/ on disk. Diff: "
        f"added={set(after) - set(before)}, "
        f"removed={set(before) - set(after)}, "
        f"mtime_changed={[k for k in before if k in after and before[k] != after[k]]}"
    )
    # Sanity: we actually emitted something (otherwise the test is vacuous).
    assert artefacts, "Expected non-empty artefact list."


# ---------------------------------------------------------------------------
# 9. EC-S7: RulesEmitter and MappingEmitter agree on canonical rules paths.
# ---------------------------------------------------------------------------


def test_rules_emitter_path_matches_umbrella_helper():
    """For SHAW, every per-record-type rules JSON path produced by
    EC-S5 also appears as an umbrella ``record_types.<name>.rules``
    path produced by EC-S4 -- by construction, because both emitters
    derive the path from the same shared helper
    :func:`src.onboarding.emitters.derive_rules_artefact_path`.

    This is the regression guard for EC-S7's headline contract: the
    two emitters cannot drift on the rules-artefact filename. If
    EC-S4 ever stops calling the helper (or EC-S5 stops calling it),
    this test will fail with a concrete mismatch.
    """
    workbook = read_workbook(SHAW_WORKBOOK)
    mapping_artefacts = emit_mapping_artefacts(workbook)
    rules_artefacts = emit_rules_artefacts(workbook)

    # Collect the canonical rules paths the rules emitter wrote.
    emitted_rules_paths = {a.path for a in rules_artefacts}

    # Collect every rules path referenced by every umbrella YAML.
    umbrella_rules_refs: set[str] = set()
    for art in mapping_artefacts:
        if art.kind != "umbrella_yaml":
            continue
        data = yaml.safe_load(art.content)
        for entry in data["record_types"].values():
            rules_ref = entry.get("rules")
            if rules_ref:
                umbrella_rules_refs.add(rules_ref)

    # Sanity: SHAW workbook has populated rules for every record type, so
    # the umbrella refs set is non-empty (otherwise the test is vacuous).
    assert umbrella_rules_refs, (
        "SHAW umbrella YAMLs reference no rules paths -- test fixture "
        "has drifted (EC-S7 contract not exercised)."
    )

    # Every umbrella reference resolves to an emitted rules artefact.
    missing = umbrella_rules_refs - emitted_rules_paths
    assert not missing, (
        f"Umbrella YAML references {len(missing)} rules paths that "
        f"EC-S5 did NOT emit: {sorted(missing)}.\n"
        f"Emitted paths: {sorted(emitted_rules_paths)}"
    )

    # Also verify the shared helper produces the same paths the rules
    # emitter actually wrote (direct contract check).
    source_code = workbook.source.source_code
    for mr_sheet in workbook.multi_record_sheets.values():
        for row in mr_sheet.rows:
            expected = derive_rules_artefact_path(
                source_code, mr_sheet.file_type, row.rules_sheet
            )
            if expected is None:
                continue  # this record type has no rules; nothing to check
            assert expected in emitted_rules_paths, (
                f"Helper says rules path is {expected!r} for "
                f"mr_sheet={mr_sheet.file_type!r} "
                f"record_type={row.record_type_name!r}, but EC-S5 did not "
                f"emit a rules artefact at that path."
            )


# ---------------------------------------------------------------------------
# Type-shape sanity: emit_rules_artefacts returns the documented type.
# ---------------------------------------------------------------------------


def test_emit_rules_artefacts_returns_emitted_rules_artefact_instances():
    """The module-level convenience returns ``EmittedRulesArtefact`` instances.

    Pinned because EC-S6 / future BA-UI callers depend on this type
    contract for routing artefacts to the correct on-disk path and for
    discriminating ``flat_rules_json`` vs ``per_type_rules_json`` in
    the CLI output (e.g. logging summary counts).
    """
    workbook = read_workbook(SHAW_WORKBOOK)
    artefacts = emit_rules_artefacts(workbook)
    assert artefacts
    for art in artefacts:
        assert isinstance(art, EmittedRulesArtefact)
        assert art.kind in {"flat_rules_json", "per_type_rules_json"}
        assert art.path.startswith("config/rules/")
        assert art.content.endswith("\n")
