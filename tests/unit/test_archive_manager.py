"""Unit tests for ArchiveManager (#28)."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest

from src.utils.archive import ArchiveManager


@pytest.fixture()
def archive_dir(tmp_path):
    return tmp_path / "archive"


@pytest.fixture()
def manager(archive_dir, monkeypatch):
    monkeypatch.setenv("REPORT_ARCHIVE_PATH", str(archive_dir))
    monkeypatch.setenv("REPORT_RETENTION_DAYS", "365")
    return ArchiveManager()


class TestArchiveRun:
    def test_creates_dated_run_dir(self, manager, tmp_path, archive_dir):
        report = tmp_path / "suite.html"
        report.write_text("<html>report</html>", encoding="utf-8")

        manager.archive_run(
            run_id="abc123",
            suite_name="P327 UAT",
            env="uat",
            timestamp="2026-03-02T09:00:00Z",
            files=[str(report)],
        )

        run_dir = archive_dir / "2026" / "03" / "02" / "abc123"
        assert run_dir.is_dir()

    def test_copies_files_to_run_dir(self, manager, tmp_path, archive_dir):
        report = tmp_path / "suite.html"
        report.write_text("<html>test content</html>", encoding="utf-8")

        manager.archive_run(
            run_id="abc123",
            suite_name="P327",
            env="dev",
            timestamp="2026-03-02T09:00:00Z",
            files=[str(report)],
        )

        run_dir = archive_dir / "2026" / "03" / "02" / "abc123"
        copied = run_dir / "suite.html"
        assert copied.exists()
        assert copied.read_text(encoding="utf-8") == "<html>test content</html>"

    def test_writes_manifest_json(self, manager, tmp_path, archive_dir):
        report = tmp_path / "report.html"
        report.write_text("content", encoding="utf-8")

        manager.archive_run(
            run_id="run001",
            suite_name="Suite A",
            env="uat",
            timestamp="2026-03-02T10:00:00Z",
            files=[str(report)],
        )

        manifest_path = archive_dir / "2026" / "03" / "02" / "run001" / "run001_manifest.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        assert manifest["run_id"] == "run001"
        assert manifest["suite_name"] == "Suite A"
        assert manifest["environment"] == "uat"
        assert manifest["timestamp"] == "2026-03-02T10:00:00Z"
        assert len(manifest["files"]) == 1
        assert manifest["files"][0]["name"] == "report.html"
        assert len(manifest["files"][0]["sha256"]) == 64  # SHA-256 hex
        assert "manifest_hash" in manifest

    def test_manifest_hash_is_verifiable(self, manager, tmp_path, archive_dir):
        report = tmp_path / "f.html"
        report.write_text("x", encoding="utf-8")

        manager.archive_run(
            run_id="verifyrun",
            suite_name="S",
            env="dev",
            timestamp="2026-03-02T00:00:00Z",
            files=[str(report)],
        )

        manifest_path = archive_dir / "2026" / "03" / "02" / "verifyrun" / "verifyrun_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        # Re-derive the hash from the manifest fields (excluding manifest_hash)
        payload = {k: manifest[k] for k in ("run_id", "suite_name", "environment", "timestamp", "files")}
        expected_hash = hashlib.sha256(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()
        assert manifest["manifest_hash"] == expected_hash

    def test_skips_nonexistent_files(self, manager, tmp_path, archive_dir):
        real = tmp_path / "exists.html"
        real.write_text("ok", encoding="utf-8")

        manager.archive_run(
            run_id="skip001",
            suite_name="S",
            env="dev",
            timestamp="2026-03-02T00:00:00Z",
            files=[str(real), str(tmp_path / "missing.html")],
        )

        run_dir = archive_dir / "2026" / "03" / "02" / "skip001"
        manifest = json.loads((run_dir / "skip001_manifest.json").read_text(encoding="utf-8"))
        # Only the real file should appear in manifest
        assert len(manifest["files"]) == 1
        assert manifest["files"][0]["name"] == "exists.html"

    def test_deduplicates_same_basename(self, manager, tmp_path, archive_dir):
        file1 = tmp_path / "dir1" / "report.html"
        file2 = tmp_path / "dir2" / "report.html"
        file1.parent.mkdir()
        file2.parent.mkdir()
        file1.write_text("content1", encoding="utf-8")
        file2.write_text("content2", encoding="utf-8")

        manager.archive_run(
            run_id="duprun",
            suite_name="Dup",
            env="dev",
            timestamp="2026-03-02T00:00:00Z",
            files=[str(file1), str(file2)],
        )

        run_dir = archive_dir / "2026" / "03" / "02" / "duprun"
        manifest = json.loads((run_dir / "duprun_manifest.json").read_text(encoding="utf-8"))
        # Both files should be in manifest with distinct names
        assert len(manifest["files"]) == 2
        names = {f["name"] for f in manifest["files"]}
        assert "report.html" in names
        assert "report_1.html" in names
        # Both files should exist in the archive dir
        assert (run_dir / "report.html").exists()
        assert (run_dir / "report_1.html").exists()


class TestListRuns:
    def test_returns_empty_when_no_archive(self, manager):
        assert manager.list_runs() == []

    def test_returns_manifest_data(self, manager, tmp_path, archive_dir):
        report = tmp_path / "r.html"
        report.write_text("x", encoding="utf-8")
        manager.archive_run(
            run_id="listrun1",
            suite_name="List Suite",
            env="dev",
            timestamp="2026-03-02T08:00:00Z",
            files=[str(report)],
        )

        runs = manager.list_runs()
        assert len(runs) == 1
        assert runs[0]["run_id"] == "listrun1"
        assert runs[0]["suite_name"] == "List Suite"


class TestGetRun:
    def test_returns_none_for_unknown_run(self, manager):
        assert manager.get_run("does-not-exist") is None

    def test_returns_manifest_and_files(self, manager, tmp_path, archive_dir):
        report = tmp_path / "g.html"
        report.write_text("y", encoding="utf-8")
        manager.archive_run(
            run_id="getrun1",
            suite_name="Get Suite",
            env="dev",
            timestamp="2026-03-02T07:00:00Z",
            files=[str(report)],
        )

        result = manager.get_run("getrun1")
        assert result is not None
        assert result["manifest"]["run_id"] == "getrun1"
        assert len(result["files"]) == 1


class TestPurgeOldRuns:
    def test_deletes_runs_older_than_retention(self, manager, tmp_path, archive_dir):
        report = tmp_path / "old.html"
        report.write_text("old", encoding="utf-8")
        manager.archive_run(
            run_id="oldrun",
            suite_name="Old",
            env="dev",
            timestamp="2020-01-01T00:00:00Z",
            files=[str(report)],
        )

        run_dir = archive_dir / "2020" / "01" / "01" / "oldrun"
        assert run_dir.exists()

        # Set mtime to 2 years ago
        import os
        old_mtime = time.time() - (2 * 365 * 24 * 3600)
        os.utime(run_dir, (old_mtime, old_mtime))

        manager.purge_old_runs(retention_days=365)
        assert not run_dir.exists()

    def test_keeps_runs_within_retention(self, manager, tmp_path, archive_dir):
        report = tmp_path / "new.html"
        report.write_text("new", encoding="utf-8")
        manager.archive_run(
            run_id="newrun",
            suite_name="New",
            env="dev",
            timestamp="2026-03-02T00:00:00Z",
            files=[str(report)],
        )

        manager.purge_old_runs(retention_days=365)

        run_dir = archive_dir / "2026" / "03" / "02" / "newrun"
        assert run_dir.exists()
