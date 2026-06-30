from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.comparators.backends import get_comparison_backend
from src.comparators.backends.factory import resolve_backend

# Backward-compatible re-exports (S25-1). The engine dispatch and its two
# helpers now live in the default ``NativeComparisonBackend``; these aliases
# keep the historical ``compare_service`` import surface stable for existing
# callers and tests with no behaviour change.
from src.comparators.backends.native_backend import (  # noqa: F401
    _build_fixed_width_specs,
    _check_structure_compatibility,
)

# Shared chunked-routing threshold (S18-5, #423). The CLI and the API both
# route large compares to the chunked comparator using this single constant
# so their behaviour stays in lock-step.
CHUNK_THRESHOLD_BYTES: int = 50 * 1024 * 1024  # 50 MB


def should_use_chunked(path: str | Path) -> bool:
    """Return True when the file at *path* is large enough to warrant chunking.

    This is the single source of truth for the size-based auto-route decision
    shared by the CLI (``valdo compare``) and the API (``POST /compare``).

    Args:
        path: Filesystem path to the candidate file.

    Returns:
        True if the file size is >= :data:`CHUNK_THRESHOLD_BYTES`, False
        otherwise (including when the file does not exist).
    """
    try:
        return Path(path).stat().st_size >= CHUNK_THRESHOLD_BYTES
    except OSError:
        return False


def run_compare_service(
    file1: str,
    file2: str,
    keys: str | None = None,
    mapping: str | None = None,
    detailed: bool = True,
    chunk_size: int = 100000,
    progress: bool = False,
    use_chunked: bool = False,
    backend: str | None = None,
) -> dict[str, Any]:
    """Run the two-phase file comparison workflow (CLI and API entry point).

    Phase 1 checks structural compatibility via
    ``_check_structure_compatibility``. If the files are not compatible,
    returns early with ``structure_compatible: False`` and a
    ``structure_errors`` list. Phase 2 delegates to
    ``FileComparator`` (standard) or ``ChunkedFileComparator`` (chunked).

    The actual engine routing (in-memory vs chunked) and execution are
    delegated to a pluggable
    :class:`~src.comparators.backends.base.ComparisonBackend` obtained from
    :func:`~src.comparators.backends.get_comparison_backend`.  The default
    backend (``"native"``) reproduces the historical dispatch exactly, so the
    public behaviour of this function is unchanged.

    Backend selection (S25-4) is resolved by
    :func:`~src.comparators.backends.factory.resolve_backend` in the order:
    explicit *backend* arg → ``COMPARISON_BACKEND`` env var → default
    ``"native"``.  The env var (and the *backend* arg) accept ``native`` /
    ``pandas`` (the default engine), ``duckdb`` (always the DuckDB backend), and
    ``auto`` (DuckDB only when the optional ``duckdb`` package is importable and
    a file is in the large regime ``>= CHUNK_THRESHOLD_BYTES``; otherwise — and
    when duckdb is absent — native, with no error).  **With the env var unset
    and no *backend* arg the behaviour is byte-identical to the historical
    native path and ``duckdb`` is never imported.**

    Args:
        file1: Path to the first file.
        file2: Path to the second file.
        keys: Comma-separated key column names for row matching.
        mapping: Optional path to a JSON mapping config file.
        detailed: When True, include field-level diff analysis.
        chunk_size: Row chunk size for chunked processing.
        progress: Show progress output during chunked processing.
        use_chunked: Use the set-based chunked engine instead of the
            in-memory comparator.
        backend: Optional explicit comparison-backend name (``native`` /
            ``pandas`` / ``duckdb`` / ``auto``).  When ``None`` (default) the
            backend is resolved from the ``COMPARISON_BACKEND`` env var, then the
            ``native`` default — preserving the historical behaviour exactly.

    Returns:
        Dict containing at minimum ``structure_compatible``,
        ``total_rows_file1``, ``total_rows_file2``, ``matching_rows``,
        ``only_in_file1``, ``only_in_file2``, and ``differences``.
        When ``structure_compatible`` is False all numeric fields are 0.

    Raises:
        ValueError: If use_chunked=True and no keys are supplied.
        ValueError: If a fixed-width file is supplied without a mapping.
    """
    key_columns = [k.strip() for k in keys.split(',')] if keys else None

    mapping_config = None
    if mapping:
        with open(mapping, 'r', encoding='utf-8') as f:
            mapping_config = json.load(f)

    resolved_backend = resolve_backend(file1, file2, backend)
    comparison_backend = get_comparison_backend(resolved_backend)
    return comparison_backend.compare(
        file1,
        file2,
        key_columns,
        mapping_config=mapping_config,
        detailed=detailed,
        chunk_size=chunk_size,
        progress=progress,
        use_chunked=use_chunked,
    )
