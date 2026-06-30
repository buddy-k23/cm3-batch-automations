"""Factory function for creating comparison-backend instances (S25-1).

The active backend is resolved in this priority order:

1. Explicit ``name`` argument passed to :func:`get_comparison_backend`.
2. ``COMPARISON_BACKEND`` environment variable.
3. Default value: ``"native"`` (the original pandas / SQLite engines —
   preserves current behaviour exactly).

Recognised names
----------------
- ``"native"`` / ``"pandas"`` — :class:`~src.comparators.backends.native_backend.NativeComparisonBackend`
  (default; wraps the existing ``FileComparator`` / ``ChunkedFileComparator`` dispatch).
- ``"duckdb"`` — :class:`~src.comparators.backends.duckdb_backend.DuckDBComparisonBackend`
  (S25-2; computes the diff with DuckDB SQL while emitting the identical native
  result contract).  Requires the optional ``duckdb`` package; selecting it
  without ``duckdb`` installed raises a clear ``ImportError`` with an install
  hint when the backend is actually used.
- ``"auto"`` (S25-4) — a *resolution mode*, not a concrete backend.  Resolved by
  :func:`resolve_backend` to ``"duckdb"`` **only when** the optional ``duckdb``
  package is importable AND the comparison is in the large regime (a file
  ``>= CHUNK_THRESHOLD_BYTES`` per
  :func:`~src.services.compare_service.should_use_chunked`); otherwise it
  silently resolves to ``"native"`` — including when ``duckdb`` is absent (no
  error).  The importability probe uses ``importlib.util.find_spec`` and never
  imports the ``duckdb`` module, so the default path stays duckdb-free.

Example::

    backend = get_comparison_backend()          # → NativeComparisonBackend
    backend = get_comparison_backend("native")  # always NativeComparisonBackend

    # Size/availability-aware selection (S25-4):
    name = resolve_backend(file1, file2)        # 'native' | 'duckdb'
    backend = get_comparison_backend(name)
"""

from __future__ import annotations

import importlib
import importlib.util
import os

from src.comparators.backends.base import ComparisonBackend

# Lazily imported by dotted path so an optional future backend's dependencies
# (e.g. duckdb in S25-2) never break import of this module.
_BACKEND_MAP = {
    "native": "src.comparators.backends.native_backend.NativeComparisonBackend",
    "pandas": "src.comparators.backends.native_backend.NativeComparisonBackend",
    "duckdb": "src.comparators.backends.duckdb_backend.DuckDBComparisonBackend",
}

# Recognised-but-unimplemented backends.  These are accepted as valid names
# (so callers get a clear "not yet implemented" signal rather than a generic
# "unknown backend" error) but raise NotImplementedError until delivered.
# (Empty now that ``duckdb`` is implemented in S25-2.)
_RESERVED_BACKENDS: set[str] = set()

# Default backend when neither an explicit name nor the env var is supplied.
_DEFAULT_BACKEND = "native"

# The ``auto`` *mode* is a valid env / explicit value but is not a concrete
# backend — it is resolved by :func:`resolve_backend` to ``native`` or
# ``duckdb``.  It is therefore accepted by :func:`resolve_backend` but is NOT a
# key of ``_BACKEND_MAP`` (passing the literal ``"auto"`` to
# :func:`get_comparison_backend` is a programming error and raises).
_AUTO_MODE = "auto"


def should_use_chunked(path: str) -> bool:
    """Thin lazy delegate to :func:`src.services.compare_service.should_use_chunked`.

    Defined here (rather than imported at module load) to avoid the circular
    import ``compare_service`` → ``backends`` → ``compare_service``.  Exposing it
    as a module attribute also gives tests a single, stable patch target
    (``src.comparators.backends.factory.should_use_chunked``) for the size route.

    Args:
        path: Filesystem path to the candidate file.

    Returns:
        True when the file is in the large regime (``>= CHUNK_THRESHOLD_BYTES``).
    """
    from src.services.compare_service import should_use_chunked as _impl

    return _impl(path)


def resolve_backend(
    file1: str,
    file2: str,
    explicit_name: str | None = None,
) -> str:
    """Resolve the effective comparison-backend name for a specific compare.

    Resolution order (S25-4): explicit name wins; else the ``COMPARISON_BACKEND``
    environment variable; else the default (``"native"``).  The result is always
    a *concrete* backend name (``"native"`` / ``"pandas"`` / ``"duckdb"``) — the
    ``"auto"`` mode is collapsed here so call sites can hand the result straight
    to :func:`get_comparison_backend`.

    ``auto`` collapses to:

    - ``"duckdb"`` **iff** the optional ``duckdb`` package is importable (cheap
      ``importlib.util.find_spec`` probe — the module is *not* imported here) AND
      either *file1* or *file2* is in the large regime
      (:func:`should_use_chunked`, i.e. ``>= CHUNK_THRESHOLD_BYTES``);
    - ``"native"`` otherwise — including when ``duckdb`` is absent.  In the
      duckdb-absent case **no error is raised and ``duckdb`` is never imported**.

    Args:
        file1: Path to the first file (used for the ``auto`` size check).
        file2: Path to the second file (used for the ``auto`` size check).
        explicit_name: Caller-supplied backend name (CLI/API/service param), or
            ``None`` to fall back to the env var / default.

    Returns:
        A concrete backend name suitable for :func:`get_comparison_backend`.

    Example::

        name = resolve_backend(f1, f2)              # env/default driven
        name = resolve_backend(f1, f2, "duckdb")    # explicit override
    """
    requested = explicit_name or os.getenv("COMPARISON_BACKEND", _DEFAULT_BACKEND)

    if requested != _AUTO_MODE:
        return requested

    # --- auto: availability + size gated, native-safe by default ------------
    # find_spec does NOT import duckdb; it only checks importability.  When
    # absent we silently use native (no error, no import).
    if importlib.util.find_spec("duckdb") is None:
        return _DEFAULT_BACKEND

    if should_use_chunked(file1) or should_use_chunked(file2):
        return "duckdb"

    return _DEFAULT_BACKEND


def get_comparison_backend(name: str | None = None) -> ComparisonBackend:
    """Return a comparison-backend instance for the requested engine.

    Args:
        name: One of ``"native"`` / ``"pandas"`` (the default engine), or the
            reserved placeholder ``"duckdb"``.  When *None*, the
            ``COMPARISON_BACKEND`` environment variable is read; defaults to
            ``"native"`` if neither is set — preserving current behaviour.

    Returns:
        A concrete :class:`~src.comparators.backends.base.ComparisonBackend`
        instance.

    Raises:
        NotImplementedError: If *name* is a recognised-but-unimplemented
            backend (currently ``"duckdb"``, reserved for S25-2).
        ValueError: If *name* is not a recognised backend at all.

    Example::

        backend = get_comparison_backend()
        result = backend.compare(file1, file2, key_columns, use_chunked=False)
    """
    resolved = name or os.getenv("COMPARISON_BACKEND", "native")

    if resolved in _RESERVED_BACKENDS:
        raise NotImplementedError(
            f"Comparison backend {resolved!r} is recognised but not yet "
            f"implemented (reserved for S25-2). Use 'native' (the default)."
        )

    if resolved not in _BACKEND_MAP:
        valid = sorted(set(_BACKEND_MAP) | _RESERVED_BACKENDS)
        raise ValueError(
            f"Unknown comparison backend: {resolved!r}. "
            f"Valid options are: {', '.join(valid)}"
        )

    module_path, class_name = _BACKEND_MAP[resolved].rsplit(".", 1)
    module = importlib.import_module(module_path)
    backend_class = getattr(module, class_name)
    return backend_class()
