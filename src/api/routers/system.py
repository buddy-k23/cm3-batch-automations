"""System endpoints - health check and system information."""

from fastapi import APIRouter, Depends
from src.api.models.response import HealthResponse, SystemInfoResponse
from datetime import datetime
import sys

from src.api.auth import require_api_key
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
async def metrics_snapshot(_=Depends(require_api_key)):
    """Return runtime metrics snapshot for dashboard ingestion."""
    return METRICS.snapshot()


@router.get("/slo-alerts")
async def slo_alerts(_=Depends(require_api_key)):
    """Return evaluated SLO alerts for operators."""
    return {"alerts": METRICS.slo_alerts()}
