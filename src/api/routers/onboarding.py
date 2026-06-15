"""Source onboarding endpoints — EE-S1 UI scaffold.

This router powers the "Source Editor" UI tab introduced in Sprint 4 /
EE-S1. The tab gives BAs two read-only operations from the browser
without dropping into the CLI:

* **List committed sources** — :func:`list_sources` walks
  ``config/e2e/sources/*.yml`` and reports each source's name, input /
  output file counts, and last-modified timestamp.
* **Preview a workbook** — :func:`preview_workbook` accepts a multipart
  ``.xlsx`` upload, runs the EC-S6 dry-run flow in memory via the EF-S5
  service helper, and returns the structured ``would_write`` list.

Both endpoints are **thin adapters**:

* The list endpoint is filesystem + ``yaml.safe_load`` only — it has no
  side effects on the engine state.
* The preview endpoint stages the upload in a per-request temp file (so
  the existing ``onboard_source_dry_run_payload`` can read it via path)
  and ALWAYS cleans up the temp file in a ``finally`` block. The dry-run
  helper itself touches no ``config/`` directory; see EC-S6 +
  EF-S5 for the contract.

EE-S2 (Sprint 5) layers a live preview tree + drift detection on top
of these endpoints. EE-S3 (Sprint 5) adds the commit-via-ZIP /
commit-via-MR flow. EE-S1 is the bones — no editing, no committing.

Auth posture:
    Both endpoints inherit the same ``require_api_key`` dependency the
    rest of the ``/api/v*`` surface uses (see :mod:`src.api.main`). When
    the UI is dev-mode (no auth), endpoints are open; when session +
    LDAPS auth is enabled, the session cookie satisfies
    ``verify_session_or_api_key``. No new auth code lives here.
"""

from __future__ import annotations

import io
import logging
import os
import re
import subprocess  # nosec B404 -- arg-array invocation only (shell disabled)
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

import yaml
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Filesystem layout
# ---------------------------------------------------------------------------

# Resolve the sources directory relative to the repository root rather than
# the current working directory so the endpoint behaves identically under
# `pytest` (monkeypatch.chdir-driven) and under `uvicorn` (server started
# from the repo root). The ``_REPO_ROOT`` walk mirrors the convention used
# in ``src/api/main.py`` so the two stay in lock-step.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_SOURCES_DIR = _REPO_ROOT / "config" / "e2e" / "sources"


def _sources_dir() -> Path:
    """Return the directory holding per-source YAML configs.

    Tests override the location via the ``VALDO_E2E_SOURCES_DIR``
    environment variable so the production tree is never touched. When
    unset, falls back to the repo-rooted ``config/e2e/sources`` path.

    Returns:
        Absolute :class:`pathlib.Path` to the sources directory. The
        directory may not exist yet; callers must handle the missing
        case explicitly.
    """
    override = os.environ.get("VALDO_E2E_SOURCES_DIR")
    if override:
        return Path(override).expanduser()
    return _DEFAULT_SOURCES_DIR


def _count_files(source_doc: Dict[str, Any], key: str) -> int:
    """Count ``file_type`` entries under *key* in a loaded source YAML.

    Defensive against (1) missing keys, (2) ``null`` values where YAML
    expected a sequence, and (3) non-list payloads from operators who
    hand-edit the YAML without re-reading the schema.

    Args:
        source_doc: A dict produced by ``yaml.safe_load`` on the source
            YAML, or ``None``.
        key: The top-level field name to count (``"input_files"`` or
            ``"output_files"``).

    Returns:
        The number of list entries under *key*; ``0`` for any
        non-list / missing payload.
    """
    if not isinstance(source_doc, dict):
        return 0
    value = source_doc.get(key)
    if not isinstance(value, list):
        return 0
    return len(value)


def _read_source(path: Path) -> Dict[str, Any] | None:
    """Read and parse one source YAML, returning ``None`` on failure.

    The endpoint must not 500 on a single malformed source file — a BA
    will commonly have an in-progress source committed alongside the
    healthy ones, and surfacing a partial list (with the bad source
    omitted) is more useful than blanking the whole UI.

    Args:
        path: Filesystem path to a candidate source YAML.

    Returns:
        The parsed YAML dict, or ``None`` if parsing failed / the file
        did not produce a dict. Errors are logged at WARNING level so
        operators can correlate via the application log.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("onboarding.list_sources read_failed path=%s err=%s", path, exc)
        return None
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        logger.warning(
            "onboarding.list_sources parse_failed path=%s err=%s", path, exc
        )
        return None
    if not isinstance(doc, dict):
        return None
    return doc


# ---------------------------------------------------------------------------
# GET /api/v2/onboarding/sources
# ---------------------------------------------------------------------------


@router.get("/sources")
async def list_sources() -> Dict[str, Any]:
    """Return the list of committed sources with input/output counts.

    Walks every ``*.yml`` file directly under ``config/e2e/sources/``
    (sub-directories are intentionally ignored — each source lives in
    its own top-level YAML; sub-trees such as
    ``config/e2e/sources/SHAW/reconciliation/`` hold supporting
    artefacts, not source configs). For each source we surface:

    * ``name`` — the YAML's ``source`` field, falling back to the
      filename stem so badly-named files still appear in the list.
    * ``input_files_count`` — length of the ``input_files`` array
      (``0`` when absent / malformed).
    * ``output_files_count`` — length of the ``output_files`` array.
    * ``last_modified`` — ISO-8601 UTC timestamp of the file's mtime.

    Returns:
        ``{"sources": [{"name": ..., "input_files_count": ...,
        "output_files_count": ..., "last_modified": ...}, ...]}``. The
        list is sorted by source name for stable UI rendering.
    """
    sources_dir = _sources_dir()
    if not sources_dir.is_dir():
        # Empty list — the UI handles the "no sources committed yet" case.
        return {"sources": []}

    entries: List[Dict[str, Any]] = []
    for path in sorted(sources_dir.glob("*.yml")):
        if not path.is_file():
            continue
        doc = _read_source(path)
        if doc is None:
            # Skip malformed files but log so operators can find them.
            continue
        try:
            mtime = path.stat().st_mtime
            last_modified = datetime.fromtimestamp(
                mtime, tz=timezone.utc
            ).isoformat()
        except OSError:
            last_modified = None
        # Prefer the YAML's declared source field; fall back to the
        # filename stem (an out-of-sync source field shouldn't make the
        # whole row vanish).
        name = doc.get("source") or path.stem
        entries.append(
            {
                "name": str(name),
                "input_files_count": _count_files(doc, "input_files"),
                "output_files_count": _count_files(doc, "output_files"),
                "last_modified": last_modified,
            }
        )

    # Stable order — alphabetical by name. The UI relies on this so the
    # list does not reshuffle on every refresh.
    entries.sort(key=lambda e: e["name"])
    return {"sources": entries}


# ---------------------------------------------------------------------------
# POST /api/v2/onboarding/preview — EE-S2 drift enrichment
# ---------------------------------------------------------------------------

# Kind tokens surfaced in the preview's ``would_write`` entries. Mirrors
# the constant the EF-S5 MCP helper uses (``src.mcp.onboarding_tools._KIND_BY_CATEGORY``)
# so the agent surface and the browser surface always speak the same
# vocabulary; tests pin this exact set in
# ``tests/integration/test_api_onboarding.py``.
_KIND_BY_CATEGORY: Dict[str, str] = {
    "source_yaml": "source_yaml",
    "mapping": "mapping_json",
    "rules": "rules_json",
    "reconciliation": "reconciliation_yaml",
    "sql": "sql",
}


# Limit the upload size so a malicious / fat-finger upload cannot fill
# the FastAPI process's tmpfs. 25 MB is generous for an .xlsx — the
# SHAW workbook in this repo is under 200 KB.
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def _plan_workbook_artefacts(workbook_path: Path) -> Tuple[Any, List[Any]]:
    """Run the EC-S6 planner over the staged workbook.

    Shared first-leg for the preview / download-zip / open-mr endpoints
    so the workbook-reader + emitter set runs exactly once per request.

    Args:
        workbook_path: Filesystem path to the staged ``.xlsx``.

    Returns:
        Tuple ``(workbook, plans)`` where ``workbook`` is the parsed
        :class:`OnboardingWorkbook` (caller reads ``source.source_code``
        off it for the response envelope) and ``plans`` is the ordered
        list of ``_PlannedWrite`` objects from the planner.

    Raises:
        WorkbookReadError / WorkbookSchemaError / EmitterError:
            Propagated to the caller, which maps them to a 4xx.
    """
    # Lazy import: the EC-S6 module pulls openpyxl + every emitter and
    # we don't want to pay that cost on a plain ``GET /sources`` request.
    from src.commands.onboard_source import _plan_writes
    from src.onboarding.workbook_reader import read_workbook

    workbook = read_workbook(workbook_path)
    plans = _plan_writes(
        workbook,
        output_root=Path.cwd(),
        source_dir=None,
        mapping_dir=None,
        rules_dir=None,
        reconciliation_dir=None,
        sql_dir=None,
        frozen_timestamp=None,
    )
    return workbook, plans


def _display_path(plan_path: Path, cwd: Path) -> str:
    """Coerce a planner-absolute path to the canonical repo-relative form.

    Args:
        plan_path: The absolute on-disk path produced by the planner.
        cwd: The current working directory (output root).

    Returns:
        A POSIX-style relative path when the plan path sits under the
        cwd, else the absolute path string as a defensive fallback.
    """
    try:
        return plan_path.resolve().relative_to(cwd).as_posix()
    except ValueError:
        return plan_path.as_posix()


def _build_preview_payload(
    workbook_path: Path, workbook_hash: str | None = None
) -> Dict[str, Any]:
    """Drive the EC-S6 planner and assemble the EE-S2 preview response.

    This replaces the EE-S1 thin delegation to the MCP helper because
    EE-S2 needs the *raw* :class:`_PlannedWrite` objects (path,
    content, category) to call into :func:`src.onboarding.drift.compare_artefact_payload`
    per artefact. The MCP helper drops ``content`` and ``path`` before
    returning, so reusing it would force a second emitter pass.

    The flow:

    1. Read the workbook via the EC-S2 reader.
    2. Run :func:`src.commands.onboard_source._plan_writes` for the
       full artefact list (source YAML + mappings + rules +
       reconciliation YAML + expected SQL).
    3. For each plan, compare against the committed counterpart using
       the shared drift helper and build a ``would_write`` entry with
       ``path``, ``bytes``, ``kind``, and ``status``.
    4. Aggregate the per-status counts into ``summary.drift``.
    5. EE-S3: when ``workbook_hash`` is supplied, seed the process-wide
       artefact-content cache so the follow-up ``/artefact-content``
       endpoint can serve emitted text without re-running the planner.
       The cache key is the SHA-256 hex digest of the workbook bytes.

    Args:
        workbook_path: Filesystem path to the uploaded ``.xlsx``.
        workbook_hash: Optional SHA-256 hex digest of the workbook
            bytes. When provided, the planner's emitted-content map is
            written into the artefact-content cache so subsequent
            ``/artefact-content`` requests can resolve it. When
            ``None`` (legacy callers / tests that don't need the
            cache), the cache is left untouched.

    Returns:
        The full preview payload (``source_code``, ``would_write``,
        ``summary``) including EE-S2's per-artefact ``status`` and
        aggregate ``drift`` block. EE-S3 also includes a top-level
        ``workbook_hash`` field when one was supplied.

    Raises:
        WorkbookReadError / WorkbookSchemaError / EmitterError:
            Propagated to the caller, which maps them to a 422.
    """
    from src.onboarding.drift import (
        compare_artefact_payload,
        get_artefact_content_cache,
    )

    workbook, plans = _plan_workbook_artefacts(workbook_path)

    cwd = Path.cwd()
    would_write: List[Dict[str, Any]] = []
    total_bytes = 0
    drift_counts: Dict[str, int] = {"new": 0, "changed": 0, "unchanged": 0}
    artefact_content_map: Dict[str, str] = {}

    for plan in plans:
        size_bytes = len(plan.content.encode("utf-8"))
        total_bytes += size_bytes

        # Drift comparison BEFORE display-path coercion: the compare
        # helper expects the absolute on-disk path so it can probe for
        # the committed file. ``plan.path`` is already absolute (the
        # planner resolved it under ``output_root``).
        status_token, drift_msg = compare_artefact_payload(
            plan.path, plan.content, plan.category
        )
        drift_counts[status_token] = drift_counts.get(status_token, 0) + 1

        display_path = _display_path(plan.path, cwd)
        artefact_content_map[display_path] = plan.content

        entry: Dict[str, Any] = {
            "path": display_path,
            "bytes": size_bytes,
            "kind": _KIND_BY_CATEGORY.get(plan.category, plan.category),
            "status": status_token,
        }
        # Surface the one-line drift hint only when the artefact would
        # change. ``new`` and ``unchanged`` entries do not carry a
        # ``drift_reason`` so the UI doesn't have to filter for empties.
        if status_token == "changed" and drift_msg:
            entry["drift_reason"] = drift_msg
        would_write.append(entry)

    if workbook_hash is not None:
        # Seed the EE-S3 artefact-content cache so the follow-up
        # ``/artefact-content`` endpoint can serve emitted text without
        # re-running the planner. The cache is bounded by both size +
        # TTL so a long-running uvicorn never accumulates state.
        get_artefact_content_cache().put(workbook_hash, artefact_content_map)

    payload: Dict[str, Any] = {
        "source_code": workbook.source.source_code,
        "would_write": would_write,
        "summary": {
            "total_files": len(would_write),
            "total_bytes": total_bytes,
            "drift": drift_counts,
        },
    }
    if workbook_hash is not None:
        payload["workbook_hash"] = workbook_hash
    return payload


async def _stage_workbook_upload(
    file: UploadFile, *, prefix: str
) -> Tuple[Path, str]:
    """Stream a multipart workbook upload to a tempfile, returning path + hash.

    Shared by ``/preview``, ``/download-zip``, and ``/open-mr`` so the
    streaming + size-limit + hash-compute logic only lives in one place.
    The caller is responsible for ``unlink``-ing the returned path in a
    ``finally`` block.

    Args:
        file: The FastAPI ``UploadFile`` to stage.
        prefix: Filename prefix for the tempfile (eases triage when
            an operator finds an orphaned tmpfile under ``/tmp``).

    Returns:
        Tuple ``(staged_path, workbook_hash)`` where
        ``workbook_hash`` is the SHA-256 hex digest of the workbook
        bytes (the cache key for the EE-S3 artefact-content cache).

    Raises:
        HTTPException: 400 when the upload is missing the ``.xlsx``
            extension, is empty, or exceeds the size limit.
    """
    from src.onboarding.drift import compute_workbook_hash

    if file.filename is None or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workbook must be an .xlsx file",
        )

    tmp = tempfile.NamedTemporaryFile(
        suffix=".xlsx", delete=False, prefix=prefix
    )
    tmp_path = Path(tmp.name)
    hasher_chunks: List[bytes] = []
    total = 0
    try:
        while True:
            chunk = await file.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Workbook exceeds {_MAX_UPLOAD_BYTES // (1024 * 1024)} "
                        "MB upload limit"
                    ),
                )
            tmp.write(chunk)
            hasher_chunks.append(chunk)
        tmp.close()
    except HTTPException:
        # Best-effort cleanup on the error path before re-raising — the
        # caller's ``finally`` block won't see the path if we raise
        # before returning.
        try:
            tmp.close()
        except Exception:  # pragma: no cover - defensive
            pass
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:  # pragma: no cover - defensive
            pass
        raise

    if total == 0:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:  # pragma: no cover - defensive
            pass
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded workbook is empty",
        )

    workbook_hash = compute_workbook_hash(b"".join(hasher_chunks))
    return tmp_path, workbook_hash


@router.post("/preview")
async def preview_workbook(
    file: UploadFile = File(..., description="Onboarding workbook (.xlsx)"),
) -> Dict[str, Any]:
    """Dry-run an uploaded workbook through EC-S6's planner.

    The flow:

    1. Validate the upload's filename has an ``.xlsx`` extension. We
       deliberately do not sniff MIME types — operators upload via
       drag-and-drop from a desktop, where the OS sets the content-type
       to anything from ``application/octet-stream`` to the actual
       Office MIME type. The extension check is sufficient at this
       layer; the EC-S1 schema validator deep-inspects the workbook
       structurally on the next step.
    2. Stream the upload into a per-request temp file under the system
       tmpdir. We use a ``NamedTemporaryFile`` with ``delete=False`` so
       the helper can re-open the path; we explicitly clean up in the
       ``finally`` block.
    3. Invoke :func:`src.mcp.onboarding_tools.onboard_source_dry_run_payload`
       — the SAME service-layer helper the EF-S5 MCP tool uses, so the
       browser preview and the agent preview see byte-identical output.
    4. Return the helper's structured ``{source_code, would_write,
       summary}`` payload verbatim.

    NO disk side-effects on the engine's ``config/`` tree. The helper
    explicitly runs in-memory; only the per-request upload tempfile is
    created and cleaned up.

    Args:
        file: A multipart-uploaded ``.xlsx`` file.

    Returns:
        ``{"source_code": ..., "would_write": [...], "summary": {
        "total_files": N, "total_bytes": N}}``. Same shape as
        ``onboard_source_dry_run_payload``'s return value.

    Raises:
        HTTPException: 400 when the upload is missing the ``.xlsx``
            extension, is empty, or exceeds the size limit. 422 when
            the workbook itself is schema-invalid (EC-S1 / emitter
            errors). 500 only for genuinely unexpected failures (the
            global exception handler will then surface an opaque
            error_id).
    """
    tmp_path, workbook_hash = await _stage_workbook_upload(
        file, prefix="valdo_onboarding_preview_"
    )
    try:
        # EE-S2: we drive the EC-S6 planner directly (rather than the
        # MCP helper) because the per-artefact drift comparison needs
        # the raw planned-write objects (path + content). The EE-S1
        # response shape is a superset of the MCP helper's, so agents
        # consuming this surface get the EF-S5 fields they already
        # know plus the new ``status`` / ``drift`` enrichment. EE-S3
        # threads the workbook hash through so the artefact-content
        # cache gets seeded for the ``/artefact-content`` lookup.
        from src.onboarding.emitters import EmitterError
        from src.onboarding.models import WorkbookReadError
        from src.onboarding.workbook_schema import WorkbookSchemaError

        try:
            payload = _build_preview_payload(
                tmp_path, workbook_hash=workbook_hash
            )
        except (WorkbookReadError, WorkbookSchemaError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
        except EmitterError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Emitter error: {exc}",
            ) from exc

        return payload
    finally:
        # Always remove the staged tempfile, even on the success path —
        # leaving it around defeats the "no disk side-effects" promise.
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError as exc:  # pragma: no cover — defensive
            logger.warning(
                "onboarding.preview tmpfile_cleanup_failed path=%s err=%s",
                tmp_path,
                exc,
            )


# ---------------------------------------------------------------------------
# GET /api/v2/onboarding/committed-artefact — EE-S2 diff backing store
# ---------------------------------------------------------------------------


# Repo-relative path prefixes that the committed-artefact endpoint will
# serve. We deliberately whitelist the exact subtrees the onboarding
# emitters write under so a malicious / fat-finger ``path`` query arg
# can't be coerced into reading ``/etc/passwd``-style targets via
# directory-traversal payloads. Mirrors the EC-S6 layout convention.
_ALLOWED_COMMITTED_PREFIXES = (
    "config/e2e/sources/",
    "config/mappings/",
    "config/rules/",
)


def _resolve_committed_path(rel_path: str) -> Path:
    """Resolve a UI-supplied repo-relative path to an on-disk absolute path.

    Defends against directory traversal in two layers:

    1. Prefix whitelist — the resolved path must sit under one of the
       three onboarding artefact directories.
    2. Containment check — after resolving ``..`` segments the absolute
       path must still be a descendant of the cwd / repo root.

    Args:
        rel_path: The ``path`` query argument supplied by the UI.

    Returns:
        Absolute :class:`pathlib.Path` to the committed file.

    Raises:
        HTTPException: 400 when the path is empty, contains traversal
            payloads, or escapes the whitelist; 404 when the resolved
            path does not point at a regular file.
    """
    if not rel_path or not isinstance(rel_path, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="path is required",
        )

    # Normalise leading slash + Windows separators. We deliberately
    # do this BEFORE the whitelist check so the prefix match is
    # unambiguous regardless of how the UI formats the path.
    normalised = rel_path.replace("\\", "/").lstrip("/")
    if not any(
        normalised.startswith(prefix) for prefix in _ALLOWED_COMMITTED_PREFIXES
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "path must reference an onboarding artefact under "
                f"one of {list(_ALLOWED_COMMITTED_PREFIXES)}"
            ),
        )

    cwd = Path.cwd().resolve()
    candidate = (cwd / normalised).resolve()
    # Containment check — refuses ``../../etc/passwd``-style payloads
    # even if the leading prefix happened to start with a whitelisted
    # token. ``relative_to`` raises ``ValueError`` when the candidate
    # escapes the cwd, which we map back to a 400.
    try:
        candidate.relative_to(cwd)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="path escapes the repository root",
        ) from exc

    if not candidate.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No committed artefact at {normalised!r}",
        )
    return candidate


@router.get("/committed-artefact")
async def get_committed_artefact(
    path: str = Query(..., description="Repo-relative path of the committed artefact"),
) -> Dict[str, Any]:
    """Return the on-disk content of a committed onboarding artefact.

    Backs the EE-S2 "view diff" modal: when the BA clicks a ``changed``
    artefact in the preview tree the UI fetches both the emitted
    content (already in memory client-side from the upload response)
    and the committed content (via this endpoint) and renders a
    line-level diff.

    The endpoint is read-only: no writes, no shell-outs, no DB calls.
    The :func:`_resolve_committed_path` helper validates the supplied
    path against a whitelist of the three onboarding directories and a
    cwd containment check.

    Args:
        path: Repo-relative path of the artefact, e.g.
            ``config/mappings/SHAW_TRANERT.yaml``.

    Returns:
        ``{"path": <repo-relative path>, "bytes": <int>, "content":
        <UTF-8 text>}``. The ``content`` is the file's verbatim text
        (no parsing / no normalisation) so the UI can render an
        exact line-level diff against the emitted content.

    Raises:
        HTTPException: 400 on a missing / off-whitelist / traversal
            path. 404 when the resolved path is not a file. 500 only
            on unexpected I/O failure.
    """
    resolved = _resolve_committed_path(path)
    try:
        content = resolved.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover — defensive
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read committed artefact: {exc}",
        ) from exc
    return {
        "path": path.replace("\\", "/").lstrip("/"),
        "bytes": len(content.encode("utf-8")),
        "content": content,
    }


# ---------------------------------------------------------------------------
# POST /api/v2/onboarding/download-zip — EE-S3 ZIP commit path
# ---------------------------------------------------------------------------


def _zip_stream_generator(
    plans: List[Any], cwd: Path
) -> Iterator[bytes]:
    """Yield a ZIP archive byte-stream from the planner output.

    Uses an in-memory :class:`io.BytesIO` buffer wrapped in a
    :class:`zipfile.ZipFile`; each artefact is written at its canonical
    repo-relative path. The buffer is drained to the caller in 64 KB
    chunks so very large archives don't fully materialise in RAM at the
    HTTP layer.

    Args:
        plans: Ordered list of ``_PlannedWrite`` objects from the
            planner. Each plan's content is encoded as UTF-8 and
            written under :func:`_display_path`.
        cwd: The current working directory used to coerce planner
            paths to repo-relative form. Mirrors the ``/preview``
            display-path logic so the ZIP and the preview tree agree
            on filenames.

    Yields:
        UTF-8 byte chunks of the ZIP archive, suitable for piping
        through a FastAPI :class:`StreamingResponse`.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for plan in plans:
            display_path = _display_path(plan.path, cwd)
            zf.writestr(display_path, plan.content.encode("utf-8"))
    # ZipFile.close() (triggered on context exit) finalises the
    # central directory; only after that does the BytesIO contain a
    # valid archive. Drain in chunks so the StreamingResponse can
    # pipeline the bytes to the client.
    buf.seek(0)
    while True:
        chunk = buf.read(64 * 1024)
        if not chunk:
            break
        yield chunk


@router.post("/download-zip")
async def download_workbook_zip(
    file: UploadFile = File(..., description="Onboarding workbook (.xlsx)"),
) -> StreamingResponse:
    """Stream a ZIP of every emitted artefact for offline review.

    The flow mirrors ``/preview``: stage the upload, run the EC-S6
    planner in memory, then materialise every planned artefact at its
    canonical repo-relative path inside a ZIP. The ZIP is streamed back
    via :class:`StreamingResponse` so very large archives (deep mapping
    trees with dozens of SQL artefacts) don't have to fully materialise
    in the API process's RSS before the first byte hits the wire.

    No disk side effects on the engine's ``config/`` tree — the planner
    runs in memory, the ZIP is built in a :class:`io.BytesIO`, and the
    staged workbook tempfile is unlinked in the ``finally`` block.

    Args:
        file: A multipart-uploaded ``.xlsx`` file.

    Returns:
        A :class:`StreamingResponse` with ``Content-Type:
        application/zip`` and a ``Content-Disposition: attachment;
        filename="<SOURCE_CODE>-onboarding-artefacts.zip"`` header.

    Raises:
        HTTPException: 400 on a non-``.xlsx`` upload / oversize /
            empty body; 422 on a schema-invalid workbook; 500 on
            unexpected emitter failure.
    """
    tmp_path, _workbook_hash = await _stage_workbook_upload(
        file, prefix="valdo_onboarding_download_"
    )
    try:
        from src.onboarding.emitters import EmitterError
        from src.onboarding.models import WorkbookReadError
        from src.onboarding.workbook_schema import WorkbookSchemaError

        try:
            workbook, plans = _plan_workbook_artefacts(tmp_path)
        except (WorkbookReadError, WorkbookSchemaError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
        except EmitterError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Emitter error: {exc}",
            ) from exc

        cwd = Path.cwd()
        # Build the archive into a freshly-allocated BytesIO inside the
        # generator. We capture both the source code and the plans now
        # so the generator closure has everything it needs after the
        # request body has been consumed.
        source_code = workbook.source.source_code
        # Sanitise the source code for use in a Content-Disposition
        # filename: only [A-Za-z0-9_-] survive. Defends against an
        # attacker-controlled workbook source name attempting header
        # injection via the filename parameter.
        safe_source = re.sub(r"[^A-Za-z0-9_-]", "_", source_code) or "source"
        filename = f"{safe_source}-onboarding-artefacts.zip"
        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
        return StreamingResponse(
            _zip_stream_generator(plans, cwd),
            media_type="application/zip",
            headers=headers,
        )
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError as exc:  # pragma: no cover — defensive
            logger.warning(
                "onboarding.download_zip tmpfile_cleanup_failed path=%s err=%s",
                tmp_path,
                exc,
            )


# ---------------------------------------------------------------------------
# POST /api/v2/onboarding/open-mr — EE-S3 GitHub PR commit path
# ---------------------------------------------------------------------------

# Hard cap on the title and description form fields so an upstream
# malicious agent cannot stuff a multi-megabyte description into the
# commit message body (which would in turn balloon the git object
# database). The numbers mirror GitHub's own PR limits.
_MR_TITLE_MAX_LEN = 256
_MR_DESCRIPTION_MAX_LEN = 65_536
_MR_BRANCH_NAME_MAX_LEN = 200

# Branch names get validated against a permissive but bounded character
# set. We deliberately match the subset of Git's `ref-format(7)` rules
# that round-trip safely through `gh pr create` and a shell-less
# subprocess argv. Anything else gets a 400.
_MR_BRANCH_NAME_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


def _open_mr_disabled_response() -> HTTPException:
    """Build the 501 raised when the open-MR feature flag is unset.

    Factored out so the test surface can pin the exact message string.

    Returns:
        A 501 :class:`HTTPException` with an actionable hint pointing
        the BA at Download ZIP / the CLI.
    """
    return HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=(
            "MR-opening from the UI is disabled in this environment. "
            "Use Download ZIP and commit manually, or run "
            "`valdo onboard-source` from the CLI."
        ),
    )


def _default_branch_name(source_code: str) -> str:
    """Synthesize a branch name when the request didn't supply one.

    Uses a UTC timestamp so back-to-back invocations don't collide on
    a single source.

    Args:
        source_code: The workbook's source code (e.g. ``"SHAW"``).

    Returns:
        A branch name of the form
        ``valdo-onboarding/<source>-<YYYYMMDD-HHMMSS>``.
    """
    safe_source = re.sub(r"[^A-Za-z0-9_-]", "_", source_code) or "source"
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"valdo-onboarding/{safe_source}-{stamp}"


def _run_git_or_gh(
    cmd: List[str], *, cwd: Path
) -> "subprocess.CompletedProcess[str]":
    """Invoke a ``git`` or ``gh`` command and return the completed process.

    Centralises the ``shell=False`` / arg-array invocation so the
    EC-S11 ``test_no_shell_true`` guardrail stays satisfied. Caller
    inspects the returncode + stdout + stderr.

    Args:
        cmd: The full argv array. The first element is the binary
            (``"git"`` / ``"gh"``).
        cwd: Working directory for the subprocess.

    Returns:
        The :class:`subprocess.CompletedProcess` from
        :func:`subprocess.run`.
    """
    return subprocess.run(  # nosec B603 -- arg-array, no shell
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def _open_mr_pipeline(
    plans: List[Any],
    *,
    source_code: str,
    mr_title: str,
    mr_description: str,
    branch_name: str,
    repo_root: Path,
) -> Dict[str, Any]:
    """Run the branch / commit / push / PR pipeline against the local repo.

    Side-effects, in order:

    1. ``git checkout -b <branch_name>`` from the current HEAD.
    2. Materialise every planned artefact at its canonical
       repo-relative path under ``repo_root``.
    3. ``git add`` the materialised paths.
    4. ``git commit`` with the conventional message.
    5. ``git push -u origin <branch_name>``.
    6. ``gh pr create --title ... --body ... --head <branch_name>``.

    Each step's stdout + stderr is captured and surfaced in the
    returned dict on failure so the UI can render a clear error.

    Args:
        plans: Ordered list of planner ``_PlannedWrite`` objects.
        source_code: The workbook's source code (for the default
            branch name).
        mr_title: The PR title from the form.
        mr_description: The PR description from the form.
        branch_name: The resolved branch name (caller-provided or
            synthesised).
        repo_root: The working tree to operate on. In production this
            is :func:`Path.cwd`; tests redirect this via
            ``monkeypatch.chdir`` so the real repo is never touched.

    Returns:
        ``{"pr_url": ..., "branch_name": ..., "commit_sha": ...}``
        on success.

    Raises:
        HTTPException: 500 on any subprocess failure, with the failing
            step + captured output surfaced in ``detail``.
    """
    # 1. Branch from the current HEAD.
    create_branch = _run_git_or_gh(
        ["git", "checkout", "-b", branch_name], cwd=repo_root
    )
    if create_branch.returncode != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"git checkout -b {branch_name} failed: "
                f"{create_branch.stderr.strip() or create_branch.stdout.strip()}"
            ),
        )

    # 2. Materialise every planned artefact on the new branch.
    written_paths: List[str] = []
    cwd = Path.cwd()
    for plan in plans:
        rel_path = _display_path(plan.path, cwd)
        on_disk = repo_root / rel_path
        on_disk.parent.mkdir(parents=True, exist_ok=True)
        on_disk.write_text(plan.content, encoding="utf-8")
        written_paths.append(rel_path)

    # 3. Stage only the paths we wrote — we never want a stray
    # ``git add -A`` to pick up an unrelated working-tree change.
    add = _run_git_or_gh(
        ["git", "add", "--"] + written_paths, cwd=repo_root
    )
    if add.returncode != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"git add failed: {add.stderr.strip() or add.stdout.strip()}"
            ),
        )

    # 4. Commit. The conventional message keeps the changelog parser
    # happy and signals the commit's provenance.
    commit_message = (
        f"feat(onboarding): {mr_title} via Source Editor UI\n\n"
        f"{mr_description}"
    )
    commit = _run_git_or_gh(
        ["git", "commit", "-m", commit_message], cwd=repo_root
    )
    if commit.returncode != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"git commit failed: "
                f"{commit.stderr.strip() or commit.stdout.strip()}"
            ),
        )

    # 5. Resolve the new commit SHA so we can surface it to the UI.
    rev_parse = _run_git_or_gh(
        ["git", "rev-parse", "HEAD"], cwd=repo_root
    )
    if rev_parse.returncode != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"git rev-parse HEAD failed: "
                f"{rev_parse.stderr.strip() or rev_parse.stdout.strip()}"
            ),
        )
    commit_sha = rev_parse.stdout.strip()

    # 6. Push the branch upstream.
    push = _run_git_or_gh(
        ["git", "push", "-u", "origin", branch_name], cwd=repo_root
    )
    if push.returncode != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"git push failed: "
                f"{push.stderr.strip() or push.stdout.strip()}"
            ),
        )

    # 7. Open the PR via the gh CLI. The body is fed via stdin (--body-file -)
    # so a multi-line description doesn't get mangled by argv quoting on
    # any future shell-wrapped invocation.
    gh_create = _run_git_or_gh(
        [
            "gh",
            "pr",
            "create",
            "--title",
            mr_title,
            "--body",
            mr_description,
            "--head",
            branch_name,
        ],
        cwd=repo_root,
    )
    if gh_create.returncode != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"gh pr create failed: "
                f"{gh_create.stderr.strip() or gh_create.stdout.strip()}"
            ),
        )

    pr_url = gh_create.stdout.strip().splitlines()[-1] if gh_create.stdout else ""
    return {
        "pr_url": pr_url,
        "branch_name": branch_name,
        "commit_sha": commit_sha,
    }


@router.post("/open-mr")
async def open_merge_request(
    file: UploadFile = File(..., description="Onboarding workbook (.xlsx)"),
    mr_title: str = Form(..., description="Pull request title"),
    mr_description: str = Form("", description="Pull request body"),
    branch_name: str = Form(
        "",
        description=(
            "Optional branch name. Defaults to "
            "valdo-onboarding/<source>-<timestamp>."
        ),
    ),
) -> Dict[str, Any]:
    """Branch + commit + push + open a PR via the ``gh`` CLI.

    Gated behind the ``VALDO_UI_ENABLE_OPEN_MR`` environment variable
    so production-by-default has no surprise side-effects on the git
    working tree. When unset the endpoint returns a 501 with an
    actionable hint pointing the BA at Download ZIP or the CLI.

    On success the response carries the PR URL, the new branch name,
    and the commit SHA so the UI can render a "click here to review"
    link.

    Args:
        file: Multipart-uploaded onboarding workbook.
        mr_title: Title for the PR (also the second segment of the
            commit subject line).
        mr_description: PR description / commit body. Plain text;
            no markdown sanitisation applied — GitHub renders it.
        branch_name: Optional branch override. When empty (the
            default), a deterministic
            ``valdo-onboarding/<source>-<timestamp>`` branch name is
            synthesised.

    Returns:
        ``{"pr_url": ..., "branch_name": ..., "commit_sha": ...}``.

    Raises:
        HTTPException: 400 on input validation errors; 501 when the
            feature flag is unset; 422 on a schema-invalid workbook;
            500 on any subprocess failure inside the pipeline.
    """
    # Feature-flag gate FIRST so we don't even bother staging the
    # workbook when the endpoint is disabled.
    if os.environ.get("VALDO_UI_ENABLE_OPEN_MR") != "1":
        raise _open_mr_disabled_response()

    # Defensive input validation BEFORE doing any I/O. These bounds
    # mirror GitHub's own limits and keep an upstream malicious agent
    # from ballooning the git object database.
    if not mr_title or not mr_title.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="mr_title is required",
        )
    if len(mr_title) > _MR_TITLE_MAX_LEN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"mr_title exceeds {_MR_TITLE_MAX_LEN} characters",
        )
    if len(mr_description) > _MR_DESCRIPTION_MAX_LEN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"mr_description exceeds {_MR_DESCRIPTION_MAX_LEN} characters"
            ),
        )
    if branch_name:
        if len(branch_name) > _MR_BRANCH_NAME_MAX_LEN:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"branch_name exceeds {_MR_BRANCH_NAME_MAX_LEN} characters"
                ),
            )
        if not _MR_BRANCH_NAME_RE.match(branch_name):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "branch_name may only contain letters, digits, "
                    "'.', '_', '/', and '-'"
                ),
            )

    tmp_path, _workbook_hash = await _stage_workbook_upload(
        file, prefix="valdo_onboarding_mr_"
    )
    try:
        from src.onboarding.emitters import EmitterError
        from src.onboarding.models import WorkbookReadError
        from src.onboarding.workbook_schema import WorkbookSchemaError

        try:
            workbook, plans = _plan_workbook_artefacts(tmp_path)
        except (WorkbookReadError, WorkbookSchemaError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
        except EmitterError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Emitter error: {exc}",
            ) from exc

        source_code = workbook.source.source_code
        resolved_branch = branch_name or _default_branch_name(source_code)
        repo_root = Path.cwd()
        return _open_mr_pipeline(
            plans,
            source_code=source_code,
            mr_title=mr_title,
            mr_description=mr_description,
            branch_name=resolved_branch,
            repo_root=repo_root,
        )
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError as exc:  # pragma: no cover — defensive
            logger.warning(
                "onboarding.open_mr tmpfile_cleanup_failed path=%s err=%s",
                tmp_path,
                exc,
            )


# ---------------------------------------------------------------------------
# GET /api/v2/onboarding/artefact-content — EE-S3 diff backing store
# ---------------------------------------------------------------------------


@router.get("/artefact-content")
async def get_artefact_content(
    workbook_hash: str = Query(
        ...,
        min_length=8,
        max_length=128,
        description="SHA-256 hex digest of the previously-uploaded workbook",
    ),
    path: str = Query(
        ..., description="Repo-relative path of the artefact"
    ),
) -> Dict[str, Any]:
    """Return the emitted text for a (workbook, path) tuple.

    Backs the EE-S3 unified-diff renderer: the UI receives a
    ``workbook_hash`` in the ``/preview`` response, then queries this
    endpoint per artefact when the View modal opens. The endpoint reads
    from the in-process LRU cache seeded by ``/preview`` — no planner
    re-run, no workbook re-upload.

    Args:
        workbook_hash: SHA-256 hex digest from ``/preview``'s response.
        path: Repo-relative artefact path (e.g.
            ``config/mappings/SHAW_TRANERT.json``).

    Returns:
        ``{"path": ..., "content": ...}`` where ``content`` is the
        emitted UTF-8 text.

    Raises:
        HTTPException: 404 when the cache entry is missing / expired
            or the path is unknown for that workbook.
    """
    from src.onboarding.drift import get_artefact_content_cache

    cache = get_artefact_content_cache()
    content = cache.get_content(workbook_hash, path)
    if content is None:
        # Distinguish missing-workbook from missing-path to make UI
        # error messages more actionable.
        if cache.get(workbook_hash) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    "Workbook hash not found in cache. Re-upload the "
                    "workbook via /api/v2/onboarding/preview."
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No artefact at {path!r} for the supplied workbook hash.",
        )
    return {"path": path, "content": content}


__all__ = ["router"]
