"""System endpoints - health check and system information."""

from fastapi import APIRouter, Depends, Form
from src.api.models.response import HealthResponse, SystemInfoResponse
from datetime import datetime
import sys

from src.api.auth import require_api_key, require_role
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


@router.post("/db-ping")
async def db_ping(
    db_host: str = Form(...),
    db_user: str = Form(...),
    db_password: str = Form(...),
    db_schema: str = Form(""),
    db_adapter: str = Form("oracle"),
    _key=Depends(require_api_key),
):
    """Test a database connection with the provided credentials.

    Oracle-only in the initial scope.  Non-Oracle adapters return
    ``{"ok": false, "error": "..."}`` without attempting a connection.

    Args:
        db_host: Host/DSN string (e.g. ``localhost:1521/FREEPDB1``).
        db_user: Database username.
        db_password: Database password.
        db_schema: Schema name (informational; not used for ping).
        db_adapter: Database adapter (``oracle``, ``postgresql``, ``sqlite``).
            Only ``oracle`` is supported; others return an error.
        _key: API key dependency.

    Returns:
        ``{"ok": True}`` on success, or ``{"ok": False, "error": "<message>"}``
        on failure.
    """
    if db_adapter != "oracle":
        return {
            "ok": False,
            "error": f"Connection test only supported for oracle adapter (got '{db_adapter}')",
        }
    try:
        conn = OracleConnection(username=db_user, password=db_password, dsn=db_host)
        conn.connect()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
