"""Unit tests for ``scripts.e2e_lib.run_source`` — the M4 orchestrator.

These tests stand in for the on-RHEL acceptance criteria in the prompt:
  * a clean run produces a per-source roll-up and zero failure rows;
  * a structural (L1) failure halts the run, writes one row per failed
    step, and exits 2;
  * a non-blocking compare failure (L3) writes a row, keeps running,
    and exits 0;
  * a missing pipeline YAML or unreachable config exits 3;
  * the Java shell-out plumbing fires when ``invoke_java`` is true and is
    cleanly skipped when ``VALDO_E2E_DISABLE_JAVA=1``.

The Valdo CLI is faked out via a stub ``_Subprocess`` that writes a JSON
report at the expected ``--output`` path. The FailureSink is wired against
an in-memory SQLite mirror of ``AUDIT.VALDO_RUN_FAILURES`` (same trick as
the M2 integration test).
"""

from __future__ import annotations

import json
import sqlite3
import sys
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import scripts.e2e_lib.run_source as run_source_mod  # noqa: E402
from scripts.e2e_lib.failure_sink import FailureSink  # noqa: E402
from scripts.e2e_lib.run_source import (  # noqa: E402
    EXIT_BLOCKING_FAILURE,
    EXIT_INFRA_ERROR,
    EXIT_OK,
    GateResult,
    _Subprocess,
    run_source,
)

# Mirror of the audit DDL — SQLite types, no schema prefix.
SQLITE_DDL = """
CREATE TABLE VALDO_RUN_FAILURES (
    failure_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            TEXT     NOT NULL,
    env               TEXT     NOT NULL,
    source            TEXT     NOT NULL,
    pipeline_name     TEXT     NOT NULL,
    gate_name         TEXT     NOT NULL,
    gate_stage        TEXT,
    layer             TEXT,
    file_name         TEXT,
    file_type         TEXT,
    mapping_path      TEXT,
    mapping_version   TEXT,
    error_type        TEXT     NOT NULL,
    error_count       INTEGER  DEFAULT 0,
    row_count         INTEGER,
    report_path       TEXT,
    blocking          INTEGER  DEFAULT 1,
    failed_at         TEXT     DEFAULT CURRENT_TIMESTAMP,
    failure_detail    TEXT
);
"""


# --------------------------------------------------------------------------- #
# Lightweight on-disk config fixtures
# --------------------------------------------------------------------------- #


_PATHS_YAML = textwrap.dedent("""
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
    """)

_SOURCE_YAML = textwrap.dedent("""
    schema_version: 1
    source: SRC_A
    release_tag: "R2026.05"
    java_scripts:
      load:     "{tmp}/fake_load.sh"
      generate: "{tmp}/fake_generate.sh"
    input_files:
      - file_type: HEADER
        glob: "SRC_A_input_HEADER_*.dat"
        mapping: "config/mappings/SRC_A_input_HEADER.json"
        target_staging_table: "STG_SRC_A_HEADER"
    output_files:
      - file_type: P327
        glob: "SRC_A_P327_*.txt"
        mapping: "config/mappings/SRC_A_P327.json"
        rules:   "config/rules/SRC_A_P327.json"
        multi_record: false
        tolerance:
          ignore_fields: []
          max_errors: 0
    gates:
      load_step:        {{ blocking: true,  invoke_java: false }}
      file_to_staging:  {{ blocking: true,  invoke_java: false }}
      generate_step:    {{ blocking: true,  invoke_java: false }}
      L1_structural:    {{ blocking: true,  invoke_java: false }}
      L3_baseline_diff: {{ blocking: false, invoke_java: false }}
    """)

# Minimal pipeline YAML that mirrors what M3 emits — the valdo-runnable gates
# the orchestrator needs to filter and dispatch.
_PIPELINE_YAML = textwrap.dedent("""
    name: e2e_sit_SRC_A
    description: test pipeline
    sources:
      - name: SRC_A__input__HEADER
        mapping: config/mappings/SRC_A_input_HEADER.json
        rules: ''
        output_pattern: ''
        input_path: /in/h.dat
        target_mapping: ''
        staging_tables: [STG_SIT.STG_SRC_A_HEADER]
      - name: SRC_A__output__P327
        mapping: config/mappings/SRC_A_P327.json
        rules: config/rules/SRC_A_P327.json
        output_pattern: /out/p327.txt
        input_path: /work/regenerated/P327.txt
        target_mapping: /baselines/P327.txt
        staging_tables: []
    gates:
      - name: file_to_staging
        stage: input
        description: ''
        for_each: ''
        blocking: true
        steps: []
      - name: L1_structural
        stage: output
        description: ''
        for_each: ''
        blocking: true
        steps: []
      - name: L3_baseline_diff
        stage: output
        description: ''
        for_each: ''
        blocking: false
        steps: []
    """).strip()


@pytest.fixture()
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Materialize a self-contained harness on disk and return its paths."""
    # Disable real Java shell-outs and Oracle by default; specific tests
    # re-enable as needed.
    monkeypatch.setenv("VALDO_E2E_DISABLE_JAVA", "1")
    monkeypatch.setenv("VALDO_E2E_DISABLE_FAILURE_SINK", "0")

    paths_yaml = tmp_path / "paths.yml"
    paths_yaml.write_text(_PATHS_YAML.format(tmp=tmp_path.as_posix()), encoding="utf-8")
    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    (sources_dir / "SRC_A.yml").write_text(
        _SOURCE_YAML.format(tmp=tmp_path.as_posix()), encoding="utf-8"
    )
    pipeline_yaml = tmp_path / "pipeline.yaml"
    pipeline_yaml.write_text(_PIPELINE_YAML, encoding="utf-8")
    return {
        "tmp": tmp_path,
        "paths_yaml": paths_yaml,
        "sources_dir": sources_dir,
        "pipeline_yaml": pipeline_yaml,
    }


@pytest.fixture()
def sink_pair():
    """Open a SQLite-backed sink + its underlying connection."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(SQLITE_DDL)
    sink = FailureSink.from_connection(
        conn, table="VALDO_RUN_FAILURES", paramstyle="qmark"
    )
    yield sink, conn
    conn.close()


# --------------------------------------------------------------------------- #
# Fake subprocess: writes a pre-canned Valdo result JSON
# --------------------------------------------------------------------------- #


class FakeValdo(_Subprocess):
    """Stand-in for the ``valdo`` CLI.

    Each call inspects the ``--output`` path in the command and writes a
    pre-canned JSON report there based on the phase being run (input vs.
    output, inferred from which gates are in the filtered pipeline YAML).
    Callers configure the per-phase outcome via the constructor.
    """

    def __init__(
        self,
        *,
        input_phase: Optional[Dict[str, Any]] = None,
        output_phase: Optional[Dict[str, Any]] = None,
        returncode_overrides: Optional[Dict[str, int]] = None,
        write_report: bool = True,
    ) -> None:
        self.input_phase = input_phase or _passing_report(
            "e2e_sit_SRC_A", ["file_to_staging"]
        )
        self.output_phase = output_phase or _passing_report(
            "e2e_sit_SRC_A", ["L1_structural", "L3_baseline_diff"]
        )
        self.returncode_overrides = returncode_overrides or {}
        self.write_report = write_report
        self.calls: List[List[str]] = []

    def run(self, cmd: Sequence[str], *, cwd=None, env=None):
        self.calls.append(list(cmd))
        # Locate --config and --output flags.
        cfg_path = _flag_value(cmd, "--config")
        out_path = _flag_value(cmd, "--output")
        phase_yaml_text = Path(cfg_path).read_text(encoding="utf-8") if cfg_path else ""
        # Distinguish phases by the gate names present.
        if "file_to_staging" in phase_yaml_text:
            payload = self.input_phase
            phase_key = "input"
        else:
            payload = self.output_phase
            phase_key = "output"

        rc = self.returncode_overrides.get(phase_key, 0)
        if self.write_report and out_path:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_text(json.dumps(payload), encoding="utf-8")
        return _FakeCompleted(returncode=rc, stdout="", stderr="")


class _FakeCompleted:
    def __init__(self, *, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _flag_value(cmd: Sequence[str], flag: str) -> Optional[str]:
    for i, tok in enumerate(cmd):
        if tok == flag and i + 1 < len(cmd):
            return cmd[i + 1]
    return None


def _passing_report(pipeline_name: str, gate_names: List[str]) -> Dict[str, Any]:
    return {
        "pipeline_name": pipeline_name,
        "status": "passed",
        "gates": [
            {
                "name": g,
                "status": "passed",
                "stage": "",
                "steps": [
                    {
                        "type": "validate",
                        "status": "passed",
                        "file": f"/out/{g}.txt",
                        "mapping": "m.json",
                        "total_rows": 100,
                        "error_count": 0,
                    }
                ],
            }
            for g in gate_names
        ],
        "started_at": "2026-05-13T00:00:00Z",
        "finished_at": "2026-05-13T00:00:01Z",
    }


def _failing_gate(
    gate_name: str,
    *,
    file_name: str = "/out/P327_20260513.txt",
    error_count: int = 3,
) -> Dict[str, Any]:
    return {
        "name": gate_name,
        "status": "failed",
        "stage": "output",
        "steps": [
            {
                "type": "validate",
                "status": "failed",
                "file": file_name,
                "mapping": "config/mappings/SRC_A_P327.json",
                "total_rows": 100,
                "error_count": error_count,
                "errors": [{"row": 17, "msg": "width mismatch"}] * error_count,
                "error": "structural mismatch",
            }
        ],
    }


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


class TestCleanRun:
    def test_clean_run_returns_exit_ok(self, harness, sink_pair):
        sink, conn = sink_pair
        result = run_source(
            env="sit",
            source="SRC_A",
            run_id="r1",
            paths_yaml=harness["paths_yaml"],
            sources_dir=harness["sources_dir"],
            pipeline_yaml=harness["pipeline_yaml"],
            subprocess_runner=FakeValdo(),
            failure_sink=sink,
        )
        assert result.exit_code == EXIT_OK
        assert all(g.status in {"passed", "skipped"} for g in result.gates), [
            (g.name, g.status) for g in result.gates
        ]
        (count,) = conn.execute("SELECT COUNT(*) FROM VALDO_RUN_FAILURES").fetchone()
        assert count == 0

    def test_clean_run_writes_rollup_and_jsonl(self, harness, sink_pair):
        sink, _ = sink_pair
        result = run_source(
            env="sit",
            source="SRC_A",
            run_id="r2",
            paths_yaml=harness["paths_yaml"],
            sources_dir=harness["sources_dir"],
            pipeline_yaml=harness["pipeline_yaml"],
            subprocess_runner=FakeValdo(),
            failure_sink=sink,
        )
        rollup = Path(result.rollup_path)
        assert rollup.is_file()
        html = rollup.read_text(encoding="utf-8")
        assert "SRC_A" in html and "r2" in html
        assert Path(result.log_path).is_file()
        lines = Path(result.log_path).read_text(encoding="utf-8").splitlines()
        events = [json.loads(line)["event"] for line in lines]
        assert "run_started" in events
        assert "run_finished" in events

    def test_l2_regeneration_gate_is_retired(self, harness, sink_pair):
        """L2_regeneration was retired (ADR 0012): it must not appear at all."""
        sink, _ = sink_pair
        result = run_source(
            env="sit",
            source="SRC_A",
            run_id="r3",
            paths_yaml=harness["paths_yaml"],
            sources_dir=harness["sources_dir"],
            pipeline_yaml=harness["pipeline_yaml"],
            subprocess_runner=FakeValdo(),
            failure_sink=sink,
        )
        gate_names = {g.name for g in result.gates}
        assert "L2_regeneration" not in gate_names
        # The output-truth axis is covered by L2b; regression by L3.
        assert "L3_baseline_diff" in gate_names


# --------------------------------------------------------------------------- #
# Blocking failure: L1 structural
# --------------------------------------------------------------------------- #


class TestBlockingFailure:
    def test_l1_failure_halts_and_writes_one_row_per_failed_step(
        self, harness, sink_pair
    ):
        sink, conn = sink_pair
        fake = FakeValdo(
            output_phase={
                "pipeline_name": "e2e_sit_SRC_A",
                "status": "failed",
                "gates": [_failing_gate("L1_structural")],
            }
        )
        result = run_source(
            env="sit",
            source="SRC_A",
            run_id="r4",
            paths_yaml=harness["paths_yaml"],
            sources_dir=harness["sources_dir"],
            pipeline_yaml=harness["pipeline_yaml"],
            subprocess_runner=fake,
            failure_sink=sink,
        )
        assert result.exit_code == EXIT_BLOCKING_FAILURE

        rows = conn.execute(
            "SELECT gate_name, layer, error_type, error_count, blocking, "
            "file_name FROM VALDO_RUN_FAILURES ORDER BY failure_id"
        ).fetchall()
        # Exactly one row (single failed step in the gate's report).
        assert rows == [
            ("L1_structural", "L1", "structural", 3, 1, "P327_20260513.txt")
        ]


# --------------------------------------------------------------------------- #
# Non-blocking failure: L3 baseline diff
# --------------------------------------------------------------------------- #


class TestNonBlockingFailure:
    def test_l3_failure_records_but_does_not_halt(self, harness, sink_pair):
        sink, conn = sink_pair
        fake = FakeValdo(
            output_phase={
                "pipeline_name": "e2e_sit_SRC_A",
                "status": "failed",  # Valdo marks overall failed
                "gates": [
                    {
                        "name": "L1_structural",
                        "status": "passed",
                        "stage": "output",
                        "steps": [],
                    },
                    _failing_gate("L3_baseline_diff", file_name="/out/P327.txt"),
                ],
            }
        )
        result = run_source(
            env="sit",
            source="SRC_A",
            run_id="r5",
            paths_yaml=harness["paths_yaml"],
            sources_dir=harness["sources_dir"],
            pipeline_yaml=harness["pipeline_yaml"],
            subprocess_runner=fake,
            failure_sink=sink,
        )
        assert result.exit_code == EXIT_OK  # non-blocking gate failure
        rows = conn.execute(
            "SELECT gate_name, layer, error_type, blocking " "FROM VALDO_RUN_FAILURES"
        ).fetchall()
        assert rows == [("L3_baseline_diff", "L3", "compare_diff", 0)]


# --------------------------------------------------------------------------- #
# Infra-error paths
# --------------------------------------------------------------------------- #


class TestInfraErrors:
    def test_missing_pipeline_yaml_returns_infra_error(self, harness, sink_pair):
        sink, _ = sink_pair
        result = run_source(
            env="sit",
            source="SRC_A",
            run_id="r6",
            paths_yaml=harness["paths_yaml"],
            sources_dir=harness["sources_dir"],
            pipeline_yaml=harness["tmp"] / "does_not_exist.yaml",
            subprocess_runner=FakeValdo(),
            failure_sink=sink,
        )
        assert result.exit_code == EXIT_INFRA_ERROR

    def test_missing_paths_yaml_returns_infra_error(self, harness, sink_pair):
        sink, _ = sink_pair
        result = run_source(
            env="sit",
            source="SRC_A",
            run_id="r7",
            paths_yaml=harness["tmp"] / "no_paths.yml",
            sources_dir=harness["sources_dir"],
            pipeline_yaml=harness["pipeline_yaml"],
            subprocess_runner=FakeValdo(),
            failure_sink=sink,
        )
        assert result.exit_code == EXIT_INFRA_ERROR

    def test_valdo_nonzero_with_no_report_marks_gates_infra_error(
        self, harness, sink_pair
    ):
        sink, _ = sink_pair
        fake = FakeValdo(
            returncode_overrides={"output": 1, "input": 1},
            write_report=False,
        )
        result = run_source(
            env="sit",
            source="SRC_A",
            run_id="r8",
            paths_yaml=harness["paths_yaml"],
            sources_dir=harness["sources_dir"],
            pipeline_yaml=harness["pipeline_yaml"],
            subprocess_runner=fake,
            failure_sink=sink,
        )
        # file_to_staging is blocking → infra error halts the run.
        assert result.exit_code == EXIT_BLOCKING_FAILURE
        f2s = next(g for g in result.gates if g.name == "file_to_staging")
        assert f2s.status == "infra_error"


# --------------------------------------------------------------------------- #
# Java shell-out plumbing
# --------------------------------------------------------------------------- #


class TestJavaShellOut:
    def test_invoke_java_load_step_runs_when_configured(
        self, harness, sink_pair, monkeypatch
    ):
        """When invoke_java=true and VALDO_E2E_DISABLE_JAVA is OFF, the
        configured script is shelled out via the subprocess runner."""
        sink, _ = sink_pair
        monkeypatch.delenv("VALDO_E2E_DISABLE_JAVA", raising=False)

        # Rewrite the source YAML to flip load_step.invoke_java=true.
        src_yaml = harness["sources_dir"] / "SRC_A.yml"
        body = src_yaml.read_text(encoding="utf-8").replace(
            "load_step:        { blocking: true,  invoke_java: false }",
            "load_step:        { blocking: true,  invoke_java: true }",
        )
        src_yaml.write_text(body, encoding="utf-8")

        fake = FakeValdo()
        result = run_source(
            env="sit",
            source="SRC_A",
            run_id="r9",
            paths_yaml=harness["paths_yaml"],
            sources_dir=harness["sources_dir"],
            pipeline_yaml=harness["pipeline_yaml"],
            subprocess_runner=fake,
            failure_sink=sink,
        )
        # The first subprocess.run call should be the Java script
        # (the source YAML configures <tmp>/fake_load.sh).
        assert result.exit_code == EXIT_OK
        # Was the script invoked? Inspect calls; the load script ends
        # in "fake_load.sh" regardless of path-separator differences
        # between Windows and POSIX YAML representations.
        flat = [" ".join(c) for c in fake.calls]
        assert any("fake_load.sh" in s for s in flat), flat

    def test_disable_java_env_var_skips_load(self, harness, sink_pair):
        """VALDO_E2E_DISABLE_JAVA=1 skips the Java step entirely."""
        # Default fixture already sets VALDO_E2E_DISABLE_JAVA=1; flip
        # invoke_java=true and verify the script is NOT in subprocess calls.
        sink, _ = sink_pair
        src_yaml = harness["sources_dir"] / "SRC_A.yml"
        body = src_yaml.read_text(encoding="utf-8").replace(
            "load_step:        { blocking: true,  invoke_java: false }",
            "load_step:        { blocking: true,  invoke_java: true }",
        )
        src_yaml.write_text(body, encoding="utf-8")

        fake = FakeValdo()
        result = run_source(
            env="sit",
            source="SRC_A",
            run_id="r10",
            paths_yaml=harness["paths_yaml"],
            sources_dir=harness["sources_dir"],
            pipeline_yaml=harness["pipeline_yaml"],
            subprocess_runner=fake,
            failure_sink=sink,
        )
        assert result.exit_code == EXIT_OK
        flat = [" ".join(c) for c in fake.calls]
        assert not any("fake_load.sh" in s for s in flat), flat
        # The load_step gate result must record status=skipped.
        load_gate = next(g for g in result.gates if g.name == "load_step")
        assert load_gate.status == "skipped"


# --------------------------------------------------------------------------- #
# Failure-sink disable switch
# --------------------------------------------------------------------------- #


class TestFailureSinkDisable:
    def test_disabled_sink_still_logs_to_jsonl(self, harness, monkeypatch):
        monkeypatch.setenv("VALDO_E2E_DISABLE_FAILURE_SINK", "1")
        fake = FakeValdo(
            output_phase={
                "pipeline_name": "e2e_sit_SRC_A",
                "status": "failed",
                "gates": [_failing_gate("L1_structural")],
            }
        )
        result = run_source(
            env="sit",
            source="SRC_A",
            run_id="r11",
            paths_yaml=harness["paths_yaml"],
            sources_dir=harness["sources_dir"],
            pipeline_yaml=harness["pipeline_yaml"],
            subprocess_runner=fake,
            # No sink passed; the orchestrator should not attempt to build one.
        )
        assert result.exit_code == EXIT_BLOCKING_FAILURE
        # The JSONL must still carry a failure_recorded event.
        lines = Path(result.log_path).read_text(encoding="utf-8").splitlines()
        events = [json.loads(line)["event"] for line in lines]
        assert "failure_recorded" in events


# --------------------------------------------------------------------------- #
# Multi-record validation HTML report step
# --------------------------------------------------------------------------- #


class _FakeLogger:
    """Minimal JsonlLogger stand-in capturing event names."""

    def __init__(self):
        self.events = []

    def info(self, event, **kw):
        self.events.append(event)

    warn = info
    error = info


class _FakeResolver:
    """Resolver stub that returns a fixed output_root."""

    def __init__(self, output_root):
        self._root = output_root

    def resolve(self, key, **kw):
        return self._root


class TestRunMultiRecordReport:
    """Unit tests for the orchestrator-driven multi-record report step."""

    def _call(self, gates_cfg, src_cfg, output_root, report_root):
        from scripts.e2e_lib.run_source import _run_multi_record_report

        return _run_multi_record_report(
            source="SHAW",
            report_root=Path(report_root),
            src_cfg=src_cfg,
            resolver=_FakeResolver(str(output_root)),
            env="sit",
            run_id="r1",
            gates_cfg=gates_cfg,
            logger=_FakeLogger(),
            sink_ctx=None,
            pipeline_name="p",
        )

    def test_skips_when_not_declared(self, tmp_path):
        res = self._call({}, {"output_files": []}, tmp_path, tmp_path)
        assert len(res) == 1
        assert res[0].name == "multi_record_report"
        assert res[0].status == "skipped"
        assert res[0].blocking is False

    def test_skips_when_env_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VALDO_E2E_DISABLE_MR_REPORT", "1")
        res = self._call(
            {"multi_record_report": {"blocking": False}},
            {
                "output_files": [
                    {
                        "file_type": "TRANERT",
                        "multi_record": True,
                        "mapping": "m.yaml",
                        "glob": "*.txt",
                    }
                ]
            },
            tmp_path,
            tmp_path,
        )
        assert res[0].status == "skipped"

    def test_skips_when_no_multi_record_outputs(self, tmp_path):
        res = self._call(
            {"multi_record_report": {"blocking": False}},
            {
                "output_files": [
                    {
                        "file_type": "P327",
                        "multi_record": False,
                        "mapping": "m.json",
                        "glob": "*.txt",
                    }
                ]
            },
            tmp_path,
            tmp_path,
        )
        assert res[0].status == "skipped"

    def test_infra_error_when_output_file_missing(self, tmp_path):
        # Declared + multi-record, but no file matches the glob under output_root.
        res = self._call(
            {"multi_record_report": {"blocking": False}},
            {
                "output_files": [
                    {
                        "file_type": "TRANERT",
                        "multi_record": True,
                        "mapping": "config/mappings/SHAW_TRANERT.yaml",
                        "glob": "tranert_shaw_*.txt",
                    }
                ]
            },
            tmp_path,
            tmp_path,
        )
        assert len(res) == 1
        assert res[0].status == "infra_error"
        assert res[0].blocking is False

    def test_renders_report_for_present_file(self, tmp_path, monkeypatch):
        # A matching output file exists; stub the renderer so the test is
        # hermetic (no Oracle, no real validation) and assert the wiring.
        out_root = tmp_path / "out"
        out_root.mkdir()
        (out_root / "tranert_shaw_20260101.txt").write_text("dummy")
        rpt_root = tmp_path / "rpt"
        rpt_root.mkdir()

        import scripts.render_multi_record_html as rmr

        captured = {}

        def _fake_render(
            *, umbrella_yaml_path, data_file_path, output_dir, suppress_pii=True
        ):
            captured.update(
                umbrella=umbrella_yaml_path,
                data=data_file_path,
                out=output_dir,
                pii=suppress_pii,
            )
            idx = Path(output_dir) / "index.html"
            idx.parent.mkdir(parents=True, exist_ok=True)
            idx.write_text("<html>ok</html>")
            return idx

        monkeypatch.setattr(rmr, "render", _fake_render)

        res = self._call(
            {"multi_record_report": {"blocking": False}},
            {
                "output_files": [
                    {
                        "file_type": "TRANERT",
                        "multi_record": True,
                        "mapping": "config/mappings/SHAW_TRANERT.yaml",
                        "glob": "tranert_shaw_*.txt",
                    }
                ]
            },
            out_root,
            rpt_root,
        )
        assert len(res) == 1
        assert res[0].status == "passed"
        assert res[0].report_path.endswith("index.html")
        # report nested under <report_root>/multi_record/<file_type>/
        assert "multi_record" in res[0].report_path
        assert captured["pii"] is False  # AGENTS.md #4 default
        assert captured["umbrella"] == "config/mappings/SHAW_TRANERT.yaml"


class _FakeSecretResolver:
    """Secret accessor stub exposing .get(name) like SecretResolver."""

    def __init__(self, values):
        self._values = values

    def get(self, name):
        if name not in self._values:
            raise KeyError(name)
        return self._values[name]


class _FakeSinkCtx:
    """Minimal _FailureSinkContext stand-in exposing ._resolver."""

    def __init__(self, resolver):
        self._resolver = resolver


class TestOpenL2bConnection:
    """R-01b: _open_l2b_connection now delegates to OracleTruthSource (ADR 0010).

    These tests pin the rewire: a successful connect returns the connection,
    and each TruthSourceError variant degrades to ``None`` with the historical
    ``l2b_*`` log event preserved.
    """

    def _resolver(self):
        return _FakeSecretResolver(
            {
                "ORACLE_DSN": "host:1521/svc",
                "ORACLE_USER": "app_int",
                "ORACLE_PASSWORD": "pw",
            }
        )

    def test_returns_connection_on_success(self, monkeypatch):
        sentinel_conn = object()

        def fake_connect(*, user, password, dsn):
            return sentinel_conn

        # Real OracleTruthSource with an injected connect_fn keeps the
        # credential-resolution path under test.
        import src.database.truth_source as ts

        orig = ts.OracleTruthSource
        monkeypatch.setattr(
            ts,
            "OracleTruthSource",
            lambda **kw: orig(connect_fn=fake_connect, **kw),
        )

        logger = _FakeLogger()
        conn = run_source_mod._open_l2b_connection(
            env="sit", sink_ctx=_FakeSinkCtx(self._resolver()), logger=logger
        )
        assert conn is sentinel_conn

    def test_missing_secret_degrades_to_none_with_event(self, monkeypatch):
        logger = _FakeLogger()
        # Resolver missing ORACLE_PASSWORD → credential-resolution failure.
        resolver = _FakeSecretResolver({"ORACLE_DSN": "d", "ORACLE_USER": "u"})
        conn = run_source_mod._open_l2b_connection(
            env="sit", sink_ctx=_FakeSinkCtx(resolver), logger=logger
        )
        assert conn is None
        assert "l2b_secret_unresolved" in logger.events

    def test_connect_failure_degrades_to_none_with_event(self, monkeypatch):
        def boom(*, user, password, dsn):
            raise RuntimeError("ORA-12154")

        import src.database.truth_source as ts

        orig = ts.OracleTruthSource
        monkeypatch.setattr(
            ts,
            "OracleTruthSource",
            lambda **kw: orig(connect_fn=boom, **kw),
        )

        logger = _FakeLogger()
        conn = run_source_mod._open_l2b_connection(
            env="sit", sink_ctx=_FakeSinkCtx(self._resolver()), logger=logger
        )
        assert conn is None
        assert "l2b_connect_failed" in logger.events
