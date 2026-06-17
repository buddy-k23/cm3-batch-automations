"""Reconcile-all MCP tool implementation (S21-2, #434).

Wires the ``reconcile_all`` MCP tool onto the shared bulk-reconcile service
seam (:func:`src.services.reconcile_all_service.reconcile_all_service`, S16-3).
Like the other action tools (#407 ``reconcile_mapping`` / S21-1 ``db_compare``)
this module is a thin adapter — no business logic lives here; the tool
registration itself stays in :mod:`src.mcp.server`.

The tool lets an agent bulk-reconcile every mapping in a directory against a
live database on whichever backend ``DB_ADAPTER`` selects (SQLite /
PostgreSQL / Oracle), returning the same aggregate summary the CLI
(``valdo reconcile-all``) produces — plus, when a baseline report is supplied,
the baseline drift-diff.

A single mapping that fails to load / parse / reconcile is a RESULT (it is
recorded as an invalid entry in ``results`` and counted in
``invalid_mappings``), NOT a tool error. Tool errors are reserved for
caller-fixable problems: an unrecognised ``db_adapter`` name, or a *baseline*
report that cannot be read / parsed.

Security: the aggregate summary carries mapping file paths and the
engine's error / warning text only — no connection credentials are ever
echoed back (``db_adapter`` is an adapter-name string, not a connection).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from mcp.server.fastmcp.exceptions import ToolError

from src.services.reconcile_all_service import (
    ReconcileAllServiceError,
    reconcile_all_service,
)

__all__ = [
    "reconcile_all_payload",
    "RECONCILE_ALL_DESCRIPTION",
]

logger = logging.getLogger(__name__)


def reconcile_all_payload(
    mappings_dir: str = "config/mappings",
    pattern: str = "*.json",
    baseline: Optional[str] = None,
    db_adapter: Optional[str] = None,
) -> Dict[str, Any]:
    """Bulk-reconcile every mapping in a directory and return the aggregate.

    Thin adapter over
    :func:`src.services.reconcile_all_service.reconcile_all_service`. The
    directory iteration, adapter selection, per-mapping reconciliation,
    aggregation, and baseline drift-diff all live in the service layer — this
    function only translates caller-fixable failures into :class:`ToolError`.

    Args:
        mappings_dir: Directory containing mapping JSON files. Defaults to
            ``config/mappings``.
        pattern: Glob pattern for mapping files within *mappings_dir*.
            Defaults to ``*.json``. Files are processed in sorted order.
        baseline: Optional path to a prior reconcile-all JSON report. When
            supplied, a ``drift`` block comparing current vs baseline
            error / warning counts is added to the result.
        db_adapter: Optional explicit adapter name (``sqlite`` /
            ``postgresql`` / ``oracle``). When omitted the env-configured
            ``DB_ADAPTER`` is honoured.

    Returns:
        The structured aggregate summary dict: ``total_mappings``,
        ``valid_mappings``, ``invalid_mappings``, ``total_errors``,
        ``total_warnings``, and the per-mapping ``results`` list. When
        *baseline* is supplied, an additional ``drift`` block is included.
        See :func:`reconcile_all_service` for the full shape.

    Raises:
        ToolError: When *db_adapter* is an unrecognised adapter name, or when
            *baseline* is supplied but cannot be read / parsed. A single
            mapping that fails to process is NOT raised — it surfaces as an
            invalid entry in ``results`` so the agent can reason over it.
    """
    if not mappings_dir or not isinstance(mappings_dir, str):
        raise ToolError("mappings_dir is required and must be a non-empty string")
    if not pattern or not isinstance(pattern, str):
        raise ToolError("pattern must be a non-empty string")

    try:
        return reconcile_all_service(
            mappings_dir=mappings_dir,
            pattern=pattern,
            baseline=baseline,
            db_adapter=db_adapter,
        )
    except (ReconcileAllServiceError, ValueError) as exc:
        raise ToolError(str(exc)) from exc
    except (OSError, json.JSONDecodeError) as exc:
        # A bad / missing baseline report is caller-fixable.
        raise ToolError(f"Failed to read baseline report: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 — surface infra failures as ToolError
        logger.exception(
            "reconcile_all tool failed for mappings_dir=%r", mappings_dir
        )
        raise ToolError(f"Bulk reconciliation failed: {exc}") from exc


RECONCILE_ALL_DESCRIPTION = (
    "Bulk-reconcile every Valdo mapping in a directory against a live "
    "database and return an aggregate verdict. Accepts a mappings_dir "
    "(defaults to config/mappings), an optional glob pattern (defaults to "
    "*.json), an optional baseline report path for drift detection, and an "
    "optional db_adapter override (sqlite | postgresql | oracle). Uses "
    "whichever backend DB_ADAPTER selects. Returns total_mappings, "
    "valid_mappings, invalid_mappings, total_errors, total_warnings, and a "
    "per-mapping 'results' list (each carrying the field-level reconcile "
    "verdict). When a baseline is supplied, an additional 'drift' block "
    "reports added / removed / changed mappings plus newly-introduced error "
    "and warning counts. A single mapping that fails to process is reported "
    "as an invalid result entry — NOT raised. Raises a tool error only for "
    "caller-fixable problems: a bad adapter name or an unreadable baseline "
    "report. No connection credentials are ever echoed in the response."
)
