"""Read-only MCP tool implementations for Valdo (EF-S2).

This module wires three MCP tools onto the existing Valdo service layer so
an agent can answer ``what is configured`` and ``what happened recently``
without any write surface:

* ``list_sources`` — enumerates the Valdo sources discovered on disk
  (``config/e2e/sources/*.yml``) and decorates each with a best-effort
  pointer to its most recent validation run.
* ``get_source_spec`` — returns the **bundle** of existing artefacts that
  define a source: its ``config/e2e/sources/<NAME>.yml`` overlay plus the
  mapping JSON/YAML files, rules JSON files, reconciliation YAML, and
  expected SQL files that live under conventional locations.
* ``list_recent_runs`` — returns recent validation-run summaries from
  ``RunHistoryRepository.fetch_history`` (Oracle / PG / SQLite via the
  shared adapter), optionally filtered by source. Degrades gracefully to
  ``[]`` when the run-history backend is unreachable — agents calling this
  from an offline dev environment should never see an exception.

The functions in this module are **thin adapters** — no business logic
lives here. The MCP tool decorators in :mod:`src.mcp.server` import these
helpers and return their results verbatim. The split exists so the
filesystem + DB plumbing is unit-testable without standing up the full
FastMCP transport.

Filesystem conventions (kept in sync with onboarding docs and
``scripts/e2e_lib/path_resolver.py``):

* Source overlay:           ``config/e2e/sources/<NAME>.yml``
* Mapping files:            ``config/mappings/<NAME>_*.{json,yaml,yml}``
* Rules files:              ``config/rules/<NAME>_*.json``
* Reconciliation YAMLs:     ``config/e2e/sources/<NAME>/reconciliation/*.yml``
* Expected SQL:             ``config/e2e/sources/<NAME>/sql/**/*.sql``

Run-history coupling (kept loose by design):

The Valdo run-history schema (``APP_RUN_HISTORY`` /
:class:`src.database.run_history.RunHistoryRepository`) records
``suite_name`` rather than an explicit ``source`` column. Source-to-run
joining is therefore a best-effort substring match against ``suite_name``
(case-insensitive). When a future story adds a first-class ``source``
column, the ``_run_matches_source`` helper here is the single place to
update.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml
from mcp.server.fastmcp.exceptions import ToolError

__all__ = [
    "list_sources_payload",
    "get_source_spec_bundle",
    "list_recent_runs_payload",
]

logger = logging.getLogger(__name__)

# Repository root — three parents up from this file:
#   src/mcp/tools.py  ->  src/mcp  ->  src  ->  <repo root>
_REPO_ROOT = Path(__file__).resolve().parents[2]

# Filesystem locations the tools read from. Kept module-level so tests can
# inspect them and future stories can move them without rewiring every
# call-site.
_SOURCES_DIR = _REPO_ROOT / "config" / "e2e" / "sources"
_MAPPINGS_DIR = _REPO_ROOT / "config" / "mappings"
_RULES_DIR = _REPO_ROOT / "config" / "rules"

# Mapping/rules artefact extensions. Mappings can be ``.json`` (flat) or
# ``.yaml``/``.yml`` (umbrella per ADR 0005). Rules are ``.json`` only.
_MAPPING_EXTENSIONS = (".json", ".yaml", ".yml")
_RULES_EXTENSIONS = (".json",)


# ---------------------------------------------------------------------------
# list_sources
# ---------------------------------------------------------------------------


def _discover_source_names() -> List[str]:
    """Return the names of every source declared under ``config/e2e/sources``.

    A source is recognised by the presence of ``<NAME>.yml`` directly under
    the sources directory. Subdirectories (such as ``SHAW/`` which holds the
    reconciliation + SQL bundle) are deliberately ignored here — the
    enumeration is driven by the overlay file, not by directory existence.

    Returns:
        Sorted list of source names. Empty list when the sources directory
        does not exist (e.g. partial checkouts) — callers see a clean empty
        enumeration rather than an exception.
    """
    if not _SOURCES_DIR.is_dir():
        return []
    names: List[str] = []
    for entry in _SOURCES_DIR.iterdir():
        if entry.is_file() and entry.suffix == ".yml":
            names.append(entry.stem)
    return sorted(names)


def _run_matches_source(run: Dict[str, Any], source: str) -> bool:
    """Return True when *run* appears to belong to *source*.

    The run-history schema does not carry an explicit ``source`` column;
    historically the source name is embedded in ``suite_name`` (e.g.
    ``SHAW_TRANERT_sit`` or ``shaw-atoctran-smoke``). We therefore match by
    case-insensitive substring. Generous on purpose: a false-positive in
    ``last_run_*`` is preferable to an empty pointer when the agent could
    have surfaced a useful link.

    Args:
        run: A row from ``RunHistoryRepository.fetch_history``.
        source: The canonical source name (e.g. ``"SHAW"``).

    Returns:
        True when ``source`` appears (case-insensitively) inside the run's
        ``suite_name``; False otherwise (including when ``suite_name`` is
        missing).
    """
    suite_name = run.get("suite_name") or ""
    return source.lower() in suite_name.lower()


def _fetch_recent_runs_safe(limit: int) -> List[Dict[str, Any]]:
    """Call the run-history service, returning ``[]`` on any failure.

    The ``RunHistoryRepository`` already logs-and-returns ``[]`` on DB
    failure, but we add a second belt-and-braces ``try/except`` here so an
    import-time failure (e.g. missing optional Oracle dependency in a slim
    dev environment) cannot bubble up into the MCP tool surface.

    Args:
        limit: Maximum number of rows to request.

    Returns:
        Recent run summary dicts (newest first) or ``[]`` when the backend
        is unavailable. A warning is logged in the degraded case so
        operators can spot the gap without affecting the agent flow.
    """
    try:
        from src.services.run_history_service import fetch_history_from_db
    except Exception as exc:  # noqa: BLE001 — defensive: importable backend is optional in dev
        logger.warning("run_history_service import failed: %s", exc)
        return []

    try:
        return fetch_history_from_db(limit=limit) or []
    except Exception as exc:  # noqa: BLE001 — repository already swallows DB errors; this guards against unexpected ones
        logger.warning("fetch_history_from_db raised unexpectedly: %s", exc)
        return []


def list_sources_payload() -> List[Dict[str, Any]]:
    """Return the source enumeration with best-effort last-run decoration.

    Each entry has the shape::

        {
            "name": "SHAW",
            "last_run_id": "exec_..." | None,
            "last_run_status": "PASS" | "FAIL" | "PARTIAL" | None,
            "last_run_at": "2026-06-13T22:00:00Z" | None,
        }

    The last-run lookup is best-effort: if the run-history backend is
    offline, or no run matches the source's name fragment, the three
    ``last_run_*`` fields are ``None``. This intentionally never raises.

    Returns:
        One entry per source discovered on disk, sorted by source name.
    """
    sources = _discover_source_names()
    if not sources:
        return []

    # Pull a single bounded slice of run history and partition it in
    # Python rather than issuing one DB query per source. A limit of 200
    # is plenty for ``last_run_*`` decoration without over-fetching.
    recent_runs = _fetch_recent_runs_safe(limit=200)

    payload: List[Dict[str, Any]] = []
    for name in sources:
        last_run: Optional[Dict[str, Any]] = next(
            (run for run in recent_runs if _run_matches_source(run, name)),
            None,
        )
        payload.append(
            {
                "name": name,
                "last_run_id": (last_run or {}).get("run_id"),
                "last_run_status": (last_run or {}).get("status"),
                "last_run_at": (last_run or {}).get("timestamp"),
            }
        )
    return payload


# ---------------------------------------------------------------------------
# get_source_spec
# ---------------------------------------------------------------------------


def _read_text_safe(path: Path) -> Optional[str]:
    """Return the UTF-8 text of *path*, or ``None`` when it cannot be read.

    Read failures are logged at WARNING level (so an operator can spot a
    broken artefact) but never propagated — the bundle is intentionally
    best-effort; a single unreadable rules JSON should not abort the
    whole call. The caller is responsible for deciding whether ``None``
    means "skip this entry" or "surface as a placeholder".

    Args:
        path: Absolute path to the file to read.

    Returns:
        The file's UTF-8 text, or ``None`` on any I/O error.
    """
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return None


def _relpath(path: Path) -> str:
    """Return *path* relative to the repo root in POSIX form.

    Bundle entries surface paths in the form ``config/mappings/SHAW_X.json``
    so an agent can quote them back verbatim regardless of where the Valdo
    deployment lives on disk. POSIX form (forward slashes) is enforced so
    Windows checkouts produce stable output.
    """
    try:
        return path.resolve().relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        # File lives outside the repo root (unusual; surface absolute).
        return path.as_posix()


def _collect_matching_files(
    directory: Path,
    prefix: str,
    extensions: Iterable[str],
) -> List[Dict[str, Optional[str]]]:
    """Return ``{path, content}`` entries for every ``prefix*.ext`` file.

    The prefix match is case-sensitive because Valdo's naming convention
    is uppercase-source-name (``SHAW_TRANERT.yaml``). The extensions are
    matched case-insensitively to absorb stray ``.YAML`` artefacts. Files
    that fail to read are still listed with ``content=None`` so the agent
    can see the path and prompt the user about the broken artefact.

    Args:
        directory: Absolute path of the directory to scan. Missing
            directories yield an empty list.
        prefix: Filename stem prefix (matched against the start of the
            stem; e.g. ``"SHAW"`` matches ``"SHAW_TRANERT"`` and
            ``"SHAW_ATOCTRAN_100_mapping"``).
        extensions: Iterable of acceptable extensions (lowercase, with
            leading dot). Files whose suffix is not in this set are
            skipped.

    Returns:
        Sorted list of ``{"path": str, "content": str | None}`` entries.
    """
    if not directory.is_dir():
        return []

    ext_set = {ext.lower() for ext in extensions}
    matches: List[Dict[str, Optional[str]]] = []
    # Sort by name so the bundle is deterministic between calls — agents
    # caching the response can diff successive bundles without spurious
    # reordering noise.
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in ext_set:
            continue
        stem = path.stem
        # Match either an exact-equal stem or a prefix followed by ``_``.
        # Naively using ``startswith(prefix)`` would also pull e.g.
        # ``SHAWS_X.json`` when scanning for ``SHAW``; the trailing
        # underscore (or exact equality) guards against that.
        if stem != prefix and not stem.startswith(f"{prefix}_"):
            continue
        matches.append(
            {
                "path": _relpath(path),
                "content": _read_text_safe(path),
            }
        )
    return matches


def _collect_recursive(
    root: Path,
    extensions: Iterable[str],
) -> List[Dict[str, Optional[str]]]:
    """Recursively collect ``{path, content}`` entries beneath *root*.

    Used for the expected-SQL bundle, which is organised into
    ``00_bootstrap``, ``10_load``, ``20_query`` subdirectories per file
    type. A missing root simply yields an empty list — sources without a
    SQL truth bundle (the common case until reconciliation is wired) are
    handled silently.

    Args:
        root: Absolute path of the directory to walk.
        extensions: Iterable of acceptable extensions (lowercase, with
            leading dot).

    Returns:
        Sorted list of bundle entries; ordering follows ``Path.rglob``'s
        sorted traversal so equivalent calls produce equivalent output.
    """
    if not root.is_dir():
        return []

    ext_set = {ext.lower() for ext in extensions}
    files = [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix.lower() in ext_set
    ]
    return [
        {
            "path": _relpath(path),
            "content": _read_text_safe(path),
        }
        for path in files
    ]


def get_source_spec_bundle(name: str) -> Dict[str, Any]:
    """Return the bundle of artefacts defining *name*.

    The shape is::

        {
            "source_yaml": "<raw YAML text>",
            "mapping_files": [{"path": "...", "content": "..."}, ...],
            "rules_files":   [{"path": "...", "content": "..."}, ...],
            "reconciliation_yaml": "<raw YAML text>" | None,
            "expected_sql_files": [{"path": "...", "content": "..."}, ...],
        }

    The bundle is the union of existing artefacts on disk — no
    transformation, no consolidation into a new spec format. Each
    ``content`` is the file's raw text so an agent can reason over the
    exact bytes it would have read itself.

    ``reconciliation_yaml`` is ``None`` for sources without a SQL-truth
    spec (the common case); the SHAW source ships
    ``config/e2e/sources/SHAW/reconciliation/tranert.yml`` so this is
    non-null in practice today. When multiple reconciliation YAMLs exist
    under the source directory their contents are concatenated with a
    YAML document separator so the existing field can stay a single
    string.

    Args:
        name: Canonical source name (e.g. ``"SHAW"``). Matched as a
            literal filename; no path traversal is performed.

    Returns:
        The artefact bundle described above.

    Raises:
        ToolError: When the source overlay file does not exist. FastMCP
            translates this into an MCP-spec error response automatically.
    """
    # Defensive normalisation: reject path-traversal payloads early. The
    # source name is interpolated into a Path below; a value with a slash
    # or a parent-directory escape would let an agent enumerate arbitrary
    # files. We accept only a plain identifier (letters, digits,
    # underscore, dash).
    if not name or any(ch in name for ch in "/\\.\x00"):
        raise ToolError(
            f"Invalid source name {name!r}: must be a plain identifier "
            "(no path separators, no dots, no NUL)."
        )

    overlay = _SOURCES_DIR / f"{name}.yml"
    if not overlay.is_file():
        raise ToolError(
            f"Unknown source {name!r}: no overlay found at "
            f"{_relpath(overlay)}. Available sources: "
            f"{_discover_source_names() or '<none>'}"
        )

    source_yaml_text = _read_text_safe(overlay) or ""

    mapping_files = _collect_matching_files(
        _MAPPINGS_DIR, prefix=name, extensions=_MAPPING_EXTENSIONS
    )
    rules_files = _collect_matching_files(
        _RULES_DIR, prefix=name, extensions=_RULES_EXTENSIONS
    )

    # Reconciliation YAML(s) live under ``config/e2e/sources/<NAME>/reconciliation``.
    # We support multiple files (different reconciliation specs per
    # output file type) by concatenating them with a YAML document
    # separator so the field stays a single string per the AC schema.
    reconciliation_dir = _SOURCES_DIR / name / "reconciliation"
    reconciliation_text: Optional[str]
    if reconciliation_dir.is_dir():
        docs: List[str] = []
        for yaml_path in sorted(reconciliation_dir.glob("*.yml")):
            content = _read_text_safe(yaml_path)
            if content is not None:
                docs.append(f"# === {_relpath(yaml_path)} ===\n{content}")
        reconciliation_text = "\n---\n".join(docs) if docs else None
    else:
        reconciliation_text = None

    # Expected SQL bundle. We walk ``config/e2e/sources/<NAME>/sql`` rather
    # than a flat directory because the harness organises SQL into
    # 00_bootstrap / 10_load / 20_query phases per file type.
    expected_sql_files = _collect_recursive(
        _SOURCES_DIR / name / "sql",
        extensions=(".sql",),
    )

    return {
        "source_yaml": source_yaml_text,
        "mapping_files": mapping_files,
        "rules_files": rules_files,
        "reconciliation_yaml": reconciliation_text,
        "expected_sql_files": expected_sql_files,
    }


# ---------------------------------------------------------------------------
# list_recent_runs
# ---------------------------------------------------------------------------


def _coerce_int(value: Any) -> Optional[int]:
    """Return *value* as an int when possible, else ``None``.

    Used to defend the ``error_count`` / ``duration_secs`` projection
    against the SQLAlchemy ``Decimal`` types that the Oracle adapter
    returns by default. ``None`` propagates through to the MCP response.
    """
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _infer_file_type(suite_name: str, source: Optional[str]) -> Optional[str]:
    """Best-effort extraction of a file-type tag from ``suite_name``.

    The Valdo run-history schema does not carry an explicit ``file_type``;
    suites are usually named like ``SHAW_TRANERT_sit`` or
    ``shaw-atoctran-smoke``. When the source name is known we strip it
    plus a separator and return the next token, uppercased. When the
    source is unknown, or no obvious file-type token follows, we return
    ``None`` rather than guessing wildly.

    Args:
        suite_name: The ``suite_name`` column of the run-history row.
        source: The canonical source name when known (None for the
            "all sources" listing).

    Returns:
        A file-type string (uppercase) or ``None`` when no confident
        extraction was possible.
    """
    if not suite_name:
        return None
    if source is None:
        return None
    lowered = suite_name.lower()
    src_lower = source.lower()
    idx = lowered.find(src_lower)
    if idx < 0:
        return None
    tail = suite_name[idx + len(source):]
    # Trim a single leading separator (underscore, dash, dot, space).
    if tail and tail[0] in "_-. ":
        tail = tail[1:]
    if not tail:
        return None
    # The next token ends at the next separator.
    for separator in ("_", "-", ".", " "):
        cut = tail.find(separator)
        if cut > 0:
            tail = tail[:cut]
            break
    return tail.upper() or None


def list_recent_runs_payload(
    source: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Return recent validation run summaries, optionally filtered by source.

    Args:
        source: Optional canonical source name (e.g. ``"SHAW"``). When
            given, only runs whose ``suite_name`` contains *source*
            (case-insensitive) are returned. When ``None``, runs from
            every source are returned.
        limit: Maximum number of summaries to return after filtering.
            Negative or zero values are treated as ``20``.

    Returns:
        Newest-first list of run summaries::

            {
                "run_id": "...",
                "source": "SHAW" | None,
                "file_type": "TRANERT" | None,
                "status": "PASS" | "FAIL" | "PARTIAL" | None,
                "started_at": "2026-06-13T22:00:00Z" | None,
                "duration_seconds": float | None,
            }

        Returns ``[]`` when the run-history backend is unreachable — the
        tool never raises on a degraded backend. A specific source with
        no matching runs also returns ``[]`` (not an error).
    """
    effective_limit = limit if isinstance(limit, int) and limit > 0 else 20

    # Over-fetch so post-filtering by source still produces ``limit`` rows
    # when the backend has lots of cross-source noise. The cap of 500 is a
    # belt-and-braces guard so a misconfigured caller cannot drain the
    # whole history table.
    fetch_limit = min(max(effective_limit * 5, effective_limit), 500)
    raw_runs = _fetch_recent_runs_safe(limit=fetch_limit)

    payload: List[Dict[str, Any]] = []
    for run in raw_runs:
        if source is not None and not _run_matches_source(run, source):
            continue
        suite_name = run.get("suite_name") or ""
        run_source = source if source is not None else None
        # When no source filter is in play we still try to surface the
        # source per row so the agent can group; we fall back to ``None``
        # when no configured source name appears in the suite_name.
        if run_source is None:
            for candidate in _discover_source_names():
                if candidate.lower() in suite_name.lower():
                    run_source = candidate
                    break
        payload.append(
            {
                "run_id": run.get("run_id"),
                "source": run_source,
                "file_type": _infer_file_type(suite_name, run_source),
                "status": run.get("status"),
                "started_at": run.get("timestamp"),
                # The Valdo run-history schema records per-test durations
                # in APP_RUN_TESTS but not a single per-run duration in
                # APP_RUN_HISTORY. We surface ``None`` rather than
                # fabricating a value; EF-S4 may aggregate this when the
                # write-side stories tighten the schema.
                "duration_seconds": None,
            }
        )
        if len(payload) >= effective_limit:
            break
    return payload


# ---------------------------------------------------------------------------
# Tool descriptions (kept here so server.py stays purely registration glue)
# ---------------------------------------------------------------------------

# Short, action-oriented descriptions. Agents read these to decide which
# tool to call — verbs first, no marketing copy.
LIST_SOURCES_DESCRIPTION = (
    "List all configured Valdo data sources discovered on disk, each "
    "decorated with a best-effort pointer to its most recent validation "
    "run (run_id, status, timestamp). Returns an empty list when no "
    "sources are configured. Never raises."
)

GET_SOURCE_SPEC_DESCRIPTION = (
    "Fetch the bundle of existing artefacts that define a single Valdo "
    "source: its source overlay YAML, every mapping file (flat JSON or "
    "umbrella YAML), every rules JSON, the reconciliation spec (when "
    "present), and the expected-SQL bundle. Each file is returned with "
    "its repo-relative path and raw text content. Raises a tool error "
    "when the named source does not exist."
)

LIST_RECENT_RUNS_DESCRIPTION = (
    "List recent Valdo validation-run summaries, optionally filtered to a "
    "single source. Each entry includes run_id, source, file_type, status, "
    "and start time. Returns an empty list (no exception) when the "
    "run-history backend is offline so agents in disconnected dev "
    "environments can still discover the tool surface."
)


def _yaml_dumps_for_text(value: Any) -> str:
    """Render *value* as deterministic YAML.

    Reserved for follow-up stories that may want to surface parsed YAML
    rather than raw text. Kept here so the import surface in tests can
    pin the helper, even though the current bundle returns raw text per
    the EF-S2 AC.
    """
    return yaml.safe_dump(value, sort_keys=False, default_flow_style=False)
