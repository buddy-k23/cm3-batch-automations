"""File downloader service — path validation, browse, archive handling, and search."""

import fnmatch
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Literal, Optional

_ARCHIVE_SUFFIXES = (".tar.gz", ".tgz", ".zip")
_MAX_SEARCH_RESULTS = 50


def _is_archive(name: str) -> bool:
    return any(name.endswith(s) for s in _ARCHIVE_SUFFIXES)


@dataclass
class BrowseEntry:
    """A file or archive entry returned by browse_path."""

    name: str
    type: Literal["plain", "archive"]
    size_bytes: int


@dataclass
class SearchHit:
    """A single line match from a search operation."""

    file: str
    line: int
    content: str
    archive: Optional[str] = None


@dataclass
class DownloadRef:
    """Reference for the UI to issue a targeted download after a truncated search."""

    path: str
    filename: str
    archive: Optional[str] = None


@dataclass
class SearchResult:
    """Result of a search operation."""

    results: list = field(default_factory=list)
    truncated: bool = False
    total_matches: int = 0
    shown: int = 0
    download_ref: Optional[DownloadRef] = None


def validate_path(requested: str, allowed_paths: list[str]) -> Path:
    """Resolve *requested* and verify it is under one of *allowed_paths*.

    Args:
        requested: Path string from user input or config.
        allowed_paths: Allowed base paths from file-downloader.yml.

    Returns:
        Resolved ``Path``.

    Raises:
        ValueError: If *requested* is not under any allowed base path.
    """
    resolved = Path(requested).resolve()
    for allowed in allowed_paths:
        try:
            resolved.relative_to(Path(allowed).resolve())
            return resolved
        except ValueError:
            continue
    raise ValueError(f"Path '{requested}' is not within any configured allowed path")


def browse_path(path: Path, pattern: Optional[str] = None) -> list:
    """List files in *path*, optionally filtered by *pattern*.

    Args:
        path: Directory to list (already validated).
        pattern: Optional ``fnmatch`` wildcard (e.g. ``"batch_*.tar.gz"``).

    Returns:
        Sorted list of :class:`BrowseEntry` objects (directories excluded).
    """
    if not path.exists():
        raise FileNotFoundError(f"Directory not found: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"Not a directory: {path}")
    entries: list[BrowseEntry] = []
    for item in sorted(path.iterdir()):
        if not item.is_file():
            continue
        if pattern and not fnmatch.fnmatch(item.name, pattern):
            continue
        entry_type: Literal["plain", "archive"] = "archive" if _is_archive(item.name) else "plain"
        entries.append(BrowseEntry(name=item.name, type=entry_type, size_bytes=item.stat().st_size))
    return entries


def list_archive_contents(archive_path: Path) -> list:
    """List files inside *archive_path* (.tar.gz, .tgz, or .zip).

    Args:
        archive_path: Path to the archive file.

    Returns:
        List of inner file paths (directories excluded).

    Raises:
        ValueError: If the archive format is not supported.
    """
    name = archive_path.name
    if name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive_path, "r:gz") as tf:
            return [m.name for m in tf.getmembers() if m.isfile()]
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive_path, "r") as zf:
            return [n for n in zf.namelist() if not n.endswith("/")]
    raise ValueError(f"Unsupported archive format: {name}")


def extract_file(archive_path: Path, inner_filename: str) -> Iterator[bytes]:
    """Stream *inner_filename* from *archive_path* in 64 KB chunks.

    Args:
        archive_path: Path to the archive file.
        inner_filename: Exact path of the file inside the archive.

    Yields:
        Raw byte chunks for a ``StreamingResponse``.

    Raises:
        FileNotFoundError: If *inner_filename* is not in the archive.
        ValueError: If the archive format is not supported.
    """
    name = archive_path.name
    chunk = 65536
    if name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive_path, "r:gz") as tf:
            try:
                member = tf.getmember(inner_filename)
            except KeyError:
                raise FileNotFoundError(f"{inner_filename!r} not in {archive_path.name}")
            fh = tf.extractfile(member)
            if fh is None:
                raise FileNotFoundError(f"{inner_filename!r} is not a regular file")
            while True:
                data = fh.read(chunk)
                if not data:
                    break
                yield data
        return
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive_path, "r") as zf:
            try:
                fh_ctx = zf.open(inner_filename)
            except KeyError:
                raise FileNotFoundError(f"{inner_filename!r} not in {archive_path.name}")
            with fh_ctx:
                while True:
                    data = fh_ctx.read(chunk)
                    if not data:
                        break
                    yield data
        return
    raise ValueError(f"Unsupported archive format: {name}")
