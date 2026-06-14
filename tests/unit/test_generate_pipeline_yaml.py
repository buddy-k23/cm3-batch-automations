"""Unit tests for ``scripts.generate_pipeline_yaml``.

Covers the M3 deliverable from ``prompts/e2e_batch_testing_prompt.md``.

The headline test is a **golden-file** comparison: building the pipeline
dict for SRC_A in env ``sit`` from the on-disk config must produce a
byte-identical match to ``tests/fixtures/e2e_pipelines/SRC_A.sit.golden.yaml``.

This guarantees:
  * Any future change to the generator that affects the emitted YAML is a
    visible diff to the golden file, forcing reviewers to look at it.
  * The emitted YAML round-trips through Valdo's real ``PipelineDefinition``
    pydantic model (validated separately below).

Additional tests cover:
  * Error handling for malformed/incomplete source configs.
  * Threshold-block normalization (missing values default to ``-1``).
  * Generator idempotency (running twice yields the same bytes).
  * The ``--check`` CLI mode detects drift without writing.
"""

from __future__ import annotations

import shutil
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.e2e_lib.path_resolver import PathResolver  # noqa: E402
from scripts.generate_pipeline_yaml import (  # noqa: E402
    PipelineGenerationError,
    build_pipeline_dict,
    main,
    render_yaml,
    write_pipeline_file,
)


_PATHS_YAML = _REPO_ROOT / "config" / "e2e" / "paths.yml"
_SOURCES_DIR = _REPO_ROOT / "config" / "e2e" / "sources"
_GOLDEN_YAML = (
    _REPO_ROOT / "tests" / "fixtures" / "e2e_pipelines" / "SRC_A.sit.golden.yaml"
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def real_resolver() -> PathResolver:
    """Resolver loaded from the actual repo config (the golden file's source)."""
    return PathResolver.from_files(_PATHS_YAML, _SOURCES_DIR)


# --------------------------------------------------------------------------- #
# Golden-file test
# --------------------------------------------------------------------------- #


class TestGoldenFile:
    def test_src_a_sit_matches_golden(self, real_resolver: PathResolver) -> None:
        """Generated SRC_A/sit YAML must match the committed golden byte-for-byte."""
        assert _GOLDEN_YAML.is_file(), (
            f"golden file missing: {_GOLDEN_YAML}. "
            "Regenerate via: python scripts/generate_pipeline_yaml.py "
            "--source SRC_A --env sit --stdout > "
            f"{_GOLDEN_YAML.relative_to(_REPO_ROOT)}"
        )
        pipeline = build_pipeline_dict(
            env="sit", source="SRC_A", resolver=real_resolver
        )
        produced = render_yaml(pipeline)
        expected = _GOLDEN_YAML.read_text(encoding="utf-8")
        assert produced == expected, (
            "Pipeline YAML drifted from golden. Inspect the diff and, if the "
            "change is intentional, regenerate the golden file."
        )

    def test_generated_yaml_round_trips_through_pipeline_definition(
        self, real_resolver: PathResolver
    ) -> None:
        """The emitted YAML must validate against Valdo's PipelineDefinition.

        This catches schema drift between the generator and Valdo internals
        (which the prompt forbids us to modify, so the generator is the side
        that must adjust).
        """
        from src.pipeline.etl_config import PipelineDefinition

        pipeline = build_pipeline_dict(
            env="sit", source="SRC_A", resolver=real_resolver
        )
        # Round-trip via YAML to mirror what valdo run-etl-pipeline actually does.
        parsed = yaml.safe_load(render_yaml(pipeline))
        validated = PipelineDefinition.model_validate(parsed)
        assert validated.name == "e2e_sit_SRC_A"
        assert {g.name for g in validated.gates} == {
            "file_to_staging",
            "L1_structural",
            "L3_baseline_diff",
        }
        # L2_regeneration was retired (ADR 0012); it must not be emitted.
        assert "L2_regeneration" not in {g.name for g in validated.gates}
        # Exactly 3 input + 6 output SourceDefinitions for SRC_A.
        assert len(validated.sources) == 9


# --------------------------------------------------------------------------- #
# Structural assertions on the generated dict
# --------------------------------------------------------------------------- #


class TestPipelineShape:
    def test_no_unresolved_placeholders_survive(
        self, real_resolver: PathResolver
    ) -> None:
        """No {source.…} or other unresolved placeholders should remain.

        After the L2_regeneration retirement (ADR 0012), the only gate that
        carried a deferred ``{run_id}`` token (its per-run regenerated-file
        path) is gone, so every emitted path is now fully concrete. ``{run_id}``
        substitution remains supported by the runner, but the generated SRC_A
        pipeline no longer emits it.
        """
        pipeline = build_pipeline_dict(
            env="sit", source="SRC_A", resolver=real_resolver
        )
        text = render_yaml(pipeline)
        assert "{" not in text and "}" not in text, (
            "Unresolved placeholder found in YAML."
        )

    def test_no_run_id_in_step_paths(
        self, real_resolver: PathResolver
    ) -> None:
        """``{run_id}`` no longer appears in any emitted step path.

        L1 reads the Java output (static) and L3 reads the pinned baseline
        (static). The only per-run path was L2's regenerated file, retired in
        ADR 0012.
        """
        pipeline = build_pipeline_dict(
            env="sit", source="SRC_A", resolver=real_resolver
        )
        assert "L2_regeneration" not in {g["name"] for g in pipeline["gates"]}
        for gate in pipeline["gates"]:
            for step in gate["steps"]:
                for field in ("file", "mapping", "rules", "query"):
                    assert "{run_id}" not in (step.get(field) or "")

    def test_blocking_flags_match_source_config(
        self, real_resolver: PathResolver
    ) -> None:
        pipeline = build_pipeline_dict(
            env="sit", source="SRC_A", resolver=real_resolver
        )
        by_name = {g["name"]: g for g in pipeline["gates"]}
        assert by_name["file_to_staging"]["blocking"] is True
        assert by_name["L1_structural"]["blocking"] is True
        assert "L2_regeneration" not in by_name  # retired (ADR 0012)
        assert by_name["L3_baseline_diff"]["blocking"] is False

    def test_java_shell_out_gates_are_not_emitted(
        self, real_resolver: PathResolver
    ) -> None:
        """``load_step`` and ``generate_step`` are wrapper-script jobs (no YAML)."""
        pipeline = build_pipeline_dict(
            env="sit", source="SRC_A", resolver=real_resolver
        )
        gate_names = {g["name"] for g in pipeline["gates"]}
        assert "load_step" not in gate_names
        assert "generate_step" not in gate_names

    def test_staging_tables_qualified_with_schema(
        self, real_resolver: PathResolver
    ) -> None:
        pipeline = build_pipeline_dict(
            env="sit", source="SRC_A", resolver=real_resolver
        )
        file_to_staging = next(
            g for g in pipeline["gates"] if g["name"] == "file_to_staging"
        )
        for step in file_to_staging["steps"]:
            assert step["query"].startswith("STG_SIT."), (
                f"unqualified staging table reference: {step['query']!r}"
            )


# --------------------------------------------------------------------------- #
# Source-config validation
# --------------------------------------------------------------------------- #


def _write_minimal_paths_yml(tmp_path: Path) -> tuple[Path, Path]:
    """Make a self-contained paths.yml + sources/ for failure-case tests."""
    paths_yaml = tmp_path / "paths.yml"
    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    paths_yaml.write_text(
        textwrap.dedent(
            """
            schema_version: 1
            envs:
              sit:
                input_root:          "/data/sit/{source}/input"
                output_root:         "/data/sit/{source}/output"
                trigger_root:        "/data/sit/{source}/triggers"
                report_root:         "/data/sit/_reports/{run_id}/{source}"
                work_root:           "/data/sit/_work/{run_id}/{source}"
                baseline_root:       "baselines/sit/{source}/{release_tag}"
                log_root:            "logs"
                oracle_dsn_env:      "ORACLE_DSN"
                oracle_user_env:     "ORACLE_USER"
                oracle_password_env: "ORACLE_PASSWORD"
                staging_schema:      "STG_SIT"
                audit_schema:        "AUDIT"
            filename_patterns:
              - name: standard_input
                pattern: '^(?P<source>[A-Z]+)_input_(?P<file_type>[A-Z0-9]+)_\\d+\\.dat$'
                direction: input
              - name: standard_output
                pattern: '^(?P<source>[A-Z]+)_(?P<file_type>P[0-9]+)_\\d+\\.txt$'
                direction: output
            """
        ).strip(),
        encoding="utf-8",
    )
    return paths_yaml, sources_dir


def _write_source(sources_dir: Path, name: str, body: str) -> None:
    (sources_dir / f"{name}.yml").write_text(body, encoding="utf-8")


class TestSourceConfigValidation:
    def test_missing_input_files_raises(self, tmp_path: Path) -> None:
        paths_yaml, sources_dir = _write_minimal_paths_yml(tmp_path)
        _write_source(
            sources_dir,
            "X",
            textwrap.dedent(
                """
                schema_version: 1
                source: X
                release_tag: "R1"
                output_files:
                  - file_type: P1
                    glob: "X_P1_*.txt"
                    mapping: "m.json"
                gates:
                  file_to_staging:  { blocking: true,  invoke_java: false }
                  L1_structural:    { blocking: true,  invoke_java: false }
                  L3_baseline_diff: { blocking: false, invoke_java: false }
                """
            ).strip(),
        )
        resolver = PathResolver.from_files(paths_yaml, sources_dir)
        with pytest.raises(PipelineGenerationError, match="input_files"):
            build_pipeline_dict(env="sit", source="X", resolver=resolver)

    def test_missing_gate_raises(self, tmp_path: Path) -> None:
        paths_yaml, sources_dir = _write_minimal_paths_yml(tmp_path)
        _write_source(
            sources_dir,
            "Y",
            textwrap.dedent(
                """
                schema_version: 1
                source: Y
                release_tag: "R1"
                input_files:
                  - file_type: H
                    glob: "Y_H_*.dat"
                    mapping: "m.json"
                    target_staging_table: "STG_Y_H"
                output_files:
                  - file_type: P1
                    glob: "Y_P1_*.txt"
                    mapping: "m.json"
                gates:
                  file_to_staging:  { blocking: true,  invoke_java: false }
                  L1_structural:    { blocking: true,  invoke_java: false }
                  # L3_baseline_diff deliberately omitted
                """
            ).strip(),
        )
        resolver = PathResolver.from_files(paths_yaml, sources_dir)
        with pytest.raises(PipelineGenerationError, match="L3_baseline_diff"):
            build_pipeline_dict(env="sit", source="Y", resolver=resolver)

    def test_missing_required_field_raises(self, tmp_path: Path) -> None:
        paths_yaml, sources_dir = _write_minimal_paths_yml(tmp_path)
        _write_source(
            sources_dir,
            "Z",
            textwrap.dedent(
                """
                schema_version: 1
                source: Z
                release_tag: "R1"
                input_files:
                  - file_type: H
                    glob: "Z_H_*.dat"
                    # mapping is required and intentionally missing
                    target_staging_table: "STG_Z_H"
                output_files:
                  - file_type: P1
                    glob: "Z_P1_*.txt"
                    mapping: "m.json"
                gates:
                  file_to_staging:  { blocking: true,  invoke_java: false }
                  L1_structural:    { blocking: true,  invoke_java: false }
                  L2_regeneration:  { blocking: false, invoke_java: false }
                  L3_baseline_diff: { blocking: false, invoke_java: false }
                """
            ).strip(),
        )
        resolver = PathResolver.from_files(paths_yaml, sources_dir)
        with pytest.raises(PipelineGenerationError, match="mapping"):
            build_pipeline_dict(env="sit", source="Z", resolver=resolver)

    def test_staging_table_with_explicit_schema_is_preserved(
        self, tmp_path: Path
    ) -> None:
        """When the source already qualifies its staging table, don't double-prefix."""
        paths_yaml, sources_dir = _write_minimal_paths_yml(tmp_path)
        _write_source(
            sources_dir,
            "Q",
            textwrap.dedent(
                """
                schema_version: 1
                source: Q
                release_tag: "R1"
                input_files:
                  - file_type: H
                    glob: "Q_H_*.dat"
                    mapping: "m.json"
                    target_staging_table: "MY_OTHER_SCHEMA.STG_Q_H"
                output_files:
                  - file_type: P1
                    glob: "Q_P1_*.txt"
                    mapping: "m.json"
                gates:
                  file_to_staging:  { blocking: true,  invoke_java: false }
                  L1_structural:    { blocking: true,  invoke_java: false }
                  L2_regeneration:  { blocking: false, invoke_java: false }
                  L3_baseline_diff: { blocking: false, invoke_java: false }
                """
            ).strip(),
        )
        resolver = PathResolver.from_files(paths_yaml, sources_dir)
        pipeline = build_pipeline_dict(env="sit", source="Q", resolver=resolver)
        f2s = next(g for g in pipeline["gates"] if g["name"] == "file_to_staging")
        assert f2s["steps"][0]["query"] == "MY_OTHER_SCHEMA.STG_Q_H"


# --------------------------------------------------------------------------- #
# Idempotency & file I/O
# --------------------------------------------------------------------------- #


class TestWriteBehavior:
    def test_generator_is_idempotent(
        self, tmp_path: Path, real_resolver: PathResolver
    ) -> None:
        pipeline = build_pipeline_dict(
            env="sit", source="SRC_A", resolver=real_resolver
        )
        out = tmp_path / "out.yaml"
        write_pipeline_file(pipeline, out)
        first = out.read_bytes()
        write_pipeline_file(pipeline, out)
        second = out.read_bytes()
        assert first == second

    def test_check_mode_returns_false_when_file_missing(
        self, tmp_path: Path, real_resolver: PathResolver
    ) -> None:
        pipeline = build_pipeline_dict(
            env="sit", source="SRC_A", resolver=real_resolver
        )
        out = tmp_path / "nope.yaml"
        assert write_pipeline_file(pipeline, out, check=True) is False
        assert not out.exists()  # check mode never writes

    def test_check_mode_returns_true_when_file_matches(
        self, tmp_path: Path, real_resolver: PathResolver
    ) -> None:
        pipeline = build_pipeline_dict(
            env="sit", source="SRC_A", resolver=real_resolver
        )
        out = tmp_path / "match.yaml"
        write_pipeline_file(pipeline, out)
        assert write_pipeline_file(pipeline, out, check=True) is True


# --------------------------------------------------------------------------- #
# CLI smoke tests
# --------------------------------------------------------------------------- #


class TestCli:
    def test_cli_writes_expected_file(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "pipelines"
        rc = main(
            [
                "--paths-yaml",
                str(_PATHS_YAML),
                "--sources-dir",
                str(_SOURCES_DIR),
                "--out-dir",
                str(out_dir),
                "--source",
                "SRC_A",
                "--env",
                "sit",
            ]
        )
        assert rc == 0
        produced = out_dir / "sit" / "SRC_A.pipeline.yaml"
        assert produced.is_file()
        assert produced.read_text(encoding="utf-8") == _GOLDEN_YAML.read_text(
            encoding="utf-8"
        )

    def test_cli_check_clean_when_golden_is_in_place(self, tmp_path: Path) -> None:
        """``--check`` over a freshly-generated tree must exit 0."""
        out_dir = tmp_path / "pipelines"
        # Generate then re-check.
        assert (
            main(
                [
                    "--paths-yaml",
                    str(_PATHS_YAML),
                    "--sources-dir",
                    str(_SOURCES_DIR),
                    "--out-dir",
                    str(out_dir),
                    "--source",
                    "SRC_A",
                    "--env",
                    "sit",
                ]
            )
            == 0
        )
        rc = main(
            [
                "--paths-yaml",
                str(_PATHS_YAML),
                "--sources-dir",
                str(_SOURCES_DIR),
                "--out-dir",
                str(out_dir),
                "--source",
                "SRC_A",
                "--env",
                "sit",
                "--check",
            ]
        )
        assert rc == 0

    def test_cli_check_detects_drift(
        self, tmp_path: Path, real_resolver: PathResolver
    ) -> None:
        out_dir = tmp_path / "pipelines"
        out_dir.mkdir()
        env_dir = out_dir / "sit"
        env_dir.mkdir()
        # Pre-populate with a stale, incorrect file.
        (env_dir / "SRC_A.pipeline.yaml").write_text("name: stale\n", encoding="utf-8")
        rc = main(
            [
                "--paths-yaml",
                str(_PATHS_YAML),
                "--sources-dir",
                str(_SOURCES_DIR),
                "--out-dir",
                str(out_dir),
                "--source",
                "SRC_A",
                "--env",
                "sit",
                "--check",
            ]
        )
        assert rc != 0

    def test_cli_rejects_stdout_with_multiple_targets(
        self, tmp_path: Path
    ) -> None:
        # Two sources requested with --stdout should be rejected before any
        # work happens, because stdout can hold only one document cleanly.
        rc = main(
            [
                "--paths-yaml",
                str(_PATHS_YAML),
                "--sources-dir",
                str(_SOURCES_DIR),
                "--source",
                "SRC_A",
                "--source",
                "SRC_A",
                "--env",
                "sit",
                "--stdout",
            ]
        )
        assert rc == 2


# --------------------------------------------------------------------------- #
# Multi-record dispatch (ADR 0005)
# --------------------------------------------------------------------------- #


def _multi_record_source_body(
    *,
    mapping: str,
    rules: str = "",
) -> str:
    """Source-YAML body fixture with one input + one output entry.

    Helper for the ADR 0005 multi-record tests below. Per EB-S1, multi-
    record dispatch is inferred from the ``mapping`` file extension; the
    legacy ``multi_record:`` key is rejected by ``OutputFileConfig`` and
    no longer appears in this fixture. Built line-by-line to keep
    indentation explicit and avoid textwrap.dedent / f-string interaction
    footguns.
    """
    lines = [
        "schema_version: 1",
        "source: M",
        'release_tag: "R1"',
        "input_files:",
        "  - file_type: H",
        '    glob: "M_H_*.dat"',
        '    mapping: "config/mappings/M_H.json"',
        '    target_staging_table: "STG_M_H"',
        "output_files:",
        "  - file_type: P1",
        '    glob: "M_P1_*.txt"',
        f'    mapping: "{mapping}"',
    ]
    if rules:
        lines.append(f'    rules: "{rules}"')
    lines.extend(
        [
            "gates:",
            "  file_to_staging:  { blocking: true,  invoke_java: false }",
            "  L1_structural:    { blocking: true,  invoke_java: false }",
            "  L3_baseline_diff: { blocking: false, invoke_java: false }",
        ]
    )
    return "\n".join(lines) + "\n"


class TestMultiRecordDispatch:
    """ADR 0005: pipeline-YAML dispatch for multi-record output files."""

    def test_l1_emits_validate_multi_record_for_umbrella(
        self, tmp_path: Path
    ) -> None:
        """``.yaml`` mapping -> L1 step is ``validate_multi_record``.

        Multi-record dispatch is inferred from the mapping extension per
        EB-S1 / ADR 0005; no explicit flag is supplied (or accepted).
        """
        paths_yaml, sources_dir = _write_minimal_paths_yml(tmp_path)
        _write_source(
            sources_dir,
            "M",
            _multi_record_source_body(
                mapping="config/mappings/M_P1.yaml",
            ),
        )
        resolver = PathResolver.from_files(paths_yaml, sources_dir)
        pipeline = build_pipeline_dict(env="sit", source="M", resolver=resolver)

        l1 = next(g for g in pipeline["gates"] if g["name"] == "L1_structural")
        assert len(l1["steps"]) == 1
        step = l1["steps"][0]
        assert step["type"] == "validate_multi_record"
        assert step["mapping"] == "config/mappings/M_P1.yaml"
        # Per ADR 0005, per-record rules live in the umbrella; the step
        # must not carry a separate rules path even if the source has one.
        assert step["rules"] == ""

    def test_l3_remains_compare_step_for_umbrella(
        self, tmp_path: Path
    ) -> None:
        """Multi-record dispatch only changes L1; L3 stays as ``compare``.

        (L2_regeneration was retired in ADR 0012 and must not be emitted.)
        """
        paths_yaml, sources_dir = _write_minimal_paths_yml(tmp_path)
        _write_source(
            sources_dir,
            "M",
            _multi_record_source_body(
                mapping="config/mappings/M_P1.yaml",
            ),
        )
        resolver = PathResolver.from_files(paths_yaml, sources_dir)
        pipeline = build_pipeline_dict(env="sit", source="M", resolver=resolver)

        by_name = {g["name"]: g for g in pipeline["gates"]}
        assert "L2_regeneration" not in by_name
        assert by_name["L3_baseline_diff"]["steps"][0]["type"] == "compare"

    def test_single_record_entries_still_emit_plain_validate(
        self, tmp_path: Path
    ) -> None:
        """``.json`` mapping keeps the current ``validate`` shape.

        Inferred single-record dispatch per EB-S1 / ADR 0005.
        """
        paths_yaml, sources_dir = _write_minimal_paths_yml(tmp_path)
        _write_source(
            sources_dir,
            "M",
            _multi_record_source_body(
                mapping="config/mappings/M_P1.json",
                rules="config/rules/M_P1.json",
            ),
        )
        resolver = PathResolver.from_files(paths_yaml, sources_dir)
        pipeline = build_pipeline_dict(env="sit", source="M", resolver=resolver)

        l1 = next(g for g in pipeline["gates"] if g["name"] == "L1_structural")
        step = l1["steps"][0]
        assert step["type"] == "validate"
        assert step["mapping"] == "config/mappings/M_P1.json"
        # Single-record path preserves the per-file rules wiring.
        assert step["rules"] == "config/rules/M_P1.json"

    def test_legacy_multi_record_key_rejected_at_source_validation(
        self, tmp_path: Path
    ) -> None:
        """Per EB-S1 the legacy ``multi_record:`` key is rejected by the
        ``OutputFileConfig`` model validator. EA-S2 wires
        :class:`SourceConfig` into the generator, so the rejection now
        propagates as a :class:`PipelineGenerationError` mentioning the
        ADR-cited error message.

        This supersedes the pre-EA-S2
        ``test_umbrella_yaml_without_multi_record_flag_raises`` test, which
        relied on the generator's own duplicate extension-mismatch check
        (now retired -- the model is the only source of truth).
        """
        paths_yaml, sources_dir = _write_minimal_paths_yml(tmp_path)
        # Hand-roll a source body that still carries the deprecated key.
        body = (
            "schema_version: 1\n"
            "source: M\n"
            'release_tag: "R1"\n'
            "input_files:\n"
            "  - file_type: H\n"
            '    glob: "M_H_*.dat"\n'
            '    mapping: "config/mappings/M_H.json"\n'
            '    target_staging_table: "STG_M_H"\n'
            "output_files:\n"
            "  - file_type: P1\n"
            '    glob: "M_P1_*.txt"\n'
            '    mapping: "config/mappings/M_P1.yaml"\n'
            "    multi_record: true\n"
            "gates:\n"
            "  file_to_staging:  { blocking: true,  invoke_java: false }\n"
            "  L1_structural:    { blocking: true,  invoke_java: false }\n"
            "  L3_baseline_diff: { blocking: false, invoke_java: false }\n"
        )
        _write_source(sources_dir, "M", body)
        resolver = PathResolver.from_files(paths_yaml, sources_dir)
        with pytest.raises(PipelineGenerationError, match="multi_record"):
            build_pipeline_dict(env="sit", source="M", resolver=resolver)

    def test_yml_extension_is_accepted_for_umbrella(
        self, tmp_path: Path
    ) -> None:
        """``.yml`` is treated identically to ``.yaml`` for umbrella detection."""
        paths_yaml, sources_dir = _write_minimal_paths_yml(tmp_path)
        _write_source(
            sources_dir,
            "M",
            _multi_record_source_body(
                mapping="config/mappings/M_P1.yml",
            ),
        )
        resolver = PathResolver.from_files(paths_yaml, sources_dir)
        pipeline = build_pipeline_dict(env="sit", source="M", resolver=resolver)
        l1 = next(g for g in pipeline["gates"] if g["name"] == "L1_structural")
        assert l1["steps"][0]["type"] == "validate_multi_record"
