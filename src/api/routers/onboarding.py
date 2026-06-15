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
from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status

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


def _build_preview_payload(workbook_path: Path) -> Dict[str, Any]:
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

    Args:
        workbook_path: Filesystem path to the uploaded ``.xlsx``.

    Returns:
        The full preview payload (``source_code``, ``would_write``,
        ``summary``) including EE-S2's per-artefact ``status`` and
        aggregate ``drift`` block.

    Raises:
        WorkbookReadError / WorkbookSchemaError / EmitterError:
            Propagated to the caller, which maps them to a 422.
    """
    # Lazy import: the EC-S6 module pulls openpyxl + every emitter and
    # we don't want to pay that cost on a plain ``GET /sources`` request.
    from src.commands.onboard_source import _plan_writes
    from src.onboarding.drift import compare_artefact_payload
    from src.onboarding.workbook_reader import read_workbook

    workbook = read_workbook(workbook_path)
    # Drive every emitter in-memory. Output root stays as Path.cwd() so
    # the surfaced + compared paths mirror what the CLI would write
    # (repo-relative by the time they're reported back to the UI).
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

    cwd = Path.cwd()
    would_write: List[Dict[str, Any]] = []
    total_bytes = 0
    drift_counts: Dict[str, int] = {"new": 0, "changed": 0, "unchanged": 0}

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

        # Repo-relative display path when possible — agents and UI
        # both quote these back to the BA. Falls back to absolute if
        # the path escapes the cwd (defensive; not used by the preview
        # endpoint today).
        try:
            display_path = plan.path.resolve().relative_to(cwd).as_posix()
        except ValueError:
            display_path = plan.path.as_posix()

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

    return {
        "source_code": workbook.source.source_code,
        "would_write": would_write,
        "summary": {
            "total_files": len(would_write),
            "total_bytes": total_bytes,
            "drift": drift_counts,
        },
    }


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

        # EE-S2: we drive the EC-S6 planner directly (rather than the
        # MCP helper) because the per-artefact drift comparison needs
        # the raw planned-write objects (path + content). The EE-S1
        # response shape is a superset of the MCP helper's, so agents
        # consuming this surface get the EF-S5 fields they already
        # know plus the new ``status`` / ``drift`` enrichment.
        from src.onboarding.emitters import EmitterError
        from src.onboarding.models import WorkbookReadError
        from src.onboarding.workbook_schema import WorkbookSchemaError

        try:
            payload = _build_preview_payload(tmp_path)
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


__all__ = ["router"]
