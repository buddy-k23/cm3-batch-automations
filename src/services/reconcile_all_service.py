"""Bulk mapping-vs-table reconciliation service seam (S16-3, #421).

This module is the single, thin service seam wrapping the ``reconcile-all``
workflow that used to be inlined in ``src/main.py``: iterate a directory of
mapping documents, build the adapter via
:func:`~src.database.adapters.factory.get_database_adapter` (honouring
``DB_ADAPTER`` per ADR 0022), reconcile each mapping against the live DB,
aggregate a summary, and — when a baseline report is supplied — compute the
baseline drift-diff.

It mirrors the shape of the single-mapping seam
:func:`src.services.reconcile_service.reconcile_mapping_service`: all business
logic lives here so the CLI (``valdo reconcile-all``), and any future REST /
MCP surface, are thin delegators (Architecture Principle #1 — no business
logic in main / routers / tools).

Result shape (the contract the CLI prints and REST/MCP could reuse)::

    {
        "total_mappings": int,
        "valid_mappings": int,
        "invalid_mappings": int,
        "total_errors": int,
        "total_warnings": int,
        "results": [                       # one per mapping file, in sort order
            {
                "mapping_file": str,
                "mapping_name": str,       # absent on a hard processing failure
                # ... plus the raw reconciler verdict keys (valid, errors,
                #     warnings, error_count, warning_count, ...)
            },
            ...
        ],
        # present ONLY when a baseline report was supplied:
        "drift": {
            "baseline": str,               # the baseline path
            "added_files": [str, ...],     # in current, not in baseline
            "removed_files": [str, ...],   # in baseline, not in current
            "changed": [                   # files whose error/warning counts moved
                {
                    "mapping_file": str,
                    "old_errors": int, "new_errors": int, "delta_errors": int,
                    "old_warnings": int, "new_warnings": int, "delta_warnings": int,
                },
                ...
            ],
            "new_errors": int,             # sum of positive error deltas
            "new_warnings": int,           # sum of positive warning deltas
        },
    }
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.config.loader import ConfigLoader
from src.config.mapping_parser import MappingParser
from src.database.adapters.factory import get_database_adapter
from src.database.reconciliation import SchemaReconciler

logger = logging.getLogger(__name__)

__all__ = ["reconcile_all_service", "compute_drift", "ReconcileAllServiceError"]


class ReconcileAllServiceError(ValueError):
    """Raised for caller-fixable bulk-reconcile errors (bad input).

    Distinct from infrastructure failures (a missing DB driver, an
    unreachable server) which surface as the underlying exception type so the
    caller's own error handling can classify them. Note that a *single*
    mapping that fails to parse/process is NOT raised — it is recorded as an
    invalid entry in ``results`` (parity with the old inlined behaviour).
    """


def reconcile_all_service(
    *,
    mappings_dir: str = "config/mappings",
    pattern: str = "*.json",
    baseline: Optional[str] = None,
    db_adapter: Optional[str] = None,
    on_mapping: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Reconcile every mapping in *mappings_dir* and aggregate the verdict.

    This is the single seam wrapping the bulk reconcile workflow for every
    surface. It builds the adapter via :func:`get_database_adapter` (honouring
    ``DB_ADAPTER``; an explicit *db_adapter* wins), opens one connection for
    the whole batch, reconciles each matching mapping file, aggregates a
    summary, and — when *baseline* is given — computes the drift-diff.

    A single mapping that fails to load/parse/reconcile is recorded as an
    invalid result entry (and counted as one error) rather than aborting the
    batch — exactly the behaviour the inlined CLI command had.

    Args:
        mappings_dir: Directory containing mapping JSON files. Defaults to
            ``config/mappings``.
        pattern: Glob pattern for mapping files within *mappings_dir*.
            Defaults to ``*.json``. Files are processed in sorted order.
        baseline: Optional path to a prior reconcile-all JSON report. When
            supplied, a ``drift`` block comparing current vs baseline
            error/warning counts is added to the result.
        db_adapter: Optional explicit adapter name (``oracle`` / ``postgresql``
            / ``sqlite``). When None the ``DB_ADAPTER`` env var is honoured.
        on_mapping: Optional callback invoked as ``on_mapping(mapping_file,
            entry)`` after each mapping is reconciled, where *entry* is the
            per-mapping result dict appended to ``results``. Lets a CLI render
            progress without leaking presentation into this service.

    Returns:
        The structured aggregate result dict documented in the module
        docstring.

    Raises:
        ValueError: When *db_adapter* is an unrecognised adapter name (from
            the factory).
        OSError: When *baseline* is supplied but cannot be read / parsed.
    """
    loader = ConfigLoader()
    parser = MappingParser()
    # Adapter selected via DB_ADAPTER (oracle/postgresql/sqlite); explicit arg wins.
    adapter = get_database_adapter(db_adapter)
    reconciler = SchemaReconciler(adapter)

    mapping_files = sorted(Path(mappings_dir).glob(pattern))

    results: List[Dict[str, Any]] = []
    total_errors = 0
    total_warnings = 0
    invalid_mappings = 0

    if mapping_files:
        # Open one connection for the whole batch (per-mapping reconcile is a
        # no-op on the already-open adapter).
        adapter.connect()
        try:
            for mapping_file in mapping_files:
                entry, errors, warnings, invalid = _reconcile_one(
                    loader, parser, reconciler, mapping_file
                )
                total_errors += errors
                total_warnings += warnings
                invalid_mappings += invalid
                results.append(entry)
                if on_mapping is not None:
                    on_mapping(str(mapping_file), entry)
        finally:
            adapter.disconnect()

    summary: Dict[str, Any] = {
        "total_mappings": len(mapping_files),
        "valid_mappings": len(mapping_files) - invalid_mappings,
        "invalid_mappings": invalid_mappings,
        "total_errors": total_errors,
        "total_warnings": total_warnings,
        "results": results,
    }

    if baseline:
        summary["drift"] = compute_drift(results, baseline)

    return summary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reconcile_one(
    loader: ConfigLoader,
    parser: MappingParser,
    reconciler: SchemaReconciler,
    mapping_file: Path,
) -> tuple[Dict[str, Any], int, int, int]:
    """Reconcile a single mapping file, returning its entry + tallies.

    Mirrors the per-file try/except body of the old inlined command: a
    successful reconcile yields the raw verdict merged with file/name keys; a
    failure is recorded as a one-error invalid entry rather than raised.

    Args:
        loader: The :class:`ConfigLoader` used to load the mapping JSON.
        parser: The :class:`MappingParser` used to parse it into a document.
        reconciler: The :class:`SchemaReconciler` bound to the open adapter.
        mapping_file: Path to the mapping JSON file.

    Returns:
        Tuple ``(entry, errors, warnings, invalid)`` where *entry* is the
        per-mapping result dict, *errors* / *warnings* are this mapping's
        counts to add to the totals, and *invalid* is ``1`` when the mapping
        is invalid (or failed to process), else ``0``.
    """
    try:
        mapping_dict = loader.load_mapping(str(mapping_file))
        mapping_doc = parser.parse(mapping_dict)
        result = reconciler.reconcile_mapping(mapping_doc)

        errors = result.get("error_count", len(result.get("errors", [])))
        warnings = result.get("warning_count", len(result.get("warnings", [])))
        invalid = 0 if result.get("valid", False) else 1

        entry = {
            "mapping_file": str(mapping_file),
            "mapping_name": mapping_doc.mapping_name,
            **result,
        }
        return entry, errors, warnings, invalid

    except Exception as file_error:  # noqa: BLE001 — parity with old CLI body
        logger.warning("Failed to process mapping %s: %s", mapping_file, file_error)
        entry = {
            "mapping_file": str(mapping_file),
            "valid": False,
            "errors": [f"Failed to process mapping: {file_error}"],
            "warnings": [],
            "error_count": 1,
            "warning_count": 0,
        }
        return entry, 1, 0, 1


def compute_drift(
    current_results: List[Dict[str, Any]], baseline: str
) -> Dict[str, Any]:
    """Compute the baseline drift-diff for a reconcile-all run.

    This is the ~90-line diff engine moved verbatim (in behaviour) out of the
    inlined CLI command: it loads the baseline report, indexes both runs by
    ``mapping_file``, and reports added / removed / changed mappings plus the
    aggregate counts of newly-introduced errors and warnings.

    Args:
        current_results: The ``results`` list from the current run.
        baseline: Path to a prior reconcile-all JSON report.

    Returns:
        The ``drift`` block documented in the module docstring.

    Raises:
        OSError: When *baseline* cannot be opened.
        json.JSONDecodeError: When *baseline* is not valid JSON.
    """
    with open(baseline, "r") as f:
        baseline_report = json.load(f)

    baseline_results = {
        r.get("mapping_file"): r
        for r in baseline_report.get("results", [])
        if r.get("mapping_file")
    }
    current = {
        r.get("mapping_file"): r
        for r in current_results
        if r.get("mapping_file")
    }

    baseline_files = set(baseline_results.keys())
    current_files = set(current.keys())

    added_files = sorted(current_files - baseline_files)
    removed_files = sorted(baseline_files - current_files)

    changed: List[Dict[str, Any]] = []
    new_errors = 0
    new_warnings = 0

    for mf in sorted(current_files & baseline_files):
        old = baseline_results[mf]
        new = current[mf]
        old_e = old.get("error_count", len(old.get("errors", [])))
        old_w = old.get("warning_count", len(old.get("warnings", [])))
        new_e = new.get("error_count", len(new.get("errors", [])))
        new_w = new.get("warning_count", len(new.get("warnings", [])))

        delta_e = new_e - old_e
        delta_w = new_w - old_w
        if delta_e != 0 or delta_w != 0:
            changed.append(
                {
                    "mapping_file": mf,
                    "old_errors": old_e,
                    "new_errors": new_e,
                    "delta_errors": delta_e,
                    "old_warnings": old_w,
                    "new_warnings": new_w,
                    "delta_warnings": delta_w,
                }
            )
            if delta_e > 0:
                new_errors += delta_e
            if delta_w > 0:
                new_warnings += delta_w

    return {
        "baseline": baseline,
        "added_files": added_files,
        "removed_files": removed_files,
        "changed": changed,
        "new_errors": new_errors,
        "new_warnings": new_warnings,
    }
