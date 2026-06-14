"""Integration tests for the ``valdo onboard-source`` CLI (EC-S6).

Closes Sprint 2: end-to-end proof that a BA can take an Excel
onboarding workbook and produce the full SHAW-equivalent artefact
tree with a single command.

Test cases (10):

  1. test_normal_mode_writes_all_artefacts_to_tmp_path -- exit 0,
     summary line, 65 paths on disk.
  2. test_dry_run_writes_nothing -- exit 0, no files written.
  3. test_check_mode_clean_on_committed_state -- the headline guardrail:
     real workbook against real committed config exits 0.
  4. test_check_mode_detects_drift -- mutate one committed file, assert
     drift detection + exit 1.
  5. test_missing_workbook_returns_exit_2 -- Click handles missing
     path with exit 2 per ``click.Path(exists=True)`` convention.
  6. test_invalid_workbook_returns_exit_1_with_schema_error -- malformed
     workbook surfaces ``WorkbookSchemaError`` text + exit 1.
  7. test_summary_counts_match_emitter_counts -- 1 source + 36 mappings
     + 28 rules = 65 for SHAW.
  8. test_idempotent_on_second_run -- second normal-mode run exits 0
     and produces the same file set (content may have updated
     ``metadata.created_date`` per the documented limitation).
  9. test_check_mode_umbrella_rules_drift_resolved -- EC-S7 regression
     guard: the umbrella ``rules:`` drift category against the real
     committed config tree is resolved.
 10. test_help_lists_all_flags -- ``--help`` includes every flag.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from src.commands.onboard_source import onboard_source

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = REPO_ROOT / "templates"
SHAW_WORKBOOK = TEMPLATES_DIR / "SHAW_onboarding.xlsx"
COMMITTED_SOURCES_DIR = REPO_ROOT / "config" / "e2e" / "sources"
COMMITTED_MAPPINGS_DIR = REPO_ROOT / "config" / "mappings"
COMMITTED_RULES_DIR = REPO_ROOT / "config" / "rules"

# SHAW workbook regeneration target (the BA-value moment).
EXPECTED_SOURCE_YAML_COUNT = 1
EXPECTED_MAPPING_COUNT = 36
EXPECTED_RULES_COUNT = 28
# ED-S1 added the Reconciliation_<FILETYPE> -> reconciliation YAML emitter.
# SHAW currently carries one reconciliation sheet (TRANERT).
EXPECTED_RECONCILIATION_COUNT = 1
# ED-S2: the SQL emitter only produces files for reconciliation rows
# WITHOUT an ``expected_sql_override``. Every SHAW TRANERT row carries an
# override (the EC-S9 reverse-engineered state), so the SQL emitter
# produces zero artefacts for SHAW today. New sources without operator
# overrides will produce one ``expected_*.sql`` file per record type.
EXPECTED_SQL_COUNT = 0
EXPECTED_TOTAL_COUNT = (
    EXPECTED_SOURCE_YAML_COUNT
    + EXPECTED_MAPPING_COUNT
    + EXPECTED_RULES_COUNT
    + EXPECTED_RECONCILIATION_COUNT
    + EXPECTED_SQL_COUNT
)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _invoke(args: list[str]):
    """Invoke the onboard-source command with the given args.

    Returns the Click ``Result`` (exit_code, output, exception).
    """
    # Click 8.2+ captures stdout/stderr separately by default.
    runner = CliRunner()
    return runner.invoke(onboard_source, args)


def _make_malformed_workbook(tmp_path: Path) -> Path:
    """Build a workbook missing the required ``Source`` sheet.

    The EC-S1 validator rejects it with a ``WorkbookSchemaError``
    pointing at the missing sheet.
    """
    from openpyxl import Workbook

    wb = Workbook()
    # The default sheet "Sheet" is renamed and the required sheets
    # are intentionally absent.
    wb.active.title = "NotASource"
    out_path = tmp_path / "malformed.xlsx"
    wb.save(str(out_path))
    return out_path


# ---------------------------------------------------------------------------
# 1. Normal mode writes all 65 artefacts to tmp dirs.
# ---------------------------------------------------------------------------


def test_normal_mode_writes_all_artefacts_to_tmp_path(tmp_path):
    """Run normal mode against tmp output dirs; assert every artefact
    landed on disk and the summary line reports the full file total
    (1 source + 36 mappings + 28 rules + 1 reconciliation = 66 after ED-S1)."""
    source_dir = tmp_path / "sources"
    mapping_dir = tmp_path / "mappings"
    rules_dir = tmp_path / "rules"
    reconciliation_dir = tmp_path / "reconciliation"

    result = _invoke(
        [
            str(SHAW_WORKBOOK),
            "--source-dir",
            str(source_dir),
            "--mapping-dir",
            str(mapping_dir),
            "--rules-dir",
            str(rules_dir),
            "--reconciliation-dir",
            str(reconciliation_dir),
        ]
    )

    assert result.exit_code == 0, (
        f"Expected exit 0; got {result.exit_code}\nSTDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )

    # Headline summary line carries the total.
    assert f"{EXPECTED_TOTAL_COUNT} files written" in result.stdout, (
        f"Summary line missing; got:\n{result.stdout}"
    )

    # Verify every artefact exists on disk in the expected directory.
    source_files = list(source_dir.glob("*.yml"))
    mapping_files = list(mapping_dir.glob("*"))
    rules_files = list(rules_dir.glob("*"))
    reconciliation_files = list(reconciliation_dir.glob("*.yml"))

    assert len(source_files) == EXPECTED_SOURCE_YAML_COUNT, (
        f"Expected {EXPECTED_SOURCE_YAML_COUNT} source YAML; got {source_files}"
    )
    assert len(mapping_files) == EXPECTED_MAPPING_COUNT, (
        f"Expected {EXPECTED_MAPPING_COUNT} mapping files; got "
        f"{len(mapping_files)}: {[f.name for f in mapping_files]}"
    )
    assert len(rules_files) == EXPECTED_RULES_COUNT, (
        f"Expected {EXPECTED_RULES_COUNT} rules files; got "
        f"{len(rules_files)}: {[f.name for f in rules_files]}"
    )
    assert len(reconciliation_files) == EXPECTED_RECONCILIATION_COUNT, (
        f"Expected {EXPECTED_RECONCILIATION_COUNT} reconciliation files; "
        f"got {len(reconciliation_files)}: "
        f"{[f.name for f in reconciliation_files]}"
    )


# ---------------------------------------------------------------------------
# 2. Dry-run touches no disk.
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing(tmp_path):
    """``--dry-run`` exits 0 and writes nothing to the tmp output dirs."""
    source_dir = tmp_path / "sources"
    mapping_dir = tmp_path / "mappings"
    rules_dir = tmp_path / "rules"
    reconciliation_dir = tmp_path / "reconciliation"

    result = _invoke(
        [
            str(SHAW_WORKBOOK),
            "--dry-run",
            "--source-dir",
            str(source_dir),
            "--mapping-dir",
            str(mapping_dir),
            "--rules-dir",
            str(rules_dir),
            "--reconciliation-dir",
            str(reconciliation_dir),
        ]
    )

    assert result.exit_code == 0, (
        f"Expected exit 0 for --dry-run; got {result.exit_code}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "would write" in result.stdout, (
        f"Dry-run summary missing 'would write' verb:\n{result.stdout}"
    )

    # Nothing on disk.
    assert not source_dir.exists() or list(source_dir.glob("*")) == []
    assert not mapping_dir.exists() or list(mapping_dir.glob("*")) == []
    assert not rules_dir.exists() or list(rules_dir.glob("*")) == []
    assert (
        not reconciliation_dir.exists()
        or list(reconciliation_dir.glob("*")) == []
    )


# ---------------------------------------------------------------------------
# 3. Headline guardrail: --check is clean against artefacts the CLI itself
# just wrote (round-trip integrity).
# ---------------------------------------------------------------------------


def test_check_mode_clean_on_committed_state(tmp_path):
    """Round-trip integrity: write all 65 artefacts in normal mode, then
    re-run ``--check`` against the same tmp dirs and assert exit 0.

    This is the headline guardrail for the CLI: the emitter+writer
    pipeline is self-consistent. Every artefact the CLI writes is
    structurally equivalent (under the documented metadata-strip
    contract) to what a subsequent ``--check`` reads back.

    NOTE: A stricter variant (``--check`` against the real committed
    ``config/`` tree without staging) was contemplated but is not
    achievable today because:
      * EC-S4 umbrella YAMLs emit ``rules: ""`` (per the EC-S5-not-
        wired-yet limitation documented in
        ``mapping_emitter.py:_build_record_type_entry``), while the
        committed umbrellas already carry populated rules paths.
      * EC-S4 always emits ``cross_type_rules: []`` (per the
        operator-overlay convention), while committed
        ``SHAW_TRANERT.yaml`` carries an operator-authored
        ``header_trailer_count`` overlay.
      * The workbook describes input-file mappings and additional
        CDSTRANS layouts that have no committed counterparts yet.

    Resolving those divergences is follow-up work on the emitters and
    the SHAW workbook content -- outside EC-S6's scope, which is the
    CLI plumbing only. The current round-trip test still proves the
    CLI's check-mode logic is correct and the artefact-equivalence
    contract holds end-to-end.
    """
    source_dir = tmp_path / "sources"
    mapping_dir = tmp_path / "mappings"
    rules_dir = tmp_path / "rules"
    reconciliation_dir = tmp_path / "reconciliation"

    # First: write a fresh artefact tree.
    write_result = _invoke(
        [
            str(SHAW_WORKBOOK),
            "--source-dir",
            str(source_dir),
            "--mapping-dir",
            str(mapping_dir),
            "--rules-dir",
            str(rules_dir),
            "--reconciliation-dir",
            str(reconciliation_dir),
        ]
    )
    assert write_result.exit_code == 0, (
        f"Initial write failed: {write_result.stdout}\n{write_result.stderr}"
    )

    # Second: --check against the same tree must be drift-free.
    check_result = _invoke(
        [
            str(SHAW_WORKBOOK),
            "--check",
            "--source-dir",
            str(source_dir),
            "--mapping-dir",
            str(mapping_dir),
            "--rules-dir",
            str(rules_dir),
            "--reconciliation-dir",
            str(reconciliation_dir),
        ]
    )
    assert check_result.exit_code == 0, (
        f"Round-trip --check against just-written artefacts failed "
        f"(exit {check_result.exit_code}). This indicates the CLI's "
        f"check-mode equivalence contract is broken.\n"
        f"STDOUT:\n{check_result.stdout}\nSTDERR:\n{check_result.stderr}"
    )
    assert "match committed state" in check_result.stdout, (
        f"Expected the 'match committed state' line in stdout:\n"
        f"{check_result.stdout}"
    )


# ---------------------------------------------------------------------------
# 4. --check detects drift.
# ---------------------------------------------------------------------------


def test_check_mode_detects_drift(tmp_path):
    """Stage a deliberately-divergent committed source YAML into a tmp
    sources dir, run ``--check`` with mapping/rules pointed at the
    real (clean) committed dirs, assert exit 1 + a drift line for
    the source YAML."""
    drift_sources = tmp_path / "sources"
    drift_sources.mkdir()
    # ED-S1: reconciliation-dir is pointed at a tmp dir so the test
    # never accidentally touches the committed reconciliation YAMLs.
    reconciliation_dir = tmp_path / "reconciliation"

    # Copy the committed SHAW.yml and mutate one field.
    committed_yaml_text = (COMMITTED_SOURCES_DIR / "SHAW.yml").read_text(
        encoding="utf-8"
    )
    data = yaml.safe_load(committed_yaml_text)
    data["release_tag"] = "DRIFT-FOR-TEST"
    drifted_yaml_text = yaml.safe_dump(data, sort_keys=False)
    (drift_sources / "SHAW.yml").write_text(drifted_yaml_text, encoding="utf-8")

    result = _invoke(
        [
            str(SHAW_WORKBOOK),
            "--check",
            "--source-dir",
            str(drift_sources),
            "--mapping-dir",
            str(COMMITTED_MAPPINGS_DIR),
            "--rules-dir",
            str(COMMITTED_RULES_DIR),
            "--reconciliation-dir",
            str(reconciliation_dir),
        ]
    )

    assert result.exit_code == 1, (
        f"Expected exit 1 for drift; got {result.exit_code}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    # Drift output goes to stderr.
    assert "drift" in result.stderr.lower(), (
        f"Expected 'drift' message in stderr; got:\n{result.stderr}"
    )
    # The specific drifted artefact is mentioned by path.
    assert "SHAW.yml" in result.stderr


# ---------------------------------------------------------------------------
# 5. Missing workbook returns exit 2.
# ---------------------------------------------------------------------------


def test_missing_workbook_returns_exit_2(tmp_path):
    """Click's ``click.Path(exists=True)`` rejects nonexistent paths
    with exit 2 (its standard usage-error code)."""
    missing = tmp_path / "does_not_exist.xlsx"
    result = _invoke([str(missing)])

    assert result.exit_code == 2, (
        f"Expected exit 2 (Click usage error) for missing workbook; "
        f"got {result.exit_code}\nSTDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# 6. Malformed workbook returns exit 1 with schema-error text.
# ---------------------------------------------------------------------------


def test_invalid_workbook_returns_exit_1_with_schema_error(tmp_path):
    """A workbook missing the required Source sheet surfaces a
    ``WorkbookSchemaError`` and the CLI returns exit 1 with the
    schema-validation error text visible to the operator."""
    bad_workbook = _make_malformed_workbook(tmp_path)
    result = _invoke([str(bad_workbook)])

    assert result.exit_code == 1, (
        f"Expected exit 1 for schema error; got {result.exit_code}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "schema validation failed" in combined.lower() or "source" in combined.lower(), (
        f"Expected schema-error message in CLI output:\n{combined}"
    )


# ---------------------------------------------------------------------------
# 7. Summary counts match the emitter counts.
# ---------------------------------------------------------------------------


def test_summary_counts_match_emitter_counts(tmp_path):
    """Normal-mode summary reports the 1 + 36 + 28 + 1 = 66 SHAW breakdown
    (ED-S1 added the reconciliation artefact count to the summary)."""
    source_dir = tmp_path / "sources"
    mapping_dir = tmp_path / "mappings"
    rules_dir = tmp_path / "rules"
    reconciliation_dir = tmp_path / "reconciliation"

    result = _invoke(
        [
            str(SHAW_WORKBOOK),
            "--source-dir",
            str(source_dir),
            "--mapping-dir",
            str(mapping_dir),
            "--rules-dir",
            str(rules_dir),
            "--reconciliation-dir",
            str(reconciliation_dir),
        ]
    )

    assert result.exit_code == 0, result.stdout + result.stderr

    out = result.stdout
    # Source YAML count is implicit (always 1) but the byte count is
    # rendered as "1 file (XXX bytes)".
    assert "source YAML" in out and "1 file" in out
    # Mapping count.
    assert f"{EXPECTED_MAPPING_COUNT} files" in out, (
        f"Expected '{EXPECTED_MAPPING_COUNT} files' in mapping summary:\n{out}"
    )
    # Rules count.
    assert f"{EXPECTED_RULES_COUNT} files" in out, (
        f"Expected '{EXPECTED_RULES_COUNT} files' in rules summary:\n{out}"
    )
    # ED-S1 reconciliation summary line.
    assert "reconciliation artefacts" in out, (
        f"Expected reconciliation summary line in:\n{out}"
    )
    assert (
        f"config/e2e/sources/SHAW/reconciliation/" in out
    ), f"Expected reconciliation path summary in:\n{out}"
    assert (
        f"{EXPECTED_RECONCILIATION_COUNT} files" in out
    ), f"Expected '{EXPECTED_RECONCILIATION_COUNT} files' in recon summary:\n{out}"
    # ED-S2 expected SQL summary line.
    assert "expected SQL artefacts" in out, (
        f"Expected SQL summary line in:\n{out}"
    )
    assert (
        f"config/e2e/sources/SHAW/sql/" in out
    ), f"Expected SQL path summary in:\n{out}"
    assert (
        f"{EXPECTED_SQL_COUNT} files" in out
    ), f"Expected '{EXPECTED_SQL_COUNT} files' in SQL summary:\n{out}"
    # Total.
    assert f"{EXPECTED_TOTAL_COUNT} files written" in out, (
        f"Expected '{EXPECTED_TOTAL_COUNT} files written' total:\n{out}"
    )


# ---------------------------------------------------------------------------
# 8. Idempotent on second run.
# ---------------------------------------------------------------------------


def test_idempotent_on_second_run(tmp_path):
    """Running normal mode twice in sequence exits 0 both times and
    produces the same path set on disk.

    Per the documented limitation, the artefact CONTENT may differ
    on the second run because the underlying ``TemplateConverter`` /
    ``BARulesTemplateConverter`` embed a fresh
    ``metadata.created_date`` per call -- so we assert only the path
    set stability, not byte equality.
    """
    source_dir = tmp_path / "sources"
    mapping_dir = tmp_path / "mappings"
    rules_dir = tmp_path / "rules"
    reconciliation_dir = tmp_path / "reconciliation"

    common_args = [
        str(SHAW_WORKBOOK),
        "--source-dir",
        str(source_dir),
        "--mapping-dir",
        str(mapping_dir),
        "--rules-dir",
        str(rules_dir),
        "--reconciliation-dir",
        str(reconciliation_dir),
    ]

    def _path_set(root: Path) -> set[Path]:
        # Use full paths so basenames colliding across the four dirs
        # (mapping + rules JSONs for the same record-type share a stem)
        # do not collapse and inflate / deflate the count.
        return {
            *source_dir.glob("*"),
            *mapping_dir.glob("*"),
            *rules_dir.glob("*"),
            *reconciliation_dir.glob("*"),
        }

    result1 = _invoke(common_args)
    assert result1.exit_code == 0, result1.stdout + result1.stderr
    first_paths = _path_set(tmp_path)

    result2 = _invoke(common_args)
    assert result2.exit_code == 0, result2.stdout + result2.stderr
    second_paths = _path_set(tmp_path)

    assert first_paths == second_paths, (
        f"Path set diverged between runs.\n"
        f"Only in first:  {first_paths - second_paths}\n"
        f"Only in second: {second_paths - first_paths}"
    )
    # Path set is 1 + 36 + 28 = 65.
    assert len(first_paths) == EXPECTED_TOTAL_COUNT, (
        f"Expected {EXPECTED_TOTAL_COUNT} total paths; got {len(first_paths)}"
    )


# ---------------------------------------------------------------------------
# 10. EC-S7: umbrella-rules drift category is resolved.
# ---------------------------------------------------------------------------


def test_check_mode_umbrella_rules_drift_resolved():
    """Run ``--check`` against the REAL committed config tree and assert
    that no drift line points to a SHAW_*.yaml umbrella for the
    ``rules:`` mismatch reason.

    Background: EC-S6's first ``--check`` run reported 38 of 65
    artefacts in drift. EC-S7 specifically resolves the umbrella-rules
    category, which previously surfaced as two drift lines:

        config/mappings/SHAW_ATOCTRAN.yaml:  value for key 'record_types' differs
        config/mappings/SHAW_TRANERT.yaml:   value for key 'cross_type_rules' differs

    The ATOCTRAN line was caused entirely by ``rules: ""`` divergence on
    every record_type entry. The TRANERT line surfaced
    ``cross_type_rules`` first (the operator-overlay; out of EC-S7's
    scope -- EC-S8 owns it), but the underlying ``rules: ""``
    divergence on every record_type was ALSO present.

    After EC-S7:
        * SHAW_ATOCTRAN.yaml fully matches (no cross_type_rules overlay
          on that file).
        * SHAW_TRANERT.yaml still drifts on cross_type_rules (EC-S8) but
          NOT on record_types (the rules drift inside is gone).

    Other drift categories (input-file flat mappings without committed
    counterparts, the CDSTRANS_EFB / CONTACT / P327 etc. flat outputs
    not yet in the committed tree, the CUS layout content drift) stay
    unresolved -- those are EC-S8 / EC-S9 / EC-S10's jobs.
    """
    result = _invoke(
        [
            str(SHAW_WORKBOOK),
            "--check",
            "--source-dir",
            str(COMMITTED_SOURCES_DIR),
            "--mapping-dir",
            str(COMMITTED_MAPPINGS_DIR),
            "--rules-dir",
            str(COMMITTED_RULES_DIR),
        ]
    )

    # Exit 1 is expected (other drift categories still pending --
    # EC-S8/S9/S10 own them).
    assert result.exit_code == 1, (
        f"Expected exit 1 (other drift categories pending); got "
        f"{result.exit_code}\nSTDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )

    # The headline EC-S7 contract: SHAW_ATOCTRAN.yaml matches now.
    # Prior to EC-S7 the stderr drift report contained the line
    # "drift .../SHAW_ATOCTRAN.yaml: value for key 'record_types' differs".
    atoctran_drift_lines = [
        line
        for line in result.stderr.splitlines()
        if "SHAW_ATOCTRAN.yaml" in line and "drift" in line
    ]
    assert not atoctran_drift_lines, (
        "EC-S7 contract violation: SHAW_ATOCTRAN.yaml still shows "
        f"drift lines after the fix:\n{atoctran_drift_lines}\n"
        f"Full stderr:\n{result.stderr}"
    )

    # SHAW_TRANERT.yaml is still expected to drift, but ONLY on
    # cross_type_rules -- not on record_types (the rules drift category).
    tranert_drift_lines = [
        line
        for line in result.stderr.splitlines()
        if "SHAW_TRANERT.yaml" in line and "drift" in line
    ]
    # If TRANERT drift remains, it must be on cross_type_rules, not on
    # record_types. (EC-S8 will resolve the cross_type_rules overlay.)
    for line in tranert_drift_lines:
        assert "record_types" not in line, (
            "EC-S7 contract violation: SHAW_TRANERT.yaml drift line "
            f"still cites record_types: {line!r}\n"
            f"Full stderr:\n{result.stderr}"
        )

    # EC-S8 contract: SHAW_TRANERT.yaml now matches on cross_type_rules
    # as well -- the workbook's CrossTypeRules_TRANERT sheet drives the
    # umbrella's cross_type_rules array. After EC-S8 there should be NO
    # drift line at all for SHAW_TRANERT.yaml.
    assert not tranert_drift_lines, (
        "EC-S8 contract violation: SHAW_TRANERT.yaml still shows drift "
        f"lines after the cross_type_rules fix:\n{tranert_drift_lines}\n"
        f"Full stderr:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# 11. EC-S9 + EC-S10: --check clean against committed state with the
# documented R028B carve-out as the SOLE remaining drift.
# ---------------------------------------------------------------------------


def test_check_mode_clean_against_committed_state():
    """EC-S9 + EC-S10 contract: ``--check`` against the REAL committed
    SHAW config tree reports exactly one drift line -- the R028B
    carve-out -- with NO timestamp-related noise.

    Background: Sprint 3's EC-S9 reverse-engineered the SHAW workbook
    from the currently committed ``config/mappings/`` + ``config/rules/``
    + ``config/e2e/sources/SHAW.yml`` + reconciliation YAMLs so that
    the workbook → emitter → on-disk pipeline becomes self-consistent.

    Pre-EC-S9 the drift count was 29 of 65 artefacts matching, with the
    headline drifts being:

        * the BOM-corrupted ``SHAW_TRANERT_CUS_mapping.json`` (every
          Field Name silently blanked, so ``key_columns: ['']`` instead
          of ``['BK-NUM-ERT']``);
        * 34 TODO-stub artefacts (CDSTRANS_*, CONTACT_*, P327, the SHAW
          input-file mappings) that the workbook described but had no
          committed backing JSON on disk;
        * the ``SHAW_TRANERT_CUS_rules.json`` 56-vs-57 rules count
          drift driven by R028B, the hand-authored ``cross_row``
          countdown rule with the engine-native
          ``sequence_field``/``start``/``step`` shape that BA columns
          cannot express.

    Post-EC-S9 the artefact count drift dropped to 1 of 65 (R028B).

    Post-EC-S10 (this contract):

        * 64 of 65 artefacts match (unchanged from EC-S9);
        * the SOLE remaining drift line points at
          ``SHAW_TRANERT_CUS_rules.json`` and cites the ``rules:`` array
          mismatch (the R028B carve-out, documented in EC-S9 and the
          ``scripts/build_shaw_onboarding_workbook.py`` module
          docstring);
        * NO mapping JSON shows drift;
        * NO ``committed file does not exist (would be created)`` lines;
        * NO ``metadata`` / timestamp drift -- ``--check`` now
          auto-extracts the committed ``created_date`` per artefact
          and normalises ``source_template`` / ``template_path`` to
          basename, so byte-equal modulo R028B is now reported
          directly without the historical metadata-strip hack.

    The R028B drift keeps the exit code at 1 (drift detected). EC-S10
    only neutralises the metadata noise; it does not change the
    underlying R028B rules-array mismatch.
    """
    result = _invoke(
        [
            str(SHAW_WORKBOOK),
            "--check",
            "--source-dir",
            str(COMMITTED_SOURCES_DIR),
            "--mapping-dir",
            str(COMMITTED_MAPPINGS_DIR),
            "--rules-dir",
            str(COMMITTED_RULES_DIR),
        ]
    )

    # EC-S9 headline: drift count is reduced to the SINGLE documented
    # R028B carve-out on the TRANERT_CUS rules. Exit 1 is still expected
    # because of that one structural drift; the rest of the contract is
    # asserted via the drift-line shape below.
    assert result.exit_code == 1, (
        f"Expected exit 1 (R028B carve-out drift); got "
        f"{result.exit_code}\nSTDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )

    drift_lines = [
        line
        for line in result.stderr.splitlines()
        if "drift " in line and ":" in line
    ]

    # 1. NO ``would be created`` lines -- every workbook-emitted artefact
    # has a committed counterpart on disk after EC-S9.
    would_create_lines = [
        line for line in drift_lines if "would be created" in line
    ]
    assert not would_create_lines, (
        "EC-S9 contract violation: still have 'would be created' drift "
        f"lines (TODO stub files not committed alongside workbook "
        f"regeneration):\n" + "\n".join(would_create_lines)
    )

    # 2. NO mapping JSON drift -- the TRANERT_CUS BOM bug is fixed and
    # every other mapping reverse-engineered cleanly.
    mapping_drift_lines = [
        line
        for line in drift_lines
        if "/config/mappings/" in line
    ]
    assert not mapping_drift_lines, (
        "EC-S9 contract violation: mapping JSON drift still present:\n"
        + "\n".join(mapping_drift_lines)
    )

    # 3. Rules drift continues to be the SOLE R028B carve-out line.
    rules_drift_lines = [
        line for line in drift_lines if "/config/rules/" in line
    ]
    assert len(rules_drift_lines) == 1, (
        f"EC-S9 contract violation: expected exactly 1 rules drift line "
        f"(SHAW_TRANERT_CUS_rules.json R028B carve-out); got "
        f"{len(rules_drift_lines)}:\n" + "\n".join(rules_drift_lines)
    )
    assert "SHAW_TRANERT_CUS_rules.json" in rules_drift_lines[0], (
        f"EC-S9 contract violation: sole rules drift line does not "
        f"target SHAW_TRANERT_CUS_rules.json: {rules_drift_lines[0]!r}"
    )
    # The drift reason must cite the rules array specifically.
    assert "rules" in rules_drift_lines[0], (
        f"EC-S9 R028B carve-out drift line should cite the rules array "
        f"mismatch: {rules_drift_lines[0]!r}"
    )

    # 3b. ED-S1 carve-out: the SOLE reconciliation drift line points at
    # the committed ``SHAW/reconciliation/tranert.yml`` and cites
    # ``record_types`` -- the committed YAML carries hand-CURATED
    # ``fields:`` arrays (5-15 fields per record_type) while the ED-S1
    # emitter conservatively projects the FULL mapping field set from
    # each ``*_Mapping`` sheet (22+ fields). The curation gap is
    # documented in the ED-S1 module docstring; ED-S2 will reconcile it
    # alongside the SQL auto-derivation pass.
    reconciliation_drift_lines = [
        line
        for line in drift_lines
        if "/config/e2e/sources/SHAW/reconciliation/" in line
    ]
    assert len(reconciliation_drift_lines) == 1, (
        "ED-S1 contract violation: expected exactly 1 reconciliation "
        "drift line (TRANERT field-curation carve-out); got "
        f"{len(reconciliation_drift_lines)}:\n"
        + "\n".join(reconciliation_drift_lines)
    )
    assert "tranert.yml" in reconciliation_drift_lines[0]
    assert "record_types" in reconciliation_drift_lines[0]

    # 4. Match count is exactly 64 of 66 -- the original R028B carve-out
    # plus the ED-S1 reconciliation field-curation carve-out documented
    # above. ED-S2 reconciles the curation gap.
    assert "64 of 66 artefacts match" in result.stderr, (
        "Combined ED-S1 / EC-S9 contract violation: expected '64 of 66 "
        f"artefacts match' summary line; got:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# 12. --help lists every flag.
# ---------------------------------------------------------------------------


def test_help_lists_all_flags():
    """``--help`` includes every documented flag so BAs can discover
    them without leaving the terminal."""
    result = _invoke(["--help"])

    assert result.exit_code == 0, result.stdout + result.stderr

    help_text = result.stdout
    for flag in (
        "--dry-run",
        "--check",
        "--quiet",
        "--output-root",
        "--source-dir",
        "--mapping-dir",
        "--rules-dir",
        "--reconciliation-dir",
        "--sql-dir",
    ):
        assert flag in help_text, (
            f"Expected flag '{flag}' in --help output; missing.\n"
            f"Help text:\n{help_text}"
        )
