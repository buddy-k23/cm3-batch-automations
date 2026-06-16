"""Reconcile REST endpoint (#407).

Exposes ``POST /api/v2/reconcile`` — a thin router that delegates to
:func:`src.services.reconcile_service.reconcile_mapping_service` and shapes
the verdict as JSON. Architecture Principle #1: no business logic here.

The endpoint honours ``DB_ADAPTER`` (so it reconciles correctly against
SQLite / PostgreSQL / Oracle) and accepts an optional ``db_adapter`` body
field to override the adapter per request. It sits under ``/api/v2`` alongside
the onboarding router and inherits the same auth posture (session cookie or
``X-API-Key``) via the router registration in ``src/api/main.py``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from src.services.reconcile_service import (
    ReconcileServiceError,
    reconcile_mapping_service,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class ReconcileRequest(BaseModel):
    """Request body for ``POST /api/v2/reconcile``."""

    mapping: str = Field(
        ...,
        description=(
            "Mapping to reconcile: a repo-relative path to a mapping JSON "
            "file, or a bare mapping id (filename stem) under config/mappings/."
        ),
        examples=["customer_mapping", "config/mappings/customer_mapping.json"],
    )
    table: Optional[str] = Field(
        default=None,
        description=(
            "Optional target table override. When omitted, the mapping's "
            "declared target.table_name is used. May be schema-qualified."
        ),
    )
    schema_name: Optional[str] = Field(
        default=None,
        alias="schema",
        description="Optional schema / owner, prepended to the table.",
    )
    db_adapter: Optional[str] = Field(
        default=None,
        description=(
            "Optional adapter override (oracle | postgresql | sqlite). "
            "Defaults to the DB_ADAPTER env var."
        ),
    )

    model_config = {"populate_by_name": True}


class ReconcileSummary(BaseModel):
    """Numeric summary of a reconciliation verdict."""

    mapped_columns: int
    database_columns: int
    error_count: int
    warning_count: int
    advisory_count: int
    mismatch_count: int


class ReconcileResponse(BaseModel):
    """Structured reconciliation verdict returned to API callers."""

    status: str
    valid: bool
    mapping_name: str
    table: Optional[str]
    schema_name: Optional[str] = Field(default=None, alias="schema")
    db_adapter: str
    summary: ReconcileSummary
    errors: List[str]
    mismatches: List[str]
    advisories: List[str]
    warnings: List[str]
    unmapped_required: List[str]

    model_config = {"populate_by_name": True}


@router.post(
    "/reconcile",
    response_model=ReconcileResponse,
    response_model_by_alias=True,
    tags=["Reconcile"],
    summary="Reconcile a mapping against a database table",
)
def reconcile_endpoint(body: ReconcileRequest) -> Dict[str, Any]:
    """Reconcile a mapping document against a database table.

    Delegates entirely to
    :func:`src.services.reconcile_service.reconcile_mapping_service`; the
    adapter is selected from the request's ``db_adapter`` field or the
    ``DB_ADAPTER`` environment variable.

    Args:
        body: The :class:`ReconcileRequest` payload.

    Returns:
        The structured verdict dict (matching :class:`ReconcileResponse`).

    Raises:
        HTTPException: 400 when the mapping cannot be loaded / parsed, the
            adapter name is invalid, or no target table can be resolved.
            500 for unexpected infrastructure failures (logged with an
            opaque message — never leaking connection internals).
    """
    try:
        verdict = reconcile_mapping_service(
            body.mapping,
            table=body.table,
            schema=body.schema_name,
            db_adapter=body.db_adapter,
        )
    except (ReconcileServiceError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except Exception as exc:  # noqa: BLE001 — never leak infra internals
        logger.exception("reconcile endpoint failed for mapping=%r", body.mapping)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Reconciliation failed due to an internal error.",
        ) from exc

    # Map the service's ``schema`` key onto the response model's aliased field.
    verdict["schema"] = verdict.pop("schema", body.schema_name)
    return verdict
