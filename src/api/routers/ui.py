"""Web UI router — serves the single-page tester UI and run history API."""

import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

try:
    from src.services.run_history_service import fetch_history_from_db
except ImportError:
    fetch_history_from_db = None  # type: ignore[assignment]

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

    Reads from Oracle DB when ORACLE_USER is configured,
    with automatic fallback to reports/run_history.json.

    Returns:
        JSONResponse: A JSON array of run result dicts (most recent first,
        max 20 entries).  Each entry contains: run_id, suite_name,
        environment, timestamp, status, report_url, pass_count, fail_count,
        skip_count, total_count.
    """
    logger = logging.getLogger(__name__)
    oracle_user = os.getenv("ORACLE_USER")
    logger.info(f"ORACLE_USER={oracle_user}, fetch_history_from_db={fetch_history_from_db}")
    
    if oracle_user and fetch_history_from_db is not None:
        try:
            results = fetch_history_from_db(limit=20)
            # Transform status to match UI expectations (PASS/FAIL instead of passed/failed)
            # and convert Decimal to float for JSON serialization
            for r in results:
                if r.get("status") == "passed":
                    r["status"] = "PASS"
                elif r.get("status") == "failed":
                    r["status"] = "FAIL"
                # Convert Decimal to float
                if "quality_score" in r and r["quality_score"] is not None:
                    r["quality_score"] = float(r["quality_score"])
            return JSONResponse(content=results)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "run_history DB read failed, falling back to JSON: %s", exc
            )

    # JSON fallback
    if not _RUN_HISTORY_PATH.exists():
        return JSONResponse(content=[])
    try:
        entries = json.loads(_RUN_HISTORY_PATH.read_text(encoding="utf-8"))
        return JSONResponse(content=entries[-20:][::-1])
    except Exception as exc:
        logging.getLogger(__name__).warning("run_history JSON read failed: %s", exc)
        return JSONResponse(content=[])
