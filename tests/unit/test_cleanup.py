"""Unit tests for src/utils/cleanup.py."""
import os
import stat
import time
from pathlib import Path

import pytest

from src.utils.cleanup import cleanup_old_files


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_file(path: Path, content: str = "x") -> Path:
    """Write a file and return its Path."""
    path.write_text(content)
    return path


def _set_mtime(path: Path, age_hours: float) -> None:
    """Back-date a file's mtime by age_hours hours from now."""
    old_time = time.time() - age_hours * 3600
    os.utime(path, (old_time, old_time))


# ---------------------------------------------------------------------------
# Basic deletion tests
# ---------------------------------------------------------------------------


class TestCleanupOldFiles:
    def test_deletes_files_older_than_ttl(self, tmp_path):
        """Files whose mtime is beyond the TTL are deleted."""
        old_file = _make_file(tmp_path / "old.txt", "old content")
        _set_mtime(old_file, age_hours=25)  # older than default 24 h

        result = cleanup_old_files(tmp_path, max_age_hours=24)

        assert not old_file.exists(), "Old file should have been deleted"
        assert result["deleted_count"] == 1
        assert result["deleted_bytes"] == len("old content")
        assert result["errors"] == []

    def test_keeps_files_newer_than_ttl(self, tmp_path):
        """Files whose mtime is within the TTL window are kept."""
        new_file = _make_file(tmp_path / "new.txt", "new content")
        _set_mtime(new_file, age_hours=1)  # well within 24 h window

        result = cleanup_old_files(tmp_path, max_age_hours=24)

        assert new_file.exists(), "New file should NOT have been deleted"
        assert result["deleted_count"] == 0
        assert result["deleted_bytes"] == 0
        assert result["errors"] == []

    def test_mixed_files_only_old_deleted(self, tmp_path):
        """Only the old file is removed when both old and new files exist."""
        old_file = _make_file(tmp_path / "old.dat", "old")
        new_file = _make_file(tmp_path / "new.dat", "new content here")
        _set_mtime(old_file, age_hours=48)
        _set_mtime(new_file, age_hours=2)

        result = cleanup_old_files(tmp_path, max_age_hours=24)

        assert not old_file.exists()
        assert new_file.exists()
        assert result["deleted_count"] == 1
        assert result["deleted_bytes"] == len("old")
        assert result["errors"] == []

    def test_return_dict_counts_bytes_correctly(self, tmp_path):
        """deleted_bytes reflects the sum of sizes of deleted files."""
        contents = ["aaaa", "bbbbbb", "cccccccc"]  # 4, 6, 8 bytes
        expected_bytes = sum(len(c) for c in contents)

        for i, c in enumerate(contents):
            f = _make_file(tmp_path / f"file_{i}.txt", c)
            _set_mtime(f, age_hours=30)

        result = cleanup_old_files(tmp_path, max_age_hours=24)

        assert result["deleted_count"] == 3
        assert result["deleted_bytes"] == expected_bytes
        assert result["errors"] == []

    def test_empty_directory_returns_zero_counts(self, tmp_path):
        """Running cleanup on an empty directory returns zeroed result."""
        result = cleanup_old_files(tmp_path, max_age_hours=24)

        assert result == {"deleted_count": 0, "deleted_bytes": 0, "errors": []}

    def test_nonexistent_directory_returns_zero_counts(self, tmp_path):
        """A directory that does not exist is handled gracefully."""
        missing = tmp_path / "does_not_exist"
        result = cleanup_old_files(missing, max_age_hours=24)

        assert result == {"deleted_count": 0, "deleted_bytes": 0, "errors": []}

    def test_accepts_string_path(self, tmp_path):
        """cleanup_old_files accepts a plain string as the directory argument."""
        old_file = _make_file(tmp_path / "old.txt", "data")
        _set_mtime(old_file, age_hours=25)

        result = cleanup_old_files(str(tmp_path), max_age_hours=24)

        assert result["deleted_count"] == 1


# ---------------------------------------------------------------------------
# Subdirectory safety
# ---------------------------------------------------------------------------


class TestSubdirectoryProtection:
    def test_subdirectories_are_not_deleted(self, tmp_path):
        """Subdirectories must never be deleted, even if they are 'old'."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        # Back-date the subdir itself
        old_time = time.time() - 48 * 3600
        os.utime(subdir, (old_time, old_time))

        result = cleanup_old_files(tmp_path, max_age_hours=24)

        assert subdir.is_dir(), "Subdirectory must survive cleanup"
        assert result["deleted_count"] == 0
        assert result["errors"] == []

    def test_files_inside_subdirectory_not_touched(self, tmp_path):
        """Files inside subdirectories are not visited (non-recursive walk)."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        nested_old = _make_file(subdir / "nested_old.txt", "nested")
        _set_mtime(nested_old, age_hours=100)

        result = cleanup_old_files(tmp_path, max_age_hours=24)

        assert nested_old.exists(), "Nested files must not be deleted in non-recursive mode"
        assert result["deleted_count"] == 0


# ---------------------------------------------------------------------------
# Error handling — no exceptions raised
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_errors_collected_not_raised_on_permission_denied(self, tmp_path):
        """A file that cannot be deleted should populate errors, not raise."""
        protected = _make_file(tmp_path / "protected.txt", "locked")
        _set_mtime(protected, age_hours=48)

        # Make the file read-only so unlink will fail on non-root Unix systems.
        protected.chmod(0o444)
        # Also make the parent directory non-writable so the file truly can't be deleted
        tmp_path.chmod(0o555)

        try:
            result = cleanup_old_files(tmp_path, max_age_hours=24)
            # On Linux as non-root: delete should fail → error recorded
            # On macOS as non-root: same expectation
            # If running as root the delete may succeed — accept either outcome.
            if result["errors"]:
                assert result["deleted_count"] == 0
                assert len(result["errors"]) == 1
                assert "protected.txt" in result["errors"][0]
            else:
                # Root or OS-specific path: file was deleted — verify counts are sane
                assert result["deleted_count"] <= 1
        finally:
            # Restore permissions for cleanup
            tmp_path.chmod(0o755)
            try:
                protected.chmod(0o644)
            except FileNotFoundError:
                pass

    def test_returns_dict_with_required_keys_on_error(self, tmp_path):
        """Result dict always contains deleted_count, deleted_bytes, errors."""
        result = cleanup_old_files(tmp_path, max_age_hours=24)
        assert "deleted_count" in result
        assert "deleted_bytes" in result
        assert "errors" in result
        assert isinstance(result["errors"], list)


# ---------------------------------------------------------------------------
# Custom TTL
# ---------------------------------------------------------------------------


class TestCustomTTL:
    def test_custom_short_ttl_deletes_recent_files(self, tmp_path):
        """A very short TTL (0.01 h = ~36 s) should catch files just created."""
        recent = _make_file(tmp_path / "recent.txt", "fresh")
        # Back-date by 1 hour — older than 0.01 h TTL
        _set_mtime(recent, age_hours=1)

        result = cleanup_old_files(tmp_path, max_age_hours=0.01)

        assert not recent.exists()
        assert result["deleted_count"] == 1

    def test_zero_byte_file_counted_correctly(self, tmp_path):
        """A zero-byte old file contributes 0 to deleted_bytes but 1 to deleted_count."""
        empty = _make_file(tmp_path / "empty.txt", "")
        _set_mtime(empty, age_hours=25)

        result = cleanup_old_files(tmp_path, max_age_hours=24)

        assert result["deleted_count"] == 1
        assert result["deleted_bytes"] == 0
        assert result["errors"] == []
