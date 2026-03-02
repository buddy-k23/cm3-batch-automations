"""Web UI router — serves the single-page tester UI and run history API."""

import json
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter()

_UI_HTML_PATH = (
    Path(__file__).parent.parent.parent.parent
    / "src"
    / "reports"
    / "static"
    / "ui.html"
)
_RUN_HISTORY_PATH = Path("reports") / "run_history.json"


@router.get("/ui", response_class=HTMLResponse, include_in_schema=False)
async def serve_ui() -> HTMLResponse:
    """Serve the self-contained single-page tester UI.

    Returns:
        HTMLResponse: The full HTML page for the Quick Test and Recent Runs UI.
    """
    return HTMLResponse(content=_UI_HTML_PATH.read_text(encoding="utf-8"))


@router.get("/api/v1/runs/history")
async def get_run_history() -> JSONResponse:
    """Return the last 20 suite run history entries.

    Reads ``reports/run_history.json`` from the working directory, reverses
    the list so the most-recent entry comes first, and caps the result at 20
    entries.  Returns an empty list when the file does not exist or cannot
    be parsed.

    Returns:
        JSONResponse: A JSON array of run result dicts (most recent first,
        max 20 entries).  Each entry contains: run_id, suite_name,
        environment, timestamp, status, report_url, pass_count, fail_count,
        skip_count, total_count.
    """
    if not _RUN_HISTORY_PATH.exists():
        return JSONResponse(content=[])
    try:
        entries = json.loads(_RUN_HISTORY_PATH.read_text(encoding="utf-8"))
        return JSONResponse(content=entries[-20:][::-1])
    except Exception:
        return JSONResponse(content=[])
