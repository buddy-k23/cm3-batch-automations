"""Unit tests for workflow command builders + subprocess runner (S17-4, #432)."""

from pathlib import Path
from unittest import mock

from src.workflows import engine


class TestResolvePath:
    def test_none_returns_none(self):
        assert engine.resolve_path(None, Path("/root")) is None
        assert engine.resolve_path("", Path("/root")) is None

    def test_absolute_path_unchanged(self):
        p = engine.resolve_path("/abs/file", Path("/root"))
        assert p == Path("/abs/file")

    def test_relative_path_joined_to_root(self):
        p = engine.resolve_path("rel/file", Path("/root"))
        assert p == Path("/root/rel/file")


class TestBuildParseCmd:
    def test_minimal(self):
        cmd = engine.build_parse_cmd("py", "in.txt", "map.json")
        assert cmd == ["py", "-m", "src.main", "parse", "-f", "in.txt", "-m", "map.json"]

    def test_all_options(self):
        cmd = engine.build_parse_cmd(
            "py", "in.txt", "map.json", output="out.json", fmt="csv",
            use_chunked=True, chunk_size=50,
        )
        assert "-o" in cmd and "out.json" in cmd
        assert "--format" in cmd and "csv" in cmd
        assert "--use-chunked" in cmd and "50" in cmd


class TestBuildValidateCmd:
    def test_basic_flag_when_not_detailed(self):
        cmd = engine.build_validate_cmd("py", "in", "map", detailed=False)
        assert "--basic" in cmd
        assert "--detailed" not in cmd

    def test_strict_fixed_width_with_level(self):
        cmd = engine.build_validate_cmd(
            "py", "in", "map", strict_fixed_width=True, strict_level="error"
        )
        assert "--strict-fixed-width" in cmd
        assert "--strict-level" in cmd and "error" in cmd

    def test_chunked_progress_toggle(self):
        on = engine.build_validate_cmd("py", "in", "map", use_chunked=True, progress=True)
        off = engine.build_validate_cmd("py", "in", "map", use_chunked=True, progress=False)
        assert "--progress" in on
        assert "--no-progress" in off

    def test_rules_and_output(self):
        cmd = engine.build_validate_cmd("py", "in", "map", rules="r.json", output="o.json")
        assert "-r" in cmd and "r.json" in cmd
        assert "-o" in cmd and "o.json" in cmd


class TestBuildCompareCmd:
    def test_keys_mapping_output(self):
        cmd = engine.build_compare_cmd(
            "py", "b.txt", "c.txt", keys="id", mapping="m.json", output="o.json"
        )
        assert "-f1" in cmd and "b.txt" in cmd
        assert "-f2" in cmd and "c.txt" in cmd
        assert "-k" in cmd and "id" in cmd
        assert "-m" in cmd and "m.json" in cmd

    def test_chunked_adds_no_progress(self):
        cmd = engine.build_compare_cmd("py", "b", "c", use_chunked=True, chunk_size=99)
        assert "--use-chunked" in cmd
        assert "--no-progress" in cmd
        assert "99" in cmd


class TestRunSubprocess:
    def test_pass_status_on_zero_exit(self):
        fake = mock.Mock(returncode=0, stdout="ok", stderr="")
        with mock.patch("src.workflows.engine.subprocess.run", return_value=fake):
            result = engine.run_subprocess(["echo", "hi"], Path("/tmp"))
        assert result["status"] == "PASS"
        assert result["exit_code"] == 0
        assert result["output"] == "ok"
        assert result["command"] == "echo hi"
        assert result["duration_seconds"] >= 0

    def test_fail_status_and_stderr_appended(self):
        fake = mock.Mock(returncode=2, stdout="out", stderr="boom")
        with mock.patch("src.workflows.engine.subprocess.run", return_value=fake):
            result = engine.run_subprocess(["cmd"], Path("/tmp"))
        assert result["status"] == "FAIL"
        assert "boom" in result["output"]
