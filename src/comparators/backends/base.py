"""Abstract base class for pluggable file-comparison backends (S25-1).

A *comparison backend* encapsulates the engine that actually computes the
diff between two batch files: it owns the choice between an in-memory
(:class:`~src.comparators.file_comparator.FileComparator`) and a set-based
(:class:`~src.comparators.chunked_comparator.ChunkedFileComparator`) strategy,
and returns the canonical comparison result dict that
:func:`~src.services.compare_service.run_compare_service` exposes to the CLI
and API.

The seam exists so that an alternative engine (e.g. a DuckDB-backed backend,
delivered in S25-2) can be slotted in via
:func:`~src.comparators.backends.factory.get_comparison_backend` **without**
changing any call site or the public result contract.  This mirrors the
established :mod:`src.database.adapters` factory pattern.

All concrete backends must implement :meth:`ComparisonBackend.compare` and
return a dict in exactly the same shape ``run_compare_service`` returns today.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ComparisonBackend(ABC):
    """Engine-agnostic contract for comparing two batch files.

    Concrete backends own the in-memory-vs-chunked routing decision and the
    actual comparison execution.  Input *parsing* (splitting the ``keys``
    string, loading the JSON mapping file) is performed by the caller — the
    backend receives already-parsed ``key_columns`` and ``mapping_config`` so
    that the routing/engine concern is cleanly isolated and an alternative
    engine can override only that concern.
    """

    @abstractmethod
    def compare(
        self,
        file1: str,
        file2: str,
        key_columns: list[str] | None,
        *,
        mapping_config: dict[str, Any] | None = None,
        detailed: bool = True,
        chunk_size: int = 100000,
        progress: bool = False,
        use_chunked: bool = False,
    ) -> dict[str, Any]:
        """Compare two files and return the canonical comparison result dict.

        The returned dict must match the contract that
        :func:`~src.services.compare_service.run_compare_service` exposes
        today.  Two shapes are produced depending on the routing:

        - **In-memory path** (``use_chunked=False``): keys include
          ``structure_compatible``, ``total_rows_file1`` / ``total_rows_file2``,
          ``matching_rows``, ``only_in_file1`` / ``only_in_file2``,
          ``differences``, ``rows_with_differences`` and ``field_statistics``.
          When the files are structurally incompatible the method returns
          early with ``structure_compatible=False``, a ``structure_errors``
          list and all numeric fields set to 0.

        - **Chunked path** (``use_chunked=True``): the set-based engine's
          contract — counts, ``only_in_file*`` lists with matching
          ``*_count`` fields, ``total_differences_found`` and
          ``differences_truncated`` flags (no ``structure_compatible`` key).

        Args:
            file1: Path to the first file.
            file2: Path to the second file.
            key_columns: Already-parsed key column names for row matching, or
                ``None`` for positional row-by-row comparison.
            mapping_config: Already-loaded mapping dict (containing a
                ``fields`` list), or ``None``.  Required for fixed-width files.
            detailed: When True, include field-level diff analysis.
            chunk_size: Row chunk size for chunked processing.
            progress: Show progress output during chunked processing.
            use_chunked: Route to the set-based chunked engine instead of the
                in-memory engine.

        Returns:
            The comparison result dict (see shapes above).

        Raises:
            ValueError: If ``use_chunked`` is True and no ``key_columns`` are
                supplied, or if a fixed-width file is supplied without a
                mapping containing ``fields`` metadata.
        """
        raise NotImplementedError
