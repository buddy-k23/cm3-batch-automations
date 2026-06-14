"""Unit tests for ``scripts.e2e_lib.promote_baseline``."""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.e2e_lib.promote_baseline import (  # noqa: E402
    PromoteBaselineError,
    load_manifest,
    main,
    manifest_contains,
    promote,
    resolve_mapping_version,
)


# --------------------------------------------------------------------------- #
# Disk fixtures: a self-contained paths.yml + sources/ + mappings/
# --------------------------------------------------------------------------- #


_PATHS_YAML = textwrap.dedent(
    """
    schema_version: 1
    envs:
      sit:
        input_root:          "{tmp}/data/sit/{{source}}/input"
        output_root:         "{tmp}/data/sit/{{source}}/output"
        trigger_root:        "{tmp}/data/sit/{{source}}/triggers"
        report_root:         "{tmp}/reports/{{run_id}}/{{source}}"
        work_root:           "{tmp}/work/{{run_id}}/{{source}}"
        baseline_root:       "{tmp}/baselines/sit/{{source}}/{{release_tag}}"
        log_root:            "{tmp}/logs"
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
)

_SOURCE_YAML = textwrap.dedent(
    """
    schema_version: 1
    source: SRC_A
    release_tag: "R2026.05"
    input_files:
      - file_type: HEADER
        glob: "SRC_A_input_HEADER_*.dat"
        mapping: "{mapping_header}"
        target_staging_table: "STG_SRC_A_HEADER"
    output_files:
      - file_type: P327
        glob: "SRC_A_P327_*.txt"
        mapping: "{mapping_p327}"
        rules: ""
      - file_type: P328
        glob: "SRC_A_P328_*.txt"
        mapping: "{mapping_p328}"
        rules: ""
    gates:
      load_step:        {{ blocking: true,  invoke_java: false }}
      file_to_staging:  {{ blocking: true,  invoke_java: false }}
      generate_step:    {{ blocking: true,  invoke_java: false }}
      L1_structural:    {{ blocking: true,  invoke_java: false }}
      L3_baseline_diff: {{ blocking: false, invoke_java: false }}
    """
)


@pytest.fixture()
def harness(tmp_path: Path):
    """Build a self-contained promote harness on disk."""
    paths_yaml = tmp_path / "paths.yml"
    paths_yaml.write_text(
        _PATHS_YAML.format(tmp=tmp_path.as_posix()), encoding="utf-8"
    )

    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    mappings_dir = tmp_path / "mappings"
    mappings_dir.mkdir()
    m_p327 = mappings_dir / "SRC_A_P327.json"
    m_p327.write_text(
        json.dumps({"mapping_name": "P327", "version": "1.2.0", "fields": []}),
        encoding="utf-8",
    )
    m_p328 = mappings_dir / "SRC_A_P328.json"
    m_p328.write_text(
        json.dumps({"mapping_name": "P328", "fields": []}),  # no version
        encoding="utf-8",
    )
    m_header = mappings_dir / "SRC_A_input_HEADER.json"
    m_header.write_text(json.dumps({"mapping_name": "H"}), encoding="utf-8")

    (sources_dir / "SRC_A.yml").write_text(
        _SOURCE_YAML.format(
            mapping_header=m_header.as_posix(),
            mapping_p327=m_p327.as_posix(),
            mapping_p328=m_p328.as_posix(),
        ),
        encoding="utf-8",
    )

    # A staged run output dir holding the candidate files.
    run_out = tmp_path / "run_out"
    run_out.mkdir()
    (run_out / "SRC_A_P327_20260514.txt").write_text(
        "p327-bytes\n", encoding="utf-8"
    )
    (run_out / "SRC_A_P328_20260514.txt").write_text(
        "p328-bytes\n", encoding="utf-8"
    )

    manifest = tmp_path / "manifest.json"  # absent at start

    # Promotion policy: allow the test approver used throughout this module.
    policy = tmp_path / "promotion_policy.yml"
    policy.write_text(
        "schema_version: 1\napprovers:\n  - qa@example.com\n",
        encoding="utf-8",
    )

    return {
        "tmp": tmp_path,
        "paths_yaml": paths_yaml,
        "sources_dir": sources_dir,
        "manifest": manifest,
        "run_out": run_out,
        "mappings_dir": mappings_dir,
        "policy": policy,
    }


# --------------------------------------------------------------------------- #
# Mapping-version resolution
# --------------------------------------------------------------------------- #


class TestResolveMappingVersion:
    def test_reads_version_key(self, tmp_path: Path) -> None:
        p = tmp_path / "m.json"
        p.write_text(json.dumps({"version": "2.0.0"}), encoding="utf-8")
        assert resolve_mapping_version(p) == "2.0.0"

    def test_reads_mapping_version_alias(self, tmp_path: Path) -> None:
        p = tmp_path / "m.json"
        p.write_text(json.dumps({"mapping_version": "3.0.0"}), encoding="utf-8")
        assert resolve_mapping_version(p) == "3.0.0"

    def test_missing_version_returns_unversioned(self, tmp_path: Path) -> None:
        p = tmp_path / "m.json"
        p.write_text(json.dumps({"name": "x"}), encoding="utf-8")
        assert resolve_mapping_version(p) == "unversioned"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(PromoteBaselineError, match="not found"):
            resolve_mapping_version(tmp_path / "nope.json")

    def test_malformed_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "m.json"
        p.write_text("not json", encoding="utf-8")
        with pytest.raises(PromoteBaselineError, match="parse"):
            resolve_mapping_version(p)


# --------------------------------------------------------------------------- #
# Manifest helpers
# --------------------------------------------------------------------------- #


class TestManifestIo:
    def test_load_missing_returns_skeleton(self, tmp_path: Path) -> None:
        out = load_manifest(tmp_path / "nope.json")
        assert out == {"schema_version": 1, "baselines": []}

    def test_load_malformed_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("{", encoding="utf-8")
        with pytest.raises(PromoteBaselineError, match="parse"):
            load_manifest(p)

    def test_load_non_object_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "list.json"
        p.write_text("[]", encoding="utf-8")
        with pytest.raises(PromoteBaselineError, match="JSON object"):
            load_manifest(p)


class TestManifestContains:
    def test_detects_existing_pin(self) -> None:
        m = {
            "schema_version": 1,
            "baselines": [
                {"env": "sit", "source": "S", "file_type": "P327",
                 "mapping_version": "1.0", "release_tag": "R1"},
            ],
        }
        entry = {
            "env": "sit", "source": "S", "file_type": "P327",
            "mapping_version": "1.0", "release_tag": "R1",
        }
        assert manifest_contains(m, entry) is True

    def test_misses_different_tag(self) -> None:
        m = {
            "schema_version": 1,
            "baselines": [
                {"env": "sit", "source": "S", "file_type": "P327",
                 "mapping_version": "1.0", "release_tag": "R1"},
            ],
        }
        entry = {
            "env": "sit", "source": "S", "file_type": "P327",
            "mapping_version": "1.0", "release_tag": "R2",
        }
        assert manifest_contains(m, entry) is False


# --------------------------------------------------------------------------- #
# Top-level promote()
# --------------------------------------------------------------------------- #


class TestPromote:
    def test_promote_all_file_types_creates_files_and_manifest(
        self, harness
    ) -> None:
        summary = promote(
            env="sit",
            source="SRC_A",
            release_tag="R2026.06",
            run_output_dir=harness["run_out"],
            approved_by="qa@example.com",
            comment="test promotion",
            paths_yaml=harness["paths_yaml"],
            sources_dir=harness["sources_dir"],
            manifest_path=harness["manifest"],
            policy_path=harness["policy"],
        )
        assert len(summary["promoted"]) == 2
        # Manifest is now on disk and parseable.
        manifest = json.loads(
            harness["manifest"].read_text(encoding="utf-8")
        )
        file_types = {e["file_type"] for e in manifest["baselines"]}
        assert file_types == {"P327", "P328"}
        # P327 picked up its version key.
        p327_entry = next(
            e for e in manifest["baselines"] if e["file_type"] == "P327"
        )
        assert p327_entry["mapping_version"] == "1.2.0"
        # P328 mapping had no version key.
        p328_entry = next(
            e for e in manifest["baselines"] if e["file_type"] == "P328"
        )
        assert p328_entry["mapping_version"] == "unversioned"
        # Baseline files copied into place.
        for entry in manifest["baselines"]:
            assert Path(entry["baseline_file"]).is_file()

    def test_specific_file_type_only(self, harness) -> None:
        summary = promote(
            env="sit",
            source="SRC_A",
            release_tag="R2026.06",
            run_output_dir=harness["run_out"],
            approved_by="qa@example.com",
            file_types=["P327"],
            paths_yaml=harness["paths_yaml"],
            sources_dir=harness["sources_dir"],
            manifest_path=harness["manifest"],
            policy_path=harness["policy"],
        )
        assert {e["file_type"] for e in summary["promoted"]} == {"P327"}

    def test_unknown_file_type_rejected(self, harness) -> None:
        with pytest.raises(PromoteBaselineError, match="not declared"):
            promote(
                env="sit",
                source="SRC_A",
                release_tag="R2026.06",
                run_output_dir=harness["run_out"],
                approved_by="qa@example.com",
                file_types=["GHOST"],
                paths_yaml=harness["paths_yaml"],
                sources_dir=harness["sources_dir"],
                manifest_path=harness["manifest"],
            policy_path=harness["policy"],
            )

    def test_unknown_env_rejected(self, harness) -> None:
        with pytest.raises(PromoteBaselineError, match="unknown env"):
            promote(
                env="prod",  # not declared in paths.yml
                source="SRC_A",
                release_tag="R2026.06",
                run_output_dir=harness["run_out"],
                approved_by="qa@example.com",
                paths_yaml=harness["paths_yaml"],
                sources_dir=harness["sources_dir"],
                manifest_path=harness["manifest"],
            policy_path=harness["policy"],
            )

    def test_no_matching_file_rejected(self, harness, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(PromoteBaselineError, match="no file matching"):
            promote(
                env="sit",
                source="SRC_A",
                release_tag="R2026.06",
                run_output_dir=empty,
                approved_by="qa@example.com",
                file_types=["P327"],
                paths_yaml=harness["paths_yaml"],
                sources_dir=harness["sources_dir"],
                manifest_path=harness["manifest"],
            policy_path=harness["policy"],
            )

    def test_idempotent_second_promote_skips(self, harness) -> None:
        promote(
            env="sit", source="SRC_A", release_tag="R2026.06",
            run_output_dir=harness["run_out"],
            approved_by="qa@example.com",
            file_types=["P327"],
            paths_yaml=harness["paths_yaml"],
            sources_dir=harness["sources_dir"],
            manifest_path=harness["manifest"],
            policy_path=harness["policy"],
            force=True,  # overwrite OK since baseline already exists
            reason="idempotency test — first promote",
        )
        # Second time: same tuple, baseline already exists. With --force
        # the file is overwritten but the manifest tuple is recognised as
        # a duplicate and skipped.
        summary = promote(
            env="sit", source="SRC_A", release_tag="R2026.06",
            run_output_dir=harness["run_out"],
            approved_by="qa@example.com",
            file_types=["P327"],
            paths_yaml=harness["paths_yaml"],
            sources_dir=harness["sources_dir"],
            manifest_path=harness["manifest"],
            policy_path=harness["policy"],
            force=True,
            reason="idempotency test — second promote",
        )
        assert summary["promoted"] == []
        assert len(summary["skipped"]) == 1
        manifest = json.loads(
            harness["manifest"].read_text(encoding="utf-8")
        )
        # Manifest has exactly one entry for P327.
        p327_entries = [
            e for e in manifest["baselines"]
            if e["file_type"] == "P327"
        ]
        assert len(p327_entries) == 1

    def test_refuses_to_overwrite_without_force(self, harness) -> None:
        # First promote populates the baseline file.
        promote(
            env="sit", source="SRC_A", release_tag="R2026.06",
            run_output_dir=harness["run_out"],
            approved_by="qa@example.com",
            file_types=["P327"],
            paths_yaml=harness["paths_yaml"],
            sources_dir=harness["sources_dir"],
            manifest_path=harness["manifest"],
            policy_path=harness["policy"],
        )
        # Second promote of a DIFFERENT release_tag against the SAME file
        # path is constructed to land on the same destination — actually
        # the baseline_root path includes the release_tag so a new tag
        # gets a new dir. We re-promote the same tag → existing file path.
        with pytest.raises(PromoteBaselineError, match="already exists"):
            promote(
                env="sit", source="SRC_A", release_tag="R2026.06",
                run_output_dir=harness["run_out"],
                approved_by="qa@example.com",
                file_types=["P327"],
                paths_yaml=harness["paths_yaml"],
                sources_dir=harness["sources_dir"],
                manifest_path=harness["manifest"],
            policy_path=harness["policy"],
            )

    def test_dry_run_writes_nothing(self, harness) -> None:
        summary = promote(
            env="sit", source="SRC_A", release_tag="R2026.06",
            run_output_dir=harness["run_out"],
            approved_by="qa@example.com",
            paths_yaml=harness["paths_yaml"],
            sources_dir=harness["sources_dir"],
            manifest_path=harness["manifest"],
            policy_path=harness["policy"],
            dry_run=True,
        )
        assert summary["dry_run"] is True
        assert summary["promoted"]
        # No manifest, no baselines.
        assert not harness["manifest"].exists()
        for entry in summary["promoted"]:
            assert not Path(entry["baseline_file"]).exists()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


class TestCli:
    def test_cli_promotes(self, harness, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(
            [
                "--env", "sit",
                "--source", "SRC_A",
                "--release-tag", "R2026.06",
                "--run-output-dir", str(harness["run_out"]),
                "--approved-by", "qa@example.com",
                "--comment", "CI promotion",
                "--paths-yaml", str(harness["paths_yaml"]),
                "--sources-dir", str(harness["sources_dir"]),
                "--manifest", str(harness["manifest"]),
                "--policy", str(harness["policy"]),
            ]
        )
        assert rc == 0
        summary = json.loads(capsys.readouterr().out)
        assert {e["file_type"] for e in summary["promoted"]} == {"P327", "P328"}

    def test_cli_returns_nonzero_on_user_error(
        self, harness, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = main(
            [
                "--env", "prod",  # unknown
                "--source", "SRC_A",
                "--release-tag", "R2026.06",
                "--run-output-dir", str(harness["run_out"]),
                "--approved-by", "qa@example.com",
                "--paths-yaml", str(harness["paths_yaml"]),
                "--sources-dir", str(harness["sources_dir"]),
                "--manifest", str(harness["manifest"]),
                "--policy", str(harness["policy"]),
            ]
        )
        assert rc == 2
        assert "unknown env" in capsys.readouterr().err
