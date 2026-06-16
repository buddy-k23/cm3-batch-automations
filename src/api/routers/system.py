"""System endpoints - health check and system information."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from src.api.models.response import (
    HealthResponse,
    ReadinessResponse,
    SystemInfoResponse,
)
from datetime import datetime
from typing import List
import sys

from src.api.auth import require_api_key, require_role
from src.api.models.db_profile import DbProfile
from src.config.db_connections import get_named_connections
from src.database.connection import OracleConnection
from src.services.db_health_service import check_db_connectivity
from src.services.db_profiles_service import load_profiles, resolve_profile
from src.services.metrics_registry import METRICS

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Liveness probe — is the process up?

    This is the **liveness** signal: it is intentionally cheap and does NOT
    touch the database, so a transient DB outage does not flap the load
    balancer or take the app out of rotation (S16-1, #425).  Use ``/ready``
    for DB-backed readiness.

    Returns:
        :class:`~src.api.models.response.HealthResponse` with ``status``,
        ``version``, and ``timestamp``.
    """
    return HealthResponse(
        status="healthy",
        version="1.0.0",
        timestamp=datetime.utcnow().isoformat() + "Z"
    )


@router.get("/ready", response_model=ReadinessResponse)
async def readiness_check():
    """Readiness probe — can the app serve DB-backed traffic?

    Performs a cheap, bounded ``SELECT 1`` against the configured database
    adapter (S16-1, #425).  Returns HTTP 200 when the database is reachable and
    HTTP 503 when it is not, so an orchestrator can stop routing DB-backed
    traffic without affecting liveness (``/health``).

    No auth required — readiness, like liveness, is an infrastructure signal.

    Returns:
        :class:`~src.api.models.response.ReadinessResponse`.  HTTP 503 when the
        database is unreachable.
    """
    connected = check_db_connectivity()
    payload = ReadinessResponse(
        ready=connected,
        database_connected=connected,
        timestamp=datetime.utcnow().isoformat() + "Z",
    )
    if not connected:
        return JSONResponse(status_code=503, content=payload.model_dump())
    return payload


@router.get("/info", response_model=SystemInfoResponse)
async def system_info(_=Depends(require_api_key)):
    """Get system information.

    Returns Python version, API version, supported formats, and the real
    database-connectivity state from a cheap, bounded probe (S16-1, #425) —
    no longer a hardcoded ``False``.

    Returns:
        :class:`~src.api.models.response.SystemInfoResponse`.
    """
    return SystemInfoResponse(
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        api_version="1.0.0",
        supported_formats=["pipe_delimited", "fixed_width", "csv", "tsv"],
        database_connected=check_db_connectivity(),
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


@router.get("/db-profiles")
async def get_db_profiles():
    """Return the list of named database connection profiles.

    Profiles are loaded from ``config/db_connections.yaml``.  Returns an
    empty list when the file does not exist.  Passwords are never included
    in the response.

    Returns:
        Dict with ``profiles`` key containing a list of
        :class:`~src.api.models.db_profile.DbProfile` dicts.
    """
    profiles = load_profiles()
    return {"profiles": [p.model_dump(by_alias=True) for p in profiles]}


@router.post("/db-ping")
async def db_ping(
    profile_name: str = Form(None),
    db_host: str = Form(None),
    db_user: str = Form(None),
    db_password: str = Form(None),
    db_schema: str = Form(""),
    db_adapter: str = Form("oracle"),
    connection_name: str = Form(None),
    _key=Depends(require_api_key),
):
    """Test a database connection using either a named profile or ad-hoc credentials.

    When ``profile_name`` is provided the connection parameters (including
    password) are resolved server-side from ``config/db_connections.yaml``
    and the matching environment variable.  Ad-hoc fields are ignored.

    When ``profile_name`` is absent, falls back to the supplied ``db_host``,
    ``db_user``, ``db_password`` fields (existing Custom behaviour).

    When ``connection_name`` is provided, credentials are resolved from the
    named connection registry (``DB_CONNECTIONS`` env var) and override any
    individual ``db_host`` / ``db_user`` / ``db_password`` / ``db_schema`` /
    ``db_adapter`` fields.  Returns HTTP 404 if the name is not found.

    Args:
        profile_name: Named profile from ``config/db_connections.yaml``.
        db_host: Host/DSN (ad-hoc path only).
        db_user: Username (ad-hoc path only).
        db_password: Password (ad-hoc path only).
        db_schema: Schema (informational; not used for ping).
        db_adapter: Adapter (ad-hoc path only).
        connection_name: Optional name of a pre-configured connection from the
            ``DB_CONNECTIONS`` env var.  When provided, credentials are resolved
            server-side and override individual fields.
        _key: API key dependency.

    Returns:
        ``{"ok": True}`` on success, or ``{"ok": False, "error": "..."}`` on
        failure.

    Raises:
        HTTPException: 404 if ``connection_name`` is provided but not found.
    """
    if profile_name:
        try:
            cfg = resolve_profile(profile_name)
        except (KeyError, RuntimeError) as exc:
            return {"ok": False, "error": str(exc)}
        resolved_adapter = cfg.db_adapter
        resolved_host = cfg.dsn
        resolved_user = cfg.user
        resolved_password = cfg.password
    elif connection_name is not None:
        named = get_named_connections()
        if connection_name not in named:
            raise HTTPException(
                status_code=404,
                detail=f"Named connection '{connection_name}' not found",
            )
        conn_cfg = named[connection_name]
        resolved_host = conn_cfg.host
        resolved_user = conn_cfg.user
        resolved_password = conn_cfg.password
        db_schema = conn_cfg.schema
        resolved_adapter = conn_cfg.adapter
    else:
        resolved_adapter = db_adapter
        resolved_host = db_host or ""
        resolved_user = db_user or ""
        resolved_password = db_password or ""

    if resolved_adapter != "oracle":
        return {
            "ok": False,
            "error": f"Connection test only supported for oracle adapter (got '{resolved_adapter}')",
        }
    try:
        conn = OracleConnection(
            username=resolved_user,
            password=resolved_password,
            dsn=resolved_host,
        )
        try:
            conn.connect()
            return {"ok": True}
        finally:
            conn.disconnect()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


_ALL_TABS = ["quick", "runs", "mapping", "tester", "dbcompare", "downloader"]


@router.get("/ui-config")
async def get_ui_config(request: Request):
    """Return effective tab visibility from config/ui.yml.

    The ``downloader`` tab is gated by ``downloader.enabled`` in
    ``config/ui.yml``.
    No auth required — tab visibility is not sensitive.

    Args:
        request: FastAPI request (used to access app.state.ui_config).

    Returns:
        Dict with key ``tabs`` mapping each tab name to a boolean.
    """
    ui_cfg = getattr(request.app.state, "ui_config", {})
    tabs_cfg = ui_cfg.get("tabs", {})
    dl_enabled = bool(ui_cfg.get("downloader", {}).get("enabled", False))

    tabs = {}
    for tab in _ALL_TABS:
        enabled = bool(tabs_cfg.get(tab, False))
        if tab == "downloader":
            enabled = enabled and dl_enabled
        tabs[tab] = enabled

    return {"tabs": tabs}
