"""Shared drift-detection helpers for onboarding artefacts (EE-S2).

This module hosts the equivalence-checking primitives used by both the
CLI (`valdo onboard-source --check`, EC-S6) and the API
(`POST /api/v2/onboarding/preview`, EE-S2). Before EE-S2 the helpers
lived inside ``src/commands/onboard_source.py`` as module-private
functions; EE-S2 promotes them here so the API layer can reuse the
exact same equivalence contract the CLI's `--check` mode uses.

The public surface intentionally mirrors what
``src/commands/onboard_source.py`` needs:

* :func:`compare_artefact_payload` — the structural-equivalence
  comparator. Given an emitted artefact (path, content string, category)
  it returns ``("new" | "changed" | "unchanged", drift_message)``.
  ``drift_message`` is ``None`` unless the status is ``"changed"``.
* :func:`normalise_metadata_for_compare` /
  :func:`normalise_sql_for_compare` — the per-category normalisation
  primitives the comparator depends on. Exposed for advanced callers
  (and for the unit tests that pin the EC-S10 timestamp behaviour).

Why a separate module?
    Two callers (CLI and API) now need the comparator. Keeping the
    helpers private to ``src/commands/onboard_source.py`` would force
    the API router to import a private name (a layering violation per
    the project's "no upward imports" rule). Extracting them here keeps
    both layers thin: the CLI orchestrates writes + UX, the API
    orchestrates HTTP + UX, and this module owns the equivalence
    contract.

Determinism (EC-S10):
    The metadata-block normalisation auto-extracts the committed
    artefact's ``metadata.created_date`` /  ``metadata.last_modified``
    and substitutes them into the emitted side, so timestamp drift is
    never reported as content drift. The basename normalisation on
    ``source_template`` / ``template_path`` lets the Windows-style
    historical paths in the committed JSONs compare equal to the
    POSIX-style synthetic basenames the emitter produces.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml

__all__ = [
    "ArtefactStatus",
    "ArtefactContentCache",
    "compare_artefact_payload",
    "compute_workbook_hash",
    "load_committed_artefact",
    "parse_emitted_artefact",
    "normalise_metadata_for_compare",
    "normalise_sql_for_compare",
    "extract_committed_timestamp",
    "get_artefact_content_cache",
    "path_basename",
    "strip_metadata",
    "summarise_dict_drift",
]


# ---------------------------------------------------------------------------
# Status tokens
# ---------------------------------------------------------------------------

# A "status string enum" rather than a typing.Literal so it round-trips
# cleanly through ``json.dumps`` and the test assertions can compare
# against the bare string. The values are part of the EE-S2 API contract
# (see ``CHANGELOG.md``).
class ArtefactStatus:
    """Static container for the three artefact-status tokens.

    The values are the wire-level constants surfaced in the API
    response. Treat them as a closed enum: the EE-S2 UI conditions
    rendering on these exact strings.
    """

    NEW = "new"
    CHANGED = "changed"
    UNCHANGED = "unchanged"


# ---------------------------------------------------------------------------
# Metadata-block normalisation (EC-S10)
# ---------------------------------------------------------------------------


def strip_metadata(data: Any) -> Any:
    """Return ``data`` with the converter-generated ``metadata`` block dropped.

    Pre-EC-S10 fallback used when ``--check`` cannot auto-extract
    timestamps from the committed artefact. EC-S10 prefers
    :func:`normalise_metadata_for_compare` which preserves the metadata
    block while neutralising only the irreducibly-variable fields.

    Args:
        data: A dict loaded from a converter-produced JSON/YAML
            artefact, or any other value (passed through unchanged).

    Returns:
        ``data`` with the top-level ``metadata`` key removed when
        ``data`` is a dict; otherwise returned as-is.
    """
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if k != "metadata"}
    return data


def path_basename(value: Any) -> Any:
    """Return ``value``'s trailing path component, treating ``\\`` like ``/``.

    Used by :func:`normalise_metadata_for_compare` so the Windows-style
    historical paths in the committed JSONs compare equal to the
    POSIX-style synthetic basenames the emitter produces. Non-string
    inputs are passed through unchanged for safety.

    Args:
        value: A string filesystem path (Windows or POSIX) or any
            other value.

    Returns:
        The trailing path component when ``value`` is a string, or
        ``value`` unchanged otherwise.
    """
    if not isinstance(value, str):
        return value
    return Path(value.replace("\\", "/")).name


def extract_committed_timestamp(data: Any) -> Optional[str]:
    """Pull a deterministic timestamp out of a committed artefact's metadata.

    Used by the ``--check`` / EE-S2 comparison path to auto-derive a
    per-artefact ``frozen_timestamp``. The returned string is substituted
    into the emitted artefact via :func:`normalise_metadata_for_compare`.

    Args:
        data: A dict loaded from a committed JSON / YAML artefact, or
            any other value.

    Returns:
        The committed ``metadata.created_date`` string when present,
        else ``None``.
    """
    if not isinstance(data, dict):
        return None
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("created_date")
    if isinstance(value, str):
        return value
    return None


def normalise_metadata_for_compare(
    emitted: Any, committed: Any
) -> Tuple[Any, Any]:
    """Normalise both sides' ``metadata`` blocks so byte equality is meaningful (EC-S10).

    Two converter-embedded fields are irreducibly variable between the
    emitter and the on-disk committed file:

      * ``created_date`` / ``last_modified`` — the converter calls
        ``datetime.utcnow()`` on every run unless ``frozen_timestamp``
        was set. Both are equally "correct"; semantically these
        fields are just an audit hint, not part of the schema.

      * ``source_template`` / ``template_path`` — the committed JSONs
        carry Windows-style paths from the historical CSV-driven
        authoring workflow; the emitter passes a synthetic POSIX-style
        basename. The two paths point at the same logical template.
        We coerce both sides to the trailing component only.

    Args:
        emitted: The dict produced by the emitter (or ``None`` /
            non-dict; passed through).
        committed: The dict loaded from disk (or ``None`` / non-dict;
            passed through).

    Returns:
        Tuple ``(emitted_normalised, committed_normalised)``. Both
        sides are shallow-copied (the metadata block is rewritten in
        place on the copy).
    """
    if not (isinstance(emitted, dict) and isinstance(committed, dict)):
        return emitted, committed

    emitted_copy = {**emitted}
    committed_copy = {**committed}

    emitted_meta = emitted_copy.get("metadata")
    committed_meta = committed_copy.get("metadata")
    if isinstance(emitted_meta, dict) and isinstance(committed_meta, dict):
        emitted_meta = {**emitted_meta}
        committed_meta = {**committed_meta}

        for ts_key in ("created_date", "last_modified"):
            if ts_key in committed_meta:
                emitted_meta[ts_key] = committed_meta[ts_key]
            elif ts_key in emitted_meta:
                emitted_meta.pop(ts_key, None)

        for path_key in ("source_template", "template_path"):
            if path_key in committed_meta:
                committed_meta[path_key] = path_basename(committed_meta[path_key])
            if path_key in emitted_meta:
                emitted_meta[path_key] = path_basename(emitted_meta[path_key])

        emitted_copy["metadata"] = emitted_meta
        committed_copy["metadata"] = committed_meta

    return emitted_copy, committed_copy


# ---------------------------------------------------------------------------
# SQL normalisation (ED-S2)
# ---------------------------------------------------------------------------


_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_WHITESPACE_RE = re.compile(r"\s+")


def normalise_sql_for_compare(text: str) -> str:
    """Collapse SQL text for whitespace-tolerant structural comparison (ED-S2).

    Strips ``-- ...`` line comments and collapses every run of
    whitespace (including newlines) to a single space. Two SQL strings
    comparing equal after this transformation have the same column
    count, alias spellings, FROM clause, and WHERE clause — the
    structural contract ED-S2 promises.

    Args:
        text: A SQL document (emitted or committed).

    Returns:
        The whitespace-collapsed, comment-stripped form.
    """
    no_comments = _LINE_COMMENT_RE.sub("", text)
    return _WHITESPACE_RE.sub(" ", no_comments).strip()


# ---------------------------------------------------------------------------
# Parse / load helpers
# ---------------------------------------------------------------------------


def parse_emitted_artefact(content: str, category: str, path: Path) -> Any:
    """Parse a planned artefact's content for ``--check`` / EE-S2 comparison.

    Args:
        content: The emitted artefact's text content.
        category: The artefact category (``"source_yaml"`` /
            ``"mapping"`` / ``"rules"`` / ``"reconciliation"`` /
            ``"sql"``).
        path: The destination path — used to discriminate JSON vs YAML
            for the ``mapping`` category (which can be either).

    Returns:
        The parsed data (YAML for ``.yml``/``.yaml`` artefacts and the
        source YAML; JSON for everything else). For ``sql`` callers
        should NOT use this helper — SQL is compared as text.
    """
    if category == "source_yaml":
        return yaml.safe_load(content)
    if path.suffix in {".yaml", ".yml"}:
        return yaml.safe_load(content)
    return json.loads(content)


def load_committed_artefact(
    path: Path, category: str
) -> Tuple[Any, bool]:
    """Load a committed artefact for ``--check`` / EE-S2 comparison.

    Args:
        path: On-disk path of the committed artefact.
        category: The artefact category.

    Returns:
        A tuple ``(parsed_data, exists)``. When the file is absent
        ``exists=False`` and ``parsed_data`` is ``None``. Parse
        failures propagate (the caller surfaces them as drift).
    """
    if not path.exists():
        return None, False
    text = path.read_text(encoding="utf-8")
    if category == "source_yaml":
        return yaml.safe_load(text), True
    if path.suffix in {".yaml", ".yml"}:
        return yaml.safe_load(text), True
    return json.loads(text), True


def summarise_dict_drift(emitted: Any, committed: Any) -> str:
    """Produce a short human-readable summary of structural drift.

    Best-effort: surfaces the first divergent top-level key when both
    sides are dicts, else falls back to a generic "values differ"
    message.

    Args:
        emitted: The emitted (in-memory) artefact structure.
        committed: The committed (on-disk) artefact structure.

    Returns:
        A one-line drift summary suitable for the CLI / UI report.
    """
    if isinstance(emitted, dict) and isinstance(committed, dict):
        emitted_keys = set(emitted.keys())
        committed_keys = set(committed.keys())
        only_emitted = sorted(emitted_keys - committed_keys)
        only_committed = sorted(committed_keys - emitted_keys)
        if only_emitted:
            return f"emitted has extra key(s): {only_emitted}"
        if only_committed:
            return f"committed has extra key(s): {only_committed}"
        for key in emitted_keys:
            if emitted[key] != committed[key]:
                return f"value for key '{key}' differs"
    return "structural values differ"


# ---------------------------------------------------------------------------
# The public comparator
# ---------------------------------------------------------------------------


def compare_artefact_payload(
    path: Path,
    content: str,
    category: str,
) -> Tuple[str, Optional[str]]:
    """Compare one emitted artefact against its committed counterpart.

    This is the EE-S2 / EC-S6 ``--check`` equivalence contract,
    returning the three-state status surfaced in the API response:

      * :data:`ArtefactStatus.NEW` — destination path does not exist.
      * :data:`ArtefactStatus.UNCHANGED` — destination exists and the
        emitted content matches under the per-category equivalence
        contract (metadata-normalised dict equality for mapping /
        rules / source YAML / reconciliation YAML; whitespace-
        normalised string equality for SQL).
      * :data:`ArtefactStatus.CHANGED` — destination exists but the
        emitted content does NOT match.

    Args:
        path: The resolved destination path the emitter would write to.
        content: The emitted artefact's serialised text content.
        category: The artefact category (``"source_yaml"`` /
            ``"mapping"`` / ``"rules"`` / ``"reconciliation"`` /
            ``"sql"``). Drives the per-category equivalence contract.

    Returns:
        Tuple ``(status, drift_message)`` where ``status`` is one of
        the :class:`ArtefactStatus` tokens and ``drift_message`` is a
        one-line summary when the status is ``"changed"``, else
        ``None``.
    """
    if category == "sql":
        if not path.exists():
            return ArtefactStatus.NEW, None
        committed_sql = path.read_text(encoding="utf-8")
        if normalise_sql_for_compare(content) == normalise_sql_for_compare(
            committed_sql
        ):
            return ArtefactStatus.UNCHANGED, None
        return (
            ArtefactStatus.CHANGED,
            "SQL structural drift (whitespace-normalised diff)",
        )

    try:
        emitted_data = parse_emitted_artefact(content, category, path)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        # Defensive: emitter contract guarantees parseable output, but
        # surface a clear message rather than crashing the whole batch.
        return ArtefactStatus.CHANGED, f"emitted-side parse error: {exc}"

    try:
        committed_data, exists = load_committed_artefact(path, category)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        return ArtefactStatus.CHANGED, f"committed-side parse error: {exc}"

    if not exists:
        return ArtefactStatus.NEW, None

    if category in {"source_yaml", "reconciliation"}:
        if emitted_data == committed_data:
            return ArtefactStatus.UNCHANGED, None
        return ArtefactStatus.CHANGED, summarise_dict_drift(
            emitted_data, committed_data
        )

    # mapping / rules — EC-S10 metadata normalisation.
    emitted_norm, committed_norm = normalise_metadata_for_compare(
        emitted_data, committed_data
    )
    if emitted_norm == committed_norm:
        return ArtefactStatus.UNCHANGED, None
    return ArtefactStatus.CHANGED, summarise_dict_drift(
        emitted_norm, committed_norm
    )


# ---------------------------------------------------------------------------
# Artefact-content cache (EE-S3)
# ---------------------------------------------------------------------------
#
# EE-S2 closed with the constraint that the ``/preview`` response does not
# carry the emitted content payloads (intentionally — keeps the JSON body
# small enough to render in the browser). EE-S3 needs that content twice:
#
#   * The "View" modal's unified-diff renderer now wants the emitted text
#     for ``changed`` and ``new`` artefacts so it can render a real
#     committed-vs-emitted diff (not just the committed file alone).
#   * The "Download ZIP" / "Open MR" buttons need the full artefact set
#     server-side after the user has already uploaded the workbook once.
#
# Rather than make the BA re-upload the workbook for each subsequent
# action, we cache the emitted artefact map keyed by the SHA-256 of the
# uploaded workbook bytes. The cache is:
#
#   * Process-local (no Redis / no SQLite — the BA workflow is a single
#     session, and a 5-entry LRU caps the worst-case footprint at a few
#     MB).
#   * Bounded by both size (5 entries) and time (15 minute TTL) so a
#     long-running uvicorn never accumulates stale entries.
#   * Thread-safe via a coarse-grained lock — the FastAPI threadpool
#     can hit ``put`` / ``get`` concurrently and we don't want a torn
#     OrderedDict.


_DEFAULT_CACHE_TTL_SECONDS = 15 * 60
_DEFAULT_CACHE_MAX_ENTRIES = 5


def compute_workbook_hash(content: bytes) -> str:
    """Return the SHA-256 hex digest of the supplied workbook bytes.

    Used as the cache key for :class:`ArtefactContentCache`. The hex
    digest is stable across processes so a re-uploaded workbook always
    hits the same cache slot.

    Args:
        content: The raw workbook bytes (e.g. the body of an
            ``UploadFile``).

    Returns:
        The SHA-256 hex digest, 64 lowercase hex characters.
    """
    return hashlib.sha256(content).hexdigest()


class ArtefactContentCache:
    """LRU + TTL cache for emitted artefact content keyed by workbook hash.

    Each entry is a mapping of ``{repo-relative path: emitted UTF-8
    text}`` produced by the EC-S6 planner. The cache is bounded by both
    size (``max_entries``) and age (``ttl_seconds``) so the per-process
    memory footprint stays predictable.

    Thread-safety:
        All public methods acquire a coarse-grained lock. Read paths
        (``get``) are cheap; write paths (``put``) trigger an eviction
        sweep which is O(n) over the current entries.

    Args:
        max_entries: Maximum number of workbook entries to retain.
            Older entries are evicted LRU-style on insert.
        ttl_seconds: Maximum age (in seconds) of any entry. Expired
            entries are dropped on access.
        time_fn: Time source for tests. Defaults to ``time.monotonic``
            so the clock cannot regress under wall-clock NTP skew.
    """

    def __init__(
        self,
        max_entries: int = _DEFAULT_CACHE_MAX_ENTRIES,
        ttl_seconds: float = _DEFAULT_CACHE_TTL_SECONDS,
        time_fn=time.monotonic,
    ) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._time_fn = time_fn
        self._entries: "OrderedDict[str, Tuple[float, Dict[str, str]]]" = (
            OrderedDict()
        )
        self._lock = threading.Lock()

    def put(self, workbook_hash: str, artefacts: Dict[str, str]) -> None:
        """Insert (or replace) the artefact map for *workbook_hash*.

        Triggers a TTL sweep + LRU eviction so the cache never exceeds
        the configured bounds.

        Args:
            workbook_hash: SHA-256 hex digest of the workbook bytes.
            artefacts: Mapping of repo-relative path to emitted text.
        """
        now = self._time_fn()
        with self._lock:
            self._evict_expired_locked(now)
            self._entries[workbook_hash] = (now, dict(artefacts))
            # Move to MRU position on replacement.
            self._entries.move_to_end(workbook_hash)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def get(self, workbook_hash: str) -> Optional[Dict[str, str]]:
        """Return the cached artefact map for *workbook_hash*, or ``None``.

        Args:
            workbook_hash: SHA-256 hex digest to look up.

        Returns:
            The cached ``{path: content}`` dict (a shallow copy so the
            caller cannot mutate the live entry), or ``None`` when the
            entry is missing or expired.
        """
        now = self._time_fn()
        with self._lock:
            self._evict_expired_locked(now)
            entry = self._entries.get(workbook_hash)
            if entry is None:
                return None
            # LRU bump: a successful read counts as recent activity.
            self._entries.move_to_end(workbook_hash)
            _ts, artefacts = entry
            return dict(artefacts)

    def get_content(
        self, workbook_hash: str, path: str
    ) -> Optional[str]:
        """Return the emitted text for one (*workbook_hash*, *path*) tuple.

        Convenience wrapper over :meth:`get` for the
        ``/artefact-content`` endpoint.

        Args:
            workbook_hash: SHA-256 hex digest to look up.
            path: Repo-relative path of the artefact.

        Returns:
            The emitted UTF-8 text, or ``None`` when either the
            workbook entry is missing/expired or the path is unknown.
        """
        artefacts = self.get(workbook_hash)
        if artefacts is None:
            return None
        return artefacts.get(path)

    def clear(self) -> None:
        """Drop every cached entry. Used by tests to reset state."""
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        """Return the current cached-entry count (post-expiration sweep)."""
        with self._lock:
            self._evict_expired_locked(self._time_fn())
            return len(self._entries)

    def _evict_expired_locked(self, now: float) -> None:
        """Drop entries older than the TTL. Must be called with the lock held."""
        expired: list[str] = []
        for key, (ts, _payload) in self._entries.items():
            if now - ts > self._ttl_seconds:
                expired.append(key)
        for key in expired:
            self._entries.pop(key, None)


# Module-level singleton — the FastAPI router holds a reference to this
# instance so every request shares the same cache. Tests reach into the
# singleton via :func:`get_artefact_content_cache` so they can call
# ``clear()`` between cases.
_ARTEFACT_CONTENT_CACHE: Optional[ArtefactContentCache] = None


def get_artefact_content_cache() -> ArtefactContentCache:
    """Return the process-wide artefact content cache singleton.

    Lazy-initialised on first access so import-time has no side effects.
    Tests can call ``.clear()`` on the returned object between cases.

    Returns:
        The shared :class:`ArtefactContentCache` instance.
    """
    global _ARTEFACT_CONTENT_CACHE
    if _ARTEFACT_CONTENT_CACHE is None:
        _ARTEFACT_CONTENT_CACHE = ArtefactContentCache()
    return _ARTEFACT_CONTENT_CACHE
