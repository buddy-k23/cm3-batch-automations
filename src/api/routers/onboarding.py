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

import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import yaml
from fastapi import APIRouter, File, HTTPException, UploadFile, status

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
# POST /api/v2/onboarding/preview
# ---------------------------------------------------------------------------


# Limit the upload size so a malicious / fat-finger upload cannot fill
# the FastAPI process's tmpfs. 25 MB is generous for an .xlsx — the
# SHAW workbook in this repo is under 200 KB.
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024


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
    if file.filename is None or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workbook must be an .xlsx file",
        )

    # Stream into a NamedTemporaryFile so the workbook-reader can mmap
    # it without buffering the whole thing in RAM. The
    # ``delete=False`` is paired with the ``finally`` cleanup below so
    # we still get the os-managed tmpdir, just with explicit removal.
    tmp = tempfile.NamedTemporaryFile(
        suffix=".xlsx", delete=False, prefix="valdo_onboarding_preview_"
    )
    tmp_path = Path(tmp.name)
    try:
        total = 0
        # Read in chunks so a malicious upload does not blow up RSS.
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
        tmp.close()

        if total == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded workbook is empty",
            )

        # Reuse the EF-S5 service layer verbatim. The helper raises
        # ``ToolError`` for any schema / emitter failure; we surface
        # that as 422 (unprocessable entity).
        from mcp.server.fastmcp.exceptions import ToolError

        from src.mcp.onboarding_tools import onboard_source_dry_run_payload

        try:
            payload = onboard_source_dry_run_payload(str(tmp_path))
        except ToolError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
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


__all__ = ["router"]
