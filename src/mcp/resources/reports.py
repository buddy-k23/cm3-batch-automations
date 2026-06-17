"""Traversal-safe ``report://<run_id>`` resolution for the MCP report resource (S23-4, #446).

Per ADR 0023, HTML reports produced by the report-producing MCP tools
(``validate_file``, ``compare_two_files``, ``db_compare``,
``reconcile_mapping``, ``reconcile_all``) are written into the shared reports
dir under a run-id-named file (``<run_id>.html``) and retrieved over MCP via a
``report://<run_id>`` resource. This module owns two responsibilities:

* :func:`reports_dir` — the single source of truth for the reports directory.
  It mirrors ``src.api.main._REPORTS_DIR`` (the ``/reports`` static mount) so
  the MCP path and the REST ``report_url`` resolve to the SAME directory, and
  is overridable via ``VALDO_REPORTS_DIR`` for ops + tests.

* :func:`resolve_report_path` — a traversal-safe id -> file resolver modelled
  on :func:`src.api.routers.files._safe_upload_path` (CWE-22 guard). It takes
  ``Path(run_id).name`` only, rejects empty / ``.`` / ``..`` / NUL-byte ids,
  builds ``<reports_root>/<run_id>.html``, resolves it, and asserts the
  candidate is contained within the reports root. A run-id that escapes the
  reports dir is rejected — never served. The resolver serves ONLY from the
  reports dir, never ``uploads/`` and never an arbitrary path.

The ``report://`` resource read is served by the MCP transport, which is
wrapped by :class:`src.mcp.auth.MCPAuthMiddleware`, so it inherits MCP auth for
free — no new auth surface (ADR 0023 §4).
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp.exceptions import ResourceError

__all__ = [
    "reports_dir",
    "resolve_report_path",
    "read_report_html",
    "report_uri_for",
    "report_url_for",
]

# Report files are named ``<run_id>.html`` so the ``report://<run_id>``
# resolver and the on-disk file share the same id — resolution is a direct
# lookup (ADR 0023 §5 "Naming").
_REPORT_SUFFIX = ".html"

# Env override for the reports directory. When unset, :func:`reports_dir`
# falls back to the package-relative ``<repo>/reports`` dir that the FastAPI
# app mounts at ``/reports`` (``src.api.main._REPORTS_DIR``).
_REPORTS_DIR_ENV = "VALDO_REPORTS_DIR"


def reports_dir() -> Path:
    """Return the reports directory, creating it if necessary.

    The directory is resolved at call time (not import time) so a test /
    deployment can set :data:`_REPORTS_DIR_ENV` before the first call. When
    the env var is unset, the path mirrors ``src.api.main._REPORTS_DIR`` — the
    ``<repo>/reports`` dir mounted at ``/reports`` — so the MCP resource and
    the REST ``report_url`` resolve to the same directory.

    Returns:
        An absolute :class:`pathlib.Path` to the reports directory; the
        directory is created (``parents=True, exist_ok=True``) if absent.
    """
    raw = os.getenv(_REPORTS_DIR_ENV)
    if raw and raw.strip():
        root = Path(raw.strip())
    else:
        # Mirror src/api/main.py: <repo_root>/reports. This module lives at
        # src/mcp/resources/reports.py, so three parents up is the repo root.
        root = Path(__file__).resolve().parent.parent.parent.parent / "reports"
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_report_path(run_id: str) -> Path:
    """Resolve a report ``run_id`` to a traversal-safe on-disk path.

    SECURITY (CWE-22): ``run_id`` arrives from a ``report://<run_id>`` resource
    read and is therefore caller-controlled. It may contain path-traversal
    sequences (``../``), absolute paths, separators, or NUL bytes. This helper
    mirrors :func:`src.api.routers.files._safe_upload_path`:

      * Strips directory components via ``Path(run_id).name``.
      * Rejects empty, ``.``, ``..``, and NUL-byte ids.
      * Builds ``<reports_root>/<run_id>.html``, resolves it, and verifies via
        ``relative_to`` that the candidate is contained within the resolved
        reports directory.

    The returned path is guaranteed to live inside the reports dir; the file
    is NOT required to exist (existence is the caller's concern — see
    :func:`read_report_html`).

    Args:
        run_id: The report id from a ``report://<run_id>`` URI.

    Returns:
        A :class:`pathlib.Path` guaranteed to live inside the reports dir.

    Raises:
        ResourceError: When *run_id* is empty, ``.`` / ``..``, contains a NUL
            byte, or otherwise escapes the reports directory.
    """
    if not run_id or not isinstance(run_id, str):
        raise ResourceError("report id is required and must be a non-empty string")
    if "\x00" in run_id:
        raise ResourceError("invalid report id")
    base = Path(run_id).name
    if not base or base in {".", ".."}:
        raise ResourceError("invalid report id")

    reports_root = reports_dir().resolve()
    candidate = (reports_root / f"{base}{_REPORT_SUFFIX}").resolve()
    try:
        candidate.relative_to(reports_root)
    except ValueError:
        # The id escaped the reports dir (e.g. ``../etc/passwd``) — reject,
        # never serve (ADR 0023 §4 "Serve only from the reports dir").
        raise ResourceError("invalid report id")
    return candidate


def read_report_html(run_id: str) -> str:
    """Read and return the HTML body of the report for *run_id*.

    Resolves *run_id* traversal-safely via :func:`resolve_report_path`, then
    reads the file. A missing file yields a clean "report not found / expired"
    error (ADR 0023 §4) — an expired report is a normal terminal state, not a
    stack trace or a directory probe.

    Args:
        run_id: The report id from a ``report://<run_id>`` URI.

    Returns:
        The report's HTML body as a UTF-8 string.

    Raises:
        ResourceError: When the id is invalid (traversal) or no report file
            exists for the id (not found / expired).
    """
    path = resolve_report_path(run_id)
    if not path.is_file():
        raise ResourceError(
            f"report not found or expired for id {Path(run_id).name!r}"
        )
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ResourceError(f"failed to read report {Path(run_id).name!r}: {exc}") from exc


def report_uri_for(run_id: str) -> str:
    """Return the ``report://<run_id>`` MCP resource URI for *run_id*."""
    return f"report://{run_id}"


def report_url_for(run_id: str) -> str:
    """Return the ``/reports/<run_id>.html`` HTTP/UI path for *run_id* (REST parity)."""
    return f"/reports/{run_id}{_REPORT_SUFFIX}"
