# API Tester Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an API Tester tab to the Valdo UI with a backend proxy so users can fire HTTP requests against any REST API, inspect responses, and run automated test suites with assertions.

**Architecture:** All outbound HTTP calls are proxied through `POST /api/v1/api-tester/proxy` (httpx async). Suites are stored as JSON files under `config/api-tester/suites/`. The UI is plain JS added to `ui.html` — no new framework or CDN.

**Tech Stack:** FastAPI, httpx (already in requirements.txt), Pydantic v2, plain JS/HTML/CSS in ui.html.

---

## Context for the implementer

- Repo root: `/Users/buddy/claude-code/automations/valdo`
- The app is a FastAPI server. Entry point: `src/api/main.py`
- Routers live in `src/api/routers/`. Register new router in `main.py` like the others.
- Models live in `src/api/models/`. Follow the same `BaseModel` pattern.
- UI is a single file: `src/reports/static/ui.html`. It has 3 tabs: quick, runs, mapping.
  - Tab buttons are in `<nav class="tabs">` with `id="tab-{name}"` and `onclick="switchTab('{name}')"`
  - Panels are `<div id="panel-{name}" class="panel">` inside `<main>`
  - `switchTab(name)` iterates `['quick','runs','mapping']` — you must add `'tester'` to this list
- Tests: `tests/unit/` for unit, `tests/integration/` for integration. Use `TestClient(app)`.
- Coverage target ≥ 80%. Run: `pytest tests/unit/ --ignore=tests/unit/test_contracts_pipeline.py --ignore=tests/unit/test_pipeline_runner.py --ignore=tests/unit/test_workflow_wrapper_parity.py -q`
- `httpx` is already in `requirements.txt` (≥0.28.0). Do NOT add it again.
- Design doc: `docs/plans/2026-03-02-api-tester-design.md`
- GitHub issues: #42 (backend), #43 (UI request builder), #44 (UI suite runner)
- **XSS safety rule:** Never use `innerHTML` with user-supplied data. Use `textContent` or `createElement` + `textContent`. `innerHTML` is only acceptable for static markup or the JSON highlighter (which explicitly escapes `<` and `>`).

---

## Task 1: Backend — Pydantic models + proxy endpoint + suite CRUD

**Files:**
- Create: `src/api/models/api_tester.py`
- Create: `src/api/routers/api_tester.py`
- Modify: `src/api/main.py` (register router)
- Create: `tests/unit/test_api_tester.py`

---

### Step 1: Write failing tests

Create `tests/unit/test_api_tester.py`:

```python
"""Unit tests for /api/v1/api-tester/* endpoints."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app, raise_server_exceptions=False)


class TestProxy:
    def test_proxy_get_success(self):
        """Proxy forwards GET and returns status/body/elapsed."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.text = '{"status": "ok"}'

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(return_value=mock_resp)

        with patch("src.api.routers.api_tester.httpx.AsyncClient", return_value=mock_client):
            resp = client.post(
                "/api/v1/api-tester/proxy",
                data={"config": json.dumps({"method": "GET", "url": "http://example.com/api"})},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status_code"] == 200
        assert data["body"] == '{"status": "ok"}'
        assert data["elapsed_ms"] >= 0

    def test_proxy_connection_error_returns_502(self):
        """Connection failure returns 502."""
        import httpx as _httpx

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(side_effect=_httpx.ConnectError("refused"))

        with patch("src.api.routers.api_tester.httpx.AsyncClient", return_value=mock_client):
            resp = client.post(
                "/api/v1/api-tester/proxy",
                data={"config": json.dumps({"method": "GET", "url": "http://unreachable"})},
            )

        assert resp.status_code == 502

    def test_proxy_timeout_returns_504(self):
        """Timeout returns 504."""
        import httpx as _httpx

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(side_effect=_httpx.TimeoutException("timeout"))

        with patch("src.api.routers.api_tester.httpx.AsyncClient", return_value=mock_client):
            resp = client.post(
                "/api/v1/api-tester/proxy",
                data={"config": json.dumps({"method": "GET", "url": "http://slow"})},
            )

        assert resp.status_code == 504

    def test_proxy_missing_config_returns_422(self):
        """Missing config form field returns 422."""
        resp = client.post("/api/v1/api-tester/proxy", data={})
        assert resp.status_code == 422


class TestSuiteCRUD:
    def test_list_suites_returns_list(self):
        resp = client.get("/api/v1/api-tester/suites")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_create_and_get_suite(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.api.routers.api_tester.SUITES_DIR", tmp_path)
        payload = {"name": "Test Suite", "base_url": "http://localhost", "requests": []}
        resp = client.post("/api/v1/api-tester/suites", json=payload)
        assert resp.status_code == 201
        suite_id = resp.json()["id"]

        resp2 = client.get(f"/api/v1/api-tester/suites/{suite_id}")
        assert resp2.status_code == 200
        assert resp2.json()["name"] == "Test Suite"

    def test_update_suite(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.api.routers.api_tester.SUITES_DIR", tmp_path)
        payload = {"name": "Original", "base_url": "http://localhost", "requests": []}
        suite_id = client.post("/api/v1/api-tester/suites", json=payload).json()["id"]

        update = {"name": "Updated", "base_url": "http://localhost", "requests": []}
        resp = client.put(f"/api/v1/api-tester/suites/{suite_id}", json=update)
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated"

    def test_delete_suite(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.api.routers.api_tester.SUITES_DIR", tmp_path)
        payload = {"name": "ToDelete", "base_url": "http://localhost", "requests": []}
        suite_id = client.post("/api/v1/api-tester/suites", json=payload).json()["id"]

        resp = client.delete(f"/api/v1/api-tester/suites/{suite_id}")
        assert resp.status_code == 204

        resp2 = client.get(f"/api/v1/api-tester/suites/{suite_id}")
        assert resp2.status_code == 404

    def test_get_nonexistent_suite_returns_404(self):
        resp = client.get("/api/v1/api-tester/suites/nonexistent-id")
        assert resp.status_code == 404
```

### Step 2: Run tests — verify they all fail

```bash
pytest tests/unit/test_api_tester.py -v 2>&1 | tail -20
```
Expected: all fail with 404 or ImportError (router not registered yet).

### Step 3: Create `src/api/models/api_tester.py`

```python
"""Pydantic models for the API Tester feature."""
from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel


class HeaderPair(BaseModel):
    """A single HTTP header key-value pair."""
    key: str
    value: str


class FormField(BaseModel):
    """A form data field; is_file=True means the value is a file upload."""
    key: str
    value: str = ""
    is_file: bool = False


class Assertion(BaseModel):
    """A single test assertion on a proxy response."""
    field: str        # "status_code" | "$.jsonPath"
    operator: str     # "equals" | "contains" | "exists"
    expected: str = ""


class SuiteRequest(BaseModel):
    """One HTTP request inside a test suite."""
    id: str
    name: str
    method: str
    path: str
    headers: list[HeaderPair] = []
    body_type: str = "none"   # none | json | form
    body_json: str = ""
    form_fields: list[FormField] = []
    assertions: list[Assertion] = []


class SuiteCreate(BaseModel):
    """Payload for creating or updating a suite."""
    name: str
    base_url: str
    requests: list[SuiteRequest] = []


class Suite(SuiteCreate):
    """A stored test suite with its assigned id."""
    id: str


class SuiteSummary(BaseModel):
    """Lightweight suite info for the list endpoint."""
    id: str
    name: str
    base_url: str
    request_count: int


class ProxyResponse(BaseModel):
    """Result returned by the proxy endpoint."""
    status_code: int
    headers: dict[str, str]
    body: str
    elapsed_ms: float
    error: Optional[str] = None
```

### Step 4: Create `src/api/routers/api_tester.py`

```python
"""API Tester — proxy endpoint and suite CRUD."""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import List

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile

from src.api.models.api_tester import (
    ProxyResponse,
    Suite,
    SuiteCreate,
    SuiteSummary,
)

router = APIRouter(prefix="/api/v1/api-tester", tags=["API Tester"])

SUITES_DIR = Path("config/api-tester/suites")
SUITES_DIR.mkdir(parents=True, exist_ok=True)


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
        HTTPException 422: config is not valid JSON.
        HTTPException 502: target host unreachable.
        HTTPException 504: request timed out.
    """
    try:
        cfg = json.loads(config)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"config is not valid JSON: {exc}")

    method = cfg.get("method", "GET").upper()
    url = cfg.get("url", "")
    timeout = int(cfg.get("timeout", 30))
    body_type = cfg.get("body_type", "none")

    # Build headers dict, strip Content-Type so httpx sets it correctly per body type
    fwd_headers: dict[str, str] = {
        h["key"]: h["value"]
        for h in cfg.get("headers", [])
        if h.get("key") and h["key"].lower() != "content-type"
    }

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
    return SUITES_DIR / f"{suite_id}.json"


def _load_suite(suite_id: str) -> Suite:
    path = _suite_path(suite_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Suite '{suite_id}' not found")
    return Suite(**json.loads(path.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# Suite CRUD endpoints
# ---------------------------------------------------------------------------

@router.get("/suites", response_model=List[SuiteSummary])
def list_suites():
    """List all saved test suites."""
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
        except Exception:
            continue
    return suites


@router.post("/suites", response_model=Suite, status_code=201)
def create_suite(body: SuiteCreate):
    """Create a new test suite."""
    suite_id = str(uuid.uuid4())
    suite = Suite(id=suite_id, **body.model_dump())
    _suite_path(suite_id).write_text(
        json.dumps(suite.model_dump(), indent=2), encoding="utf-8"
    )
    return suite


@router.get("/suites/{suite_id}", response_model=Suite)
def get_suite(suite_id: str):
    """Load a test suite by ID."""
    return _load_suite(suite_id)


@router.put("/suites/{suite_id}", response_model=Suite)
def update_suite(suite_id: str, body: SuiteCreate):
    """Replace a test suite's content."""
    _load_suite(suite_id)  # raises 404 if missing
    suite = Suite(id=suite_id, **body.model_dump())
    _suite_path(suite_id).write_text(
        json.dumps(suite.model_dump(), indent=2), encoding="utf-8"
    )
    return suite


@router.delete("/suites/{suite_id}", status_code=204)
def delete_suite(suite_id: str):
    """Delete a test suite."""
    _load_suite(suite_id)  # raises 404 if missing
    _suite_path(suite_id).unlink()
    return Response(status_code=204)
```

### Step 5: Register router in `src/api/main.py`

Add import after the existing router imports:
```python
from src.api.routers.api_tester import router as api_tester_router
```

Add after the other `app.include_router(...)` calls:
```python
app.include_router(api_tester_router)
```

### Step 6: Run tests — verify they all pass

```bash
pytest tests/unit/test_api_tester.py -v
```
Expected: 9 tests PASS.

### Step 7: Check coverage

```bash
pytest tests/unit/ \
  --ignore=tests/unit/test_contracts_pipeline.py \
  --ignore=tests/unit/test_pipeline_runner.py \
  --ignore=tests/unit/test_workflow_wrapper_parity.py \
  --cov=src --cov-report=term-missing -q 2>&1 | tail -5
```
Expected: ≥ 80% coverage, 0 failures.

### Step 8: Commit

```bash
git add src/api/models/api_tester.py src/api/routers/api_tester.py src/api/main.py tests/unit/test_api_tester.py
git commit -m "feat(api): add API Tester proxy endpoint and suite CRUD (#42)"
```

---

## Task 2: UI — Request Builder + Response Viewer

**Files:**
- Modify: `src/reports/static/ui.html`

**XSS note:** All user-supplied data (URL, headers, body, suite names, paths) must be set via `textContent` or `value`, never via `innerHTML`. The only exception is the JSON syntax highlighter which explicitly escapes `<` and `>` before inserting HTML.

---

### Step 1: Add CSS for the API Tester panel

Inside the `<style>` block (before the closing `</style>`), append:

```css
  /* ── API Tester ─────────────────────────────────────────── */
  .at-layout {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
  }
  @media (max-width: 700px) { .at-layout { grid-template-columns: 1fr; } }

  .at-section { margin-bottom: 12px; }
  .at-section-title {
    font-size: 11px; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.6px; color: var(--muted); margin-bottom: 6px;
    cursor: pointer; user-select: none;
  }
  .at-section-title::before { content: '▾ '; }
  .at-section-title.collapsed::before { content: '▸ '; }
  .at-section-body.hidden { display: none; }

  .at-url-row { display: flex; gap: 6px; margin-bottom: 10px; align-items: center; }
  .at-method { width: 110px; flex-shrink: 0; }
  .at-url-input { flex: 1; min-width: 0; }
  .at-path-row { display: flex; gap: 6px; margin-bottom: 10px; }
  .at-path-input { flex: 1; }

  .at-kv-row { display: flex; gap: 6px; margin-bottom: 4px; align-items: center; }
  .at-kv-key { flex: 1; }
  .at-kv-val { flex: 2; }
  .at-kv-remove {
    background: none; border: none; color: var(--muted);
    cursor: pointer; font-size: 16px; padding: 0 4px;
  }
  .at-kv-remove:hover { color: var(--fail); }
  .at-add-row {
    background: none; border: 1px dashed var(--border); border-radius: var(--radius);
    color: var(--muted); cursor: pointer; font-size: 12px;
    padding: 4px 10px; margin-top: 4px; width: 100%;
  }
  .at-add-row:hover { color: var(--text); border-color: var(--accent2); }

  .at-body-toggle { display: flex; gap: 8px; margin-bottom: 8px; }
  .at-body-toggle button {
    background: none; border: 1px solid var(--border); border-radius: var(--radius);
    color: var(--muted); cursor: pointer; font-size: 12px; padding: 3px 10px;
  }
  .at-body-toggle button.active {
    background: var(--accent2); border-color: var(--accent2); color: var(--text);
  }

  .at-textarea {
    width: 100%; background: var(--bg); border: 1px solid var(--border);
    border-radius: var(--radius); color: var(--text); font-family: monospace;
    font-size: 12px; min-height: 80px; padding: 8px; resize: vertical;
  }

  .at-send-row { display: flex; gap: 8px; align-items: center; margin-top: 12px; }
  .at-name-input { flex: 1; }
  .at-suite-sel  { flex: 1; }

  .at-resp-header { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; }
  .at-status-badge {
    border-radius: 4px; font-size: 12px; font-weight: 700; padding: 2px 10px;
  }
  .at-status-2xx { background: var(--pass);    color: #fff; }
  .at-status-3xx { background: var(--partial); color: #fff; }
  .at-status-4xx, .at-status-5xx { background: var(--fail); color: #fff; }
  .at-elapsed { font-size: 11px; color: var(--muted); }

  .at-resp-tabs { display: flex; border-bottom: 1px solid var(--border); margin-bottom: 8px; }
  .at-resp-tabs button {
    background: none; border: none; border-bottom: 2px solid transparent;
    color: var(--muted); cursor: pointer; font-size: 12px; padding: 6px 14px;
  }
  .at-resp-tabs button.active { color: var(--text); border-bottom-color: #4a9eff; }

  .at-resp-body {
    background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius);
    font-family: monospace; font-size: 12px; max-height: 320px;
    overflow: auto; padding: 10px; white-space: pre-wrap; word-break: break-all;
  }
  .at-resp-placeholder { color: var(--muted); font-style: italic; }

  /* JSON highlight — only used with sanitized (< > escaped) content */
  .jh-key  { color: #79b8ff; }
  .jh-str  { color: #9ecbff; }
  .jh-num  { color: #f8c555; }
  .jh-bool { color: #f97583; }
  .jh-null { color: #f97583; }

  .at-runner { margin-top: 20px; border-top: 1px solid var(--border); padding-top: 16px; }
  .at-runner h3 { font-size: 13px; font-weight: 600; color: #aac4ff; margin-bottom: 10px; }
  .at-runner-row { display: flex; gap: 8px; align-items: center; margin-bottom: 10px; }
  .at-req-list { margin-bottom: 10px; }
  .at-req-row {
    display: flex; gap: 8px; align-items: center;
    background: var(--bg); border: 1px solid var(--border);
    border-radius: var(--radius); margin-bottom: 4px; padding: 6px 10px;
    font-size: 12px;
  }
  .at-req-method { font-weight: 700; min-width: 52px; color: #4a9eff; }
  .at-req-name   { flex: 1; }
  .at-req-path   { color: var(--muted); flex: 2; font-family: monospace; font-size: 11px; }
  .at-assertion-result { font-size: 11px; margin-left: 8px; }
  .at-assertion-pass { color: var(--pass); }
  .at-assertion-fail { color: var(--fail); }
  .at-runner-summary {
    background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius);
    font-size: 13px; font-weight: 600; padding: 8px 14px; margin-top: 8px;
  }
```

### Step 2: Add tab button

In `<nav class="tabs">`, after the Mapping Generator button, add:

```html
  <button id="tab-tester" role="tab" aria-selected="false"
          onclick="switchTab('tester')">API Tester</button>
```

### Step 3: Add the panel HTML

After the closing `</div>` of `panel-mapping` (before `</main>`), add:

```html
  <!-- API TESTER PANEL -->
  <div id="panel-tester" class="panel" role="tabpanel" aria-labelledby="tab-tester" style="display:none">
    <h2>API Tester</h2>
    <div class="at-layout">

      <!-- LEFT: Request Builder -->
      <div>
        <div class="at-url-row">
          <select id="atMethod" class="at-method">
            <option>GET</option><option>POST</option><option>PUT</option>
            <option>PATCH</option><option>DELETE</option>
          </select>
          <input id="atBaseUrl" class="text-input at-url-input" type="text"
                 placeholder="http://127.0.0.1:8000">
        </div>
        <div class="at-path-row">
          <input id="atPath" class="text-input at-path-input" type="text"
                 placeholder="/api/v1/system/health">
        </div>

        <div class="at-section">
          <div class="at-section-title" onclick="atToggleSection(this)">Headers</div>
          <div class="at-section-body" id="atHeadersBody">
            <div id="atHeaderRows"></div>
            <button class="at-add-row" onclick="atAddHeader()">+ Add Header</button>
          </div>
        </div>

        <div class="at-section">
          <div class="at-section-title" onclick="atToggleSection(this)">Body</div>
          <div class="at-section-body" id="atBodyBody">
            <div class="at-body-toggle">
              <button id="atBodyNone" class="active" onclick="atSetBodyType('none')">None</button>
              <button id="atBodyJson"                onclick="atSetBodyType('json')">JSON</button>
              <button id="atBodyForm"                onclick="atSetBodyType('form')">Form Data</button>
            </div>
            <div id="atBodyJsonArea" style="display:none">
              <textarea id="atBodyJsonText" class="at-textarea" placeholder='{"key": "value"}'></textarea>
            </div>
            <div id="atBodyFormArea" style="display:none">
              <div id="atFormRows"></div>
              <button class="at-add-row" onclick="atAddFormField(false)">+ Add Field</button>
              <button class="at-add-row" onclick="atAddFormField(true)" style="margin-top:4px">+ Add File</button>
            </div>
          </div>
        </div>

        <div class="at-section">
          <div class="at-section-title" onclick="atToggleSection(this)">Assertions</div>
          <div class="at-section-body" id="atAssertionsBody">
            <div id="atAssertionRows"></div>
            <button class="at-add-row" onclick="atAddAssertion()">+ Add Assertion</button>
          </div>
        </div>

        <div class="at-send-row">
          <button class="btn btn-primary" onclick="atSend()">Send</button>
          <input id="atReqName" class="text-input at-name-input" type="text"
                 placeholder="Request name (for saving)">
          <select id="atSuiteSel" class="at-suite-sel">
            <option value="">— save to suite —</option>
          </select>
          <button class="btn btn-secondary" onclick="atSaveRequest()">Save</button>
        </div>
      </div>

      <!-- RIGHT: Response Viewer -->
      <div>
        <div class="at-resp-header" id="atRespHeader" style="display:none">
          <span class="at-status-badge" id="atStatusBadge"></span>
          <span class="at-elapsed"      id="atElapsed"></span>
        </div>
        <div class="at-resp-tabs">
          <button class="active" onclick="atRespTab('Body',    this)">Body</button>
          <button                onclick="atRespTab('Headers', this)">Headers</button>
          <button                onclick="atRespTab('Raw',     this)">Raw</button>
        </div>
        <div class="at-resp-body" id="atRespBody">
          <span class="at-resp-placeholder">Send a request to see the response.</span>
        </div>
        <div class="at-resp-body" id="atRespHeaders" style="display:none"></div>
        <div class="at-resp-body" id="atRespRaw"     style="display:none"></div>
      </div>

    </div><!-- /.at-layout -->

    <!-- Suite Runner -->
    <div class="at-runner">
      <h3>Suite Runner</h3>
      <div class="at-runner-row">
        <select id="atRunnerSuiteSel" style="flex:1" onchange="atLoadSuiteIntoRunner()">
          <option value="">— select a suite —</option>
        </select>
        <button class="btn btn-primary"   onclick="atRunSuite()">Run Suite</button>
        <button class="btn btn-secondary" onclick="atNewSuite()">New Suite</button>
      </div>
      <div class="at-req-list"      id="atRunnerReqList"></div>
      <div class="at-runner-summary" id="atRunnerSummary" style="display:none"></div>
    </div>

  </div><!-- /#panel-tester -->
```

### Step 4: Update `switchTab` to include 'tester'

Find:
```js
  ['quick', 'runs', 'mapping'].forEach(function(t) {
```
Change to:
```js
  ['quick', 'runs', 'mapping', 'tester'].forEach(function(t) {
```

### Step 5: Add API Tester JS

Append the following JS block inside `<script>`, after all existing JS, before `</script>`.

**Critical XSS rules this code follows:**
- User data is always set via `.textContent` or `.value`, never `innerHTML`
- `innerHTML` is used ONLY in `atHighlightJson` which first calls `.replace(/</g,'&lt;').replace(/>/g,'&gt;')` on all matched tokens — making the output safe

```javascript
// ===========================================================================
// API TESTER
// ===========================================================================
var _atBodyType  = 'none';
var _atRespData  = null;
var _atSuites    = [];

// ── Section collapse ──────────────────────────────────────────────────────────
function atToggleSection(titleEl) {
  titleEl.classList.toggle('collapsed');
  titleEl.nextElementSibling.classList.toggle('hidden');
}

// ── Body type toggle ──────────────────────────────────────────────────────────
function atSetBodyType(type) {
  _atBodyType = type;
  ['none','json','form'].forEach(function(t) {
    var id = 'atBody' + t.charAt(0).toUpperCase() + t.slice(1);
    document.getElementById(id).classList.toggle('active', t === type);
  });
  document.getElementById('atBodyJsonArea').style.display = (type === 'json') ? '' : 'none';
  document.getElementById('atBodyFormArea').style.display = (type === 'form') ? '' : 'none';
}

// ── Header rows (DOM-only, no innerHTML with user data) ───────────────────────
function atMakeKvRow(keyVal, valVal, isFile) {
  var row = document.createElement('div');
  row.className = 'at-kv-row';

  var keyInput = document.createElement('input');
  keyInput.className = 'text-input at-kv-key';
  keyInput.placeholder = 'Key';
  keyInput.value = keyVal || '';

  var rmBtn = document.createElement('button');
  rmBtn.className = 'at-kv-remove';
  rmBtn.title = 'Remove';
  rmBtn.textContent = '\u00d7';
  rmBtn.addEventListener('click', function() { row.remove(); });

  if (isFile) {
    row.dataset.isFile = '1';
    var fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.style.cssText = 'flex:2;font-size:12px';
    row.appendChild(keyInput);
    row.appendChild(fileInput);
  } else {
    var valInput = document.createElement('input');
    valInput.className = 'text-input at-kv-val';
    valInput.placeholder = isFile ? '' : 'Value';
    valInput.value = valVal || '';
    row.appendChild(keyInput);
    row.appendChild(valInput);
  }
  row.appendChild(rmBtn);
  return row;
}

function atAddHeader(key, val) {
  document.getElementById('atHeaderRows').appendChild(atMakeKvRow(key, val, false));
}

function atGetHeaders() {
  var rows = document.getElementById('atHeaderRows').querySelectorAll('.at-kv-row');
  var result = [];
  rows.forEach(function(row) {
    var inputs = row.querySelectorAll('input');
    var k = inputs[0].value.trim();
    if (k) result.push({key: k, value: inputs[1] ? inputs[1].value.trim() : ''});
  });
  return result;
}

function atAddFormField(isFile, key, val) {
  document.getElementById('atFormRows').appendChild(atMakeKvRow(key, val, isFile));
}

function atGetFormFields() {
  var rows = document.getElementById('atFormRows').querySelectorAll('.at-kv-row');
  var fields = []; var files = [];
  rows.forEach(function(row) {
    var keyEl = row.querySelector('.at-kv-key');
    var k = keyEl ? keyEl.value.trim() : '';
    if (!k) return;
    if (row.dataset.isFile === '1') {
      var fileEl = row.querySelector('input[type=file]');
      if (fileEl && fileEl.files[0]) {
        fields.push({key: k, value: '', is_file: true});
        files.push(fileEl.files[0]);
      }
    } else {
      var valEl = row.querySelector('.at-kv-val');
      fields.push({key: k, value: valEl ? valEl.value : '', is_file: false});
    }
  });
  return {fields: fields, files: files};
}

// ── Assertion rows (DOM-only) ─────────────────────────────────────────────────
function atAddAssertion(field, op, expected) {
  var row = document.createElement('div');
  row.className = 'at-kv-row';

  var fieldIn = document.createElement('input');
  fieldIn.className = 'text-input';
  fieldIn.style.flex = '2';
  fieldIn.placeholder = 'status_code or $.field';
  fieldIn.value = field || '';

  var opSel = document.createElement('select');
  opSel.className = 'text-input';
  opSel.style.flex = '1';
  ['equals','contains','exists'].forEach(function(o) {
    var opt = document.createElement('option');
    opt.value = o;
    opt.textContent = o;
    if (o === op) opt.selected = true;
    opSel.appendChild(opt);
  });

  var expIn = document.createElement('input');
  expIn.className = 'text-input at-kv-val';
  expIn.style.flex = '2';
  expIn.placeholder = 'expected';
  expIn.value = expected || '';

  var rmBtn = document.createElement('button');
  rmBtn.className = 'at-kv-remove';
  rmBtn.textContent = '\u00d7';
  rmBtn.addEventListener('click', function() { row.remove(); });

  row.appendChild(fieldIn);
  row.appendChild(opSel);
  row.appendChild(expIn);
  row.appendChild(rmBtn);
  document.getElementById('atAssertionRows').appendChild(row);
}

function atGetAssertions() {
  var rows = document.getElementById('atAssertionRows').querySelectorAll('.at-kv-row');
  var result = [];
  rows.forEach(function(row) {
    var inputs = row.querySelectorAll('input, select');
    var f = inputs[0].value.trim();
    if (f) result.push({field: f, operator: inputs[1].value, expected: inputs[2].value.trim()});
  });
  return result;
}

// ── Send ──────────────────────────────────────────────────────────────────────
async function atSend() {
  var method  = document.getElementById('atMethod').value;
  var baseUrl = document.getElementById('atBaseUrl').value.trim().replace(/\/$/, '');
  var path    = document.getElementById('atPath').value.trim();
  var url     = baseUrl + path;

  var cfg = {
    method: method, url: url,
    headers: atGetHeaders(),
    body_type: _atBodyType,
    body_json: document.getElementById('atBodyJsonText').value,
    form_fields: [],
    timeout: 30,
  };

  var fd = new FormData();
  if (_atBodyType === 'form') {
    var formData = atGetFormFields();
    cfg.form_fields = formData.fields;
    formData.files.forEach(function(f) { fd.append('uploaded_files', f); });
  }
  fd.append('config', JSON.stringify(cfg));

  var respBody = document.getElementById('atRespBody');
  respBody.textContent = 'Sending\u2026';
  document.getElementById('atRespHeader').style.display = 'none';

  try {
    var resp = await fetch('/api/v1/api-tester/proxy', {method: 'POST', body: fd});
    var data = await resp.json();
    if (!resp.ok) {
      respBody.textContent = 'Proxy error: ' + JSON.stringify(data.detail || resp.statusText);
      return;
    }
    _atRespData = data;
    atRenderResponse(data);
  } catch (err) {
    respBody.textContent = 'Error: ' + err.message;
  }
}

// ── Response render ───────────────────────────────────────────────────────────
function atRenderResponse(data) {
  var code  = data.status_code;
  var badge = document.getElementById('atStatusBadge');
  badge.textContent = String(code);
  badge.className = 'at-status-badge at-status-' +
    (code < 300 ? '2xx' : code < 400 ? '3xx' : code < 500 ? '4xx' : '5xx');
  document.getElementById('atElapsed').textContent = data.elapsed_ms.toFixed(1) + ' ms';
  document.getElementById('atRespHeader').style.display = '';

  // Body tab — innerHTML only after JSON highlighter escapes all < >
  var bodyEl = document.getElementById('atRespBody');
  try {
    var parsed = JSON.parse(data.body);
    bodyEl.innerHTML = atHighlightJson(JSON.stringify(parsed, null, 2));
  } catch (_) {
    bodyEl.textContent = data.body;
  }

  // Headers tab — textContent only
  var headersEl = document.getElementById('atRespHeaders');
  headersEl.textContent = Object.entries(data.headers)
    .map(function(kv) { return kv[0] + ': ' + kv[1]; }).join('\n');

  // Raw tab — textContent only
  document.getElementById('atRespRaw').textContent = data.body;
}

function atRespTab(name, btn) {
  ['Body','Headers','Raw'].forEach(function(t) {
    document.getElementById('atResp' + t).style.display = (t === name) ? '' : 'none';
  });
  btn.parentNode.querySelectorAll('button').forEach(function(b) { b.classList.remove('active'); });
  btn.classList.add('active');
}

// JSON syntax highlighter — safe: escapes < and > before setting innerHTML
function atHighlightJson(str) {
  return str.replace(
    /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g,
    function(match) {
      var safe = match.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      var cls  = 'jh-num';
      if (/^"/.test(match))          { cls = /:$/.test(match) ? 'jh-key' : 'jh-str'; }
      else if (/true|false/.test(match)) { cls = 'jh-bool'; }
      else if (/null/.test(match))   { cls = 'jh-null'; }
      return '<span class="' + cls + '">' + safe + '</span>';
    }
  );
}

// ── Suite selector ────────────────────────────────────────────────────────────
async function atLoadSuites() {
  try {
    var resp = await fetch('/api/v1/api-tester/suites');
    _atSuites = await resp.json();
    ['atSuiteSel','atRunnerSuiteSel'].forEach(function(selId) {
      var sel = document.getElementById(selId);
      while (sel.options.length > 1) sel.remove(1);
      _atSuites.forEach(function(s) {
        var opt = document.createElement('option');
        opt.value = s.id;
        opt.textContent = s.name + ' (' + s.request_count + ')';
        sel.appendChild(opt);
      });
    });
  } catch (_) {}
}

// ── Save request into a suite ─────────────────────────────────────────────────
async function atSaveRequest() {
  var suiteId = document.getElementById('atSuiteSel').value;
  var name    = document.getElementById('atReqName').value.trim() || 'Unnamed';
  if (!suiteId) { alert('Select a suite first, or create one with New Suite.'); return; }

  var req = {
    id:         (crypto.randomUUID ? crypto.randomUUID() : String(Date.now())),
    name:       name,
    method:     document.getElementById('atMethod').value,
    path:       document.getElementById('atBaseUrl').value.trim().replace(/\/$/, '') +
                document.getElementById('atPath').value.trim(),
    headers:    atGetHeaders(),
    body_type:  _atBodyType,
    body_json:  document.getElementById('atBodyJsonText').value,
    form_fields: _atBodyType === 'form' ? atGetFormFields().fields : [],
    assertions: atGetAssertions(),
  };

  var getResp  = await fetch('/api/v1/api-tester/suites/' + suiteId);
  var suite    = await getResp.json();
  suite.requests.push(req);
  await fetch('/api/v1/api-tester/suites/' + suiteId, {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(suite),
  });
  await atLoadSuites();
  alert('Saved \u201c' + name + '\u201d to suite.');
}

// ── New suite ─────────────────────────────────────────────────────────────────
async function atNewSuite() {
  var name = prompt('Suite name:');
  if (!name) return;
  var base = document.getElementById('atBaseUrl').value.trim() || 'http://127.0.0.1:8000';
  await fetch('/api/v1/api-tester/suites', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: name, base_url: base, requests: []}),
  });
  await atLoadSuites();
}

// ── Suite Runner ──────────────────────────────────────────────────────────────
var _atCurrentSuite = null;

async function atLoadSuiteIntoRunner() {
  var suiteId = document.getElementById('atRunnerSuiteSel').value;
  document.getElementById('atRunnerReqList').textContent = '';
  document.getElementById('atRunnerSummary').style.display = 'none';
  _atCurrentSuite = null;
  if (!suiteId) return;
  var resp = await fetch('/api/v1/api-tester/suites/' + suiteId);
  _atCurrentSuite = await resp.json();
  atRenderRunnerList(_atCurrentSuite.requests, []);
}

function atRenderRunnerList(requests, results) {
  var list = document.getElementById('atRunnerReqList');
  list.textContent = '';
  requests.forEach(function(req, idx) {
    var row = document.createElement('div');
    row.className = 'at-req-row';

    var methodSpan = document.createElement('span');
    methodSpan.className = 'at-req-method';
    methodSpan.textContent = req.method;

    var nameSpan = document.createElement('span');
    nameSpan.className = 'at-req-name';
    nameSpan.textContent = req.name;

    var pathSpan = document.createElement('span');
    pathSpan.className = 'at-req-path';
    pathSpan.textContent = req.path;

    row.appendChild(methodSpan);
    row.appendChild(nameSpan);
    row.appendChild(pathSpan);

    var result = results[idx];
    if (result) {
      result.assertions.forEach(function(a) {
        var span = document.createElement('span');
        span.className = 'at-assertion-result ' + (a.pass ? 'at-assertion-pass' : 'at-assertion-fail');
        span.textContent = (a.pass ? '\u2713 ' : '\u2717 ') + a.field + ' ' + a.operator +
          (a.operator !== 'exists' ? ' ' + a.expected : '');
        row.appendChild(span);
      });
    }
    list.appendChild(row);
  });
}

async function atRunSuite() {
  if (!_atCurrentSuite) { alert('Select a suite first.'); return; }
  var requests = _atCurrentSuite.requests;
  if (!requests.length) { alert('Suite has no requests.'); return; }

  var results = [];
  var totalPass = 0, totalFail = 0;
  var t0 = Date.now();

  for (var i = 0; i < requests.length; i++) {
    var req = requests[i];
    var url = (req.path.startsWith('http') ? '' : _atCurrentSuite.base_url) + req.path;

    var cfg = {
      method: req.method, url: url,
      headers: req.headers || [],
      body_type: req.body_type || 'none',
      body_json: req.body_json || '',
      form_fields: req.form_fields || [],
      timeout: 30,
    };
    var fd = new FormData();
    fd.append('config', JSON.stringify(cfg));

    var proxyResp = {status_code: 0, body: '', headers: {}, elapsed_ms: 0};
    try {
      var resp = await fetch('/api/v1/api-tester/proxy', {method: 'POST', body: fd});
      proxyResp = await resp.json();
    } catch (_) {}

    var assertResults = (req.assertions || []).map(function(a) {
      var pass = atEvaluateAssertion(a, proxyResp);
      if (pass) totalPass++; else totalFail++;
      return {field: a.field, operator: a.operator, expected: a.expected, pass: pass};
    });
    results.push({assertions: assertResults});
    atRenderRunnerList(requests, results);
  }

  var elapsed = Date.now() - t0;
  var summary = document.getElementById('atRunnerSummary');
  summary.style.display = '';
  summary.textContent = totalPass + ' passed  /  ' + totalFail + ' failed  /  ' +
    (totalPass + totalFail) + ' assertions  /  ' + elapsed + ' ms total';
  summary.style.color = totalFail > 0 ? 'var(--fail)' : 'var(--pass)';
}

function atEvaluateAssertion(assertion, proxyResp) {
  var field = assertion.field, operator = assertion.operator, expected = String(assertion.expected);
  var actual;
  if (field === 'status_code') {
    actual = String(proxyResp.status_code);
  } else if (field.startsWith('$.')) {
    try {
      var data = JSON.parse(proxyResp.body);
      actual = atJsonPath(data, field.slice(2));
    } catch (_) { return false; }
  } else { return false; }

  if (operator === 'exists')   return actual !== undefined && actual !== null;
  if (operator === 'equals')   return String(actual) === expected;
  if (operator === 'contains') return String(actual).includes(expected);
  return false;
}

function atJsonPath(obj, path) {
  return path.split('.').reduce(function(cur, part) {
    if (cur === undefined || cur === null) return undefined;
    var m = part.match(/^(\w+)\[(\d+)\]$/);
    return m ? (cur[m[1]] && cur[m[1]][parseInt(m[2])]) : cur[part];
  }, obj);
}

// Load suites when tab is opened
document.getElementById('tab-tester').addEventListener('click', atLoadSuites);
```

### Step 6: Verify in browser

```bash
pkill -f "uvicorn src.api.main" 2>/dev/null; sleep 1
python3 -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000 &
sleep 2
```

Open `http://127.0.0.1:8000/ui`, click **API Tester**:
- Set GET / `http://127.0.0.1:8000` / `/api/v1/system/health` → Send → green `200` badge, highlighted JSON
- Create suite "Smoke", add assertions, Run Suite → ✓ green pass indicators

### Step 7: Run all tests

```bash
pytest tests/unit/ \
  --ignore=tests/unit/test_contracts_pipeline.py \
  --ignore=tests/unit/test_pipeline_runner.py \
  --ignore=tests/unit/test_workflow_wrapper_parity.py \
  --cov=src --cov-report=term-missing -q 2>&1 | tail -5
```
Expected: ≥ 80%, 0 failures.

### Step 8: Commit and push

```bash
git add src/reports/static/ui.html
git commit -m "feat(ui): add API Tester tab — request builder, response viewer, suite runner (#43 #44)"
git push origin main
```
