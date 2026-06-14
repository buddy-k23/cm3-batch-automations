"""FastAPI main application."""

from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
import logging
import os
import sys
import uuid
import yaml
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.api.auth import require_api_key
from src.api.routers import mappings, files, system, tasks
from src.api.routers.ui import router as ui_router
from src.api.routers.runs import router as runs_router, schedule_router
from src.api.routers import rules as rules_router_mod
from src.api.routers.api_tester import router as api_tester_router
from src.api.routers.webhook import router as webhook_router
from src.api.routers.multi_record import router as multi_record_router
from src.mcp.server import build_mcp_server
from src.utils.cleanup import cleanup_old_files

logger = logging.getLogger(__name__)

FILE_RETENTION_HOURS = float(os.getenv("FILE_RETENTION_HOURS", "24"))
_UPLOADS_DIR = Path(__file__).parent.parent.parent / "uploads"
_UI_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "ui.yml"

# Read ui.yml early (before app creation) so the downloader router can be
# conditionally registered based on downloader.enabled in config/ui.yml.
_ui_cfg_early: dict = {}
if _UI_CONFIG_PATH.exists():
    import yaml as _yaml_early
    _ui_cfg_early = _yaml_early.safe_load(_UI_CONFIG_PATH.read_text()) or {}

# Build the MCP (Model Context Protocol) sub-app once at module import time
# so the lifespan handler below can nest its session manager. EF-S1 scaffold
# only — tools / resources / prompts land in EF-S2 / EF-S3 / EF-S6, and the
# real LDAPS + X-API-Key auth bridge lands in EF-S7.
_mcp_server, _mcp_sub_app = build_mcp_server()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler: runs startup cleanup, then yields.

    Composes the MCP Streamable HTTP session manager around the existing
    Valdo startup body so the MCP transport is live for the whole window
    in which the FastAPI app accepts requests. The MCP session manager
    MUST be entered before any ``/mcp/*`` request is served and exited on
    shutdown — see ``src/mcp/server.py`` for the rationale.
    """
    # Startup: remove stale uploaded files
    result = cleanup_old_files(_UPLOADS_DIR, FILE_RETENTION_HOURS)
    if result["deleted_count"] > 0:
        logger.info(
            "Startup cleanup: removed %d files (%d bytes)",
            result["deleted_count"],
            result["deleted_bytes"],
        )

    # Load tab visibility + downloader config from ui.yml
    if _UI_CONFIG_PATH.exists():
        with open(_UI_CONFIG_PATH) as _f:
            app.state.ui_config = yaml.safe_load(_f) or {}
    else:
        app.state.ui_config = {}

    logger.info(
        "Loaded ui_config with %d tab entries; downloader.enabled=%s",
        len((app.state.ui_config or {}).get("tabs", {})),
        (app.state.ui_config or {}).get("downloader", {}).get("enabled", False),
    )

    # Nest the MCP session manager so it bookends the yield. On shutdown,
    # the async context manager will be cleanly exited before this function
    # returns, ensuring the MCP transport releases its resources.
    async with _mcp_server.session_manager.run():
        yield
    # Shutdown: nothing else needed


# Create FastAPI application
app = FastAPI(
    lifespan=lifespan,
    title="Valdo API",
    description="""
    REST API for Valdo - File parsing, validation, and comparison tool.
    
    ## Features
    
    * **Universal Mapping Management**: Create and manage file mappings
    * **File Operations**: Parse, validate, and compare files
    * **Format Detection**: Automatically detect file formats
    * **Database Integration**: Oracle database connectivity
    * **Report Generation**: Generate HTML comparison reports
    
    ## Getting Started
    
    1. Upload an Excel/CSV template to create a mapping
    2. Use the mapping to parse and validate files
    3. Compare files and generate reports
    """,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

allowed_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "https://localhost,https://127.0.0.1").split(",") if o.strip()]

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# IP Whitelist middleware — loaded from ui.yml security section at module load time.
# add_middleware must be called before the app starts serving, so we read the config
# here rather than in the lifespan handler.
_ui_cfg_for_security: dict = {}
if _UI_CONFIG_PATH.exists():
    import yaml as _yaml_sec
    _ui_cfg_for_security = _yaml_sec.safe_load(_UI_CONFIG_PATH.read_text()) or {}

_security_cfg = _ui_cfg_for_security.get("security", {})
_ip_whitelist = _security_cfg.get("ip_whitelist", [])
_trust_proxy = _security_cfg.get("trust_proxy", False)
_trusted_proxies = _security_cfg.get("trusted_proxies", []) or []

if _ip_whitelist:  # only add middleware if whitelist is configured
    from src.api.middleware.ip_whitelist import IPWhitelistMiddleware
    app.add_middleware(
        IPWhitelistMiddleware,
        whitelist=_ip_whitelist,
        trust_proxy=_trust_proxy,
        trusted_proxies=_trusted_proxies,
    )

# ---------------------------------------------------------------------------
# Login / session — when auth.enabled is true, register Starlette
# SessionMiddleware (signed cookies) and the public /auth router. Service
# clients can keep using X-API-Key; verify_session_or_api_key prefers the
# session when both are present.
# ---------------------------------------------------------------------------
_auth_cfg = _ui_cfg_for_security.get("auth", {})
if _auth_cfg.get("enabled", False):
    from starlette.middleware.sessions import SessionMiddleware
    from src.utils.secrets import get_secrets_provider

    _sess_cfg = _auth_cfg.get("session", {})
    _signing_key_env = _sess_cfg.get("signing_key_env", "VALDO_SESSION_SIGNING_KEY")
    _signing_key = get_secrets_provider().get_secret(_signing_key_env)
    if not _signing_key:
        # Fail-closed: never run with an empty/default key. Mirrors the
        # API-key fail-closed behaviour from issue #9.
        raise RuntimeError(
            f"auth.enabled=true but no session signing key found at "
            f"env/secret '{_signing_key_env}'. Refusing to start."
        )

    app.add_middleware(
        SessionMiddleware,
        secret_key=_signing_key,
        session_cookie=_sess_cfg.get("cookie_name", "valdo_session"),
        https_only=_sess_cfg.get("cookie_secure", True),
        same_site=_sess_cfg.get("cookie_samesite", "lax"),
        max_age=int(_sess_cfg.get("max_age_minutes", 60)) * 60,
    )

    from src.api.routers.auth import router as auth_router
    app.include_router(auth_router)  # public — no Depends(require_api_key)

    # Surface the loaded ui_config on app.state so _try_resolve_session_cookie
    # can read auth.session.max_age_minutes during request handling.
    app.state.ui_config = _ui_cfg_for_security

# Include routers
app.include_router(
    mappings.router,
    prefix="/api/v1/mappings",
    tags=["Mappings"],
    dependencies=[Depends(require_api_key)],
)
app.include_router(
    files.router,
    prefix="/api/v1/files",
    tags=["Files"],
    dependencies=[Depends(require_api_key)],
)
app.include_router(
    system.router,
    prefix="/api/v1/system",
    tags=["System"],
)
app.include_router(
    tasks.router,
    prefix="/api/v1/tasks",
    tags=["Tasks"],
    dependencies=[Depends(require_api_key)],
)
app.include_router(ui_router)
app.include_router(runs_router, dependencies=[Depends(require_api_key)])
app.include_router(schedule_router, dependencies=[Depends(require_api_key)])
app.include_router(
    rules_router_mod.router,
    prefix="/api/v1/rules",
    tags=["Rules"],
    dependencies=[Depends(require_api_key)],
)
app.include_router(api_tester_router, dependencies=[Depends(require_api_key)])
app.include_router(
    webhook_router,
    prefix="/api/v1/webhook",
    tags=["Webhook"],
    dependencies=[Depends(require_api_key)],
)
app.include_router(
    multi_record_router,
    prefix="/api/v1/multi-record",
    tags=["Multi-Record"],
    dependencies=[Depends(require_api_key)],
)

# File Downloader — registered when downloader.enabled is true in config/ui.yml
if _ui_cfg_early.get("downloader", {}).get("enabled", False):
    from src.api.routers import downloader as _dl_mod
    app.include_router(_dl_mod.router, prefix="/api/v1/downloader", tags=["downloader"])

# Mount the MCP Streamable HTTP sub-app at /mcp. The sub-app carries its
# own Starlette auth middleware (VALDO_MCP_AUTH=dev gate); mount-time
# ordering matters here because the lifespan above already references
# `_mcp_server.session_manager`.
app.mount("/mcp", _mcp_sub_app, name="mcp")

# Serve generated reports
_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(_UPLOADS_DIR)), name="uploads")
_REPORTS_DIR = Path(__file__).parent.parent.parent / "reports"
_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/reports", StaticFiles(directory=str(_REPORTS_DIR)), name="reports")

_STATIC_DIR = Path(__file__).parent.parent / "reports" / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static-assets")

_TEMPLATES_DIR = _STATIC_DIR / "templates"


@app.get("/api/v1/templates/{filename}", tags=["Templates"])
async def download_template(filename: str):
    """Download a sample mapping or rules CSV template.

    SECURITY: ``Path(name).name`` alone does not protect against absolute
    paths or symlinks. We additionally resolve both the templates directory
    and the candidate path and require ``relative_to`` containment, plus a
    ``.csv`` suffix and ``is_file()`` check.
    """
    from fastapi.responses import FileResponse
    safe_name = Path(filename).name
    if not safe_name or safe_name in {".", ".."} or "\x00" in safe_name:
        return JSONResponse(status_code=400, content={"error": "Invalid template name"})
    templates_root = _TEMPLATES_DIR.resolve()
    file_path = (templates_root / safe_name).resolve()
    try:
        file_path.relative_to(templates_root)
    except ValueError:
        return JSONResponse(status_code=404, content={"error": "Template not found"})
    if file_path.suffix != ".csv" or not file_path.is_file():
        return JSONResponse(status_code=404, content={"error": "Template not found"})
    return FileResponse(file_path, filename=safe_name, media_type="text/csv")

_DOCS_DIR = Path(__file__).parent.parent.parent / "docs"


@app.get("/api/v1/guide", tags=["Docs"])
async def get_usage_guide(format: str = "markdown"):
    """Serve the usage and operations guide as markdown text."""
    guide_path = _DOCS_DIR / "USAGE_AND_OPERATIONS_GUIDE.md"
    if not guide_path.exists():
        return PlainTextResponse("# Usage Guide\n\nGuide not found.", status_code=404)
    content = guide_path.read_text(encoding="utf-8")
    return PlainTextResponse(content, media_type="text/plain; charset=utf-8")


_FAVICON_PATH = Path(__file__).parent.parent / "reports" / "static" / "favicon.svg"


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Serve the Valdo favicon."""
    if _FAVICON_PATH.exists():
        return FileResponse(_FAVICON_PATH, media_type="image/svg+xml")
    return JSONResponse(status_code=404, content={})


# Root endpoint
@app.get("/", tags=["Root"])
async def root():
    """Root endpoint - API information."""
    return {
        "name": "Valdo API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/api/v1/system/health"
    }

# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Handle all unhandled exceptions.

    SECURITY: Do not return ``str(exc)`` to the caller — internal exception
    messages frequently leak file paths, DB connection strings, and stack
    fragments. Instead, log the full traceback server-side and return an
    opaque ``error_id`` the operator can correlate against the logs.
    """
    error_id = uuid.uuid4().hex
    logger.exception(
        "unhandled_error error_id=%s method=%s path=%s",
        error_id,
        request.method,
        request.url.path,
    )
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": "Internal server error",
            "error_id": error_id,
        }
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
