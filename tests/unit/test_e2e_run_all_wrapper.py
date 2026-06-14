"""Smoke tests for the M6 bash fan-out wrapper ``scripts/run_e2e_all.sh``.

These tests treat the bash script as an opaque executable. They are
skipped on environments that lack a usable Bourne-Again Shell (notably
plain Windows without Git Bash / WSL); the CI environment on RHEL runs
them for real.

What is verified
----------------
* The script's grammar is valid (``bash -n``).
* The script rejects missing/invalid arguments with exit 3.
* When pointed at a stubbed ``run_e2e_source.sh`` that fabricates
  per-source artifacts, the fan-out:
    - runs once per source,
    - respects the ``--source`` filter,
    - resolves the global reports dir via the path resolver,
    - invokes the Python roll-up to produce ``index.html`` +
      ``summary.json`` at the run's reports root,
    - returns exit 0 on a clean run.

The richer success/failure-propagation paths are covered in
``tests/unit/test_e2e_build_rollup_index.py`` (the Python side).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import List

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _bash() -> str | None:
    return shutil.which("bash")


pytestmark = pytest.mark.skipif(
    _bash() is None,
    reason="bash not available on this host (test runs on RHEL CI)",
)


# --------------------------------------------------------------------------- #
# Disk fixture
# --------------------------------------------------------------------------- #


_PATHS_YAML_TPL = textwrap.dedent(
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

_SOURCE_YAML_TPL = textwrap.dedent(
    """
    schema_version: 1
    source: {name}
    release_tag: "R1"
    input_files:
      - file_type: H
        glob: "{name}_input_H_*.dat"
        mapping: "m.json"
        target_staging_table: "STG_{name}_H"
    output_files:
      - file_type: P327
        glob: "{name}_P327_*.txt"
        mapping: "m.json"
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
    """Build a self-contained harness with two sources and a stub runner."""
    paths_yaml = tmp_path / "paths.yml"
    paths_yaml.write_text(
        _PATHS_YAML_TPL.format(tmp=tmp_path.as_posix()), encoding="utf-8"
    )
    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    (sources_dir / "SRC_A.yml").write_text(
        _SOURCE_YAML_TPL.format(name="SRC_A"), encoding="utf-8"
    )
    (sources_dir / "SRC_B.yml").write_text(
        _SOURCE_YAML_TPL.format(name="SRC_B"), encoding="utf-8"
    )

    # Stub run_e2e_source.sh: writes a minimal per-source summary.json so
    # the fan-out has something for the roll-up to scan. Exit code 0.
    stub_dir = tmp_path / "stub_scripts"
    stub_dir.mkdir()
    stub = stub_dir / "run_e2e_source.sh"
    stub.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -uo pipefail
            ENV_NAME=""; SRC=""; RUN_ID=""
            while [[ $# -gt 0 ]]; do
              case "$1" in
                --env) ENV_NAME="$2"; shift 2 ;;
                --source) SRC="$2"; shift 2 ;;
                --run-id) RUN_ID="$2"; shift 2 ;;
                *) shift ;;
              esac
            done
            DEST="{tmp}/reports/${{RUN_ID}}/${{SRC}}"
            mkdir -p "${{DEST}}"
            cat > "${{DEST}}/summary.json" <<JSON
            {{
              "run_id": "${{RUN_ID}}",
              "env": "${{ENV_NAME}}",
              "source": "${{SRC}}",
              "gates": [
                {{
                  "name": "L1_structural",
                  "status": "passed",
                  "blocking": true,
                  "step_count": 1,
                  "error": null
                }}
              ]
            }}
            JSON
            echo "<html></html>" > "${{DEST}}/index.html"
            exit 0
            """.format(tmp=tmp_path.as_posix())
        ),
        encoding="utf-8",
    )
    os.chmod(stub, 0o755)
    return {
        "tmp": tmp_path,
        "paths_yaml": paths_yaml,
        "sources_dir": sources_dir,
        "stub_dir": stub_dir,
    }


def _run_all(args: List[str], *, stub_dir: Path) -> subprocess.CompletedProcess:
    """Invoke scripts/run_e2e_all.sh with a PATH that prefers the stub.

    The fan-out script calls ``bash scripts/run_e2e_source.sh ...``
    by an explicit relative path, so we patch the SCRIPT_DIR via a
    sibling-by-side copy: we copy the real wrapper into the stub dir
    and place the stub run_e2e_source.sh next to it. The fan-out
    script's ``SCRIPT_DIR`` then resolves to the stub dir.
    """
    real = _REPO_ROOT / "scripts" / "run_e2e_all.sh"
    target = stub_dir / "run_e2e_all.sh"
    shutil.copy2(real, target)
    cmd = [_bash(), str(target), *args]
    return subprocess.run(
        cmd, capture_output=True, text=True, check=False,
        cwd=str(_REPO_ROOT),
    )


# --------------------------------------------------------------------------- #
# Static checks
# --------------------------------------------------------------------------- #


class TestStatic:
    def test_bash_syntax_is_valid(self) -> None:
        script = _REPO_ROOT / "scripts" / "run_e2e_all.sh"
        rc = subprocess.run(
            [_bash(), "-n", str(script)],
            capture_output=True, text=True,
        )
        assert rc.returncode == 0, rc.stderr

    def test_help_flag_exits_zero(self) -> None:
        script = _REPO_ROOT / "scripts" / "run_e2e_all.sh"
        rc = subprocess.run(
            [_bash(), str(script), "--help"],
            capture_output=True, text=True, cwd=str(_REPO_ROOT),
        )
        assert rc.returncode == 0


# --------------------------------------------------------------------------- #
# Argument validation
# --------------------------------------------------------------------------- #


class TestArgValidation:
    def test_missing_env_returns_three(self, harness) -> None:
        cp = _run_all(
            ["--paths-yaml", str(harness["paths_yaml"]),
             "--sources-dir", str(harness["sources_dir"])],
            stub_dir=harness["stub_dir"],
        )
        assert cp.returncode == 3
        assert "env" in (cp.stderr + cp.stdout).lower()

    def test_unknown_arg_returns_three(self, harness) -> None:
        cp = _run_all(
            ["--env", "sit", "--bogus", "x",
             "--paths-yaml", str(harness["paths_yaml"]),
             "--sources-dir", str(harness["sources_dir"])],
            stub_dir=harness["stub_dir"],
        )
        assert cp.returncode == 3

    def test_invalid_concurrency_returns_three(self, harness) -> None:
        cp = _run_all(
            ["--env", "sit", "--concurrency", "0",
             "--paths-yaml", str(harness["paths_yaml"]),
             "--sources-dir", str(harness["sources_dir"])],
            stub_dir=harness["stub_dir"],
        )
        assert cp.returncode == 3

    def test_unknown_source_filter_returns_three(self, harness) -> None:
        cp = _run_all(
            ["--env", "sit", "--source", "GHOST",
             "--paths-yaml", str(harness["paths_yaml"]),
             "--sources-dir", str(harness["sources_dir"])],
            stub_dir=harness["stub_dir"],
        )
        assert cp.returncode == 3


# --------------------------------------------------------------------------- #
# Happy path: end-to-end fan-out with stubbed per-source runner
# --------------------------------------------------------------------------- #


class TestFanOutHappyPath:
    def test_fans_out_all_sources_and_writes_global_rollup(self, harness) -> None:
        run_id = "20260514_120000"
        cp = _run_all(
            [
                "--env", "sit",
                "--run-id", run_id,
                "--concurrency", "2",
                "--paths-yaml", str(harness["paths_yaml"]),
                "--sources-dir", str(harness["sources_dir"]),
            ],
            stub_dir=harness["stub_dir"],
        )
        assert cp.returncode == 0, cp.stderr + cp.stdout

        # Per-source summary.json files were created by the stub.
        rep_root = harness["tmp"] / "reports" / run_id
        assert (rep_root / "SRC_A" / "summary.json").is_file()
        assert (rep_root / "SRC_B" / "summary.json").is_file()

        # Global roll-up exists with the expected shape.
        assert (rep_root / "index.html").is_file()
        global_summary = json.loads(
            (rep_root / "summary.json").read_text(encoding="utf-8")
        )
        assert global_summary["totals"]["sources"] == 2
        assert global_summary["totals"]["gates_passed"] == 2
        assert global_summary["totals"]["gates_failed"] == 0
        assert global_summary["sources_with_blocking_failure"] == []

    def test_source_filter_restricts_run(self, harness) -> None:
        run_id = "20260514_130000"
        cp = _run_all(
            [
                "--env", "sit",
                "--run-id", run_id,
                "--source", "SRC_A",
                "--paths-yaml", str(harness["paths_yaml"]),
                "--sources-dir", str(harness["sources_dir"]),
            ],
            stub_dir=harness["stub_dir"],
        )
        assert cp.returncode == 0, cp.stderr + cp.stdout
        rep_root = harness["tmp"] / "reports" / run_id
        assert (rep_root / "SRC_A" / "summary.json").is_file()
        assert not (rep_root / "SRC_B").exists()
        global_summary = json.loads(
            (rep_root / "summary.json").read_text(encoding="utf-8")
        )
        assert global_summary["totals"]["sources"] == 1


# --------------------------------------------------------------------------- #
# Failure propagation
# --------------------------------------------------------------------------- #


class TestFanOutFailurePropagation:
    def test_blocking_failure_in_one_source_yields_exit_two(
        self, harness
    ) -> None:
        # Rewrite the stub to fail SRC_B with a blocking-failure exit code.
        stub = harness["stub_dir"] / "run_e2e_source.sh"
        stub.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -uo pipefail
                ENV_NAME=""; SRC=""; RUN_ID=""
                while [[ $# -gt 0 ]]; do
                  case "$1" in
                    --env) ENV_NAME="$2"; shift 2 ;;
                    --source) SRC="$2"; shift 2 ;;
                    --run-id) RUN_ID="$2"; shift 2 ;;
                    *) shift ;;
                  esac
                done
                DEST="{tmp}/reports/${{RUN_ID}}/${{SRC}}"
                mkdir -p "${{DEST}}"
                if [[ "${{SRC}}" == "SRC_B" ]]; then
                  cat > "${{DEST}}/summary.json" <<JSON
                {{
                  "run_id": "${{RUN_ID}}",
                  "env": "${{ENV_NAME}}",
                  "source": "${{SRC}}",
                  "gates": [
                    {{ "name": "L1_structural", "status": "failed",
                       "blocking": true, "step_count": 1, "error": "boom" }}
                  ]
                }}
                JSON
                  echo "<html></html>" > "${{DEST}}/index.html"
                  exit 2
                fi
                cat > "${{DEST}}/summary.json" <<JSON
                {{
                  "run_id": "${{RUN_ID}}",
                  "env": "${{ENV_NAME}}",
                  "source": "${{SRC}}",
                  "gates": [
                    {{ "name": "L1_structural", "status": "passed",
                       "blocking": true, "step_count": 1, "error": null }}
                  ]
                }}
                JSON
                echo "<html></html>" > "${{DEST}}/index.html"
                exit 0
                """.format(tmp=harness["tmp"].as_posix())
            ),
            encoding="utf-8",
        )
        os.chmod(stub, 0o755)

        run_id = "20260514_140000"
        cp = _run_all(
            [
                "--env", "sit",
                "--run-id", run_id,
                "--paths-yaml", str(harness["paths_yaml"]),
                "--sources-dir", str(harness["sources_dir"]),
            ],
            stub_dir=harness["stub_dir"],
        )
        assert cp.returncode == 2, cp.stderr + cp.stdout

        rep_root = harness["tmp"] / "reports" / run_id
        global_summary = json.loads(
            (rep_root / "summary.json").read_text(encoding="utf-8")
        )
        assert global_summary["sources_with_blocking_failure"] == ["SRC_B"]
