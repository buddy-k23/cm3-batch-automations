# API Tester Feature — Design Document

**Date:** 2026-03-02

---

## Goal

Add an **API Tester tab** to the Valdo UI that lets users fire HTTP requests against any REST API, inspect responses, and run automated test suites with assertions — all without leaving the app.

## Architecture

All requests are proxied through a new FastAPI backend endpoint so there are no browser CORS restrictions and any external API can be reached.

### New backend routes

```
POST   /api/v1/api-tester/proxy           — execute a single proxied HTTP request
GET    /api/v1/api-tester/suites          — list saved suites
POST   /api/v1/api-tester/suites          — create a suite
GET    /api/v1/api-tester/suites/{id}     — load a suite
PUT    /api/v1/api-tester/suites/{id}     — update a suite
DELETE /api/v1/api-tester/suites/{id}     — delete a suite
```

### New files

| File | Purpose |
|------|---------|
| `src/api/routers/api_tester.py` | Proxy endpoint + suite CRUD |
| `src/api/models/api_tester.py` | Pydantic request/response models |
| `config/api-tester/suites/` | Suite JSON files (one file per suite) |

### Proxy implementation

Uses `httpx` (async) to make the outbound call server-side. Forwards method, URL, headers, and body. Returns status code, response headers, body, and elapsed time in milliseconds.

### Suite storage

Each suite is a JSON file named `{uuid}.json` under `config/api-tester/suites/`. Loaded and saved via the CRUD endpoints above.

---

## UI Layout

A fourth tab **"API Tester"** is added to the existing nav bar.

### Left panel — Request Builder

- **Base URL** text input (e.g. `http://127.0.0.1:8000`)
- **Method** dropdown: GET / POST / PUT / PATCH / DELETE
- **Path** text input (e.g. `/api/v1/system/health`)
- Collapsible sections:
  - **Headers** — key/value row editor with Add / Remove rows
  - **Body** — toggle: JSON (textarea) or Form Data (key/value rows + file upload per row)
  - **Assertions** — list of assertions (field, operator, expected value) used in suite runs
- **Send** button — fires a single request through the proxy
- **Request name** input + **Save to Suite** dropdown — saves the current request into a named suite

### Right panel — Response

- Status badge (green for 2xx, red for 4xx/5xx) + elapsed time in ms
- Three sub-tabs:
  - **Body** — pretty-printed, syntax-highlighted JSON (falls back to raw text for non-JSON)
  - **Headers** — response headers as a key/value table
  - **Raw** — unformatted response body

### Bottom panel — Suite Runner

- Suite selector dropdown (loads from server)
- Editable request list (name, method, path) — reorderable
- **Run Suite** button — executes all requests sequentially; shows inline pass/fail per assertion
- Summary bar: `X passed / Y failed / total Xms`

---

## Data Model

### Suite JSON schema

```json
{
  "id": "uuid",
  "name": "Valdo Smoke Tests",
  "base_url": "http://127.0.0.1:8000",
  "requests": [
    {
      "id": "uuid",
      "name": "Health check",
      "method": "GET",
      "path": "/api/v1/system/health",
      "headers": [
        { "key": "Accept", "value": "application/json" }
      ],
      "body_type": "none",
      "body_json": "",
      "form_fields": [],
      "assertions": [
        { "field": "status_code", "operator": "equals",   "expected": "200"     },
        { "field": "$.status",    "operator": "equals",   "expected": "healthy" }
      ]
    }
  ]
}
```

### Assertion operators

| Operator | Meaning |
|----------|---------|
| `equals` | Exact match |
| `contains` | String contains substring, or array contains element |
| `exists` | Field is present and non-null |

### JSONPath field syntax

- `status_code` — special keyword for HTTP status code
- `$.fieldName` — top-level response body field
- `$.data[0].id` — nested / array path

---

## Error Handling

| Scenario | Proxy response |
|----------|---------------|
| Target unreachable / connection refused | 502 `{"error": "connection_failed", "detail": "..."}` |
| Request timeout (default 30s) | 504 `{"error": "timeout"}` |
| Invalid / malformed URL | 422 validation error |
| Target returns non-JSON | Proxy returns body as plain text with correct status |

Suite runner behaviour: a failed assertion does **not** stop the run. All requests execute; the full pass/fail summary is shown at the end.

---

## Tech Stack

- **Backend proxy:** `httpx` (async HTTP client, already common in FastAPI projects)
- **Frontend:** plain JS added to `ui.html` — no new framework
- **JSON syntax highlighting:** small inline renderer (no external CDN dependency, consistent with existing Chart.js inline approach)

---

## Out of Scope (future issues)

- Authentication helpers (OAuth2 flow, AWS Sig4)
- Environment variables / variable interpolation in URLs (`{{base_url}}`)
- Export to curl / OpenAPI
- Response time graphing over multiple runs
