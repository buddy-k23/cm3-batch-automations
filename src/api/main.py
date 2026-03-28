"""FastAPI main application."""

from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
import logging
import os
import sys
from pathlib import Path

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
from src.utils.cleanup import cleanup_old_files

logger = logging.getLogger(__name__)

FILE_RETENTION_HOURS = float(os.getenv("FILE_RETENTION_HOURS", "24"))
_UPLOADS_DIR = Path(__file__).parent.parent.parent / "uploads"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler: runs startup cleanup, then yields."""
    # Startup: remove stale uploaded files
    result = cleanup_old_files(_UPLOADS_DIR, FILE_RETENTION_HOURS)
    if result["deleted_count"] > 0:
        logger.info(
            "Startup cleanup: removed %d files (%d bytes)",
            result["deleted_count"],
            result["deleted_bytes"],
        )
    yield
    # Shutdown: nothing needed


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

allowed_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost,http://127.0.0.1").split(",") if o.strip()]

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
app.include_router(runs_router)
app.include_router(schedule_router)
app.include_router(
    rules_router_mod.router,
    prefix="/api/v1/rules",
    tags=["Rules"]
)
app.include_router(api_tester_router)
app.include_router(
    webhook_router,
    prefix="/api/v1/webhook",
    tags=["Webhook"]
)
app.include_router(
    multi_record_router,
    prefix="/api/v1/multi-record",
    tags=["Multi-Record"],
    dependencies=[Depends(require_api_key)],
)

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
    """Download a sample mapping or rules CSV template."""
    from fastapi.responses import FileResponse
    safe_name = Path(filename).name  # prevent path traversal
    file_path = _TEMPLATES_DIR / safe_name
    if not file_path.exists() or not file_path.suffix == ".csv":
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
    """Handle all unhandled exceptions."""
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": "Internal server error",
            "details": str(exc)
        }
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
