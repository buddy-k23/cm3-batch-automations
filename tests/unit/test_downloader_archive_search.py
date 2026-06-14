# tests/unit/test_downloader_archive_search.py
"""Unit tests for downloader_service.search_archives (Issue #366)."""

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from src.services.downloader_service import search_archives


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_zip(path: Path, members: dict) -> Path:
    """Create a zip archive at *path* with *members* (name → bytes)."""
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def _make_tar_gz(path: Path, members: dict) -> Path:
    """Create a .tar.gz archive at *path* with *members* (name → bytes)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in members.items():
            raw = data if isinstance(data, bytes) else data.encode()
            info = tarfile.TarInfo(name=name)
            info.size = len(raw)
            tf.addfile(info, io.BytesIO(raw))
    path.write_bytes(buf.getvalue())
    return path


def _make_tar(path: Path, members: dict) -> Path:
    """Create a plain .tar archive at *path* with *members* (name → bytes)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:") as tf:
        for name, data in members.items():
            raw = data if isinstance(data, bytes) else data.encode()
            info = tarfile.TarInfo(name=name)
            info.size = len(raw)
            tf.addfile(info, io.BytesIO(raw))
    path.write_bytes(buf.getvalue())
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_search_zip_root_level(tmp_path):
    """zip at root of base_dir with a matching member is found."""
    _make_zip(tmp_path / "archive_2026.zip", {"TRANS_001.txt": b"hello"})
    results = search_archives(str(tmp_path), "archive_*.zip", "TRANS_*.txt")
    assert len(results) == 1
    assert results[0]["archive_path"] == str(tmp_path / "archive_2026.zip")
    assert results[0]["member_path"] == "TRANS_001.txt"


def test_search_zip_nested_2_levels(tmp_path):
    """zip nested two sub-directories deep is discovered via rglob."""
    nested = tmp_path / "subdir" / "subdir2"
    nested.mkdir(parents=True)
    _make_zip(nested / "archive_deep.zip", {"BATCH.txt": b"data"})
    results = search_archives(str(tmp_path), "archive_deep.zip", "BATCH.txt")
    assert len(results) == 1
    assert "subdir2" in results[0]["archive_path"]


def test_search_tar_gz(tmp_path):
    """.tar.gz archive member match is returned."""
    _make_tar_gz(tmp_path / "logs_2026.tar.gz", {"server.log": b"entry"})
    results = search_archives(str(tmp_path), "logs_*.tar.gz", "server.log")
    assert len(results) == 1
    assert results[0]["member_path"] == "server.log"


def test_search_tar(tmp_path):
    """Plain .tar archive member match is returned."""
    _make_tar(tmp_path / "backup.tar", {"data.csv": b"row1"})
    results = search_archives(str(tmp_path), "backup.tar", "data.csv")
    assert len(results) == 1
    assert results[0]["member_path"] == "data.csv"


def test_wildcard_archive_name(tmp_path):
    """archive_name_*.zip pattern matches only the targeted archive."""
    _make_zip(tmp_path / "archive_JAN.zip", {"report.txt": b"x"})
    _make_zip(tmp_path / "other_FEB.zip", {"report.txt": b"y"})
    results = search_archives(str(tmp_path), "archive_*.zip", "report.txt")
    paths = [r["archive_path"] for r in results]
    assert any("archive_JAN.zip" in p for p in paths)
    assert not any("other_FEB.zip" in p for p in paths)


def test_wildcard_member_name(tmp_path):
    """TRANS_*.txt wildcard matches a nested member."""
    _make_zip(tmp_path / "batch.zip", {"TRANS_20260401.txt": b"payload"})
    results = search_archives(str(tmp_path), "batch.zip", "TRANS_*.txt")
    assert len(results) == 1
    assert "TRANS_20260401.txt" in results[0]["member_path"]


def test_no_match_archive(tmp_path):
    """archive_pattern that matches no file returns empty list."""
    _make_zip(tmp_path / "data.zip", {"file.txt": b"content"})
    results = search_archives(str(tmp_path), "nonexistent_*.zip", "file.txt")
    assert results == []


def test_no_match_member(tmp_path):
    """Archive found but member pattern matches nothing returns empty list."""
    _make_zip(tmp_path / "batch.zip", {"other.csv": b"row"})
    results = search_archives(str(tmp_path), "batch.zip", "TRANS_*.txt")
    assert results == []


def test_member_in_nested_dir_inside_archive(tmp_path):
    """Member at data/2026/april/TRANS.txt is matched by TRANS*.txt via basename."""
    _make_zip(tmp_path / "archive.zip", {"data/2026/april/TRANS.txt": b"nested"})
    results = search_archives(str(tmp_path), "archive.zip", "TRANS*.txt")
    assert len(results) == 1
    assert results[0]["member_path"] == "data/2026/april/TRANS.txt"


def test_result_has_archive_path_and_member_path(tmp_path):
    """Every result dict has exactly 'archive_path' and 'member_path' keys."""
    _make_zip(tmp_path / "a.zip", {"f.txt": b"x"})
    results = search_archives(str(tmp_path), "a.zip", "f.txt")
    assert len(results) == 1
    assert set(results[0].keys()) == {"archive_path", "member_path"}


def test_corrupt_archive_skipped(tmp_path):
    """A corrupt/unreadable archive is silently skipped; valid ones still return results."""
    (tmp_path / "corrupt.zip").write_bytes(b"this is not a zip")
    _make_zip(tmp_path / "valid.zip", {"data.txt": b"ok"})
    results = search_archives(str(tmp_path), "*.zip", "data.txt")
    # corrupt archive is skipped; valid.zip contributes 1 result
    paths = [r["archive_path"] for r in results]
    assert any("valid.zip" in p for p in paths)
    assert not any("corrupt.zip" in p for p in paths)


def test_multiple_members_matched(tmp_path):
    """Multiple matching members in a single archive are all returned."""
    _make_zip(tmp_path / "multi.zip", {
        "TRANS_A.txt": b"a",
        "TRANS_B.txt": b"b",
        "OTHER.csv": b"c",
    })
    results = search_archives(str(tmp_path), "multi.zip", "TRANS_*.txt")
    member_names = {r["member_path"] for r in results}
    assert "TRANS_A.txt" in member_names
    assert "TRANS_B.txt" in member_names
    assert "OTHER.csv" not in member_names
