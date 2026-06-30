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

Example::

    backend = get_comparison_backend()          # → NativeComparisonBackend
    backend = get_comparison_backend("native")  # always NativeComparisonBackend
"""

from __future__ import annotations

import importlib
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
