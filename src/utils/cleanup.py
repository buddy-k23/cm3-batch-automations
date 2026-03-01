"""TTL-based cleanup for temporary and uploaded files."""
import os
import time
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def cleanup_old_files(directory: "str | Path", max_age_hours: float = 24) -> dict:
    """Delete files older than max_age_hours from directory.

    Walks the top-level directory (non-recursive) and removes any file whose
    modification time is older than max_age_hours * 3600 seconds.  Subdirectories
    are never deleted.  Per-file errors are collected and returned rather than
    raised so that a single unreadable file cannot abort an entire cleanup run.

    Args:
        directory: Path to the directory to clean.
        max_age_hours: Maximum age of files to keep, in hours. Defaults to 24.

    Returns:
        dict with keys:
            deleted_count (int): Number of files deleted.
            deleted_bytes (int): Total bytes freed.
            errors (list[str]): Human-readable error messages for failed deletes.
    """
    result: dict = {"deleted_count": 0, "deleted_bytes": 0, "errors": []}

    directory = Path(directory)
    if not directory.exists():
        logger.debug("cleanup_old_files: directory does not exist: %s", directory)
        return result

    cutoff = time.time() - max_age_hours * 3600

    try:
        entries = list(directory.iterdir())
    except OSError as exc:
        msg = f"Cannot list directory {directory}: {exc}"
        logger.error(msg)
        result["errors"].append(msg)
        return result

    for entry in entries:
        # Never attempt to delete subdirectories.
        if not entry.is_file():
            continue

        try:
            stat = entry.stat()
        except OSError as exc:
            msg = f"Cannot stat {entry.name}: {exc}"
            logger.warning(msg)
            result["errors"].append(msg)
            continue

        if stat.st_mtime >= cutoff:
            # File is within the retention window — keep it.
            continue

        file_size = stat.st_size
        try:
            entry.unlink()
            result["deleted_count"] += 1
            result["deleted_bytes"] += file_size
            logger.info(
                "Deleted old file: %s (mtime=%s, size=%d bytes)",
                entry.name,
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                file_size,
            )
        except OSError as exc:
            msg = f"Failed to delete {entry.name}: {exc}"
            logger.error(msg)
            result["errors"].append(msg)

    return result
