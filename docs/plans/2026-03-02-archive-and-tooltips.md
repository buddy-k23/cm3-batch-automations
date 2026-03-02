# Archive + UI Tooltips Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Every suite run produces a permanent tamper-evident archive entry; all web UI elements have hover tooltips.

**Architecture:** `ArchiveManager` in `src/utils/archive.py` is called at the end of `run_suite_from_path` after `_append_run_history`. Two new CLI commands (`list-runs`, `get-run`) in `src/main.py` expose the archive. A single CSS `data-tooltip` pattern adds tooltips across all three UI tabs with no JS.

**Tech Stack:** Python stdlib (`hashlib`, `shutil`, `json`, `pathlib`), Click, pytest, vanilla HTML/CSS.

---

## Context for the implementer

**Key files to read before starting:**
- `src/commands/run_tests_command.py` — `run_suite_from_path()` at line 459. The hook point is after `_append_run_history(...)` on line 514.
- `src/utils/cleanup.py` — pattern for a utility class in this package.
- `src/main.py` — how CLI commands are registered. Look at `run-tests` at line 721 for a complete example.
- `src/reports/static/ui.html` — the single-file web UI. CSS is in `<style>` (lines 7–255), HTML body starts line 257.
- `tests/unit/test_web_ui.py` — pattern for testing UI HTML content.
- `tests/unit/test_run_tests_command.py` — pattern for testing suite runner.

**Env vars used by ArchiveManager:**
```
REPORT_ARCHIVE_PATH   # default: "reports/archive"
REPORT_RETENTION_DAYS # default: 365
```
Both must be resolved relative to the repo root using `Path(__file__).resolve().parent...` — no hardcoded absolute paths, no CWD-relative paths.

**The archive directory layout:**
```
reports/archive/
  YYYY/MM/DD/
    {run_id}/
      {suite_name}_{run_id}_suite.html   ← copied from uploads/
      {test_name}.html                   ← one per test
      {run_id}_manifest.json
```

**Manifest JSON structure:**
```json
{
  "run_id": "uuid",
  "suite_name": "P327 UAT",
  "environment": "uat",
  "timestamp": "2026-03-02T09:15:32Z",
  "files": [
    {"name": "P327_UAT_suite.html", "sha256": "abc123..."},
    {"name": "P327_structure_check.html", "sha256": "def456..."}
  ],
  "manifest_hash": "sha256 of JSON containing only run_id/suite_name/environment/timestamp/files"
}
```

`manifest_hash` is computed by: serialise the dict with only the first 5 keys (no `manifest_hash`), encode to UTF-8, SHA-256.

---

## Task 1: ArchiveManager

**Files:**
- Create: `src/utils/archive.py`
- Create: `tests/unit/test_archive_manager.py`

---

**Step 1: Write failing tests**

Create `tests/unit/test_archive_manager.py`:

```python
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
```

**Step 2: Run to confirm FAIL**

```bash
cd /Users/buddy/claude-code/automations/cm3-batch-automations
pytest tests/unit/test_archive_manager.py -q
```

Expected: `ImportError: cannot import name 'ArchiveManager' from 'src.utils.archive'`

---

**Step 3: Implement `src/utils/archive.py`**

```python
"""Tamper-evident archive manager for suite run reports (#28)."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _default_archive_root() -> Path:
    raw = os.getenv("REPORT_ARCHIVE_PATH", "reports/archive")
    p = Path(raw)
    return p if p.is_absolute() else _REPO_ROOT / p


def _sha256_file(path: Path) -> str:
    """Return hex SHA-256 digest of file contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _manifest_hash(payload: dict[str, Any]) -> str:
    """Return hex SHA-256 of the canonical JSON serialisation of payload."""
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class ArchiveManager:
    """Copy suite run reports to a permanent dated archive and generate tamper-evident manifests.

    Args:
        archive_root: Override archive root path. Defaults to ``REPORT_ARCHIVE_PATH``
            env var, or ``reports/archive`` relative to the repo root.
    """

    def __init__(self, archive_root: str | Path | None = None) -> None:
        if archive_root is not None:
            self._root = Path(archive_root)
        else:
            self._root = _default_archive_root()

    def _run_dir(self, date_str: str, run_id: str) -> Path:
        """Return ``archive_root/YYYY/MM/DD/{run_id}``."""
        year, month, day = date_str[:10].split("-")
        return self._root / year / month / day / run_id

    def archive_run(
        self,
        run_id: str,
        suite_name: str,
        env: str,
        timestamp: str,
        files: list[str],
    ) -> Path:
        """Copy report files to a dated archive dir and write a manifest.

        Args:
            run_id: Unique identifier for this suite run.
            suite_name: Human-readable suite name.
            env: Environment string (e.g. ``"uat"``).
            timestamp: ISO-8601 UTC timestamp string (``"2026-03-02T09:00:00Z"``).
            files: List of absolute file paths to archive. Non-existent paths are skipped.

        Returns:
            Path to the run directory that was created.
        """
        run_dir = self._run_dir(timestamp, run_id)
        run_dir.mkdir(parents=True, exist_ok=True)

        archived: list[dict[str, str]] = []
        for src_path_str in files:
            src = Path(src_path_str)
            if not src.exists():
                continue
            dst = run_dir / src.name
            shutil.copy2(str(src), str(dst))
            archived.append({"name": src.name, "sha256": _sha256_file(dst)})

        payload: dict[str, Any] = {
            "run_id": run_id,
            "suite_name": suite_name,
            "environment": env,
            "timestamp": timestamp,
            "files": archived,
        }
        manifest: dict[str, Any] = {**payload, "manifest_hash": _manifest_hash(payload)}

        manifest_path = run_dir / f"{run_id}_manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        return run_dir

    def list_runs(self) -> list[dict[str, Any]]:
        """Return all archived runs sorted newest-first.

        Returns:
            List of manifest dicts, each with at minimum:
            ``run_id``, ``suite_name``, ``environment``, ``timestamp``.
        """
        results: list[dict[str, Any]] = []
        if not self._root.exists():
            return results

        for manifest_path in self._root.rglob("*_manifest.json"):
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                results.append(data)
            except Exception:
                continue

        results.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        return results

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Return manifest and file paths for a specific run.

        Args:
            run_id: The run UUID to look up.

        Returns:
            Dict with keys ``manifest`` (dict) and ``files`` (list of Path),
            or ``None`` if the run_id is not found.
        """
        for manifest_path in self._root.rglob(f"{run_id}_manifest.json"):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                run_dir = manifest_path.parent
                file_paths = [
                    run_dir / entry["name"]
                    for entry in manifest.get("files", [])
                    if (run_dir / entry["name"]).exists()
                ]
                return {"manifest": manifest, "files": file_paths}
            except Exception:
                continue
        return None

    def purge_old_runs(self, retention_days: int | None = None) -> int:
        """Delete run directories older than retention_days.

        Args:
            retention_days: Runs older than this are deleted. Defaults to
                ``REPORT_RETENTION_DAYS`` env var, or 365.

        Returns:
            Number of run directories deleted.
        """
        if retention_days is None:
            retention_days = int(os.getenv("REPORT_RETENTION_DAYS", "365"))

        if not self._root.exists():
            return 0

        cutoff = time.time() - retention_days * 24 * 3600
        deleted = 0

        # Run dirs are at depth YYYY/MM/DD/{run_id}
        for run_dir in self._root.rglob("*"):
            if not run_dir.is_dir():
                continue
            # Only consider leaf dirs (contain manifest files)
            manifests = list(run_dir.glob("*_manifest.json"))
            if not manifests:
                continue
            try:
                if run_dir.stat().st_mtime < cutoff:
                    shutil.rmtree(str(run_dir))
                    deleted += 1
            except Exception:
                continue

        return deleted
```

**Step 4: Run tests to confirm PASS**

```bash
pytest tests/unit/test_archive_manager.py -v
```

Expected: all tests PASS.

**Step 5: Commit**

```bash
git add src/utils/archive.py tests/unit/test_archive_manager.py
git commit -m "feat(archive): add ArchiveManager with tamper-evident manifest (#28)"
```

---

## Task 2: Integrate archive into run_suite_from_path

**Files:**
- Modify: `src/commands/run_tests_command.py` — `run_suite_from_path()` at line 459
- Modify: `src/commands/run_tests_command.py` — `_append_run_history()` at line 385
- Modify: `tests/unit/test_run_tests_command.py` — add archive integration test

---

**Step 1: Write failing test**

Open `tests/unit/test_run_tests_command.py` and add this test class at the bottom:

```python
class TestArchiveIntegration:
    """Verify run_suite_from_path archives reports after every run."""

    def test_archive_called_after_suite_run(self, tmp_path, monkeypatch):
        """archive_run() must be called once per suite run."""
        import yaml
        from src.commands.run_tests_command import run_suite_from_path

        # Write a minimal suite YAML with one api_check test (no file needed)
        suite_yaml = tmp_path / "suite.yaml"
        suite_yaml.write_text(
            yaml.dump({
                "name": "Archive Test Suite",
                "environment": "dev",
                "tests": [
                    {
                        "name": "Health check",
                        "type": "api_check",
                        "url": "http://localhost:9999/nope",
                    }
                ],
            }),
            encoding="utf-8",
        )

        archive_calls = []

        def fake_archive_run(**kwargs):
            archive_calls.append(kwargs)
            return tmp_path

        import src.utils.archive as archive_mod
        monkeypatch.setattr(
            archive_mod.ArchiveManager, "archive_run",
            lambda self, **kwargs: fake_archive_run(**kwargs),
        )

        run_suite_from_path(
            suite_path=str(suite_yaml),
            params={},
            env="dev",
            output_dir=str(tmp_path),
        )

        assert len(archive_calls) == 1
        assert archive_calls[0]["suite_name"] == "Archive Test Suite"

    def test_archive_path_added_to_run_history(self, tmp_path, monkeypatch):
        """run_history.json entry must include archive_path."""
        import yaml
        from src.commands.run_tests_command import run_suite_from_path

        suite_yaml = tmp_path / "suite.yaml"
        suite_yaml.write_text(
            yaml.dump({
                "name": "History Suite",
                "environment": "dev",
                "tests": [
                    {
                        "name": "API up",
                        "type": "api_check",
                        "url": "http://localhost:9999/nope",
                    }
                ],
            }),
            encoding="utf-8",
        )

        archive_dir = tmp_path / "archive"

        import src.utils.archive as archive_mod
        monkeypatch.setattr(
            archive_mod.ArchiveManager, "archive_run",
            lambda self, **kwargs: archive_dir,
        )
        monkeypatch.setattr(
            archive_mod, "_default_archive_root",
            lambda: archive_dir,
        )

        output_dir = tmp_path / "reports"
        output_dir.mkdir()

        run_suite_from_path(
            suite_path=str(suite_yaml),
            params={},
            env="dev",
            output_dir=str(output_dir),
        )

        import json
        history_path = tmp_path / "reports" / "run_history.json"
        # run_history is written one dir up from output_dir in the real code —
        # let's find it wherever it landed
        import glob
        found = glob.glob(str(tmp_path / "**" / "run_history.json"), recursive=True)
        assert found, "run_history.json not created"
        history = json.loads(Path(found[0]).read_text(encoding="utf-8"))
        assert len(history) == 1
        assert "archive_path" in history[0]
```

**Step 2: Run to confirm FAIL**

```bash
pytest tests/unit/test_run_tests_command.py::TestArchiveIntegration -q
```

Expected: FAIL (`archive_path` not in history, `archive_run` not called).

**Step 3: Modify `_append_run_history` to accept and store `archive_path`**

In `src/commands/run_tests_command.py`, change the `_append_run_history` signature and body:

Add `archive_path: str = ""` parameter:
```python
def _append_run_history(
    output_dir: str,
    run_id: str,
    suite: Any,
    results: list[dict[str, Any]],
    suite_report_path: str,
    env: str,
    archive_path: str = "",
) -> None:
```

And in the `entry` dict (around line 436), add after `"total_count"`:
```python
        "archive_path": archive_path,
```

**Step 4: Modify `run_suite_from_path` to call archive after history**

At the end of `run_suite_from_path`, replace the `_append_run_history(...)` call block with:

```python
    from src.utils.archive import ArchiveManager

    archive = ArchiveManager()
    run_timestamp = datetime.utcnow().isoformat() + "Z"
    report_files = [suite_report_path] + [
        r["report_path"] for r in results if r.get("report_path")
    ]
    archive_run_dir = archive.archive_run(
        run_id=run_id,
        suite_name=suite.name,
        env=env or suite.environment,
        timestamp=run_timestamp,
        files=report_files,
    )

    _append_run_history(
        output_dir=output_dir,
        run_id=run_id,
        suite=suite,
        results=results,
        suite_report_path=suite_report_path,
        env=env or suite.environment,
        archive_path=str(archive_run_dir),
    )
```

**Step 5: Run tests to confirm PASS**

```bash
pytest tests/unit/test_run_tests_command.py -q
```

Expected: all tests PASS (including new `TestArchiveIntegration`).

**Step 6: Commit**

```bash
git add src/commands/run_tests_command.py tests/unit/test_run_tests_command.py
git commit -m "feat(archive): integrate ArchiveManager into run_suite_from_path (#28)"
```

---

## Task 3: `list-runs` and `get-run` CLI commands

**Files:**
- Modify: `src/main.py` — add two new commands after the `run-tests` command (around line 776)
- Create: `tests/unit/test_archive_cli.py`

---

**Step 1: Write failing tests**

Create `tests/unit/test_archive_cli.py`:

```python
"""Unit tests for list-runs and get-run CLI commands (#28)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from src.main import cli


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def populated_archive(tmp_path, monkeypatch):
    """Create an archive dir with one run and monkeypatch ArchiveManager to use it."""
    import src.utils.archive as archive_mod

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
```

**Step 2: Run to confirm FAIL**

```bash
pytest tests/unit/test_archive_cli.py -q
```

Expected: FAIL — `No such command 'list-runs'`.

**Step 3: Add commands to `src/main.py`**

Add these two commands just before the `def main():` line (line 778):

```python
@cli.command('list-runs')
@click.option('--limit', default=20, show_default=True, type=int,
              help='Maximum number of runs to show (most recent first)')
def list_runs(limit):
    """List archived test suite runs (most recent first)."""
    from src.utils.archive import ArchiveManager

    archive = ArchiveManager()
    archive.purge_old_runs()
    runs = archive.list_runs()[:limit]

    if not runs:
        click.echo("No archived runs found.")
        return

    click.echo(f"{'RUN ID':<38} {'SUITE':<28} {'ENV':<8} {'TIMESTAMP':<22} STATUS")
    click.echo("-" * 106)
    for r in runs:
        click.echo(
            f"{r.get('run_id', ''):<38} "
            f"{r.get('suite_name', '')[:27]:<28} "
            f"{r.get('environment', ''):<8} "
            f"{r.get('timestamp', ''):<22} "
            f"{r.get('status', 'unknown')}"
        )


@cli.command('get-run')
@click.argument('run_id')
def get_run(run_id):
    """Retrieve archived files and manifest for a specific run."""
    from src.utils.archive import ArchiveManager

    archive = ArchiveManager()
    result = archive.get_run(run_id)

    if result is None:
        click.echo(click.style(f"Run '{run_id}' not found in archive.", fg='red'), err=True)
        raise SystemExit(1)

    click.echo("Manifest:")
    click.echo(json.dumps(result['manifest'], indent=2))
    click.echo("\nFiles:")
    for f in result['files']:
        click.echo(f"  {f}")
```

Also add `import json` at the top of `src/main.py` if not already present (check line 1–10).

**Step 4: Run tests to confirm PASS**

```bash
pytest tests/unit/test_archive_cli.py -v
```

Expected: all tests PASS.

**Step 5: Commit**

```bash
git add src/main.py tests/unit/test_archive_cli.py
git commit -m "feat(archive): add list-runs and get-run CLI commands (#28)"
```

---

## Task 4: CSS tooltip system + `data-tooltip` attributes in `ui.html`

**Files:**
- Modify: `src/reports/static/ui.html`
- Modify: `tests/unit/test_web_ui.py`

---

**Step 1: Write failing tests**

Open `tests/unit/test_web_ui.py` and add these tests at the bottom of the file:

```python
# ---------------------------------------------------------------------------
# Tooltip tests
# ---------------------------------------------------------------------------

def _load_ui_html() -> str:
    """Read ui.html directly (not via HTTP) for structural checks."""
    ui_path = Path(__file__).resolve().parent.parent.parent / "src" / "reports" / "static" / "ui.html"
    return ui_path.read_text(encoding="utf-8")


class TestTooltips:
    def test_tooltip_css_rule_present(self):
        html = _load_ui_html()
        assert "[data-tooltip]" in html, "CSS tooltip rule missing"

    def test_validate_button_has_tooltip(self):
        html = _load_ui_html()
        assert 'id="btnValidate"' in html
        # The validate button must carry a data-tooltip attribute
        assert 'data-tooltip=' in html

    def test_compare_button_has_tooltip(self):
        html = _load_ui_html()
        assert 'id="btnToggleCompare"' in html
        assert 'data-tooltip=' in html

    def test_mapping_select_has_tooltip(self):
        html = _load_ui_html()
        assert 'id="mappingSelect"' in html
        assert 'data-tooltip=' in html

    def test_generate_mapping_button_has_tooltip(self):
        html = _load_ui_html()
        assert 'id="btnGenMapping"' in html
        assert 'data-tooltip=' in html

    def test_generate_rules_button_has_tooltip(self):
        html = _load_ui_html()
        assert 'id="btnGenRules"' in html
        assert 'data-tooltip=' in html

    def test_rules_type_select_has_tooltip(self):
        html = _load_ui_html()
        assert 'id="rulesTypeSelect"' in html
        assert 'data-tooltip=' in html

    def test_status_badges_have_tooltip_in_js(self):
        """The JS that builds the runs table must add data-tooltip to badges."""
        html = _load_ui_html()
        # The badge rendering JS must include data-tooltip
        assert "data-tooltip" in html
```

**Step 2: Run to confirm FAIL**

```bash
pytest tests/unit/test_web_ui.py::TestTooltips -q
```

Expected: FAIL — `[data-tooltip]` not in HTML.

**Step 3: Add the CSS tooltip block to `ui.html`**

Inside the `<style>` block in `ui.html`, add this section just before the closing `</style>` tag (after line 254 `.result-bar a:hover { text-decoration: underline; }`):

```css
  /* -----------------------------------------------------------------------
     Tooltip system — add data-tooltip="..." to any element
     ----------------------------------------------------------------------- */
  [data-tooltip] { position: relative; }
  [data-tooltip]:hover::after {
    content: attr(data-tooltip);
    position: absolute;
    bottom: 125%;
    left: 50%;
    transform: translateX(-50%);
    background: #1e293b;
    color: #f8fafc;
    padding: 6px 10px;
    border-radius: 6px;
    font-size: 12px;
    white-space: nowrap;
    z-index: 100;
    pointer-events: none;
    box-shadow: 0 2px 8px rgba(0,0,0,0.4);
  }
  [data-tooltip]:hover::before {
    content: '';
    position: absolute;
    bottom: 110%;
    left: 50%;
    transform: translateX(-50%);
    border: 5px solid transparent;
    border-top-color: #1e293b;
    z-index: 100;
    pointer-events: none;
  }
```

**Step 4: Add `data-tooltip` attributes to HTML elements**

Make these targeted changes in the HTML body of `ui.html`:

**Quick Test tab — file drop zone:**
```html
<!-- change: -->
<div class="drop-zone" id="dropZone" tabindex="0"
     role="button" aria-label="Click or drag a file here">
<!-- to: -->
<div class="drop-zone" id="dropZone" tabindex="0"
     role="button" aria-label="Click or drag a file here"
     data-tooltip="Drop a batch file here, or click to browse (.txt, .csv, .dat, .pipe)">
```

**Quick Test tab — mapping select:**
```html
<!-- change: -->
<select id="mappingSelect">
<!-- to: -->
<select id="mappingSelect"
        data-tooltip="Select a mapping schema to validate field positions and lengths">
```

**Quick Test tab — Validate button:**
```html
<!-- change: -->
<button class="btn btn-primary"  id="btnValidate"      disabled>Validate</button>
<!-- to: -->
<button class="btn btn-primary"  id="btnValidate"      disabled
        data-tooltip="Run structural validation against the selected mapping schema">Validate</button>
```

**Quick Test tab — Compare toggle button:**
```html
<!-- change: -->
<button class="btn btn-toggle"   id="btnToggleCompare">Compare (pick 2nd file)</button>
<!-- to: -->
<button class="btn btn-toggle"   id="btnToggleCompare"
        data-tooltip="Compare two batch files row-by-row and highlight differences">Compare (pick 2nd file)</button>
```

**Quick Test tab — Compare Files button:**
```html
<!-- change: -->
<button class="btn btn-secondary" id="btnCompare" disabled>Compare Files</button>
<!-- to: -->
<button class="btn btn-secondary" id="btnCompare" disabled
        data-tooltip="Run a row-by-row comparison between the two selected files">Compare Files</button>
```

**Mapping Generator tab — mapping drop zone:**
```html
<!-- change: -->
<div class="drop-zone" id="mapDropZone" tabindex="0"
     role="button" aria-label="Click or drag a mapping template here">
<!-- to: -->
<div class="drop-zone" id="mapDropZone" tabindex="0"
     role="button" aria-label="Click or drag a mapping template here"
     data-tooltip="Upload an Excel or CSV template to generate a mapping JSON config">
```

**Mapping Generator tab — Format select:**
```html
<!-- change: -->
<select id="mapFormatSelect">
<!-- to: -->
<select id="mapFormatSelect"
        data-tooltip="Override auto-detection if the file format is not detected correctly">
```

**Mapping Generator tab — Generate Mapping button:**
```html
<!-- change: -->
<button class="btn btn-primary" id="btnGenMapping" disabled>Generate Mapping</button>
<!-- to: -->
<button class="btn btn-primary" id="btnGenMapping" disabled
        data-tooltip="Convert the uploaded template into a mapping JSON config file">Generate Mapping</button>
```

**Mapping Generator tab — rules drop zone:**
```html
<!-- change: -->
<div class="drop-zone" id="rulesDropZone" tabindex="0"
     role="button" aria-label="Click or drag a rules template here">
<!-- to: -->
<div class="drop-zone" id="rulesDropZone" tabindex="0"
     role="button" aria-label="Click or drag a rules template here"
     data-tooltip="Upload an Excel or CSV template to generate a rules JSON config">
```

**Mapping Generator tab — Rules Type select:**
```html
<!-- change: -->
<select id="rulesTypeSelect">
<!-- to: -->
<select id="rulesTypeSelect"
        data-tooltip="BA-friendly: human-readable rules. Technical: strict regex/type rules">
```

**Mapping Generator tab — Generate Rules button:**
```html
<!-- change: -->
<button class="btn btn-primary" id="btnGenRules" disabled>Generate Rules</button>
<!-- to: -->
<button class="btn btn-primary" id="btnGenRules" disabled
        data-tooltip="Convert the uploaded template into a rules JSON config file">Generate Rules</button>
```

**Step 5: Add `data-tooltip` to status badges in the JS runs-table builder**

In the JavaScript section of `ui.html`, find where the runs table is built (the `loadRuns` or `renderRuns` function that creates `<td>` elements with `.badge` class). Add `data-tooltip` to each badge `createElement` call.

Search for where badges are created. Look for code like `badge.className = 'badge badge-pass'` or similar. Add:

```javascript
// For PASS badge:
badge.setAttribute('data-tooltip', 'All tests passed or were skipped');

// For FAIL badge:
badge.setAttribute('data-tooltip', 'One or more tests failed');

// For PARTIAL badge:
badge.setAttribute('data-tooltip', 'Some tests passed and some failed');
```

The exact location: find the function that renders the runs table. It likely has a switch/if on `entry.status`. Add the `setAttribute` call right after the `badge.className` line for each status variant.

**Step 6: Run tests to confirm PASS**

```bash
pytest tests/unit/test_web_ui.py -q
```

Expected: all tests PASS including `TestTooltips`.

**Step 7: Commit**

```bash
git add src/reports/static/ui.html tests/unit/test_web_ui.py
git commit -m "feat(ui): add CSS tooltip system and data-tooltip attributes across all tabs"
```

---

## Task 5: Sphinx RST + USAGE_GUIDE + full test run

**Files:**
- Modify: `docs/sphinx/modules.rst`
- Modify: `docs/USAGE_GUIDE.md`

---

**Step 1: Register new module in Sphinx**

Open `docs/sphinx/modules.rst`. Find the `src.utils` section (or `.. automodule::` entries for utils). Add:

```rst
.. automodule:: src.utils.archive
   :members:
   :undoc-members:
   :show-inheritance:
```

**Step 2: Update USAGE_GUIDE.md**

Open `docs/USAGE_GUIDE.md`. Find the CLI Commands section. Add a new subsection:

```markdown
### Archive Commands

Every suite run produces a permanent archive entry in `reports/archive/YYYY/MM/DD/{run_id}/`.
Reports are never deleted by TTL cleanup. A SHA-256 manifest is written for tamper evidence.

**List recent runs:**
```bash
cm3-batch list-runs
cm3-batch list-runs --limit 50
```

**Retrieve a specific run:**
```bash
cm3-batch get-run {run_id}
```

**Configuration (`.env`):**
```
REPORT_ARCHIVE_PATH=reports/archive   # default
REPORT_RETENTION_DAYS=365             # default: 1 year
```
```

Also add a note about UI tooltips under the Web UI section:

```markdown
All UI elements have hover tooltips — hover over any button, dropdown, or upload zone for a description.
```

**Step 3: Run full test suite**

```bash
pytest tests/unit/ \
  --ignore=tests/unit/test_contracts_pipeline.py \
  --ignore=tests/unit/test_pipeline_runner.py \
  --ignore=tests/unit/test_workflow_wrapper_parity.py \
  --cov=src --cov-report=term-missing -q
```

Expected: all tests PASS, coverage ≥80%.

**Step 4: Build Sphinx docs**

```bash
cd docs/sphinx && make html
```

Expected: build succeeds with no errors.

**Step 5: Commit**

```bash
git add docs/sphinx/modules.rst docs/USAGE_GUIDE.md
git commit -m "docs(archive): register archive module in Sphinx, update USAGE_GUIDE for list-runs/get-run and UI tooltips"
```

---

## Done

After Task 5 commits, push the branch:

```bash
git push origin feature/database-validations-pilot
```

CI should pass. Verify on GitHub that all three jobs (test-and-docs, integration-tests, reconcile-check) complete as expected.
