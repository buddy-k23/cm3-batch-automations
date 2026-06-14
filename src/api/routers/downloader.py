"""File Downloader API router.

Provides browse, download, archive-inspection, and nested-archive-search
endpoints. All endpoints require a valid API key and the requested path must
be under a configured allowed path (from the ``downloader.paths`` section in
``config/ui.yml``).
"""

import asyncio
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.api.auth import require_api_key
from src.services import downloader_service as svc
from src.services.downloader_logger import DownloaderLogger, resolve_client_info

router = APIRouter()

_DEFAULT_SEARCH_TIMEOUT = 30
_DEFAULT_MAX_SEARCH_TIMEOUT = 300


def _search_cfg(request: Request) -> dict:
    """Return the ``downloader.search`` config dict (or defaults)."""
    return _dl_cfg(request).get("search", {}) or {}


def _resolve_timeout(request: Request, requested: Optional[float]) -> Optional[float]:
    """Resolve effective per-search timeout in seconds.

    Falls back to ``downloader.search.timeout_seconds`` (default 30s) when the
    client does not supply one, and clamps any client-supplied value to the
    server-configured ``max_timeout_seconds`` (default 300s) to prevent abuse.
    A value of ``0`` disables the timeout (admin/debug use only).
    """
    cfg = _search_cfg(request)
    default_timeout = cfg.get("timeout_seconds", _DEFAULT_SEARCH_TIMEOUT)
    max_timeout = cfg.get("max_timeout_seconds", _DEFAULT_MAX_SEARCH_TIMEOUT)
    timeout = requested if requested is not None else default_timeout
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        timeout = float(default_timeout)
    if timeout <= 0:
        return None  # disabled
    if max_timeout and timeout > float(max_timeout):
        timeout = float(max_timeout)
    return timeout


def _dl_cfg(request: Request) -> dict:
    """Extract the downloader config dict from app state.

    Args:
        request: Current FastAPI request.

    Returns:
        The ``downloader`` sub-dict from ``app.state.ui_config``, or an empty
        dict when not configured.
    """
    return getattr(request.app.state, "ui_config", {}).get("downloader", {})


def _allowed_paths(request: Request) -> list:
    """Extract allowed path strings from the downloader config in ui_config.

    Args:
        request: Current FastAPI request.

    Returns:
        List of allowed path strings from ``config/ui.yml`` downloader section.
    """
    return [p["path"] for p in _dl_cfg(request).get("paths", [])]


def _get_logger(request: Request) -> DownloaderLogger:
    """Construct a :class:`DownloaderLogger` using the configured log path.

    Args:
        request: Current FastAPI request.

    Returns:
        :class:`DownloaderLogger` instance pointing at the path from
        ``downloader.log_path`` in ``config/ui.yml``.
    """
    log_path = _dl_cfg(request).get("log_path", "logs/file-downloads.log")
    return DownloaderLogger(log_path=log_path)


def _safe_filename(name: str) -> str:
    """Ensure *name* is a plain filename with no path components.

    Args:
        name: User-supplied filename or archive name.

    Returns:
        The original name if safe.

    Raises:
        HTTPException: 400 if the name contains path separators or is
            absolute.
    """
    if Path(name).name != name:
        raise HTTPException(status_code=400, detail=f"Invalid filename: {name!r}")
    return name


def _validate(requested: str, request: Request) -> Path:
    """Validate *requested* against the allowed paths in app state.

    Args:
        requested: Path string from query parameter or request body.
        request: Current FastAPI request (used to read app state).

    Returns:
        Resolved and validated ``Path``.

    Raises:
        HTTPException: 403 if path is outside all configured allowed paths.
    """
    try:
        return svc.validate_path(requested, _allowed_paths(request))
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


class DownloadRequest(BaseModel):
    """POST /download request body."""

    path: str
    filename: str
    archive: Optional[str] = None


class SearchFilesRequest(BaseModel):
    """POST /search-files request body.

    ``search_id`` and ``timeout_seconds`` are optional. When ``search_id`` is
    provided, the same id can later be passed to ``POST /search-cancel`` to
    stop this search from another request. ``timeout_seconds`` overrides the
    server default but is clamped to ``downloader.search.max_timeout_seconds``.
    """

    path: str
    filename_pattern: str
    search_string: str
    search_id: Optional[str] = None
    timeout_seconds: Optional[float] = None


class SearchArchiveRequest(BaseModel):
    """POST /search-archive request body. See :class:`SearchFilesRequest`."""

    path: str
    archive_pattern: str
    file_pattern: str
    search_string: str
    search_id: Optional[str] = None
    timeout_seconds: Optional[float] = None


class CancelSearchRequest(BaseModel):
    """POST /search-cancel request body."""

    search_id: str


@router.get("/paths")
async def get_paths(request: Request, _: object = Depends(require_api_key)):
    """Return configured paths from the downloader section of config/ui.yml.

    Args:
        request: Current FastAPI request.
        _: Unused auth context (validates API key).

    Returns:
        Dict with ``paths`` list from the downloader config.
    """
    return {"paths": _dl_cfg(request).get("paths", [])}


@router.get("/browse")
async def browse(
    path: str,
    request: Request,
    pattern: Optional[str] = None,
    _: object = Depends(require_api_key),
):
    """List files in *path*, optionally filtered by *pattern*.

    Args:
        path: Directory to list (must be under an allowed configured path).
        request: Current FastAPI request.
        pattern: Optional fnmatch wildcard to filter filenames.
        _: Unused auth context (validates API key).

    Returns:
        Dict with ``entries`` list, each having ``name``, ``type``,
        ``size_bytes``.

    Raises:
        HTTPException: 403 if path is not under an allowed configured path.
        HTTPException: 404 if directory does not exist.
    """
    resolved = _validate(path, request)
    try:
        entries = svc.browse_path(resolved, pattern)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {
        "entries": [
            {"name": e.name, "type": e.type, "size_bytes": e.size_bytes}
            for e in entries
        ]
    }


@router.get("/archive-contents")
async def archive_contents(
    path: str,
    archive: str,
    request: Request,
    _: object = Depends(require_api_key),
):
    """List files inside *archive* found in *path*.

    Args:
        path: Directory containing the archive (must be under an allowed path).
        archive: Archive filename (relative to *path*).
        request: Current FastAPI request.
        _: Unused auth context (validates API key).

    Returns:
        Dict with ``files`` list of inner file paths.

    Raises:
        HTTPException: 403 if path is not under an allowed configured path.
        HTTPException: 404 if archive file is not found.
        HTTPException: 400 if archive format is unsupported.
    """
    _safe_filename(archive)
    resolved = _validate(path, request)
    arc_path = resolved / archive
    if not arc_path.is_file():
        raise HTTPException(status_code=404, detail=f"Archive '{archive}' not found")
    try:
        return {"files": svc.list_archive_contents(arc_path)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/download")
async def download_file(
    body: DownloadRequest,
    request: Request,
    _: object = Depends(require_api_key),
):
    """Stream a single file from disk or extracted from an archive.

    Args:
        body: Download request with ``path``, ``filename``, and optional
            ``archive``.
        request: Current FastAPI request.
        _: Unused auth context (validates API key).

    Returns:
        ``StreamingResponse`` with ``application/octet-stream`` content type.

    Raises:
        HTTPException: 403 if path is not under an allowed configured path.
        HTTPException: 404 if file or archive is not found.
        HTTPException: 400 if archive format is unsupported.
    """
    resolved = _validate(body.path, request)
    client_ip, client_host = resolve_client_info(request)
    dl_logger = _get_logger(request)

    if body.archive:
        _safe_filename(body.archive)  # archive itself must be a simple filename
        # body.filename may contain '/' — it is an inner archive key, not a filesystem path
        arc_path = resolved / body.archive
        if not arc_path.is_file():
            raise HTTPException(status_code=404, detail=f"Archive '{body.archive}' not found")
        try:
            stream = svc.extract_file(arc_path, body.filename)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    else:
        _safe_filename(body.filename)  # plain file must be a simple filename
        file_path = resolved / body.filename
        if not file_path.is_file():
            raise HTTPException(status_code=404, detail=f"File '{body.filename}' not found")

        def _plain():
            with open(file_path, "rb") as fh:
                while True:
                    chunk = fh.read(65536)
                    if not chunk:
                        break
                    yield chunk

        stream = _plain()

    dl_logger.log_activity(
        operation="download",
        client_ip=client_ip,
        client_host=client_host,
        path=body.path,
        filename=body.filename,
        archive=body.archive,
        status="success",
    )

    download_name = Path(body.filename).name
    # Sanitize filename for Content-Disposition header (strip chars unsafe in quoted-string)
    safe_name = download_name.replace("\\", "").replace('"', "").replace("\r", "").replace("\n", "")
    return StreamingResponse(
        stream,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


def _fmt_result(r) -> dict:
    """Format a :class:`SearchResult` as a JSON-serialisable dict.

    Args:
        r: A ``SearchResult`` dataclass returned by the downloader service.

    Returns:
        Dict with keys: ``results``, ``truncated``, ``total_matches``,
        ``shown``, and ``download_ref`` (``None`` or a dict with ``path``,
        ``filename``, ``archive``).
    """
    return {
        "results": [
            {"file": h.file, "line": h.line, "content": h.content, "archive": h.archive}
            for h in r.results
        ],
        "truncated": r.truncated,
        "total_matches": r.total_matches,
        "shown": r.shown,
        "download_ref": (
            {"path": r.download_ref.path, "filename": r.download_ref.filename,
             "archive": r.download_ref.archive}
            if r.download_ref else None
        ),
        "stopped_reason": r.stopped_reason,
        "search_id": r.search_id,
    }


@router.post("/search-files")
async def search_files(body: SearchFilesRequest, request: Request, _: object = Depends(require_api_key)):
    """Search a string in plain files matching a filename wildcard.

    The search is killable in two ways:

    1. **Automatic timeout** — the server enforces ``downloader.search.timeout_seconds``
       (overridable per-request via the ``timeout_seconds`` body field, clamped to
       ``downloader.search.max_timeout_seconds``).
    2. **Manual cancel** — pass a client-generated ``search_id`` in the body and
       call ``POST /search-cancel`` with the same id from another request.

    Args:
        body: Request body with ``path``, ``filename_pattern``, ``search_string``,
            and optional ``search_id`` and ``timeout_seconds``.
        request: Current FastAPI request.
        _: Unused auth context (validates API key).

    Returns:
        Dict with ``results``, ``truncated``, ``total_matches``, ``shown``,
        ``download_ref``, ``stopped_reason`` (``"timeout"``/``"cancelled"``/``None``),
        and ``search_id``.

    Raises:
        HTTPException: 403 if path is not under an allowed configured path.
    """
    resolved = _validate(body.path, request)
    client_ip, client_host = resolve_client_info(request)
    dl_logger = _get_logger(request)
    timeout = _resolve_timeout(request, body.timeout_seconds)
    token = svc.new_search_token(timeout, search_id=body.search_id)
    try:
        # Run synchronous search off the event loop so /search-cancel can be
        # serviced concurrently by other workers/threads.
        result = await asyncio.to_thread(
            svc.search_in_files, resolved, body.filename_pattern, body.search_string, token,
        )
    finally:
        svc.release_search_token(token)
    log_status = result.stopped_reason or "success"
    dl_logger.log_activity(operation="search_files", client_ip=client_ip, client_host=client_host,
                           path=body.path, filename=body.filename_pattern, archive=None, status=log_status)
    return _fmt_result(result)


@router.post("/search-archive")
async def search_archive(body: SearchArchiveRequest, request: Request, _: object = Depends(require_api_key)):
    """Search a string inside archive inner files — both patterns support wildcards.

    See :func:`search_files` for the cancellation/timeout contract; the same
    rules apply here.

    Args:
        body: Request body with ``path``, ``archive_pattern``,
            ``file_pattern``, ``search_string``, and optional ``search_id`` and
            ``timeout_seconds``.
        request: Current FastAPI request.
        _: Unused auth context (validates API key).

    Returns:
        Dict with ``results``, ``truncated``, ``total_matches``, ``shown``,
        ``download_ref``, ``stopped_reason``, and ``search_id``.

    Raises:
        HTTPException: 403 if path is not under an allowed configured path.
    """
    resolved = _validate(body.path, request)
    client_ip, client_host = resolve_client_info(request)
    dl_logger = _get_logger(request)
    timeout = _resolve_timeout(request, body.timeout_seconds)
    token = svc.new_search_token(timeout, search_id=body.search_id)
    try:
        result = await asyncio.to_thread(
            svc.search_in_archives, resolved, body.archive_pattern,
            body.file_pattern, body.search_string, token,
        )
    finally:
        svc.release_search_token(token)
    log_status = result.stopped_reason or "success"
    dl_logger.log_activity(operation="search_archive", client_ip=client_ip, client_host=client_host,
                           path=body.path, filename=body.file_pattern, archive=body.archive_pattern,
                           status=log_status)
    return _fmt_result(result)


@router.post("/search-cancel")
async def search_cancel(body: CancelSearchRequest, _: object = Depends(require_api_key)):
    """Cancel an in-flight search identified by ``search_id``.

    Used by the UI "Stop search" button. The cancel is best-effort: the search
    code path checks the cancel flag at file/member/line boundaries and any
    in-flight ``grep`` subprocesses are terminated immediately.

    Args:
        body: Request body containing the ``search_id`` to cancel.
        _: Unused auth context (validates API key).

    Returns:
        Dict with ``cancelled`` (bool) — ``True`` if a matching in-flight
        search was found, ``False`` if the id is unknown (already finished
        or never registered).
    """
    cancelled = svc.get_search_registry().cancel(body.search_id)
    return {"cancelled": cancelled, "search_id": body.search_id}


@router.post("/search-nested-archives")
async def search_nested_archives(
    request: Request,
    archive_pattern: str = Body(...),
    member_pattern: str = Body(...),
    _: object = Depends(require_api_key),
):
    """Recursively search archives in the configured base directory.

    Walks ``downloader.archive_base_dir`` (from ``config/ui.yml``) recursively,
    finds archives whose filename matches *archive_pattern*, and returns
    members whose basename matches *member_pattern*. No extraction is
    performed.

    Args:
        request: Current FastAPI request.
        archive_pattern: Wildcard for archive filenames
            (e.g. ``"archive_*.zip"``).
        member_pattern: Wildcard for member basenames
            (e.g. ``"TRANS_*.txt"``).
        _: Unused auth context (validates API key).

    Returns:
        Dict with ``results`` (list of ``{archive_path, member_path}``) and
        ``count`` (int).

    Raises:
        HTTPException: 400 if ``archive_base_dir`` is not configured in
            ``config/ui.yml``.
    """
    cfg = _dl_cfg(request)
    archive_base_dir = cfg.get("archive_base_dir")
    if not archive_base_dir:
        raise HTTPException(
            status_code=400,
            detail="archive_base_dir is not configured in config/ui.yml downloader section",
        )
    results = svc.search_archives(archive_base_dir, archive_pattern, member_pattern)
    return {"results": results, "count": len(results)}
