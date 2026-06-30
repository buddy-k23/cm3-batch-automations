"""Pluggable file-comparison backend package (S25-1).

Exports the abstract base class and the factory function so that call sites
need only import from this package:

    from src.comparators.backends import ComparisonBackend, get_comparison_backend

Available backends
------------------
- ``"native"`` / ``"pandas"`` —
  :class:`~src.comparators.backends.native_backend.NativeComparisonBackend`
  (default; wraps the existing in-memory ``FileComparator`` and set-based
  ``ChunkedFileComparator`` dispatch with zero behaviour change).
- ``"duckdb"`` —
  :class:`~src.comparators.backends.duckdb_backend.DuckDBComparisonBackend`
  (S25-2; computes the diff with DuckDB SQL while emitting the identical native
  result contract).  Requires the optional ``duckdb`` package.

The active backend is selected via the ``COMPARISON_BACKEND`` environment
variable or explicitly by passing ``name`` to :func:`get_comparison_backend`;
it defaults to ``"native"``, preserving current behaviour.
"""

from src.comparators.backends.base import ComparisonBackend
from src.comparators.backends.factory import get_comparison_backend

__all__ = [
    "ComparisonBackend",
    "get_comparison_backend",
]
