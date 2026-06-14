"""Unit tests for the EC-S4 mapping JSON + umbrella YAML emitter.

Covers (7 cases):

  * test_shaw_workbook_emits_all_expected_artefacts -- SHAW round-trip
    yields the full set of mapping artefacts (6 input + 14 flat output
    + 14 per-record-type + 2 umbrella = 36 total).
  * test_emitted_flat_json_matches_committed_for_known_good_file --
    the SHAW TRANERT BATCH_HEADER per-record-type JSON the emitter
    produces is byte-equivalent (modulo the timestamped ``metadata``
    block) to the committed
    ``config/mappings/SHAW_TRANERT_BATCH_HEADER_mapping.json``.
  * test_emitted_umbrella_yaml_matches_committed -- the SHAW TRANERT
    umbrella YAML matches the committed file structurally
    (discriminator + record_types keys + mapping paths). The
    committed file's hand-edited ``rules`` paths and
    ``cross_type_rules`` overlay are documented exemptions; the
    emitter never claims to author those (EC-S5 wires rules; cross-
    type rules are operator overlays per ADR convention).
  * test_emitted_per_type_mapping_path_matches_umbrella_reference --
    the umbrella's ``record_types[].mapping`` paths all resolve to
    artefacts emitted in the same ``emit_all`` call (no broken
    cross-references).
  * test_dash_field_names_preserved_through_emitter -- the
    DASH-style canonical field name ``LN-NUM-ERT`` survives verbatim
    from workbook -> emitted JSON.
  * test_mapping_emitter_handles_todo_stub_sheets -- a synthetic
    workbook with a single-row placeholder mapping sheet (per EC-S1's
    TODO-stub convention) emits without crashing and produces one
    field entry.
  * test_emit_all_returns_no_disk_writes -- the emitter never touches
    the filesystem; ``config/mappings/`` mtimes are unchanged across
    an ``emit_all`` call (EC-S6's CLI does the writing).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from openpyxl import Workbook

from src.config.multi_record_config import MultiRecordConfig
from src.onboarding.emitters.mapping_emitter import (
    EmittedMappingArtefact,
    MappingEmitter,
    emit_mapping_artefacts,
)
from src.onboarding.workbook_reader import read_workbook
from src.onboarding.workbook_schema import (
    CROSS_TYPE_RULES_REQUIRED_COLUMNS,
    INPUT_FILES_REQUIRED_COLUMNS,
    MAPPING_SHEET_REQUIRED_COLUMNS,
    MULTI_RECORD_REQUIRED_COLUMNS,
    OUTPUT_FILES_REQUIRED_COLUMNS,
    SOURCE_SHEET_REQUIRED_COLUMNS,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = REPO_ROOT / "templates"
SHAW_WORKBOOK = TEMPLATES_DIR / "SHAW_onboarding.xlsx"
COMMITTED_MAPPINGS_DIR = REPO_ROOT / "config" / "mappings"


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _basename(value: str) -> str:
    """Return the trailing path component, treating ``\\`` like ``/``.

    The committed mapping JSONs were originally generated on a Windows
    host so their ``metadata.source_template`` field carries a
    backslash-separated path (``mappings\\csv\\shaw_tranert\\...``).
    The workbook-driven emitter produces a synthetic POSIX-style
    basename. Both sides agree on the basename -- which is what we
    need for regression comparisons. Out of EC-S10 scope to unify the
    paths fully.
    """
    from pathlib import Path as _Path
    return _Path(value.replace("\\", "/")).name


def _normalise_path_metadata(data: dict) -> dict:
    """Coerce ``metadata.source_template`` to its basename.

    Used by the EC-S10 byte-equality regression test against the
    committed mapping JSONs to neutralise the Windows-vs-POSIX path
    divergence that lives in the committed files (separate concern
    from EC-S10's timestamp work).
    """
    result = {**data}
    metadata = result.get("metadata")
    if isinstance(metadata, dict) and "source_template" in metadata:
        metadata = {**metadata}
        metadata["source_template"] = _basename(metadata["source_template"])
        result["metadata"] = metadata
    return result


# Default Source data row used when synthetic-workbook tests don't
# override individual cells. Mirrors the EC-S2 / EC-S3 helpers.
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
    multi_record_sheets: dict[str, list[dict[str, object]]] | None = None,
    rules_sheets: dict[str, list[dict[str, object]]] | None = None,
    cross_type_rules_sheets: dict[str, list[dict[str, object]]] | None = None,
    filename: str = "synthetic.xlsx",
) -> Path:
    """Synthesise a minimal valid-shape workbook with arbitrary mapping sheets.

    Args:
        tmp_path: pytest tmp_path fixture.
        input_files_rows: Optional ``InputFiles`` rows.
        output_files_rows: Optional ``OutputFiles`` rows.
        mapping_sheets: Dict mapping sheet name -> list of mapping-row dicts.
            Each row dict's keys must be a subset of
            :data:`MAPPING_SHEET_REQUIRED_COLUMNS` plus the optional columns
            EC-S2 reads (Position, Length, Target Name, Required, Format,
            Transformation, Valid Values, Description).
        multi_record_sheets: Dict ``MultiRecord_<FILETYPE>`` sheet name ->
            list of multi-record row dicts. Row dict keys must be a subset
            of :data:`MULTI_RECORD_REQUIRED_COLUMNS`.
        rules_sheets: Dict rules sheet name -> list of rules-row dicts.
            Each row dict's keys must be a subset of the BA-friendly column
            set (Rule ID, Field, Rule Type, plus optional columns).
        filename: Output filename in tmp_path.

    Returns:
        Path to the saved workbook.
    """
    input_files_rows = input_files_rows or []
    output_files_rows = output_files_rows or []
    mapping_sheets = mapping_sheets or {}
    multi_record_sheets = multi_record_sheets or {}
    rules_sheets = rules_sheets or {}
    cross_type_rules_sheets = cross_type_rules_sheets or {}

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

    # MultiRecord_<FILETYPE> sheets.
    for sheet_name, rows in multi_record_sheets.items():
        ws = wb.create_sheet(sheet_name)
        _write_row(ws, 1, list(MULTI_RECORD_REQUIRED_COLUMNS))
        for idx, row_dict in enumerate(rows, start=2):
            _write_row(
                ws, idx, [row_dict.get(col) for col in MULTI_RECORD_REQUIRED_COLUMNS]
            )

    # CrossTypeRules_<FILETYPE> sheets (EC-S8). Tests may include extra
    # non-canonical columns by passing column keys beyond the canonical
    # set in the row dicts; this helper writes only canonical columns
    # plus ``enabled`` plus any extra keys present in the first row.
    for sheet_name, rows in cross_type_rules_sheets.items():
        ws = wb.create_sheet(sheet_name)
        # Discover any extra columns the test rows carry.
        extra_cols: list[str] = []
        seen: set[str] = set()
        canonical_lower = {c.lower() for c in CROSS_TYPE_RULES_REQUIRED_COLUMNS}
        for row_dict in rows:
            for key in row_dict.keys():
                if (
                    key.lower() not in canonical_lower
                    and key.lower() != "enabled"
                    and key not in seen
                ):
                    extra_cols.append(key)
                    seen.add(key)
        cols = (
            list(CROSS_TYPE_RULES_REQUIRED_COLUMNS) + ["enabled"] + extra_cols
        )
        _write_row(ws, 1, cols)
        for idx, row_dict in enumerate(rows, start=2):
            _write_row(ws, idx, [row_dict.get(col) for col in cols])

    # Full mapping-sheet column set the EC-S2 reader looks up.
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

    # Rules sheets (BA-friendly column set).
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


def test_shaw_workbook_emits_all_expected_artefacts():
    """The SHAW workbook emits the full set of mapping artefacts.

    Expected from EC-S1's worked example:
        * 6 input-file flat JSONs (COLLATERAL_MASTER, FEE_MASTER,
          LOAN_MASTER, LOANS_NAME, POSTED_TRANS, TRANS_MASTER).
        * 14 flat output JSONs (every CDSTRANS_* + CONTACT + CONTACT_ACCOUNT
          + P327; everything that isn't ATOCTRAN or TRANERT).
        * 14 per-record-type JSONs (7 ATOCTRAN + 7 TRANERT layouts;
          the 8th TRANERT discriminator value ``rt_32001`` shares the
          ``NEW1`` layout with ``rt_32000`` so the LAYOUT-keyed dedup
          collapses to 7 files for TRANERT).
        * 2 umbrella YAMLs (ATOCTRAN, TRANERT).
        * Total: 36.
    """
    workbook = read_workbook(SHAW_WORKBOOK)
    artefacts = emit_mapping_artefacts(workbook)

    counts: dict[str, int] = {}
    for art in artefacts:
        counts[art.kind] = counts.get(art.kind, 0) + 1

    assert len(artefacts) == 36, (
        f"Expected 36 artefacts; got {len(artefacts)} (counts={counts})"
    )
    assert counts == {
        "flat_json": 20,  # 6 input + 14 flat output
        "per_type_json": 14,  # 7 ATOCTRAN + 7 TRANERT layouts
        "umbrella_yaml": 2,  # ATOCTRAN + TRANERT
    }, f"Per-kind breakdown mismatch: {counts}"

    # Paths must be unique -- a duplicate path would silently overwrite
    # in EC-S6's writer.
    paths = [a.path for a in artefacts]
    assert len(paths) == len(set(paths)), (
        f"Emitted artefacts have duplicate paths: {sorted(paths)}"
    )


# ---------------------------------------------------------------------------
# 2. Per-record-type JSON matches committed (modulo ``metadata`` timestamps).
# ---------------------------------------------------------------------------


def test_emitted_flat_json_matches_committed_for_known_good_file():
    """The emitted TRANERT BATCH_HEADER per-record-type JSON matches the
    committed file dict-equal (post EC-S10).

    The committed
    ``config/mappings/SHAW_TRANERT_BATCH_HEADER_mapping.json`` was
    originally generated by the same
    :class:`src.config.template_converter.TemplateConverter` the EC-S4
    emitter delegates to (from the CSV at
    ``mappings/csv/shaw_tranert/SHAW_TRANERT_BATCH_HEADER_mapping.csv``).
    The workbook's ``TRANERT_BATCH_HEADER_Mapping`` sheet carries the
    same field rows.

    Post EC-S10 the test pulls the committed file's
    ``metadata.created_date`` and feeds it back to the emitter via
    ``frozen_timestamp=<committed_value>`` so the timestamps in both
    JSONs match exactly. The only remaining (out-of-scope-for-EC-S10)
    divergence is ``metadata.source_template`` where the committed
    file carries a Windows-style absolute path
    (``mappings\\csv\\shaw_tranert\\...``) and the emitter produces a
    POSIX-style basename. Both sides are normalised to the basename
    via :func:`_normalise_path_metadata` so the dict equality is
    meaningful for every other field.
    """
    workbook = read_workbook(SHAW_WORKBOOK)

    committed_path = COMMITTED_MAPPINGS_DIR / "SHAW_TRANERT_BATCH_HEADER_mapping.json"
    committed_data = json.loads(committed_path.read_text(encoding="utf-8"))
    committed_ts = committed_data["metadata"]["created_date"]

    artefacts = emit_mapping_artefacts(workbook, frozen_timestamp=committed_ts)

    emitted = next(
        a
        for a in artefacts
        if a.path == "config/mappings/SHAW_TRANERT_BATCH_HEADER_mapping.json"
    )
    emitted_data = json.loads(emitted.content)

    # EC-S10: With ``frozen_timestamp`` set to the committed file's
    # ``created_date``, both timestamps now match. The committed file's
    # ``last_modified`` historically differs by a few microseconds from
    # ``created_date`` (two separate ``datetime.utcnow()`` calls during
    # original generation) so we normalise to a single value on both
    # sides for the deterministic dict-equality assertion.
    committed_data = {**committed_data}
    committed_data["metadata"] = {**committed_data["metadata"], "last_modified": committed_ts}

    assert _normalise_path_metadata(emitted_data) == _normalise_path_metadata(
        committed_data
    ), (
        "Emitted TRANERT BATCH_HEADER JSON diverges from committed file "
        "(after normalising source_template to basename; timestamps are "
        "synchronised via frozen_timestamp -- EC-S10)."
    )


def test_emitter_is_byte_stable_with_frozen_timestamp():
    """EC-S10 contract: two back-to-back ``emit_all`` calls with the same
    ``frozen_timestamp`` produce byte-identical output for every artefact.

    The historical idempotence gap (``datetime.utcnow()`` baked into
    every ``metadata`` block) is closed; running ``valdo onboard-source
    --frozen-timestamp <value>`` twice now leaves the artefact tree's
    mtimes unchanged byte-for-byte.
    """
    import hashlib

    workbook = read_workbook(SHAW_WORKBOOK)

    artefacts_a = emit_mapping_artefacts(workbook, frozen_timestamp="GENERATED")
    artefacts_b = emit_mapping_artefacts(workbook, frozen_timestamp="GENERATED")

    assert len(artefacts_a) == len(artefacts_b), (
        f"Different artefact counts between runs: "
        f"{len(artefacts_a)} vs {len(artefacts_b)}"
    )

    for art_a, art_b in zip(artefacts_a, artefacts_b):
        assert art_a.path == art_b.path
        # SHA256 comparison gives a precise byte-equality assertion
        # without printing 1000-line diffs on failure.
        hash_a = hashlib.sha256(art_a.content.encode("utf-8")).hexdigest()
        hash_b = hashlib.sha256(art_b.content.encode("utf-8")).hexdigest()
        assert hash_a == hash_b, (
            f"EC-S10 byte-stability violation for {art_a.path}: "
            f"sha256 {hash_a} != {hash_b}"
        )


# ---------------------------------------------------------------------------
# 3. Umbrella YAML matches committed structurally.
# ---------------------------------------------------------------------------


def test_emitted_umbrella_yaml_matches_committed():
    """The emitted TRANERT umbrella YAML matches the committed file
    structurally.

    Strict checks:
        * ``discriminator`` block (field, position, length) is exactly equal.
        * ``record_types`` keys are exactly equal and in the same order.
        * Per-record-type ``match`` (or ``position``), ``mapping`` path,
          AND ``rules`` path are exactly equal (EC-S7: ``rules`` is no
          longer masked from the comparison; EC-S4 now populates the
          rules paths via the shared
          :func:`derive_rules_artefact_path` helper).

    Documented exemptions:
        * ``cross_type_rules`` -- committed file has an operator-authored
          ``header_trailer_count`` overlay; EC-S4 always emits an empty
          list (EC-S8 follow-up).
        * ``default_action`` -- committed file uses ``error``; EC-S4 also
          uses ``error`` (both match here).
    """
    workbook = read_workbook(SHAW_WORKBOOK)
    artefacts = emit_mapping_artefacts(workbook)

    emitted = next(
        a for a in artefacts if a.path == "config/mappings/SHAW_TRANERT.yaml"
    )
    emitted_data = yaml.safe_load(emitted.content)

    committed_path = COMMITTED_MAPPINGS_DIR / "SHAW_TRANERT.yaml"
    committed_data = yaml.safe_load(committed_path.read_text(encoding="utf-8"))

    # Discriminator: exact equality.
    assert emitted_data["discriminator"] == committed_data["discriminator"]

    # Record-type keys: exact equality, same iteration order.
    assert list(emitted_data["record_types"].keys()) == list(
        committed_data["record_types"].keys()
    )

    # Per-entry: match/position, mapping path AND rules path equal.
    # EC-S7: the umbrella YAML now carries populated rules paths. The
    # ``derive_rules_artefact_path`` shared helper drives both EC-S4's
    # umbrella YAML emission and EC-S5's per-record-type JSON
    # filenames, so the committed and emitted ``rules`` values are
    # guaranteed to agree by construction.
    for rt_name, emitted_entry in emitted_data["record_types"].items():
        committed_entry = committed_data["record_types"][rt_name]
        for key in ("match", "position"):
            if key in emitted_entry or key in committed_entry:
                assert emitted_entry.get(key) == committed_entry.get(key), (
                    f"record_types.{rt_name}.{key} diverges: "
                    f"emitted={emitted_entry.get(key)!r} "
                    f"committed={committed_entry.get(key)!r}"
                )
        assert emitted_entry["mapping"] == committed_entry["mapping"], (
            f"record_types.{rt_name}.mapping path mismatch"
        )
        # EC-S7: rules path must now exactly match the committed value.
        assert emitted_entry.get("rules") == committed_entry.get("rules"), (
            f"record_types.{rt_name}.rules path mismatch: "
            f"emitted={emitted_entry.get('rules')!r} "
            f"committed={committed_entry.get('rules')!r}"
        )

    # Top-level key set: emitted has the canonical four keys.
    assert set(emitted_data) == {
        "discriminator",
        "record_types",
        "cross_type_rules",
        "default_action",
    }
    # Default action is hardcoded to error (matches committed).
    assert emitted_data["default_action"] == committed_data["default_action"] == "error"

    # The emitted umbrella loads cleanly through MultiRecordConfig --
    # i.e. it is engine-consumable.
    MultiRecordConfig.model_validate(emitted_data)


# ---------------------------------------------------------------------------
# 4. Cross-reference integrity: umbrella mapping paths resolve to emitted JSONs.
# ---------------------------------------------------------------------------


def test_emitted_per_type_mapping_path_matches_umbrella_reference():
    """Every umbrella YAML's ``record_types[].mapping`` path resolves to a
    per-record-type artefact emitted in the same ``emit_all`` call.

    Guards against silent drift between the umbrella's references and
    the actual per-type artefact filenames (a class of bug that would
    only surface at validate-time when the engine can't find the
    referenced JSON).
    """
    workbook = read_workbook(SHAW_WORKBOOK)
    artefacts = emit_mapping_artefacts(workbook)

    emitted_paths = {a.path for a in artefacts}

    umbrellas = [a for a in artefacts if a.kind == "umbrella_yaml"]
    assert umbrellas, "Expected at least one umbrella artefact for SHAW."

    for umbrella in umbrellas:
        data = yaml.safe_load(umbrella.content)
        for rt_name, entry in data["record_types"].items():
            mapping_path = entry["mapping"]
            assert mapping_path in emitted_paths, (
                f"Umbrella {umbrella.path} references "
                f"record_types.{rt_name}.mapping={mapping_path!r} but no "
                f"emitted artefact has that path. Emitted paths: "
                f"{sorted(emitted_paths)}"
            )


# ---------------------------------------------------------------------------
# 5. DASH-style field names preserved end-to-end.
# ---------------------------------------------------------------------------


def test_dash_field_names_preserved_through_emitter():
    """DASH-style canonical field names survive verbatim from workbook
    cells to emitted JSON.

    The workbook stores field names like ``LN-NUM-ERT`` exactly as the
    BA typed them (no snake_case normalisation in EC-S2 -- per the
    project convention that DASH is the canonical form). The EC-S4
    emitter must preserve this verbatim. Picks the TRANERT NEW1 layout
    (which carries ``LN-NUM-ERT`` per the SHAW worked example) and
    confirms one of the emitted JSON's field entries has the DASH form.
    """
    workbook = read_workbook(SHAW_WORKBOOK)
    artefacts = emit_mapping_artefacts(workbook)

    new1 = next(
        a
        for a in artefacts
        if a.path == "config/mappings/SHAW_TRANERT_NEW1_mapping.json"
    )
    data = json.loads(new1.content)

    field_names = [f["name"] for f in data["fields"]]
    assert "LN-NUM-ERT" in field_names, (
        f"Expected DASH-form field 'LN-NUM-ERT' in emitted NEW1 JSON; "
        f"got fields: {field_names}"
    )


# ---------------------------------------------------------------------------
# 6. TODO-stub mapping sheets emit cleanly.
# ---------------------------------------------------------------------------


def test_mapping_emitter_handles_todo_stub_sheets(tmp_path):
    """A synthetic workbook with a single-row TODO-stub mapping sheet
    emits a valid JSON artefact without crashing.

    Mirrors the EC-S1 convention: when a mapping sheet refers to a
    source spec not yet authored, the workbook seeds it with a single
    ``TODO_FIELD_1_<FILETYPE>`` row so the schema validator stays
    green. The EC-S4 emitter must not choke on this -- it produces a
    placeholder JSON with one field that the BA can subsequently
    overwrite.
    """
    wb_path = _build_synthetic_workbook(
        tmp_path,
        input_files_rows=[
            {
                "file_type": "STUB",
                "glob": "stub_*.txt",
                "mapping_sheet": "STUB_Mapping",
                "target_staging_table": "STG_STUB",
                "thresholds_max_errors": None,
            }
        ],
        mapping_sheets={
            "STUB_Mapping": [
                {
                    "Field Name": "TODO_FIELD_1_STUB",
                    "Data Type": "String",
                    "Position": 1,
                    "Length": 1,
                    "Target Name": "todo_field_1",
                    "Required": "Yes",
                    "Description": (
                        "TODO(mapping-pending): STUB layout not yet authored."
                    ),
                }
            ]
        },
    )

    workbook = read_workbook(wb_path)
    artefacts = emit_mapping_artefacts(workbook)
    assert len(artefacts) == 1
    stub = artefacts[0]
    assert stub.kind == "flat_json"
    assert stub.path == "config/mappings/TEST_STUB.json"
    data = json.loads(stub.content)
    assert len(data["fields"]) == 1
    assert data["fields"][0]["name"] == "TODO_FIELD_1_STUB"


# ---------------------------------------------------------------------------
# 7. emit_all is a pure function: no disk writes.
# ---------------------------------------------------------------------------


def test_emit_all_returns_no_disk_writes():
    """``emit_all`` MUST NOT touch the filesystem.

    The EC-S6 CLI is responsible for writing artefacts to disk. The
    emitter staying pure preserves the "preview-before-write" use case
    (e.g. the future BA UI rendering proposed artefacts) and keeps the
    EC-S6 CLI in charge of file-system policy (path validation, dry-run
    mode, write-permission checks).

    Snapshots ``config/mappings/`` directory listing + per-file mtimes
    before and after ``emit_all``; the snapshot must be unchanged.
    """
    mappings_dir = COMMITTED_MAPPINGS_DIR
    assert mappings_dir.exists(), (
        f"Test fixture missing: {mappings_dir} should exist in the repo."
    )

    def _snapshot() -> dict[str, float]:
        return {
            p.name: p.stat().st_mtime
            for p in mappings_dir.iterdir()
            if p.is_file()
        }

    before = _snapshot()
    workbook = read_workbook(SHAW_WORKBOOK)
    emitter = MappingEmitter("SHAW")
    artefacts = emitter.emit_all(workbook)
    after = _snapshot()

    assert before == after, (
        f"emit_all() modified config/mappings/ on disk. Diff: "
        f"added={set(after) - set(before)}, "
        f"removed={set(before) - set(after)}, "
        f"mtime_changed={[k for k in before if k in after and before[k] != after[k]]}"
    )
    # Sanity: we actually emitted something (otherwise the test is vacuous).
    assert artefacts, "Expected non-empty artefact list."


# ---------------------------------------------------------------------------
# 8. EC-S7: umbrella YAML populates record_types.<name>.rules paths.
# ---------------------------------------------------------------------------


def test_umbrella_yaml_populates_rules_path_per_record_type(tmp_path):
    """A synthetic 2-record-type multi-record workbook emits an umbrella
    YAML whose ``record_types.<name>.rules`` paths match the shared
    helper's canonical layout-tagged convention.

    Guards against regression of the EC-S7 fix: prior to EC-S7, EC-S4
    emitted ``rules: ""`` for every record type because rules-path
    derivation was deferred to EC-S5. EC-S7 wires the shared
    :func:`src.onboarding.emitters.derive_rules_artefact_path` helper
    into EC-S4 so both emitters agree on the path by construction.
    """
    wb_path = _build_synthetic_workbook(
        tmp_path,
        output_files_rows=[
            {
                "file_type": "ATOC",
                "glob": "atoc_*.txt",
                "mapping_sheet": "(umbrella)",
                "rules_sheet": "(umbrella)",
                "tolerance_max_errors": None,
                "tolerance_max_error_pct": None,
                "tolerance_ignore_fields": None,
            },
        ],
        multi_record_sheets={
            "MultiRecord_ATOC": [
                {
                    "record_type_name": "rt_100",
                    "discriminator_field": "TYPE",
                    "discriminator_position": 1,
                    "discriminator_length": 3,
                    "match_kind": "discriminator_equals",
                    "match_value": "100",
                    "mapping_sheet": "ATOC_HDR_Mapping",
                    "rules_sheet": "ATOC_HDR_Rules",
                    "cardinality": "one_per_driver_row",
                },
                {
                    "record_type_name": "rt_200",
                    "discriminator_field": "TYPE",
                    "discriminator_position": 1,
                    "discriminator_length": 3,
                    "match_kind": "discriminator_equals",
                    "match_value": "200",
                    "mapping_sheet": "ATOC_DTL_Mapping",
                    "rules_sheet": "ATOC_DTL_Rules",
                    "cardinality": "many_per_driver_row",
                },
            ],
        },
        mapping_sheets={
            "ATOC_HDR_Mapping": [
                {
                    "Field Name": "TYPE",
                    "Data Type": "String",
                    "Position": 1,
                    "Length": 3,
                    "Required": "Yes",
                }
            ],
            "ATOC_DTL_Mapping": [
                {
                    "Field Name": "TYPE",
                    "Data Type": "String",
                    "Position": 1,
                    "Length": 3,
                    "Required": "Yes",
                }
            ],
        },
        rules_sheets={
            "ATOC_HDR_Rules": [
                {
                    "Rule ID": "R001",
                    "Rule Name": "TYPE required",
                    "Field": "TYPE",
                    "Rule Type": "not_empty",
                    "Severity": "error",
                    "Enabled": "Yes",
                    "Message": "TYPE required",
                }
            ],
            "ATOC_DTL_Rules": [
                {
                    "Rule ID": "R001",
                    "Rule Name": "TYPE required",
                    "Field": "TYPE",
                    "Rule Type": "not_empty",
                    "Severity": "error",
                    "Enabled": "Yes",
                    "Message": "TYPE required",
                }
            ],
        },
    )

    workbook = read_workbook(wb_path)
    artefacts = emit_mapping_artefacts(workbook)

    umbrella = next(
        a for a in artefacts if a.path == "config/mappings/TEST_ATOC.yaml"
    )
    data = yaml.safe_load(umbrella.content)

    # Each record-type entry carries the canonical layout-tagged rules path.
    assert data["record_types"]["rt_100"]["rules"] == (
        "config/rules/TEST_ATOC_HDR_rules.json"
    )
    assert data["record_types"]["rt_200"]["rules"] == (
        "config/rules/TEST_ATOC_DTL_rules.json"
    )


def test_umbrella_yaml_omits_rules_key_when_rules_sheet_empty(tmp_path):
    """When a multi-record row's ``rules_sheet`` cell is blank, the
    corresponding umbrella record-type entry MUST omit the ``rules`` key
    entirely (NOT emit ``rules: ""``).

    Acceptance criterion #2 in EC-S7: the empty string was the original
    bug. The MultiRecordConfig schema treats ``rules`` as an optional
    field with default ``""`` so omitting the key is the cleanest way
    to signal "no rules for this layout" without polluting the umbrella
    YAML's shape.
    """
    wb_path = _build_synthetic_workbook(
        tmp_path,
        output_files_rows=[
            {
                "file_type": "MIX",
                "glob": "mix_*.txt",
                "mapping_sheet": "(umbrella)",
                "rules_sheet": "(umbrella)",
                "tolerance_max_errors": None,
                "tolerance_max_error_pct": None,
                "tolerance_ignore_fields": None,
            },
        ],
        multi_record_sheets={
            "MultiRecord_MIX": [
                {
                    "record_type_name": "rt_with_rules",
                    "discriminator_field": "TYPE",
                    "discriminator_position": 1,
                    "discriminator_length": 3,
                    "match_kind": "discriminator_equals",
                    "match_value": "100",
                    "mapping_sheet": "MIX_HDR_Mapping",
                    "rules_sheet": "MIX_HDR_Rules",
                    "cardinality": "one_per_driver_row",
                },
                {
                    "record_type_name": "rt_no_rules",
                    "discriminator_field": "TYPE",
                    "discriminator_position": 1,
                    "discriminator_length": 3,
                    "match_kind": "discriminator_equals",
                    "match_value": "200",
                    "mapping_sheet": "MIX_DTL_Mapping",
                    "rules_sheet": "",  # explicit opt-out
                    "cardinality": "many_per_driver_row",
                },
            ],
        },
        mapping_sheets={
            "MIX_HDR_Mapping": [
                {
                    "Field Name": "TYPE",
                    "Data Type": "String",
                    "Position": 1,
                    "Length": 3,
                    "Required": "Yes",
                }
            ],
            "MIX_DTL_Mapping": [
                {
                    "Field Name": "TYPE",
                    "Data Type": "String",
                    "Position": 1,
                    "Length": 3,
                    "Required": "Yes",
                }
            ],
        },
        rules_sheets={
            "MIX_HDR_Rules": [
                {
                    "Rule ID": "R001",
                    "Rule Name": "TYPE required",
                    "Field": "TYPE",
                    "Rule Type": "not_empty",
                    "Severity": "error",
                    "Enabled": "Yes",
                    "Message": "TYPE required",
                }
            ],
        },
    )

    workbook = read_workbook(wb_path)
    artefacts = emit_mapping_artefacts(workbook)

    umbrella = next(
        a for a in artefacts if a.path == "config/mappings/TEST_MIX.yaml"
    )
    data = yaml.safe_load(umbrella.content)

    # The record_type WITH rules has the rules key.
    assert "rules" in data["record_types"]["rt_with_rules"], (
        "Expected 'rules' key on record type with non-blank rules_sheet."
    )
    assert data["record_types"]["rt_with_rules"]["rules"] == (
        "config/rules/TEST_MIX_HDR_rules.json"
    )
    # The record_type WITHOUT rules OMITS the rules key entirely.
    assert "rules" not in data["record_types"]["rt_no_rules"], (
        "Expected 'rules' key to be ABSENT (not '', not None) when "
        "rules_sheet is blank; got entry: "
        f"{data['record_types']['rt_no_rules']!r}"
    )


# ---------------------------------------------------------------------------
# Type-shape sanity: emit_mapping_artefacts returns the documented type.
# ---------------------------------------------------------------------------


def test_emit_mapping_artefacts_returns_emitted_mapping_artefact_instances():
    """The module-level convenience returns ``EmittedMappingArtefact`` instances.

    Pinned because EC-S6 / future BA-UI callers depend on this type
    contract for routing artefacts to the correct on-disk path and for
    discriminating ``flat_json`` vs ``umbrella_yaml`` in the CLI output
    (e.g. logging summary counts).
    """
    workbook = read_workbook(SHAW_WORKBOOK)
    artefacts = emit_mapping_artefacts(workbook)
    assert artefacts
    for art in artefacts:
        assert isinstance(art, EmittedMappingArtefact)
        assert art.kind in {"flat_json", "umbrella_yaml", "per_type_json"}
        assert art.path.startswith("config/mappings/")
        assert art.content.endswith("\n")


# ---------------------------------------------------------------------------
# 11. EC-S8: umbrella YAML populates cross_type_rules from workbook sheet.
# ---------------------------------------------------------------------------


def test_umbrella_yaml_emits_cross_type_rules_from_workbook():
    """The SHAW workbook now ships a ``CrossTypeRules_TRANERT`` sheet and
    the emitted ``SHAW_TRANERT.yaml`` umbrella carries the
    ``header_trailer_count`` rule from that sheet — matching the
    committed overlay exactly.

    This is the headline EC-S8 acceptance criterion: previously the
    emitter unconditionally rendered ``cross_type_rules: []`` and the
    operator-authored overlay drifted; after EC-S8 the workbook drives
    the overlay shape end-to-end.
    """
    workbook = read_workbook(SHAW_WORKBOOK)
    artefacts = emit_mapping_artefacts(workbook)

    emitted = next(
        a for a in artefacts if a.path == "config/mappings/SHAW_TRANERT.yaml"
    )
    emitted_data = yaml.safe_load(emitted.content)

    committed_path = COMMITTED_MAPPINGS_DIR / "SHAW_TRANERT.yaml"
    committed_data = yaml.safe_load(committed_path.read_text(encoding="utf-8"))

    # Headline check: the cross_type_rules arrays are byte-equal.
    assert emitted_data["cross_type_rules"] == committed_data["cross_type_rules"], (
        f"cross_type_rules diverges from committed.\n"
        f"emitted:  {emitted_data['cross_type_rules']}\n"
        f"committed: {committed_data['cross_type_rules']}"
    )
    # And the umbrella still loads cleanly through MultiRecordConfig.
    MultiRecordConfig.model_validate(emitted_data)


def test_umbrella_yaml_emits_empty_cross_type_rules_when_sheet_absent(tmp_path):
    """Synthetic workbook without a ``CrossTypeRules_<FILETYPE>`` sheet
    falls back to ``cross_type_rules: []`` (the legacy shape).

    Guards EC-S8 acceptance criterion #5 ("If absent or empty, emit
    cross_type_rules: [] as today"): the new code path is OPT-IN and
    multi-record outputs without operator overlays are unaffected.
    """
    wb_path = _build_synthetic_workbook(
        tmp_path,
        output_files_rows=[
            {
                "file_type": "NORULES",
                "glob": "norules_*.txt",
                "mapping_sheet": "(umbrella)",
                "rules_sheet": "(umbrella)",
                "tolerance_max_errors": None,
                "tolerance_max_error_pct": None,
                "tolerance_ignore_fields": None,
            },
        ],
        multi_record_sheets={
            "MultiRecord_NORULES": [
                {
                    "record_type_name": "rt_100",
                    "discriminator_field": "TYPE",
                    "discriminator_position": 1,
                    "discriminator_length": 3,
                    "match_kind": "discriminator_equals",
                    "match_value": "100",
                    "mapping_sheet": "NORULES_HDR_Mapping",
                    "rules_sheet": "",
                    "cardinality": "one_per_driver_row",
                },
            ],
        },
        mapping_sheets={
            "NORULES_HDR_Mapping": [
                {
                    "Field Name": "TYPE",
                    "Data Type": "String",
                    "Position": 1,
                    "Length": 3,
                    "Required": "Yes",
                }
            ],
        },
    )

    workbook = read_workbook(wb_path)
    # Sanity: no CrossTypeRules sheet was emitted into the workbook.
    assert workbook.cross_type_rules_sheets == {}

    artefacts = emit_mapping_artefacts(workbook)
    umbrella = next(
        a for a in artefacts if a.path == "config/mappings/TEST_NORULES.yaml"
    )
    data = yaml.safe_load(umbrella.content)
    assert data["cross_type_rules"] == [], (
        f"Expected empty list when CrossTypeRules sheet absent; "
        f"got {data['cross_type_rules']!r}"
    )


def test_umbrella_yaml_skips_disabled_cross_type_rules(tmp_path):
    """Workbook rows with ``enabled=false`` are skipped at emission.

    The ``enabled`` flag is a workbook-only soft toggle so an operator
    can soft-disable an overlay rule without deleting the row (preserving
    the audit trail). The emitter MUST drop disabled rows so the engine
    only evaluates active rules.
    """
    wb_path = _build_synthetic_workbook(
        tmp_path,
        output_files_rows=[
            {
                "file_type": "MIX",
                "glob": "mix_*.txt",
                "mapping_sheet": "(umbrella)",
                "rules_sheet": "(umbrella)",
                "tolerance_max_errors": None,
                "tolerance_max_error_pct": None,
                "tolerance_ignore_fields": None,
            },
        ],
        multi_record_sheets={
            "MultiRecord_MIX": [
                {
                    "record_type_name": "batch_header",
                    "discriminator_field": "TYPE",
                    "discriminator_position": 1,
                    "discriminator_length": 3,
                    "match_kind": "position_first",
                    "match_value": "first",
                    "mapping_sheet": "MIX_HDR_Mapping",
                    "rules_sheet": "",
                    "cardinality": "one_per_driver_row",
                },
            ],
        },
        mapping_sheets={
            "MIX_HDR_Mapping": [
                {
                    "Field Name": "TYPE",
                    "Data Type": "String",
                    "Position": 1,
                    "Length": 3,
                    "Required": "Yes",
                }
            ],
        },
        cross_type_rules_sheets={
            "CrossTypeRules_MIX": [
                {
                    "rule_id": "CT001",
                    "check": "header_trailer_count",
                    "record_type": "batch_header",
                    "trailer_field": "ITM-CNT",
                    "count_of": "detail",
                    "allow_empty_batch": "false",
                    "severity": "error",
                    "message": "active rule",
                    "enabled": "true",
                },
                {
                    "rule_id": "CT002",
                    "check": "header_trailer_count",
                    "record_type": "batch_header",
                    "trailer_field": "SUM-AMT",
                    "count_of": "detail",
                    "allow_empty_batch": "false",
                    "severity": "warning",
                    "message": "disabled rule",
                    "enabled": "false",
                },
            ],
        },
    )

    workbook = read_workbook(wb_path)
    artefacts = emit_mapping_artefacts(workbook)
    umbrella = next(
        a for a in artefacts if a.path == "config/mappings/TEST_MIX.yaml"
    )
    data = yaml.safe_load(umbrella.content)
    rules = data["cross_type_rules"]
    assert len(rules) == 1, (
        f"Expected only the active rule; got {len(rules)} entries: {rules}"
    )
    assert rules[0]["message"] == "active rule"
    # The disabled rule must not appear under any key.
    assert all("disabled" not in (r.get("message") or "") for r in rules)


def test_umbrella_yaml_cross_type_rules_passes_through_extra_columns(tmp_path):
    """Non-canonical columns (``sum_of``, ``header_field``, etc.) on a
    workbook ``CrossTypeRules_*`` row pass through to the emitted YAML
    entry verbatim, with pipe-separated lists split into YAML arrays.

    Guards EC-S8 acceptance criterion: the canonical column set is
    minimal; other engine-supported fields ride through via ``extra``
    so authoring a ``header_trailer_sum`` overlay (which needs ``sum_of``)
    does not require a schema-version bump.
    """
    wb_path = _build_synthetic_workbook(
        tmp_path,
        output_files_rows=[
            {
                "file_type": "SUM",
                "glob": "sum_*.txt",
                "mapping_sheet": "(umbrella)",
                "rules_sheet": "(umbrella)",
                "tolerance_max_errors": None,
                "tolerance_max_error_pct": None,
                "tolerance_ignore_fields": None,
            },
        ],
        multi_record_sheets={
            "MultiRecord_SUM": [
                {
                    "record_type_name": "batch_header",
                    "discriminator_field": "TYPE",
                    "discriminator_position": 1,
                    "discriminator_length": 3,
                    "match_kind": "position_first",
                    "match_value": "first",
                    "mapping_sheet": "SUM_HDR_Mapping",
                    "rules_sheet": "",
                    "cardinality": "one_per_driver_row",
                },
            ],
        },
        mapping_sheets={
            "SUM_HDR_Mapping": [
                {
                    "Field Name": "TYPE",
                    "Data Type": "String",
                    "Position": 1,
                    "Length": 3,
                    "Required": "Yes",
                }
            ],
        },
        cross_type_rules_sheets={
            "CrossTypeRules_SUM": [
                {
                    "rule_id": "CT001",
                    "check": "header_trailer_sum",
                    "record_type": "batch_header",
                    "trailer_field": "TOTAL",
                    "count_of": "",
                    "allow_empty_batch": "false",
                    "severity": "error",
                    "message": "sum mismatch",
                    "enabled": "true",
                    "header_field": "HDR-TOTAL",
                    "sum_of": "AMT_A|AMT_B|AMT_C",
                },
            ],
        },
    )

    workbook = read_workbook(wb_path)
    artefacts = emit_mapping_artefacts(workbook)
    umbrella = next(
        a for a in artefacts if a.path == "config/mappings/TEST_SUM.yaml"
    )
    data = yaml.safe_load(umbrella.content)
    rule = data["cross_type_rules"][0]

    assert rule["check"] == "header_trailer_sum"
    assert rule["header_field"] == "HDR-TOTAL"
    # sum_of split from pipe-list to YAML array (engine expects a list).
    assert rule["sum_of"] == ["AMT_A", "AMT_B", "AMT_C"]
    # The umbrella still loads cleanly through MultiRecordConfig.
    MultiRecordConfig.model_validate(data)
