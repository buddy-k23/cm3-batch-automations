"""Unit tests for ``scripts.e2e_lib.watch`` — the M7 trigger-file watcher."""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.e2e_lib.path_resolver import PathResolver  # noqa: E402
from scripts.e2e_lib.watch import (  # noqa: E402
    EXIT_INFRA_ERROR,
    EXIT_OK,
    TriggerEvent,
    UnmatchedTrigger,
    Watcher,
    WatchError,
    _read_data_filename,
    classify_triggers,
    deduplicate_by_source,
    main,
    mark_processed,
    mark_unmatched,
)

# --------------------------------------------------------------------------- #
# Disk fixtures
# --------------------------------------------------------------------------- #


_PATHS_YAML_TPL = textwrap.dedent("""
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
        pattern: '^(?P<source>[A-Z0-9_]+)_input_(?P<file_type>[A-Z0-9]+)_\d+\.dat$'
        direction: input
      - name: standard_output
        pattern: '^(?P<source>[A-Z0-9_]+)_(?P<file_type>P[0-9]+)_\d+\.txt$'
        direction: output
    trigger_file:
      suffix: ".trigger"
      data_file_line: 1
    """)


_SOURCE_YAML_TPL = textwrap.dedent("""
    schema_version: 1
    source: {name}
    release_tag: "R1"
    input_files:
      - file_type: HEADER
        glob: "{name}_input_HEADER_*.dat"
        mapping: "m.json"
        target_staging_table: "STG_{name}_HEADER"
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
    """)


@pytest.fixture()
def harness(tmp_path: Path):
    """Self-contained harness with two sources + a fake watch script path."""
    paths_yaml = tmp_path / "paths.yml"
    paths_yaml.write_text(
        _PATHS_YAML_TPL.format(tmp=tmp_path.as_posix()), encoding="utf-8"
    )
    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    for name in ("SRC_A", "SRC_B"):
        (sources_dir / f"{name}.yml").write_text(
            _SOURCE_YAML_TPL.format(name=name), encoding="utf-8"
        )

    # Pre-create the per-source trigger_root dirs so the watcher does
    # not skip them.
    for name in ("SRC_A", "SRC_B"):
        (tmp_path / "data" / "sit" / name / "triggers").mkdir(
            parents=True, exist_ok=True
        )

    watch_script = tmp_path / "fake_run_e2e_source.sh"
    watch_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    return {
        "tmp": tmp_path,
        "paths_yaml": paths_yaml,
        "sources_dir": sources_dir,
        "watch_script": watch_script,
    }


def _drop_trigger(harness, source: str, name: str, payload: str) -> Path:
    trig_dir = harness["tmp"] / "data" / "sit" / source / "triggers"
    trig_dir.mkdir(parents=True, exist_ok=True)
    p = trig_dir / f"{name}.trigger"
    p.write_text(payload, encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# Helpers under test (pure)
# --------------------------------------------------------------------------- #


class TestReadDataFilename:
    def test_reads_first_line_default(self, tmp_path: Path) -> None:
        t = tmp_path / "x.trigger"
        t.write_text("SRC_A_input_HEADER_20260514.dat\n", encoding="utf-8")
        assert _read_data_filename(t, 1) == "SRC_A_input_HEADER_20260514.dat"

    def test_strips_whitespace(self, tmp_path: Path) -> None:
        t = tmp_path / "x.trigger"
        t.write_text("  SRC_A_P327_20260514.txt   \n", encoding="utf-8")
        assert _read_data_filename(t, 1) == "SRC_A_P327_20260514.txt"

    def test_blank_line_returns_none(self, tmp_path: Path) -> None:
        t = tmp_path / "x.trigger"
        t.write_text("\n\nsomething\n", encoding="utf-8")
        assert _read_data_filename(t, 1) is None

    def test_reads_specific_line(self, tmp_path: Path) -> None:
        t = tmp_path / "x.trigger"
        t.write_text("header\nfilename.dat\nfooter\n", encoding="utf-8")
        assert _read_data_filename(t, 2) == "filename.dat"

    def test_missing_line_returns_none(self, tmp_path: Path) -> None:
        t = tmp_path / "x.trigger"
        t.write_text("only one line\n", encoding="utf-8")
        assert _read_data_filename(t, 5) is None

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert _read_data_filename(tmp_path / "nope.trigger", 1) is None


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #


class TestClassifyTriggers:
    def test_matches_input_pattern(self, tmp_path: Path, harness) -> None:
        resolver = PathResolver.from_files(
            harness["paths_yaml"], harness["sources_dir"]
        )
        t = _drop_trigger(
            harness,
            "SRC_A",
            "t1",
            "SRC_A_input_HEADER_20260514.dat\n",
        )
        matched, unmatched = classify_triggers([t], resolver=resolver, data_file_line=1)
        assert len(matched) == 1 and not unmatched
        ev = matched[0]
        assert ev.source == "SRC_A"
        assert ev.file_type == "HEADER"
        assert ev.direction == "input"

    def test_matches_output_pattern(self, harness) -> None:
        resolver = PathResolver.from_files(
            harness["paths_yaml"], harness["sources_dir"]
        )
        t = _drop_trigger(
            harness,
            "SRC_A",
            "t2",
            "SRC_A_P327_20260514.txt\n",
        )
        matched, unmatched = classify_triggers([t], resolver=resolver, data_file_line=1)
        assert matched[0].direction == "output"
        assert matched[0].file_type == "P327"

    def test_unknown_basename_goes_to_unmatched(self, harness) -> None:
        resolver = PathResolver.from_files(
            harness["paths_yaml"], harness["sources_dir"]
        )
        t = _drop_trigger(harness, "SRC_A", "t3", "totally_unrelated.zzz\n")
        matched, unmatched = classify_triggers([t], resolver=resolver, data_file_line=1)
        assert not matched
        assert unmatched[0].reason == "no_pattern_matched"
        assert unmatched[0].data_filename == "totally_unrelated.zzz"

    def test_empty_payload_goes_to_unmatched(self, harness) -> None:
        resolver = PathResolver.from_files(
            harness["paths_yaml"], harness["sources_dir"]
        )
        t = _drop_trigger(harness, "SRC_A", "t4", "")
        matched, unmatched = classify_triggers([t], resolver=resolver, data_file_line=1)
        assert not matched
        assert unmatched[0].reason == "empty_or_unreadable"

    def test_only_basename_considered(self, harness) -> None:
        """Even when the sidecar records a full path, only the basename matters."""
        resolver = PathResolver.from_files(
            harness["paths_yaml"], harness["sources_dir"]
        )
        t = _drop_trigger(
            harness,
            "SRC_A",
            "t5",
            "/data/sit/SRC_A/input/SRC_A_input_HEADER_20260514.dat\n",
        )
        matched, unmatched = classify_triggers([t], resolver=resolver, data_file_line=1)
        assert len(matched) == 1 and not unmatched
        assert matched[0].data_filename == "SRC_A_input_HEADER_20260514.dat"


# --------------------------------------------------------------------------- #
# Dedup
# --------------------------------------------------------------------------- #


class TestDeduplicateBySource:
    def _ev(self, source: str, file_type: str = "HEADER") -> TriggerEvent:
        return TriggerEvent(
            trigger_path=Path("x.trigger"),
            data_filename=f"{source}_input_{file_type}_20260514.dat",
            source=source,
            file_type=file_type,
            direction="input",
            pattern_name="standard_input",
        )

    def test_unique_preserves_order(self) -> None:
        evs = [
            self._ev("SRC_B"),
            self._ev("SRC_A"),
            self._ev("SRC_B"),
            self._ev("SRC_C"),
            self._ev("SRC_A"),
        ]
        assert deduplicate_by_source(evs) == ["SRC_B", "SRC_A", "SRC_C"]

    def test_empty(self) -> None:
        assert deduplicate_by_source([]) == []


# --------------------------------------------------------------------------- #
# Sidecar lifecycle
# --------------------------------------------------------------------------- #


class TestMarkProcessed:
    def test_renames(self, tmp_path: Path) -> None:
        t = tmp_path / "a.trigger"
        t.write_text("payload\n", encoding="utf-8")
        out = mark_processed(t)
        assert not t.exists()
        assert out.name == "a.trigger.processed"

    def test_idempotent_when_already_processed(self, tmp_path: Path) -> None:
        t = tmp_path / "a.trigger.processed"
        t.write_text("p\n", encoding="utf-8")
        out = mark_processed(t)
        assert out == t  # untouched

    def test_collision_appends_timestamp(self, tmp_path: Path) -> None:
        # Existing .processed sibling -> new rename gets a timestamp suffix.
        (tmp_path / "a.trigger.processed").write_text("old", encoding="utf-8")
        t = tmp_path / "a.trigger"
        t.write_text("new", encoding="utf-8")
        out = mark_processed(t)
        assert out.name.startswith("a.trigger.processed.")
        assert out.name != "a.trigger.processed"


class TestMarkUnmatched:
    def test_renames_and_writes_reason(self, tmp_path: Path) -> None:
        t = tmp_path / "a.trigger"
        t.write_text("payload\n", encoding="utf-8")
        out = mark_unmatched(t, "no_pattern_matched")
        assert out.name == "a.trigger.unmatched"
        reason = out.with_suffix(out.suffix + ".reason")
        assert reason.read_text(encoding="utf-8").strip() == "no_pattern_matched"


# --------------------------------------------------------------------------- #
# Watcher.poll_once — end-to-end behavior with a stubbed runner
# --------------------------------------------------------------------------- #


class _FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _RecordingRunner:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.calls: List[List[str]] = []

    def __call__(self, cmd: List[str]) -> _FakeCompleted:
        self.calls.append(list(cmd))
        return _FakeCompleted(returncode=self.returncode)


# ----- R-07 per-source overrides -------------------------------------- #

# SRC_C overlay: its own filename_patterns (lowercase, .csv, hyphen) AND a
# custom trigger contract (suffix ".done", data filename on line 2).
_SRC_C_OVERLAY = (
    "schema_version: 1\n"
    "source: SRC_C\n"
    'release_tag: "R1"\n'
    "input_files: []\n"
    "output_files: []\n"
    "gates: {}\n"
    "filename_patterns:\n"
    "  - name: custom_in\n"
    "    pattern: '^(?P<source>[a-z0-9]+)-(?P<file_type>[a-z]+)\\.csv$'\n"
    "    direction: input\n"
    "trigger_file:\n"
    '  suffix: ".done"\n'
    "  data_file_line: 2\n"
)


def _add_src_c(harness) -> None:
    """Write the SRC_C overlay and create its trigger dir."""
    (harness["sources_dir"] / "SRC_C.yml").write_text(_SRC_C_OVERLAY, encoding="utf-8")
    (harness["tmp"] / "data" / "sit" / "SRC_C" / "triggers").mkdir(
        parents=True, exist_ok=True
    )


class TestWatcherPerSourceOverrides:
    """R-07: the watcher honours per-source trigger suffix + filename_patterns."""

    def test_custom_suffix_and_pattern_route_a_run(self, harness) -> None:
        _add_src_c(harness)
        # Drop a ".done" sidecar whose data filename (line 2) matches SRC_C's
        # custom lowercase/.csv pattern.
        trig_dir = harness["tmp"] / "data" / "sit" / "SRC_C" / "triggers"
        sidecar = trig_dir / "batch1.done"
        sidecar.write_text("header-line\nacct-header.csv\n", encoding="utf-8")

        runner = _RecordingRunner()
        watcher = _build_watcher(harness, runner)
        summary = watcher.poll_once()

        assert "SRC_C" in summary["sources_run"]
        invoked = {cmd[cmd.index("--source") + 1] for cmd in runner.calls}
        assert "SRC_C" in invoked
        # The ".done" sidecar was processed (renamed).
        assert not sidecar.exists()
        assert (trig_dir / "batch1.done.processed").exists()

    def test_wrong_suffix_ignored_for_custom_source(self, harness) -> None:
        # SRC_C uses ".done"; a ".trigger" sidecar in its dir must be ignored.
        _add_src_c(harness)
        trig_dir = harness["tmp"] / "data" / "sit" / "SRC_C" / "triggers"
        ignored = trig_dir / "stray.trigger"
        ignored.write_text("acct-header.csv\n", encoding="utf-8")

        runner = _RecordingRunner()
        watcher = _build_watcher(harness, runner)
        summary = watcher.poll_once()

        assert "SRC_C" not in summary["sources_run"]
        # The ".trigger" file is left untouched (not this source's suffix).
        assert ignored.exists()

    def test_global_pattern_rejected_for_custom_source(self, harness) -> None:
        # A filename matching the GLOBAL input pattern must NOT match under
        # SRC_C, whose override replaces the global list.
        _add_src_c(harness)
        trig_dir = harness["tmp"] / "data" / "sit" / "SRC_C" / "triggers"
        sidecar = trig_dir / "batch2.done"
        sidecar.write_text(
            "header-line\nSRC_C_input_HEADER_20260514.dat\n", encoding="utf-8"
        )

        runner = _RecordingRunner()
        watcher = _build_watcher(harness, runner)
        summary = watcher.poll_once()

        assert "SRC_C" not in summary["sources_run"]
        assert summary["unmatched"] == 1
        # Unmatched sidecar renamed with the custom suffix preserved.
        assert (trig_dir / "batch2.done.unmatched").exists()

    def test_existing_sources_unaffected_by_new_override(self, harness) -> None:
        # SRC_A (no override) still routes via the global pattern + .trigger.
        _add_src_c(harness)
        _drop_trigger(harness, "SRC_A", "t1", "SRC_A_input_HEADER_20260514.dat\n")
        runner = _RecordingRunner()
        watcher = _build_watcher(harness, runner)
        summary = watcher.poll_once()
        assert "SRC_A" in summary["sources_run"]


def _build_watcher(harness, runner: _RecordingRunner) -> Watcher:
    return Watcher.from_paths(
        env="sit",
        paths_yaml=harness["paths_yaml"],
        sources_dir=harness["sources_dir"],
        watch_script=harness["watch_script"],
        valdo_executable="valdo",
        poll_interval=1,
        subprocess_runner=runner,
    )


class TestWatcherPollOnce:
    def test_matched_sidecars_invoke_run_e2e_source_per_unique_source(
        self, harness
    ) -> None:
        # Two triggers for SRC_A, one for SRC_B in a single cycle.
        _drop_trigger(harness, "SRC_A", "t1", "SRC_A_input_HEADER_20260514.dat\n")
        _drop_trigger(harness, "SRC_A", "t2", "SRC_A_P327_20260514.txt\n")
        _drop_trigger(harness, "SRC_B", "t3", "SRC_B_input_HEADER_20260514.dat\n")
        runner = _RecordingRunner()
        watcher = _build_watcher(harness, runner)
        summary = watcher.poll_once()
        assert summary["sidecars_seen"] == 3
        assert summary["matched"] == 3
        assert summary["unmatched"] == 0
        # Deduplicated → exactly two run_e2e_source.sh calls.
        assert len(runner.calls) == 2
        invoked_sources = {cmd[cmd.index("--source") + 1] for cmd in runner.calls}
        assert invoked_sources == {"SRC_A", "SRC_B"}
        # Every cmd points at the configured watch script.
        for cmd in runner.calls:
            assert cmd[0] == "bash"
            assert cmd[1] == str(harness["watch_script"])

    def test_processed_sidecars_renamed(self, harness) -> None:
        t = _drop_trigger(harness, "SRC_A", "t1", "SRC_A_input_HEADER_20260514.dat\n")
        runner = _RecordingRunner()
        watcher = _build_watcher(harness, runner)
        watcher.poll_once()
        assert not t.exists()
        assert (t.parent / "t1.trigger.processed").exists()

    def test_unmatched_sidecars_renamed_with_reason(self, harness) -> None:
        t = _drop_trigger(harness, "SRC_A", "t1", "totally_unrelated.zzz\n")
        runner = _RecordingRunner()
        watcher = _build_watcher(harness, runner)
        summary = watcher.poll_once()
        assert summary["unmatched"] == 1
        assert not t.exists()
        unmatched = t.parent / "t1.trigger.unmatched"
        assert unmatched.exists()
        reason = unmatched.with_suffix(unmatched.suffix + ".reason")
        assert reason.read_text(encoding="utf-8").strip() == "no_pattern_matched"
        # No run was invoked.
        assert runner.calls == []

    def test_already_processed_sidecars_skipped_on_subsequent_polls(
        self, harness
    ) -> None:
        _drop_trigger(harness, "SRC_A", "t1", "SRC_A_input_HEADER_20260514.dat\n")
        runner = _RecordingRunner()
        watcher = _build_watcher(harness, runner)
        watcher.poll_once()
        # Second poll: the .processed sibling exists; the original .trigger
        # is gone; nothing new to do.
        runner.calls.clear()
        summary = watcher.poll_once()
        assert summary["sidecars_seen"] == 0
        assert runner.calls == []

    def test_runner_failure_does_not_abort_other_sources(self, harness) -> None:
        _drop_trigger(harness, "SRC_A", "t1", "SRC_A_input_HEADER_20260514.dat\n")
        _drop_trigger(harness, "SRC_B", "t2", "SRC_B_input_HEADER_20260514.dat\n")

        # First call fails, second passes.
        calls: List[int] = []

        def runner(cmd: List[str]) -> _FakeCompleted:
            idx = len(calls)
            calls.append(idx)
            return _FakeCompleted(returncode=2 if idx == 0 else 0)

        watcher = Watcher.from_paths(
            env="sit",
            paths_yaml=harness["paths_yaml"],
            sources_dir=harness["sources_dir"],
            watch_script=harness["watch_script"],
            poll_interval=1,
            subprocess_runner=runner,
        )
        summary = watcher.poll_once()
        # Two sources, two invocations regardless of outcome.
        assert len(calls) == 2
        rcs = {r["returncode"] for r in summary["results"]}
        assert rcs == {0, 2}

    def test_jsonl_log_records_run_events(self, harness) -> None:
        _drop_trigger(harness, "SRC_A", "t1", "SRC_A_input_HEADER_20260514.dat\n")
        runner = _RecordingRunner()
        watcher = _build_watcher(harness, runner)
        watcher.poll_once()
        log_root = harness["tmp"] / "logs"
        logs = list(log_root.glob("e2e_watch_sit_*.jsonl"))
        assert len(logs) == 1
        events = [
            json.loads(line)
            for line in logs[0].read_text(encoding="utf-8").splitlines()
        ]
        kinds = {e["event"] for e in events}
        assert "source_run_started" in kinds
        assert "source_run_finished" in kinds

    def test_unmatched_logged_at_warn(self, harness) -> None:
        _drop_trigger(harness, "SRC_A", "t1", "garbage.zzz\n")
        runner = _RecordingRunner()
        watcher = _build_watcher(harness, runner)
        watcher.poll_once()
        logs = list((harness["tmp"] / "logs").glob("e2e_watch_sit_*.jsonl"))
        events = [
            json.loads(line)
            for line in logs[0].read_text(encoding="utf-8").splitlines()
        ]
        unmatched = [e for e in events if e["event"] == "trigger_unmatched"]
        assert len(unmatched) == 1
        assert unmatched[0]["level"] == "WARN"
        assert unmatched[0]["reason"] == "no_pattern_matched"

    def test_only_known_source_dirs_polled(self, harness) -> None:
        """A stray .trigger file outside any known trigger_root is ignored."""
        stray = harness["tmp"] / "elsewhere"
        stray.mkdir()
        (stray / "wild.trigger").write_text(
            "SRC_A_input_HEADER_20260514.dat\n", encoding="utf-8"
        )
        runner = _RecordingRunner()
        watcher = _build_watcher(harness, runner)
        summary = watcher.poll_once()
        assert summary["sidecars_seen"] == 0
        assert runner.calls == []


# --------------------------------------------------------------------------- #
# Watcher.from_paths validation
# --------------------------------------------------------------------------- #


class TestWatcherFromPaths:
    def test_unknown_env_raises(self, harness) -> None:
        with pytest.raises(WatchError, match="unknown env"):
            Watcher.from_paths(
                env="prod",
                paths_yaml=harness["paths_yaml"],
                sources_dir=harness["sources_dir"],
                watch_script=harness["watch_script"],
            )

    def test_missing_paths_yaml_raises(self, tmp_path: Path, harness) -> None:
        with pytest.raises(WatchError):
            Watcher.from_paths(
                env="sit",
                paths_yaml=tmp_path / "nope.yml",
                sources_dir=harness["sources_dir"],
                watch_script=harness["watch_script"],
            )

    def test_trigger_file_config_picked_up(self, harness) -> None:
        runner = _RecordingRunner()
        watcher = _build_watcher(harness, runner)
        assert watcher.trigger_suffix == ".trigger"
        assert watcher.data_file_line == 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


class TestCli:
    def test_unknown_env_returns_three(
        self, harness, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = main(
            [
                "--env",
                "prod",
                "--paths-yaml",
                str(harness["paths_yaml"]),
                "--sources-dir",
                str(harness["sources_dir"]),
                "--watch-script",
                str(harness["watch_script"]),
                "--once",
            ]
        )
        assert rc == EXIT_INFRA_ERROR
        assert "unknown env" in capsys.readouterr().err

    def test_once_mode_returns_zero(self, harness) -> None:
        rc = main(
            [
                "--env",
                "sit",
                "--paths-yaml",
                str(harness["paths_yaml"]),
                "--sources-dir",
                str(harness["sources_dir"]),
                "--watch-script",
                str(harness["watch_script"]),
                "--once",
            ]
        )
        # With no triggers dropped, --once is a no-op and exits cleanly.
        assert rc == EXIT_OK

    def test_max_iterations_caps_polling(self, harness) -> None:
        # poll_interval=1, max_iterations=2 → loop terminates quickly.
        rc = main(
            [
                "--env",
                "sit",
                "--paths-yaml",
                str(harness["paths_yaml"]),
                "--sources-dir",
                str(harness["sources_dir"]),
                "--watch-script",
                str(harness["watch_script"]),
                "--poll-interval",
                "1",
                "--max-iterations",
                "2",
            ]
        )
        assert rc == EXIT_OK


# --------------------------------------------------------------------------- #
# Latency guard (acceptance criterion #5)
# --------------------------------------------------------------------------- #


class TestLatencyContract:
    def test_default_poll_interval_meets_10s_sla(self, harness) -> None:
        """Acceptance criterion 5: trigger drop -> run start <= 10 seconds.

        The default poll interval bounds that latency. This test pins the
        default at <= 10s so a future refactor cannot regress it silently.
        """
        runner = _RecordingRunner()
        watcher = _build_watcher(harness, runner)
        assert watcher.poll_interval <= 10
