"""API Tester — proxy endpoint and suite CRUD."""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import List
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile

from src.api.models.api_tester import (
    ProxyResponse,
    Suite,
    SuiteCreate,
    SuiteSummary,
)

router = APIRouter(prefix="/api/v1/api-tester", tags=["API Tester"])

SUITES_DIR = Path(__file__).parent.parent.parent.parent / "config" / "api-tester" / "suites"
SUITES_DIR.mkdir(parents=True, exist_ok=True)

_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


# ---------------------------------------------------------------------------
# Proxy
# ---------------------------------------------------------------------------

@router.post("/proxy", response_model=ProxyResponse)
async def proxy_request(
    config: str = Form(...),
    uploaded_files: List[UploadFile] = File([]),
):
    """Proxy an HTTP request to any URL and return the response.

    Args:
        config: JSON string with keys: method, url, headers, body_type,
                body_json, form_fields, timeout.
        uploaded_files: Optional file uploads for form-data requests.

    Returns:
        ProxyResponse with status_code, headers, body, elapsed_ms.

    Raises:
        HTTPException: 422 if config is not valid JSON.
        HTTPException: 502 if target host is unreachable.
        HTTPException: 504 if the request timed out.
    """
    try:
        cfg = json.loads(config)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"config is not valid JSON: {exc}")

    method = cfg.get("method", "GET").upper()
    url = cfg.get("url", "")
    try:
        timeout = max(1, min(120, int(cfg.get("timeout", 30))))
    except (ValueError, TypeError):
        timeout = 30
    body_type = cfg.get("body_type", "none")

    # Build headers dict, strip Content-Type so httpx sets it correctly per body type
    fwd_headers: dict[str, str] = {
        h["key"]: h["value"]
        for h in cfg.get("headers", [])
        if h.get("key") and h["key"].lower() != "content-type"
    }

    # Validate URL scheme to prevent file:// and other non-HTTP(S) schemes
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=422, detail="Only http and https URLs are supported")
    if not parsed.netloc:
        raise HTTPException(status_code=422, detail="Invalid URL: missing host")

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as http:
            t0 = time.monotonic()

            if body_type == "json":
                resp = await http.request(
                    method, url,
                    headers={**fwd_headers, "Content-Type": "application/json"},
                    content=cfg.get("body_json", ""),
                )
            elif body_type == "form":
                non_file = {
                    f["key"]: f["value"]
                    for f in cfg.get("form_fields", [])
                    if f.get("key") and not f.get("is_file")
                }
                file_fields = [
                    f for f in cfg.get("form_fields", [])
                    if f.get("is_file") and f.get("key")
                ]
                files_map: dict = {}
                for idx, ff in enumerate(file_fields):
                    if idx < len(uploaded_files):
                        uf = uploaded_files[idx]
                        content = await uf.read()
                        files_map[ff["key"]] = (
                            uf.filename,
                            content,
                            uf.content_type or "application/octet-stream",
                        )
                resp = await http.request(
                    method, url,
                    headers=fwd_headers,
                    data=non_file,
                    files=files_map or None,
                )
            else:
                resp = await http.request(method, url, headers=fwd_headers)

            elapsed = (time.monotonic() - t0) * 1000

    except httpx.ConnectError as exc:
        raise HTTPException(
            status_code=502,
            detail={"error": "connection_failed", "detail": str(exc)},
        )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail={"error": "timeout"})
    except httpx.TooManyRedirects:
        raise HTTPException(status_code=502, detail={"error": "too_many_redirects"})
    except httpx.InvalidURL as exc:
        raise HTTPException(status_code=422, detail=f"Invalid URL: {exc}")

    try:
        body_text = resp.text
    except Exception:
        body_text = resp.content.decode("utf-8", errors="replace")

    return ProxyResponse(
        status_code=resp.status_code,
        headers=dict(resp.headers),
        body=body_text,
        elapsed_ms=round(elapsed, 1),
    )


# ---------------------------------------------------------------------------
# Suite CRUD helpers
# ---------------------------------------------------------------------------

def _suite_path(suite_id: str) -> Path:
    """Return the filesystem path for a suite JSON file.

    Args:
        suite_id: The UUID of the suite (must match standard UUID format).

    Returns:
        Path to the suite's JSON file within SUITES_DIR.

    Raises:
        HTTPException: 400 if suite_id is not a valid UUID format.
    """
    if not _UUID_RE.match(suite_id):
        raise HTTPException(status_code=400, detail="Invalid suite_id format")
    return SUITES_DIR / f"{suite_id}.json"


def _load_suite(suite_id: str) -> Suite:
    """Load a suite from disk, raising 404 if it does not exist.

    Args:
        suite_id: The UUID of the suite to load.

    Returns:
        The deserialized Suite object.

    Raises:
        HTTPException: 404 if no suite with that ID exists on disk.
    """
    path = _suite_path(suite_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Suite '{suite_id}' not found")
    return Suite(**json.loads(path.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# Suite CRUD endpoints
# ---------------------------------------------------------------------------

@router.get("/suites", response_model=List[SuiteSummary])
def list_suites():
    """List all saved test suites.

    Returns:
        List of SuiteSummary objects with id, name, base_url, request_count.
    """
    suites = []
    for path in sorted(SUITES_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            suites.append(SuiteSummary(
                id=data["id"],
                name=data["name"],
                base_url=data["base_url"],
                request_count=len(data.get("requests", [])),
            ))
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return suites


@router.post("/suites", response_model=Suite, status_code=201)
def create_suite(body: SuiteCreate):
    """Create a new test suite.

    Args:
        body: Suite payload with name, base_url, and optional requests list.

    Returns:
        The newly created Suite with its assigned UUID.
    """
    suite_id = str(uuid.uuid4())
    suite = Suite(id=suite_id, **body.model_dump())
    _suite_path(suite_id).write_text(
        json.dumps(suite.model_dump(), indent=2), encoding="utf-8"
    )
    return suite


@router.get("/suites/{suite_id}", response_model=Suite)
def get_suite(suite_id: str):
    """Load a test suite by ID.

    Args:
        suite_id: The UUID of the suite to retrieve.

    Returns:
        The full Suite object.

    Raises:
        HTTPException: 404 if the suite does not exist.
    """
    return _load_suite(suite_id)


@router.put("/suites/{suite_id}", response_model=Suite)
def update_suite(suite_id: str, body: SuiteCreate):
    """Replace a test suite's content.

    Args:
        suite_id: The UUID of the suite to update.
        body: New suite payload.

    Returns:
        The updated Suite object.

    Raises:
        HTTPException: 404 if the suite does not exist.
    """
    _load_suite(suite_id)  # raises 404 if missing
    suite = Suite(id=suite_id, **body.model_dump())
    _suite_path(suite_id).write_text(
        json.dumps(suite.model_dump(), indent=2), encoding="utf-8"
    )
    return suite


@router.delete("/suites/{suite_id}", status_code=204)
def delete_suite(suite_id: str):
    """Delete a test suite.

    Args:
        suite_id: The UUID of the suite to delete.

    Returns:
        Empty 204 response on success.

    Raises:
        HTTPException: 404 if the suite does not exist.
    """
    _load_suite(suite_id)  # raises 404 if missing
    _suite_path(suite_id).unlink()
    return Response(status_code=204)
