# tests/unit/test_downloader_service.py
import io
import tarfile
import zipfile
import pytest
from pathlib import Path
from src.services.downloader_service import (
    validate_path, browse_path, BrowseEntry, list_archive_contents,
    extract_file, search_in_files, search_in_archives,
    _safe_inner_path, _GREP_AVAILABLE, _grep_search_file,
    _grep_search_archive_file,
)


def test_validate_path_accepts_allowed(tmp_path):
    assert validate_path(str(tmp_path), [str(tmp_path)]) == tmp_path.resolve()


def test_validate_path_accepts_subpath(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    assert validate_path(str(sub), [str(tmp_path)]) == sub.resolve()


def test_validate_path_rejects_traversal(tmp_path):
    with pytest.raises(ValueError, match="not within any configured"):
        validate_path(str(tmp_path), [str(tmp_path / "safe")])


def test_validate_path_rejects_unlisted(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ValueError):
        validate_path(str(other), [str(tmp_path / "safe")])


def test_browse_lists_plain_files(tmp_path):
    (tmp_path / "report.csv").write_text("a,b")
    entries = browse_path(tmp_path)
    assert any(e.name == "report.csv" and e.type == "plain" for e in entries)


def test_browse_identifies_archives(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"x"
        info = tarfile.TarInfo(name="f.txt")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    (tmp_path / "a.tar.gz").write_bytes(buf.getvalue())
    with zipfile.ZipFile(tmp_path / "b.zip", "w") as zf:
        zf.writestr("f.txt", "x")
    buf2 = io.BytesIO()
    with tarfile.open(fileobj=buf2, mode="w:gz") as tf:
        data2 = b"y"
        info2 = tarfile.TarInfo(name="g.txt")
        info2.size = len(data2)
        tf.addfile(info2, io.BytesIO(data2))
    (tmp_path / "c.tgz").write_bytes(buf2.getvalue())
    types = {e.name: e.type for e in browse_path(tmp_path)}
    assert types["a.tar.gz"] == "archive"
    assert types["b.zip"] == "archive"
    assert types["c.tgz"] == "archive"


def test_browse_includes_subdirectories(tmp_path):
    (tmp_path / "20260406").mkdir()
    (tmp_path / "20260405").mkdir()
    (tmp_path / "report.txt").write_text("x")
    entries = browse_path(tmp_path)
    types = {e.name: e.type for e in entries}
    assert types["20260406"] == "directory"
    assert types["20260405"] == "directory"
    assert types["report.txt"] == "plain"


def test_browse_directories_sorted_before_files(tmp_path):
    (tmp_path / "z_dir").mkdir()
    (tmp_path / "a_file.txt").write_text("x")
    entries = browse_path(tmp_path)
    names = [e.name for e in entries]
    assert names.index("z_dir") < names.index("a_file.txt")


def test_browse_pattern_does_not_filter_directories(tmp_path):
    (tmp_path / "20260406").mkdir()
    (tmp_path / "batch_01.tar.gz").write_bytes(b"x")
    (tmp_path / "unrelated.txt").write_text("x")
    entries = browse_path(tmp_path, pattern="batch_*.tar.gz")
    names = [e.name for e in entries]
    assert "20260406" in names
    assert "batch_01.tar.gz" in names
    assert "unrelated.txt" not in names


def test_browse_directory_entry_has_no_size(tmp_path):
    (tmp_path / "subdir").mkdir()
    entry = next(e for e in browse_path(tmp_path) if e.name == "subdir")
    assert entry.size_bytes is None


def test_browse_wildcard_filter(tmp_path):
    (tmp_path / "batch_01.tar.gz").write_bytes(b"x")
    (tmp_path / "unrelated.txt").write_text("x")
    names = [e.name for e in browse_path(tmp_path, pattern="batch_*.tar.gz")]
    assert "batch_01.tar.gz" in names
    assert "unrelated.txt" not in names


def test_browse_returns_size(tmp_path):
    (tmp_path / "f.txt").write_text("hello")
    entry = next(e for e in browse_path(tmp_path) if e.name == "f.txt")
    assert entry.size_bytes == 5


def test_browse_raises_if_path_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        browse_path(tmp_path / "does_not_exist")


def test_browse_raises_if_not_directory(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    with pytest.raises(NotADirectoryError):
        browse_path(f)


# ---------------------------------------------------------------------------
# Archive handling helpers (reused by archive tests below)
# ---------------------------------------------------------------------------
def _make_targz(path: Path, inner_files: dict) -> Path:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in inner_files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    path.write_bytes(buf.getvalue())
    return path


def _make_zip(path: Path, inner_files: dict) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in inner_files.items():
            zf.writestr(name, data)
    return path


def test_list_archive_contents_targz(tmp_path):
    arc = _make_targz(tmp_path / "a.tar.gz", {"r.csv": b"a,b", "e.log": b"none"})
    assert set(list_archive_contents(arc)) == {"r.csv", "e.log"}


def test_list_archive_contents_zip(tmp_path):
    arc = _make_zip(tmp_path / "a.zip", {"data.csv": b"x,y"})
    assert "data.csv" in list_archive_contents(arc)


def test_extract_file_targz(tmp_path):
    data = b"hello tar"
    arc = _make_targz(tmp_path / "a.tar.gz", {"inner.txt": data})
    assert b"".join(extract_file(arc, "inner.txt")) == data


def test_extract_file_zip(tmp_path):
    data = b"hello zip"
    arc = _make_zip(tmp_path / "a.zip", {"inner.txt": data})
    assert b"".join(extract_file(arc, "inner.txt")) == data


def test_extract_file_missing_raises(tmp_path):
    arc = _make_targz(tmp_path / "a.tar.gz", {"exists.txt": b"x"})
    with pytest.raises(FileNotFoundError):
        list(extract_file(arc, "missing.txt"))


def test_extract_file_missing_raises_zip(tmp_path):
    arc = _make_zip(tmp_path / "a.zip", {"exists.txt": b"x"})
    with pytest.raises(FileNotFoundError):
        list(extract_file(arc, "missing.txt"))


# ---------------------------------------------------------------------------
# search_in_files
# ---------------------------------------------------------------------------

def test_search_files_finds_match(tmp_path):
    (tmp_path / "errors.log").write_text("line1\nERROR here\nline3\n")
    r = search_in_files(tmp_path, "*.log", "ERROR")
    assert r.total_matches == 1
    assert r.results[0].file == "errors.log"
    assert r.results[0].line == 2
    assert r.truncated is False
    assert r.download_ref is None


def test_search_files_truncates_at_50_single_file(tmp_path):
    (tmp_path / "big.log").write_text("\n".join(f"ERROR {i}" for i in range(60)))
    r = search_in_files(tmp_path, "*.log", "ERROR")
    assert r.shown == 50
    # grep path caps total at 51 (via -m), Python path returns full count (60);
    # either way total_matches must be > 50 to flag truncation
    assert r.total_matches > 50
    assert r.truncated is True
    assert r.download_ref is not None
    assert r.download_ref.filename == "big.log"
    assert r.download_ref.archive is None


def test_search_files_truncated_multi_file_no_ref(tmp_path):
    for i in range(2):
        (tmp_path / f"f{i}.log").write_text("\n".join(f"ERROR {j}" for j in range(30)))
    r = search_in_files(tmp_path, "*.log", "ERROR")
    assert r.truncated is True
    assert r.download_ref is None


def test_search_files_no_match(tmp_path):
    (tmp_path / "clean.log").write_text("all fine\n")
    r = search_in_files(tmp_path, "*.log", "ERROR")
    assert r.total_matches == 0
    assert r.truncated is False


def test_search_files_pattern_filters(tmp_path):
    (tmp_path / "errors.log").write_text("ERROR in log\n")
    (tmp_path / "data.csv").write_text("ERROR in csv\n")
    r = search_in_files(tmp_path, "*.log", "ERROR")
    names = [h.file for h in r.results]
    assert "errors.log" in names
    assert "data.csv" not in names


# ---------------------------------------------------------------------------
# search_in_archives
# ---------------------------------------------------------------------------

def test_search_archives_finds_match(tmp_path):
    _make_targz(tmp_path / "batch.tar.gz", {"errors.log": b"line1\nERROR bad\nline3\n"})
    r = search_in_archives(tmp_path, "batch*.tar.gz", "*.log", "ERROR")
    assert r.total_matches == 1
    assert r.results[0].archive == "batch.tar.gz"
    assert r.results[0].line == 2
    assert r.truncated is False


def test_search_archives_truncates_single_file(tmp_path):
    lines = b"\n".join(f"ERROR {i}".encode() for i in range(60))
    _make_targz(tmp_path / "batch.tar.gz", {"big.log": lines})
    r = search_in_archives(tmp_path, "*.tar.gz", "*.log", "ERROR")
    assert r.shown == 50
    assert r.truncated is True
    assert r.download_ref is not None
    assert r.download_ref.archive == "batch.tar.gz"


def test_search_archives_truncated_multi_file_no_ref(tmp_path):
    _make_targz(tmp_path / "batch.tar.gz", {
        "f1.log": b"\n".join(f"ERROR {i}".encode() for i in range(30)),
        "f2.log": b"\n".join(f"ERROR {i}".encode() for i in range(30)),
    })
    r = search_in_archives(tmp_path, "*.tar.gz", "*.log", "ERROR")
    assert r.truncated is True
    assert r.download_ref is None


def test_search_archives_archive_pattern_filters(tmp_path):
    _make_targz(tmp_path / "batch.tar.gz", {"e.log": b"ERROR here"})
    _make_targz(tmp_path / "other.tar.gz", {"e.log": b"ERROR here too"})
    r = search_in_archives(tmp_path, "batch*.tar.gz", "*.log", "ERROR")
    archives = {h.archive for h in r.results}
    assert "batch.tar.gz" in archives
    assert "other.tar.gz" not in archives


def test_search_archives_file_pattern_filters(tmp_path):
    _make_targz(tmp_path / "batch.tar.gz", {"e.log": b"ERROR log", "d.csv": b"ERROR csv"})
    r = search_in_archives(tmp_path, "*.tar.gz", "*.log", "ERROR")
    files = {h.file for h in r.results}
    assert "e.log" in files
    assert "d.csv" not in files


# ---------------------------------------------------------------------------
# _safe_inner_path
# ---------------------------------------------------------------------------

def test_safe_inner_path_accepts_normal():
    assert _safe_inner_path("logs/errors.log") is True


def test_safe_inner_path_accepts_nested():
    assert _safe_inner_path("subdir/nested/file.log") is True


def test_safe_inner_path_accepts_single():
    assert _safe_inner_path("file.txt") is True


def test_safe_inner_path_rejects_dotdot():
    assert _safe_inner_path("../../etc/passwd") is False


def test_safe_inner_path_rejects_dotdot_relative():
    assert _safe_inner_path("../sibling.txt") is False


def test_safe_inner_path_rejects_absolute():
    assert _safe_inner_path("/etc/shadow") is False


def test_safe_inner_path_rejects_absolute_tmp():
    assert _safe_inner_path("/tmp/evil") is False


def test_safe_inner_path_rejects_null_byte():
    assert _safe_inner_path("name\x00injected") is False


# ---------------------------------------------------------------------------
# list_archive_contents skips unsafe paths
# ---------------------------------------------------------------------------

def test_list_archive_skips_traversal_paths(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        safe_data = b"safe"
        info1 = tarfile.TarInfo(name="safe.txt")
        info1.size = len(safe_data)
        tf.addfile(info1, io.BytesIO(safe_data))
        info2 = tarfile.TarInfo(name="../../etc/passwd")
        evil_data = b"evil"
        info2.size = len(evil_data)
        tf.addfile(info2, io.BytesIO(evil_data))
    arc = tmp_path / "crafted.tar.gz"
    arc.write_bytes(buf.getvalue())
    contents = list_archive_contents(arc)
    assert "safe.txt" in contents
    assert "../../etc/passwd" not in contents


def test_list_archive_all_unsafe_returns_empty(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo(name="../../evil")
        data = b"x"
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    arc = tmp_path / "evil.tar.gz"
    arc.write_bytes(buf.getvalue())
    assert list_archive_contents(arc) == []


# ---------------------------------------------------------------------------
# extract_file raises on traversal path
# ---------------------------------------------------------------------------

def test_extract_file_rejects_traversal_path(tmp_path):
    arc = _make_targz(tmp_path / "a.tar.gz", {"safe.txt": b"x"})
    with pytest.raises(ValueError, match="Unsafe archive inner path"):
        list(extract_file(arc, "../../etc/passwd"))


def test_extract_file_rejects_absolute_path(tmp_path):
    arc = _make_targz(tmp_path / "a.tar.gz", {"safe.txt": b"x"})
    with pytest.raises(ValueError, match="Unsafe archive inner path"):
        list(extract_file(arc, "/etc/shadow"))


# ---------------------------------------------------------------------------
# _grep_search_file — security tests (run regardless of _GREP_AVAILABLE)
# ---------------------------------------------------------------------------

def test_grep_search_file_shell_metacharacters(tmp_path):
    """Shell metacharacters in search string must be treated as literals."""
    f = tmp_path / "test.log"
    f.write_text("safe line\n; rm -rf / line\n$(whoami) line\n")
    hits, total = _grep_search_file(f, "; rm -rf /")
    assert total == 1
    assert hits[0].content == "; rm -rf / line"


def test_grep_search_file_regex_chars_literal(tmp_path):
    """Regex special chars must match literally (grep -F)."""
    f = tmp_path / "test.log"
    f.write_text("price: $100\nnormal line\n")
    hits, total = _grep_search_file(f, "$100")
    assert total == 1
    assert hits[0].content == "price: $100"


def test_grep_search_file_leading_dash_not_a_flag(tmp_path):
    """Search strings starting with '-' must not be interpreted as grep flags."""
    f = tmp_path / "test.log"
    f.write_text("-v flag\nnormal\n")
    hits, total = _grep_search_file(f, "-v")
    assert total == 1


def test_search_files_shell_injection_in_string(tmp_path):
    """search_in_files must not execute injected shell commands."""
    (tmp_path / "f.log").write_text("line with ; echo injected\n")
    r = search_in_files(tmp_path, "*.log", "; echo injected")
    assert r.total_matches == 1


# ---------------------------------------------------------------------------
# _grep_search_file — positive tests
# ---------------------------------------------------------------------------

def test_grep_search_file_finds_match(tmp_path):
    """grep path finds match at correct line number."""
    f = tmp_path / "errors.log"
    f.write_text("line1\nERROR here\nline3\n")
    hits, total = _grep_search_file(f, "ERROR")
    assert total == 1
    assert hits[0].line == 2
    assert "ERROR here" in hits[0].content


def test_grep_search_file_no_match(tmp_path):
    """grep path returns empty results for no match."""
    f = tmp_path / "clean.log"
    f.write_text("all fine\n")
    hits, total = _grep_search_file(f, "ERROR")
    assert total == 0
    assert hits == []


def test_grep_search_file_truncates_at_50(tmp_path):
    """grep path caps hits at _MAX_SEARCH_RESULTS, total reflects 51 cap."""
    f = tmp_path / "big.log"
    f.write_text("\n".join(f"ERROR {i}" for i in range(60)))
    hits, total = _grep_search_file(f, "ERROR")
    assert len(hits) == 50
    assert total == 51  # grep capped at 51, so we know >= 51


def test_search_files_grep_path_finds_match(tmp_path):
    """search_in_files end-to-end with grep path (if available)."""
    (tmp_path / "errors.log").write_text("line1\nERROR here\nline3\n")
    r = search_in_files(tmp_path, "*.log", "ERROR")
    assert r.total_matches == 1
    assert r.results[0].line == 2
    assert r.truncated is False


# ---------------------------------------------------------------------------
# search_in_files — fallback tests
# ---------------------------------------------------------------------------

def test_search_files_fallback_when_grep_unavailable(tmp_path, monkeypatch):
    """Python fallback path produces same results as grep path."""
    monkeypatch.setattr("src.services.downloader_service._GREP_AVAILABLE", False)
    (tmp_path / "errors.log").write_text("line1\nERROR here\nline3\n")
    r = search_in_files(tmp_path, "*.log", "ERROR")
    assert r.total_matches == 1
    assert r.results[0].line == 2
    assert r.truncated is False


def test_search_files_fallback_truncation(tmp_path, monkeypatch):
    """Python fallback path truncates correctly at 50 results."""
    monkeypatch.setattr("src.services.downloader_service._GREP_AVAILABLE", False)
    (tmp_path / "big.log").write_text("\n".join(f"ERROR {i}" for i in range(60)))
    r = search_in_files(tmp_path, "*.log", "ERROR")
    assert r.shown == 50
    assert r.truncated is True
    assert r.download_ref is not None


# ---------------------------------------------------------------------------
# _grep_search_archive_file — security tests
# ---------------------------------------------------------------------------

def test_grep_archive_search_shell_metacharacters(tmp_path):
    """Shell metacharacters in search string treated as literals in archive search."""
    _make_targz(tmp_path / "a.tar.gz", {"f.log": b"safe\n; rm -rf / line\n"})
    r = search_in_archives(tmp_path, "*.tar.gz", "*.log", "; rm -rf /")
    assert r.total_matches == 1
    assert r.results[0].content == "; rm -rf / line"


def test_grep_archive_search_regex_chars_literal(tmp_path):
    """Regex special chars matched literally in archive search."""
    _make_targz(tmp_path / "a.tar.gz", {"f.log": b"price: $100\nnormal\n"})
    r = search_in_archives(tmp_path, "*.tar.gz", "*.log", "$100")
    assert r.total_matches == 1


def test_grep_archive_search_leading_dash_not_a_flag(tmp_path):
    """Search string starting with '-' not interpreted as grep flag in archive search."""
    _make_targz(tmp_path / "a.tar.gz", {"f.log": b"-v flag\nnormal\n"})
    r = search_in_archives(tmp_path, "*.tar.gz", "*.log", "-v")
    assert r.total_matches == 1


# ---------------------------------------------------------------------------
# _grep_search_archive_file — positive tests
# ---------------------------------------------------------------------------

def test_grep_archive_file_finds_match_targz(tmp_path):
    """Grep archive path finds match at correct line number in tar.gz."""
    arc = _make_targz(tmp_path / "a.tar.gz", {"f.log": b"line1\nERROR here\nline3\n"})
    hits, total = _grep_search_archive_file(arc, "f.log", "ERROR")
    assert total == 1
    assert hits[0].line == 2
    assert "ERROR here" in hits[0].content
    assert hits[0].archive == "a.tar.gz"


def test_grep_archive_file_finds_match_zip(tmp_path):
    """Grep archive path finds match at correct line number in zip."""
    arc = _make_zip(tmp_path / "a.zip", {"f.log": b"line1\nERROR here\nline3\n"})
    hits, total = _grep_search_archive_file(arc, "f.log", "ERROR")
    assert total == 1
    assert hits[0].line == 2


def test_grep_archive_file_no_match(tmp_path):
    """Grep archive path returns empty results for no match."""
    arc = _make_targz(tmp_path / "a.tar.gz", {"f.log": b"all fine\n"})
    hits, total = _grep_search_archive_file(arc, "f.log", "ERROR")
    assert total == 0
    assert hits == []


def test_grep_archive_file_truncates_at_50(tmp_path):
    """Grep archive path caps hits at _MAX_SEARCH_RESULTS, total reflects 51 cap."""
    lines = b"\n".join(f"ERROR {i}".encode() for i in range(60))
    arc = _make_targz(tmp_path / "a.tar.gz", {"big.log": lines})
    hits, total = _grep_search_archive_file(arc, "big.log", "ERROR")
    assert len(hits) == 50
    assert total == 51


def test_search_archives_grep_path_end_to_end(tmp_path):
    """search_in_archives end-to-end with grep path (if available)."""
    _make_targz(tmp_path / "batch.tar.gz", {"errors.log": b"line1\nERROR bad\nline3\n"})
    r = search_in_archives(tmp_path, "batch*.tar.gz", "*.log", "ERROR")
    assert r.total_matches == 1
    assert r.results[0].archive == "batch.tar.gz"
    assert r.results[0].line == 2
    assert r.truncated is False


# ---------------------------------------------------------------------------
# _grep_search_archive_file — fallback tests
# ---------------------------------------------------------------------------

def test_search_archives_fallback_when_grep_unavailable(tmp_path, monkeypatch):
    """Python fallback path produces same results as grep path for archives."""
    monkeypatch.setattr("src.services.downloader_service._GREP_AVAILABLE", False)
    _make_targz(tmp_path / "batch.tar.gz", {"errors.log": b"line1\nERROR bad\nline3\n"})
    r = search_in_archives(tmp_path, "*.tar.gz", "*.log", "ERROR")
    assert r.total_matches == 1
    assert r.results[0].line == 2
    assert r.truncated is False


def test_search_archives_fallback_truncation(tmp_path, monkeypatch):
    """Python fallback path truncates archive search correctly at 50 results."""
    monkeypatch.setattr("src.services.downloader_service._GREP_AVAILABLE", False)
    lines = b"\n".join(f"ERROR {i}".encode() for i in range(60))
    _make_targz(tmp_path / "batch.tar.gz", {"big.log": lines})
    r = search_in_archives(tmp_path, "*.tar.gz", "*.log", "ERROR")
    assert r.shown == 50
    assert r.truncated is True
    assert r.download_ref is not None


# ---------------------------------------------------------------------------
# _grep_search_archive_file — edge case
# ---------------------------------------------------------------------------

def test_grep_archive_file_unsupported_format_raises(tmp_path):
    """ValueError raised for unsupported archive formats."""
    f = tmp_path / "data.csv"
    f.write_text("x")
    with pytest.raises(ValueError, match="Unsupported archive format"):
        _grep_search_archive_file(f, "inner.txt", "ERROR")


# ---------------------------------------------------------------------------
# Search cancellation / timeout
# ---------------------------------------------------------------------------

def test_search_token_registers_and_cancels(tmp_path):
    """A registered search can be cancelled by id via the global registry."""
    from src.services.downloader_service import (
        new_search_token, release_search_token, get_search_registry,
    )
    token = new_search_token(timeout_seconds=60)
    try:
        assert token.is_done() is None
        assert get_search_registry().cancel(token.search_id) is True
        assert token.is_done() == "cancelled"
    finally:
        release_search_token(token)


def test_search_token_unknown_id_cancel_returns_false():
    from src.services.downloader_service import get_search_registry
    assert get_search_registry().cancel("does-not-exist") is False


def test_search_in_files_returns_cancelled_partial(tmp_path, monkeypatch):
    """When cancelled mid-flight, search_in_files returns stopped_reason='cancelled'."""
    from src.services.downloader_service import new_search_token, release_search_token
    # Force the Python fallback so we don't depend on grep being installed.
    monkeypatch.setattr("src.services.downloader_service._GREP_AVAILABLE", False)
    # Create several files. Cancelling before the loop ensures stopped_reason is set.
    for i in range(5):
        (tmp_path / f"f{i}.log").write_text("\n".join(f"ERROR {j}" for j in range(10)))
    token = new_search_token(timeout_seconds=60)
    token.cancel("cancelled")
    try:
        r = search_in_files(tmp_path, "*.log", "ERROR", token=token)
    finally:
        release_search_token(token)
    assert r.stopped_reason == "cancelled"
    assert r.search_id == token.search_id
    # No matches accumulated because we cancelled at the very first checkpoint.
    assert r.total_matches == 0


def test_search_in_files_timeout_sets_reason(tmp_path, monkeypatch):
    """An already-expired token reports stopped_reason='timeout'."""
    from src.services.downloader_service import new_search_token, release_search_token
    monkeypatch.setattr("src.services.downloader_service._GREP_AVAILABLE", False)
    (tmp_path / "f.log").write_text("ERROR\n")
    # Negative timeout -> deadline already passed when the token is created.
    token = new_search_token(timeout_seconds=-1)
    try:
        r = search_in_files(tmp_path, "*.log", "ERROR", token=token)
    finally:
        release_search_token(token)
    assert r.stopped_reason == "timeout"


def test_search_in_archives_cancelled(tmp_path, monkeypatch):
    """search_in_archives respects the cancel token and surfaces stopped_reason."""
    from src.services.downloader_service import new_search_token, release_search_token
    monkeypatch.setattr("src.services.downloader_service._GREP_AVAILABLE", False)
    # Make a valid archive so the loop has something to iterate over.
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"ERROR\n"
        info = tarfile.TarInfo(name="inner.log")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    (tmp_path / "a.tar.gz").write_bytes(buf.getvalue())
    token = new_search_token(timeout_seconds=60)
    token.cancel("cancelled")
    try:
        r = search_in_archives(tmp_path, "*.tar.gz", "*.log", "ERROR", token=token)
    finally:
        release_search_token(token)
    assert r.stopped_reason == "cancelled"


def test_search_without_token_unchanged(tmp_path, monkeypatch):
    """Backward compatibility: calling without a token gives stopped_reason=None."""
    monkeypatch.setattr("src.services.downloader_service._GREP_AVAILABLE", False)
    (tmp_path / "f.log").write_text("ERROR\n")
    r = search_in_files(tmp_path, "*.log", "ERROR")
    assert r.stopped_reason is None
    assert r.search_id is None
    assert r.total_matches == 1
