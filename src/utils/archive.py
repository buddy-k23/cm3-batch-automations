"""Tamper-evident archive manager for suite run reports (#28)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

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
        seen_names: set[str] = set()
        for src_path_str in files:
            src = Path(src_path_str)
            if not src.exists():
                continue
            name = src.name
            if name in seen_names:
                stem, suffix = src.stem, src.suffix
                counter = 1
                while name in seen_names:
                    name = f"{stem}_{counter}{suffix}"
                    counter += 1
            seen_names.add(name)
            dst = run_dir / name
            shutil.copy2(str(src), str(dst))
            archived.append({"name": name, "sha256": _sha256_file(dst)})

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
                logger.warning("Skipping unreadable manifest: %s", manifest_path, exc_info=True)
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
                logger.warning("Skipping unreadable manifest: %s", manifest_path, exc_info=True)
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
                logger.warning("Could not delete run dir: %s", run_dir, exc_info=True)
                continue

        return deleted
