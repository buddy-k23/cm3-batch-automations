"""File downloader service — path validation, browse, archive handling, and search."""

import fnmatch
import logging
import shutil
import subprocess
import tarfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Literal, Optional

_ARCHIVE_SUFFIXES = (".tar.gz", ".tgz", ".zip")
_MAX_SEARCH_RESULTS = 50
_DEFAULT_SEARCH_TIMEOUT_SECONDS = 30
_log = logging.getLogger(__name__)
_GREP_AVAILABLE = bool(shutil.which("grep"))


# ---------------------------------------------------------------------------
# Search cancellation / timeout
# ---------------------------------------------------------------------------

class SearchCancelled(Exception):
    """Raised internally when a search is cancelled by the user or times out.

    Carries a ``reason`` of ``"timeout"`` or ``"cancelled"`` so callers can
    distinguish between an automatic kill and a user-initiated stop.
    """

    def __init__(self, reason: str = "cancelled"):
        super().__init__(reason)
        self.reason = reason


class SearchToken:
    """Cooperative cancellation/timeout token shared across a single search.

    The search code path checks :meth:`raise_if_done` at coarse-grained points
    (per file, per archive member, per output line) so a kill takes effect
    promptly without leaking resources. Any in-flight grep subprocess is
    registered via :meth:`track_process` so it can be terminated immediately.

    Attributes:
        search_id: Opaque UUID assigned to this search.
        deadline:  Monotonic clock value after which the search times out.
                   ``None`` disables the timeout (test/debug only).
    """

    def __init__(self, search_id: str, timeout_seconds: Optional[float]):
        self.search_id = search_id
        self.deadline: Optional[float] = (
            time.monotonic() + timeout_seconds if timeout_seconds else None
        )
        self._cancel_event = threading.Event()
        self._cancel_reason: Optional[str] = None
        self._procs: list = []
        self._lock = threading.Lock()

    # -- query --------------------------------------------------------------

    def is_done(self) -> Optional[str]:
        """Return the kill reason (``"timeout"``/``"cancelled"``) or ``None``."""
        if self._cancel_event.is_set():
            return self._cancel_reason or "cancelled"
        if self.deadline is not None and time.monotonic() >= self.deadline:
            return "timeout"
        return None

    def raise_if_done(self) -> None:
        """Raise :class:`SearchCancelled` if the search has been killed."""
        reason = self.is_done()
        if reason:
            raise SearchCancelled(reason)

    # -- mutate -------------------------------------------------------------

    def cancel(self, reason: str = "cancelled") -> None:
        """Mark this search as cancelled and terminate any tracked subprocesses."""
        with self._lock:
            if self._cancel_event.is_set():
                return
            self._cancel_reason = reason
            self._cancel_event.set()
            procs, self._procs = self._procs, []
        for p in procs:
            try:
                if p.poll() is None:
                    p.terminate()
            except Exception:
                pass

    def track_process(self, proc) -> None:
        """Register a subprocess so it can be terminated on cancel/timeout."""
        with self._lock:
            if self._cancel_event.is_set() or (
                self.deadline is not None and time.monotonic() >= self.deadline
            ):
                # Already done — kill immediately rather than leaking the proc.
                try:
                    if proc.poll() is None:
                        proc.terminate()
                except Exception:
                    pass
                return
            self._procs.append(proc)

    def untrack_process(self, proc) -> None:
        """Remove a finished subprocess from the tracking list."""
        with self._lock:
            try:
                self._procs.remove(proc)
            except ValueError:
                pass


class _SearchRegistry:
    """Process-wide registry of in-flight searches keyed by ``search_id``.

    Lets the HTTP cancel endpoint find and kill a search started by a separate
    request. Entries are removed when the search finishes or is cancelled.
    """

    def __init__(self):
        self._by_id: dict = {}
        self._lock = threading.Lock()

    def register(self, token: SearchToken) -> None:
        with self._lock:
            self._by_id[token.search_id] = token

    def unregister(self, search_id: str) -> None:
        with self._lock:
            self._by_id.pop(search_id, None)

    def cancel(self, search_id: str) -> bool:
        """Cancel the search with *search_id*. Returns True if it existed."""
        with self._lock:
            token = self._by_id.get(search_id)
        if token is None:
            return False
        token.cancel("cancelled")
        return True


_search_registry = _SearchRegistry()


def get_search_registry() -> _SearchRegistry:
    """Return the process-wide search registry (used by the API router)."""
    return _search_registry


def new_search_token(timeout_seconds: Optional[float], search_id: Optional[str] = None) -> SearchToken:
    """Allocate a :class:`SearchToken` and register it for remote cancellation.

    Args:
        timeout_seconds: Per-search timeout. ``None`` disables the auto-kill.
        search_id:       Optional client-supplied id (must be a string). When
                         omitted, a UUID4 is generated.

    Returns:
        The newly registered :class:`SearchToken`. Callers must invoke
        :func:`release_search_token` when done (typically in a ``finally``).
    """
    sid = search_id or uuid.uuid4().hex
    token = SearchToken(sid, timeout_seconds)
    _search_registry.register(token)
    return token


def release_search_token(token: SearchToken) -> None:
    """Remove *token* from the registry. Safe to call multiple times."""
    _search_registry.unregister(token.search_id)


def _is_archive(name: str) -> bool:
    return any(name.endswith(s) for s in _ARCHIVE_SUFFIXES)


def _safe_inner_path(name: str) -> bool:
    """Return True if *name* is a safe archive inner path.

    Rejects absolute paths, paths containing '..' components, and paths
    containing null bytes to prevent path traversal attacks from crafted archives.

    Args:
        name: The inner path string from an archive member.

    Returns:
        True if the path is safe to use, False otherwise.
    """
    if '\x00' in name:
        return False
    p = Path(name)
    return not p.is_absolute() and '..' not in p.parts


@dataclass
class BrowseEntry:
    """A file, archive, or directory entry returned by browse_path."""

    name: str
    type: Literal["plain", "archive", "directory"]
    size_bytes: Optional[int] = None


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
    """Result of a search operation.

    The ``stopped_reason`` field is set when the search was terminated early:

    * ``"timeout"``   — server-side wall-clock budget exceeded.
    * ``"cancelled"`` — user-initiated stop via the cancel endpoint.

    Partial results gathered before the kill are still returned so the UI can
    show what was found before the search ended.
    """

    results: list = field(default_factory=list)
    truncated: bool = False
    total_matches: int = 0
    shown: int = 0
    download_ref: Optional[DownloadRef] = None
    stopped_reason: Optional[str] = None
    search_id: Optional[str] = None


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
    """List files and subdirectories in *path*, optionally filtering files by *pattern*.

    Subdirectories are always included (pattern does not apply to them) and
    are sorted before files so dated folders (e.g. ``20260406/``) appear at
    the top of the results.

    Args:
        path: Directory to list (already validated).
        pattern: Optional ``fnmatch`` wildcard applied to files only
            (e.g. ``"batch_*.tar.gz"``).  Directories are never filtered.

    Returns:
        Sorted list of :class:`BrowseEntry` objects — directories first,
        then files.
    """
    if not path.exists():
        raise FileNotFoundError(f"Directory not found: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"Not a directory: {path}")
    dirs: list[BrowseEntry] = []
    files: list[BrowseEntry] = []
    for item in sorted(path.iterdir()):
        if item.is_dir():
            dirs.append(BrowseEntry(name=item.name, type="directory", size_bytes=None))
        elif item.is_file():
            if pattern and not fnmatch.fnmatch(item.name, pattern):
                continue
            entry_type: Literal["plain", "archive"] = "archive" if _is_archive(item.name) else "plain"
            files.append(BrowseEntry(name=item.name, type=entry_type, size_bytes=item.stat().st_size))
    return dirs + files


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
            result = []
            for m in tf.getmembers():
                if not m.isfile():
                    continue
                if not _safe_inner_path(m.name):
                    _log.warning("Skipping unsafe inner path in archive: %r", m.name)
                    continue
                result.append(m.name)
            return result
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive_path, "r") as zf:
            result = []
            for n in zf.namelist():
                if n.endswith("/"):
                    continue
                if not _safe_inner_path(n):
                    _log.warning("Skipping unsafe inner path in archive: %r", n)
                    continue
                result.append(n)
            return result
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
    if not _safe_inner_path(inner_filename):
        raise ValueError(f"Unsafe archive inner path: {inner_filename!r}")
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


def _grep_search_file(filepath: Path, search_string: str,
                      token: Optional[SearchToken] = None) -> tuple:
    """Search *search_string* in *filepath* using grep.

    Uses grep -Fn (fixed string, line numbers) capped at _MAX_SEARCH_RESULTS + 1
    hits so we can detect truncation without scanning the whole file.

    When a :class:`SearchToken` is supplied, the underlying grep process is
    tracked so it can be terminated immediately on cancel/timeout.

    Args:
        filepath: Path to the plain file to search.
        search_string: Literal string to find.
        token: Optional cancellation token.

    Returns:
        Tuple of (hits, total) where hits is a list of SearchHit objects
        (capped at _MAX_SEARCH_RESULTS) and total is the full match count.
    """
    cmd = ["grep", "-Fn", "-m", str(_MAX_SEARCH_RESULTS + 1), "--", search_string, str(filepath)]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True)
    except OSError:
        return [], 0
    if token is not None:
        token.track_process(proc)
    try:
        stdout, _ = proc.communicate()
    finally:
        if token is not None:
            token.untrack_process(proc)
    # Preserve previous attribute name used below for stdout text.
    proc = type("_P", (), {"stdout": stdout})()
    hits = []
    total = 0
    for raw_line in proc.stdout.splitlines():
        colon = raw_line.find(":")
        if colon == -1:
            continue
        total += 1
        if len(hits) < _MAX_SEARCH_RESULTS:
            try:
                lineno = int(raw_line[:colon])
            except ValueError:
                continue
            hits.append(SearchHit(file=filepath.name, line=lineno, content=raw_line[colon + 1:]))
    return hits, total


def search_in_files(path: Path, filename_pattern: str, search_string: str,
                    token: Optional[SearchToken] = None) -> SearchResult:
    """Search *search_string* in plain files matching *filename_pattern*.

    Uses ``grep -Fn`` when grep is available on the system (Linux/OpenShift),
    falling back to a pure-Python line scan otherwise (Windows, containers
    without grep).

    When a :class:`SearchToken` is supplied, the search is cooperatively
    cancellable: per-file and per-line checkpoints raise :class:`SearchCancelled`
    if the user cancels or the timeout fires. Any partial results gathered up
    to that point are returned in the :class:`SearchResult`.

    Args:
        path: Directory to search (already validated).
        filename_pattern: ``fnmatch`` wildcard for filenames (e.g. ``"*.log"``).
        search_string: Literal string to find in each line.
        token: Optional cancellation/timeout token.

    Returns:
        :class:`SearchResult` capped at ``_MAX_SEARCH_RESULTS`` hits.
        When truncated and hits come from one file, ``download_ref`` is set.
        When truncated across multiple files, ``download_ref`` is ``None`` —
        the UI must prompt the user to use a single exact filename.
        ``stopped_reason`` is set to ``"timeout"`` or ``"cancelled"`` when the
        search was killed early.
    """
    results: list = []
    total = 0
    matched_files: set = set()
    stopped_reason: Optional[str] = None

    try:
        for filepath in sorted(path.iterdir()):
            if token is not None:
                token.raise_if_done()
            if not filepath.is_file():
                continue
            if not fnmatch.fnmatch(filepath.name, filename_pattern):
                continue
            if _GREP_AVAILABLE:
                hits, count = _grep_search_file(filepath, search_string, token=token)
                if token is not None:
                    token.raise_if_done()
                if count > 0:
                    matched_files.add(filepath.name)
                results.extend(hits[:max(0, _MAX_SEARCH_RESULTS - len(results))])
                total += count
            else:
                try:
                    with filepath.open("r", errors="replace") as fh:
                        for lineno, line in enumerate(fh, 1):
                            # Cheap periodic checkpoint so big files stay killable.
                            if token is not None and lineno % 1024 == 0:
                                token.raise_if_done()
                            if search_string in line:
                                total += 1
                                matched_files.add(filepath.name)
                                if len(results) < _MAX_SEARCH_RESULTS:
                                    results.append(SearchHit(file=filepath.name, line=lineno, content=line.rstrip()))
                except OSError:
                    continue
    except SearchCancelled as exc:
        stopped_reason = exc.reason

    truncated = total > _MAX_SEARCH_RESULTS
    download_ref = None
    if truncated and len(matched_files) == 1:
        download_ref = DownloadRef(path=str(path), filename=next(iter(matched_files)))

    return SearchResult(results=results, truncated=truncated, total_matches=total,
                        shown=len(results), download_ref=download_ref,
                        stopped_reason=stopped_reason,
                        search_id=token.search_id if token else None)


def _read_inner_lines(archive_path: Path, inner_filename: str) -> list:
    """Read all lines from *inner_filename* inside *archive_path*.

    Args:
        archive_path: Path to the archive.
        inner_filename: Exact inner path to read.

    Returns:
        List of decoded lines (UTF-8, errors replaced).
    """
    content = b"".join(extract_file(archive_path, inner_filename))
    return content.decode("utf-8", errors="replace").splitlines()


def _grep_search_archive_file(
    archive_path: Path, inner_name: str, search_string: str,
    token: Optional[SearchToken] = None,
) -> tuple:
    """Search *search_string* in *inner_name* inside *archive_path* using grep.

    Pipes decompression output directly into grep so the full inner file is
    never loaded into memory. Supports .tar.gz/.tgz (via tar) and .zip (via
    unzip).

    Args:
        archive_path: Path to the archive file.
        inner_name: Inner file path (already validated by _safe_inner_path).
        search_string: Literal string to find.

    Returns:
        Tuple of (hits, total) where hits is a list of SearchHit objects
        (capped at _MAX_SEARCH_RESULTS) and total is the full match count.

    Raises:
        ValueError: If the archive format is not supported.
    """
    name = archive_path.name
    if name.endswith((".tar.gz", ".tgz")):
        decomp_cmd = ["tar", "-xzOf", str(archive_path), inner_name]
    elif name.endswith(".zip"):
        decomp_cmd = ["unzip", "-p", str(archive_path), inner_name]
    else:
        raise ValueError(f"Unsupported archive format: {name}")

    grep_cmd = ["grep", "-Fn", "-m", str(_MAX_SEARCH_RESULTS + 1), "--", search_string]

    try:
        decomp = subprocess.Popen(decomp_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        grep = subprocess.Popen(
            grep_cmd, stdin=decomp.stdout, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True,
        )
        decomp.stdout.close()  # allow decomp to receive SIGPIPE when grep exits
        if token is not None:
            token.track_process(decomp)
            token.track_process(grep)
        try:
            stdout, _ = grep.communicate()
            decomp.wait()
        finally:
            if token is not None:
                token.untrack_process(grep)
                token.untrack_process(decomp)
    except OSError:
        return [], 0

    hits = []
    total = 0
    for raw_line in stdout.splitlines():
        colon = raw_line.find(":")
        if colon == -1:
            continue
        total += 1
        if len(hits) < _MAX_SEARCH_RESULTS:
            try:
                lineno = int(raw_line[:colon])
            except ValueError:
                continue
            hits.append(SearchHit(
                file=inner_name, line=lineno,
                content=raw_line[colon + 1:], archive=archive_path.name,
            ))
    return hits, total


def search_archives(
    base_dir: str,
    archive_pattern: str,
    member_pattern: str,
) -> list:
    """Recursively search for files inside archives matching wildcard patterns.

    Walks *base_dir* recursively to find archive files (``.zip``, ``.tar.gz``,
    ``.tar``) whose filename matches *archive_pattern* (fnmatch). Then searches
    inside each matched archive for members whose basename matches
    *member_pattern*. No full extraction is performed.

    Args:
        base_dir: Root directory to walk recursively.
        archive_pattern: Wildcard pattern for archive filenames
            (e.g. ``"archive_*.zip"``).
        member_pattern: Wildcard pattern for member basenames
            (e.g. ``"TRANS_*.txt"``).

    Returns:
        List of dicts with keys:

        * ``archive_path``: Absolute path to the archive file on disk.
        * ``member_path``: Full internal path of the matching member.
    """
    results = []
    base = Path(base_dir)

    for archive_file in sorted(base.rglob("*")):
        if not archive_file.is_file():
            continue
        # Filter by supported archive extensions
        name = archive_file.name
        is_zip = name.endswith(".zip")
        is_tar_gz = name.endswith(".tar.gz") or name.endswith(".tgz")
        is_tar = name.endswith(".tar") and not is_tar_gz
        if not (is_zip or is_tar_gz or is_tar):
            continue
        # Filter archive filename by pattern
        if not fnmatch.fnmatch(name, archive_pattern):
            continue

        try:
            if is_zip:
                with zipfile.ZipFile(archive_file, "r") as zf:
                    for member in zf.namelist():
                        if fnmatch.fnmatch(Path(member).name, member_pattern):
                            results.append({
                                "archive_path": str(archive_file),
                                "member_path": member,
                            })
            else:
                # .tar.gz, .tgz, or plain .tar
                mode = "r:gz" if is_tar_gz else "r:"
                with tarfile.open(archive_file, mode) as tf:
                    for m in tf.getmembers():
                        if fnmatch.fnmatch(Path(m.name).name, member_pattern):
                            results.append({
                                "archive_path": str(archive_file),
                                "member_path": m.name,
                            })
        except Exception as exc:
            _log.warning("Skipping unreadable archive %s: %s", archive_file, exc)
            continue

    return results


def search_in_archives(
    path: Path,
    archive_pattern: str,
    file_pattern: str,
    search_string: str,
    token: Optional[SearchToken] = None,
) -> SearchResult:
    """Search *search_string* in archive inner files matching *file_pattern*.

    Uses ``grep`` piped from decompression when grep is available on the
    system (Linux/OpenShift), avoiding loading inner files fully into memory.
    Falls back to a pure-Python line scan otherwise (Windows, containers
    without grep).

    When a :class:`SearchToken` is supplied the search is cooperatively
    cancellable: per-archive, per-member, and per-line checkpoints raise
    :class:`SearchCancelled` if the user cancels or the timeout fires.

    Args:
        path: Directory to search (already validated).
        archive_pattern: ``fnmatch`` wildcard for archive filenames.
        file_pattern: ``fnmatch`` wildcard for inner filenames.
        search_string: Literal string to find.
        token: Optional cancellation/timeout token.

    Returns:
        :class:`SearchResult` capped at ``_MAX_SEARCH_RESULTS`` hits.
        ``download_ref`` populated when truncated from a single (archive, file)
        pair. Otherwise ``None`` — UI prompts user to refine to exact filename.
        ``stopped_reason`` is set to ``"timeout"`` or ``"cancelled"`` when the
        search was killed early.
    """
    results: list = []
    total = 0
    matched_pairs: set = set()
    stopped_reason: Optional[str] = None

    try:
        for archive_path in sorted(path.iterdir()):
            if token is not None:
                token.raise_if_done()
            if not archive_path.is_file():
                continue
            if not fnmatch.fnmatch(archive_path.name, archive_pattern):
                continue
            if not _is_archive(archive_path.name):
                continue
            try:
                inner_files = list_archive_contents(archive_path)
            except Exception:
                continue
            for inner_name in inner_files:
                if token is not None:
                    token.raise_if_done()
                if not fnmatch.fnmatch(Path(inner_name).name, file_pattern):
                    continue
                if _GREP_AVAILABLE:
                    try:
                        hits, count = _grep_search_archive_file(
                            archive_path, inner_name, search_string, token=token,
                        )
                    except Exception:
                        continue
                    if token is not None:
                        token.raise_if_done()
                    if count > 0:
                        matched_pairs.add((archive_path.name, inner_name))
                    results.extend(hits[:max(0, _MAX_SEARCH_RESULTS - len(results))])
                    total += count
                else:
                    try:
                        lines = _read_inner_lines(archive_path, inner_name)
                    except Exception:
                        continue
                    for lineno, line in enumerate(lines, 1):
                        if token is not None and lineno % 1024 == 0:
                            token.raise_if_done()
                        if search_string in line:
                            total += 1
                            matched_pairs.add((archive_path.name, inner_name))
                            if len(results) < _MAX_SEARCH_RESULTS:
                                results.append(SearchHit(file=inner_name, line=lineno,
                                                          content=line.rstrip(), archive=archive_path.name))
    except SearchCancelled as exc:
        stopped_reason = exc.reason

    truncated = total > _MAX_SEARCH_RESULTS
    download_ref = None
    if truncated and len(matched_pairs) == 1:
        arc_name, inner_name = next(iter(matched_pairs))
        download_ref = DownloadRef(path=str(path), archive=arc_name, filename=inner_name)

    return SearchResult(results=results, truncated=truncated, total_matches=total,
                        shown=len(results), download_ref=download_ref,
                        stopped_reason=stopped_reason,
                        search_id=token.search_id if token else None)
