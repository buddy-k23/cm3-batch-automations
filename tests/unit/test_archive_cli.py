"""Unit tests for list-runs and get-run CLI commands (#28)."""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from src.main import cli


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def populated_archive(tmp_path, monkeypatch):
    """Create an archive dir with one run and monkeypatch ArchiveManager to use it."""
    archive_root = tmp_path / "archive"
    run_dir = archive_root / "2026" / "03" / "02" / "run-abc"
    run_dir.mkdir(parents=True)
    manifest = {
        "run_id": "run-abc",
        "suite_name": "P327 UAT",
        "environment": "uat",
        "timestamp": "2026-03-02T09:00:00Z",
        "files": [{"name": "suite.html", "sha256": "deadbeef" * 8}],
        "manifest_hash": "aabbcc",
    }
    (run_dir / "run-abc_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    monkeypatch.setenv("REPORT_ARCHIVE_PATH", str(archive_root))
    return archive_root


class TestListRunsCommand:
    def test_exits_zero_with_no_archive(self, runner, tmp_path, monkeypatch):
        monkeypatch.setenv("REPORT_ARCHIVE_PATH", str(tmp_path / "empty"))
        result = runner.invoke(cli, ["list-runs"])
        assert result.exit_code == 0

    def test_shows_run_id_in_output(self, runner, populated_archive):
        result = runner.invoke(cli, ["list-runs"])
        assert result.exit_code == 0
        assert "run-abc" in result.output

    def test_shows_suite_name_in_output(self, runner, populated_archive):
        result = runner.invoke(cli, ["list-runs"])
        assert "P327 UAT" in result.output

    def test_limit_option_accepted(self, runner, tmp_path, monkeypatch):
        monkeypatch.setenv("REPORT_ARCHIVE_PATH", str(tmp_path / "empty"))
        result = runner.invoke(cli, ["list-runs", "--limit", "5"])
        assert result.exit_code == 0


class TestGetRunCommand:
    def test_exits_nonzero_for_unknown_run(self, runner, tmp_path, monkeypatch):
        monkeypatch.setenv("REPORT_ARCHIVE_PATH", str(tmp_path / "empty"))
        result = runner.invoke(cli, ["get-run", "does-not-exist"])
        assert result.exit_code != 0

    def test_prints_manifest_for_known_run(self, runner, populated_archive):
        result = runner.invoke(cli, ["get-run", "run-abc"])
        assert result.exit_code == 0
        assert "run-abc" in result.output
        assert "P327 UAT" in result.output

    def test_prints_file_list(self, runner, populated_archive):
        # Create the actual file in the run dir so get_run finds it
        run_dir = populated_archive / "2026" / "03" / "02" / "run-abc"
        (run_dir / "suite.html").write_text("html", encoding="utf-8")

        result = runner.invoke(cli, ["get-run", "run-abc"])
        assert result.exit_code == 0
        assert "suite.html" in result.output
