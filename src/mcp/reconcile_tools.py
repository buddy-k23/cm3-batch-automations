"""Reconcile MCP tool implementation (#407).

Wires the ``reconcile_mapping`` MCP tool onto the shared reconcile service
seam (:func:`src.services.reconcile_service.reconcile_mapping_service`). Like
the other action tools (EF-S4 / S7-4) this module is a thin adapter — no
business logic lives here; the tool registration itself stays in
:mod:`src.mcp.server`.

The tool lets an agent reconcile a Valdo mapping against a live database
table on whichever backend ``DB_ADAPTER`` selects (SQLite / PostgreSQL /
Oracle), returning the same structured field-level verdict the REST endpoint
and the CLI produce.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from mcp.server.fastmcp.exceptions import ToolError

from src.services.reconcile_service import (
    ReconcileServiceError,
    reconcile_mapping_service,
)

__all__ = [
    "reconcile_mapping_payload",
    "RECONCILE_MAPPING_DESCRIPTION",
]

logger = logging.getLogger(__name__)


def reconcile_mapping_payload(
    mapping: str,
    table: Optional[str] = None,
    schema: Optional[str] = None,
) -> Dict[str, Any]:
    """Reconcile a mapping against a database table and return the verdict.

    Thin adapter over
    :func:`src.services.reconcile_service.reconcile_mapping_service`.

    Args:
        mapping: A repo-relative path to a mapping JSON file, or a bare
            mapping id (filename stem) under ``config/mappings/``.
        table: Optional target table override. When omitted the mapping's
            declared ``target.table_name`` is used.
        schema: Optional schema / owner, prepended to the table.

    Returns:
        The structured verdict dict — ``status`` (clean | advisories |
        mismatch | error), ``valid``, ``summary`` counts, and the
        ``errors`` / ``mismatches`` / ``advisories`` field-level lists. See
        :func:`reconcile_mapping_service` for the full shape.

    Raises:
        ToolError: When the mapping cannot be loaded / parsed, the adapter
            name is invalid, or no target table can be resolved. Genuine
            type conflicts are NOT raised — they surface in the verdict's
            ``mismatches`` list so the agent can reason over them.
    """
    if not mapping or not isinstance(mapping, str):
        raise ToolError("mapping is required and must be a non-empty string")

    try:
        return reconcile_mapping_service(
            mapping,
            table=table,
            schema=schema,
        )
    except (ReconcileServiceError, ValueError) as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface infra failures as ToolError
        logger.exception("reconcile_mapping tool failed for mapping=%r", mapping)
        raise ToolError(f"Reconciliation failed: {exc}") from exc


RECONCILE_MAPPING_DESCRIPTION = (
    "Reconcile a Valdo mapping against a database table and return a "
    "field-level verdict. Accepts a mapping path or bare mapping id (under "
    "config/mappings/), an optional table override, and an optional schema. "
    "Uses whichever backend DB_ADAPTER selects (sqlite | postgresql | "
    "oracle). Returns status (clean | advisories | mismatch | error), a "
    "validity flag, summary counts, and the errors / mismatches / advisories "
    "lists: errors are hard failures (missing table or required column), "
    "mismatches are genuine type conflicts, advisories are non-blocking "
    "informational notes (e.g. boolean stored as integer, or a typeless "
    "column). Raises a tool error when the mapping cannot be loaded or no "
    "target table can be resolved — but a type conflict is reported in the "
    "verdict, not raised."
)
