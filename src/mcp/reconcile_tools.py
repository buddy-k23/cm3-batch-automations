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
import uuid
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
    include_report: bool = False,
    suppress_pii: bool = True,
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
        include_report: Opt-in HTML report (S23-4, ADR 0023). When ``False``
            (default) behaviour is unchanged — JSON only, no HTML rendered.
            When ``True`` a :class:`ReconcileReporter` report is rendered into
            ``<reports_dir>/<id>.html`` and the response also carries
            ``report_uri`` / ``report_url`` / ``report_path``.
        suppress_pii: Forwarded to the renderer when ``include_report`` is set
            (default ``True``, ADR 0023 §4). Ignored otherwise.

    Returns:
        The structured verdict dict — ``status`` (clean | advisories |
        mismatch | error), ``valid``, ``summary`` counts, and the
        ``errors`` / ``mismatches`` / ``advisories`` field-level lists. See
        :func:`reconcile_mapping_service` for the full shape. When
        ``include_report=True`` three additional keys are present:
        ``report_uri`` / ``report_url`` / ``report_path``.

    Raises:
        ToolError: When the mapping cannot be loaded / parsed, the adapter
            name is invalid, or no target table can be resolved. Genuine
            type conflicts are NOT raised — they surface in the verdict's
            ``mismatches`` list so the agent can reason over them.
    """
    if not mapping or not isinstance(mapping, str):
        raise ToolError("mapping is required and must be a non-empty string")

    try:
        verdict = reconcile_mapping_service(
            mapping,
            table=table,
            schema=schema,
        )
    except (ReconcileServiceError, ValueError) as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface infra failures as ToolError
        logger.exception("reconcile_mapping tool failed for mapping=%r", mapping)
        raise ToolError(f"Reconciliation failed: {exc}") from exc

    # S23-4 (#446) / ADR 0023: opt-in HTML report rendered via ReconcileReporter
    # into ``<reports_dir>/<id>.html`` so the ``report://`` resolver finds it.
    if include_report:
        from src.mcp.resources.reports import (
            reports_dir,
            report_uri_for,
            report_url_for,
        )
        from src.reports.renderers.reconcile_renderer import ReconcileReporter

        report_id = uuid.uuid4().hex
        report_path = reports_dir() / f"{report_id}.html"
        ReconcileReporter().generate(
            verdict, str(report_path), suppress_pii=suppress_pii
        )
        verdict["report_uri"] = report_uri_for(report_id)
        verdict["report_url"] = report_url_for(report_id)
        verdict["report_path"] = str(report_path)

    return verdict


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
