"""System endpoints - health check and system information."""

from fastapi import APIRouter, Depends, Form, HTTPException
from src.api.models.response import HealthResponse, SystemInfoResponse
from datetime import datetime
from typing import List
import sys

from src.api.auth import require_api_key, require_role
from src.config.db_connections import get_named_connections
from src.database.connection import OracleConnection
from src.services.metrics_registry import METRICS

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """
    Health check endpoint.
    
    Returns system status and version information.
    """
    return HealthResponse(
        status="healthy",
        version="1.0.0",
        timestamp=datetime.utcnow().isoformat() + "Z"
    )


@router.get("/info", response_model=SystemInfoResponse)
async def system_info(_=Depends(require_api_key)):
    """
    Get system information.
    
    Returns Python version, API version, and supported formats.
    """
    return SystemInfoResponse(
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        api_version="1.0.0",
        supported_formats=["pipe_delimited", "fixed_width", "csv", "tsv"],
        database_connected=False  # TODO: Check actual database connection
    )


@router.get("/metrics")
async def metrics_snapshot(_=Depends(require_role("admin"))):
    """Return runtime metrics snapshot for dashboard ingestion."""
    return METRICS.snapshot()


@router.get("/slo-alerts")
async def slo_alerts(_=Depends(require_role("admin"))):
    """Return evaluated SLO alerts for operators."""
    return {"alerts": METRICS.slo_alerts()}


@router.get("/db-connections")
async def list_db_connections(_=Depends(require_api_key)) -> List[dict]:
    """Return a summary list of all named database connections.

    Reads connection profiles from the ``DB_CONNECTIONS`` environment variable
    via :func:`~src.config.db_connections.get_named_connections`.  Passwords
    are **never** included in the response.

    Args:
        _: API key dependency (injected by FastAPI).

    Returns:
        A list of dicts, each containing ``name``, ``host``, ``user``,
        ``schema``, and ``adapter``.  Returns an empty list when no
        connections are configured.
    """
    connections = get_named_connections()
    return [
        {
            "name": conn.name,
            "host": conn.host,
            "user": conn.user,
            "schema": conn.schema,
            "adapter": conn.adapter,
        }
        for conn in connections.values()
    ]


@router.post("/db-ping")
async def db_ping(
    db_host: str = Form(None),
    db_user: str = Form(None),
    db_password: str = Form(None),
    db_schema: str = Form(""),
    db_adapter: str = Form("oracle"),
    connection_name: str = Form(None),
    _key=Depends(require_api_key),
):
    """Test a database connection with the provided credentials.

    Oracle-only in the initial scope.  Non-Oracle adapters return
    ``{"ok": false, "error": "..."}`` without attempting a connection.

    When ``connection_name`` is provided, credentials are resolved from the
    named connection registry (``DB_CONNECTIONS`` env var) and override any
    individual ``db_host`` / ``db_user`` / ``db_password`` / ``db_schema`` /
    ``db_adapter`` fields.  Returns HTTP 404 if the name is not found.

    Args:
        db_host: Host/DSN string (e.g. ``localhost:1521/FREEPDB1``).
        db_user: Database username.
        db_password: Database password.
        db_schema: Schema name (informational; not used for ping).
        db_adapter: Database adapter (``oracle``, ``postgresql``, ``sqlite``).
            Only ``oracle`` is supported; others return an error.
        connection_name: Optional name of a pre-configured connection from the
            ``DB_CONNECTIONS`` env var (e.g. ``"STAGING"``).  When provided,
            credentials are resolved server-side and override individual fields.
        _key: API key dependency.

    Returns:
        ``{"ok": True}`` on success, or ``{"ok": False, "error": "<message>"}``
        on failure.

    Raises:
        HTTPException: 404 if ``connection_name`` is provided but not found.
    """
    if connection_name is not None:
        named = get_named_connections()
        if connection_name not in named:
            raise HTTPException(
                status_code=404,
                detail=f"Named connection '{connection_name}' not found",
            )
        conn_cfg = named[connection_name]
        db_host = conn_cfg.host
        db_user = conn_cfg.user
        db_password = conn_cfg.password
        db_schema = conn_cfg.schema
        db_adapter = conn_cfg.adapter

    if db_adapter != "oracle":
        return {
            "ok": False,
            "error": f"Connection test only supported for oracle adapter (got '{db_adapter}')",
        }
    try:
        conn = OracleConnection(username=db_user, password=db_password, dsn=db_host)
        try:
            conn.connect()
            return {"ok": True}
        finally:
            conn.disconnect()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
