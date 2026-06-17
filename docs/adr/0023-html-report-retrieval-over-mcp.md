# ADR 0023 — HTML report retrieval over MCP

- Status: Accepted
- Date: 2026-06-17
- Sprint: 23 (S23-1, [#443](https://github.com/buddy-k23/valdo/issues/443))
- Decision: **Make rendered HTML reports reachable over MCP via a new `report://<run_id>` MCP resource served through `resources/read` (mirroring the existing `templates://` / `taxonomy://` resources). The report-producing tools gain an opt-in `include_report: bool = False` parameter; when set, the tool renders the HTML into the shared reports dir under a run-id-named file and adds three response fields — `report_uri` (the `report://<run_id>` the agent reads), `report_url` (the existing `/reports/<file>` HTTP/UI path), and `report_path` (the absolute server path). The resource read resolves the run-id to a file with a traversal-safe resolver modelled on `_safe_upload_path`, serves ONLY from the reports dir, runs behind the same `MCPAuthMiddleware` as every other MCP call, and inherits the renderers' existing `suppress_pii` redaction.**
- Related:
  [ADR 0018](0018-mcp-tool-surface.md) (the MCP tool/resource surface this extends),
  [ADR 0021](0021-mcp-background-jobs.md) (the run-id / run-registry lifecycle the report id reuses),
  [ADR 0022](0022-adapter-agnostic-db-integration.md) (the adapter-agnostic db-compare / reconcile tools that plug into this contract in S23-2/3),
  `src/mcp/server.py` (resource + tool registration),
  `src/mcp/action_tools.py` (`validate_file` — the `output=None` gap),
  `src/mcp/compare_tools.py`, `src/mcp/reconcile_tools.py`, `src/mcp/db_compare_tools.py`, `src/mcp/reconcile_all_tools.py`,
  `src/api/routers/files.py` (the REST `report_url` precedent + `_safe_upload_path`),
  `src/reports/renderers/validation_renderer.py`, `src/reports/renderers/comparison_renderer.py`,
  `src/api/main.py` (the `/reports` static mount + `FILE_RETENTION_HOURS` cleanup).

## Context

Valdo renders rich, self-contained HTML dashboards for validation and
comparison results: `ValidationReporter.generate(...)` writes an HTML report
plus CSV sidecars (`src/reports/renderers/validation_renderer.py:35-58`) and
`HTMLReporter.generate(...)` renders a comparison report from a Jinja template
(`src/reports/renderers/comparison_renderer.py:8-58`). Today those reports are
reachable two ways: the CLI (`valdo validate --output report.html`) and the
REST API. The REST validate endpoint writes the HTML and returns a
`report_url` (`src/api/routers/files.py:279-301`); the REST compare endpoint
renders via `HTMLReporter().generate(...)` and returns a `report_url`
(`src/api/routers/files.py:437-439`).

The MCP surface has no equivalent. Every report-producing MCP tool returns
**structured JSON only** — there is no path by which an MCP client (an LLM
agent) can obtain the rendered HTML to hand to a human. This ADR decides HOW an
MCP client/agent retrieves an HTML report, and defines the retrieval contract
that the S23-2/3/4 stories build their per-tool wiring against.

## Current state — JSON-only over MCP (evidence)

- **`validate_file`** runs the validation service with **`output=None`**
  (`src/mcp/action_tools.py:510-516`), so the HTML renderer is never invoked;
  the run record only ever carries canonicalised JSON violations
  (`action_tools.py:525-528`). The tool returns `{run_id, started_at}`
  (`action_tools.py:577-579`) and results are paged out as JSON violations.

- **`compare_two_files`** returns `{comparison_id, summary, top_differences}`
  with the `comparison_id` explicitly **ephemeral and never persisted**
  (`src/mcp/compare_tools.py:51-56`, `:535-539`). No HTML is produced and the
  `top_differences` slice is capped at 10 to protect the response budget
  (`compare_tools.py:85-89`).

- **`reconcile_mapping`** returns the structured field-level verdict dict only
  (`src/mcp/reconcile_tools.py:52-57`). `db_compare` and `reconcile_all` are
  registered as thin JSON-returning adapters (`src/mcp/server.py:682-737`).

- **Resources already serve content over MCP.** `server.py` registers
  `taxonomy://violations` / `taxonomy://rules`
  (`src/mcp/server.py:321-351`), the `templates://etl/{shape}` family
  (`:376-423`), and `formats://supported` (`:436-454`) — each via
  `@mcp_server.resource(...)` returning a body string through
  `resources/read`. `templates://etl/{shape}` in particular returns a
  **raw text body verbatim** with a `{shape}` URI placeholder bound by a
  single handler (`server.py:392-406`) — this is the exact precedent a
  `report://<run_id>` resource mirrors.

- **Where REST writes reports.** The REST endpoints write into `UPLOADS_DIR`
  (`uploads/`) and return `/uploads/<file>` (`files.py:279-300`, `:437-439`),
  even though `src/api/main.py:314-316` mounts a dedicated **`/reports`** static
  dir (`_REPORTS_DIR = .../reports`). Uploads are cleaned on startup by
  `cleanup_old_files(_UPLOADS_DIR, FILE_RETENTION_HOURS)` where
  `FILE_RETENTION_HOURS` defaults to 24 (`src/api/main.py:38`, `:66-73`); the
  archive util has a separate `REPORT_RETENTION_DAYS` (default 365) purge
  (`src/utils/archive.py:164-180`).

- **Traversal-safe resolution precedent.** `_safe_upload_path` strips
  directory components via `Path(name).name`, rejects empty / `.` / `..` /
  NUL-byte names, resolves the candidate, and verifies containment with
  `relative_to(uploads_root)` (`src/api/routers/files.py:91-132`). This is the
  pattern the report-id resolver reuses.

- **Auth.** Every MCP request — tools AND resource reads — flows through
  `MCPAuthMiddleware`, mounted on the MCP sub-app only
  (`src/mcp/server.py:1104-1108`; auth posture documented at `server.py:40-50`).
  A `report://` resource read therefore inherits MCP auth for free.

- **PII.** Both renderers already redact: `ValidationReporter.generate` takes
  `suppress_pii: bool = True` (`validation_renderer.py:35-50`) and the REST
  validate path defaults `suppress_pii=True` (`files.py:222`, `:289`), matching
  the CLI `--suppress-pii` default.

## Decision

### 1. Retrieval mechanism — a `report://<run_id>` MCP resource (primary)

A new MCP resource is registered in `src/mcp/server.py` mirroring the
`templates://etl/{shape}` handler:

```
report://{run_id}        →  mime_type="text/html"  →  returns the rendered HTML body
```

The handler resolves `run_id` to the on-disk report file (see §4), reads it,
and returns the HTML body verbatim through `resources/read`. The report's
existence is advertised to the agent by the **tool response** (§2): the tool
that produced it returns the `report_uri` string, which the agent then passes
to `resources/read`. (The `report://` template URI is bound by one handler, so
no per-run registration is needed — same model as `templates://etl/{shape}`.)

- **Picked: the MCP resource** because the agentic use case is "an LLM client
  hands a report to a human." `resources/read` is the MCP-native way to deliver
  a content body to a client; it goes through the same transport and the same
  `MCPAuthMiddleware` as the tool call, requires no second (HTTP) credential,
  and works for stdio / Streamable-HTTP clients that have **no** route to the
  FastAPI `/reports` mount. It directly reuses the proven `templates://` /
  `taxonomy://` resource machinery.

- **Also returned (fallback / dual-surface): the REST `report_url`.** The tool
  ALSO returns the existing `report_url` (`/reports/<file>`) and an absolute
  `report_path`. This is not a separate mechanism so much as a courtesy for the
  HTTP-reachable consumer: a browser-driving agent or the Web UI can open the
  URL directly, and an operator can find the file on disk. The resource is the
  primary contract; the URL/path are advisory.

- **Rejected (a) report_url-only:** would force a stdio MCP client through a
  second, separately-authenticated HTTP hop it may not be able to make, and
  breaks the "everything an agent needs comes back over MCP" property.

- **Rejected (c) inline HTML in the tool JSON response:** a full validation
  dashboard with inlined Chart.js (`validation_renderer.py:10-17`) is hundreds
  of KB — it blows the MCP response budget the same way the 10-row
  `top_differences` cap (`compare_tools.py:85-89`) exists to protect. The
  resource read is a separate, on-demand fetch, so the lean JSON tool response
  stays small.

### 2. Tool contract — HTML is opt-in

HTML generation is **opt-in per call**, not always-on. The default MCP response
stays lean JSON (consistent with the existing budget-conscious design). Each
report-producing tool gains:

- **New parameter:** `include_report: bool = False`. When `False` (default) the
  tool behaves exactly as today and no HTML is rendered (so `validate_file`
  keeps `output=None`). When `True`, the tool drives the matching renderer into
  the reports dir.

- **New response fields** (present only when `include_report=True` and rendering
  succeeded):
  - `report_uri`: `"report://<run_id>"` — the MCP resource the agent reads.
  - `report_url`: `"/reports/<file>.html"` — the HTTP/UI path (REST parity).
  - `report_path`: absolute server filesystem path to the report.

  When `include_report=False` these fields are absent (not `null`-padded), so
  the existing JSON contract is unchanged for current callers.

Opt-in is chosen over always-generate because (a) rendering + CSV sidecars is
real work that most agentic calls (status polling, violation paging) do not
need, and (b) it keeps the report lifecycle (§5) bounded — we only write a file
when a human-facing report was actually requested.

### 3. Which tools

The report-producing set, with the renderer each plugs into:

| Tool | Renderer | Status |
|---|---|---|
| `validate_file` | `ValidationReporter` (`validation_renderer.py`) | renderer exists |
| `compare_two_files` | `HTMLReporter` (`comparison_renderer.py`) | renderer exists |
| `db_compare` | comparison-style renderer | **renderer to be built (S23-2)** |
| `reconcile_mapping` | reconcile renderer | **renderer to be built (S23-3)** |
| `reconcile_all` | reconcile/summary renderer | **renderer to be built (S23-3)** |

`validate_file` and `compare_two_files` have working HTML renderers today and
are the reference implementations of this contract (S23-4). `db_compare`,
`reconcile_mapping`, and `reconcile_all` return structured JSON but have **no
HTML renderer yet** — S23-2 (db-compare HTML) and S23-3 (reconcile HTML) build
those renderers; once built they plug into the identical `include_report` /
`report_uri` contract this ADR defines, with no further server-side wiring
changes beyond registering each tool's render call.

### 4. Security

- **Traversal-safe id → file resolution.** A shared helper (e.g.
  `src/mcp/resources/reports.py::resolve_report_path(run_id)`) resolves a
  report id to a file using the `_safe_upload_path` pattern
  (`files.py:91-132`): take `Path(run_id).name` only, reject empty / `.` /
  `..` / NUL-byte ids, build `<reports_root>/<run_id>.html`, `.resolve()` it,
  and assert `candidate.relative_to(reports_root)`. A run-id that escapes the
  reports dir is rejected (not served). Run ids are server-minted UUIDs
  (`action_tools.py` mints `run_id`; compare mints `uuid.uuid4().hex`,
  `compare_tools.py:536`), so legitimate ids are already path-safe; the guard is
  defence-in-depth against a malicious / malformed `report://` read.

- **Serve only from the reports dir.** The resource handler reads exclusively
  from `_REPORTS_DIR` (`src/api/main.py:314-316`) — never `uploads/`, never an
  arbitrary path. A missing file yields a clean "report not found / expired"
  error, not a stack trace or a directory probe.

- **Auth.** The `report://` resource read is served by the MCP transport, which
  is wrapped by `MCPAuthMiddleware` on the sub-app (`server.py:1104-1108`). It
  therefore requires the same LDAPS cookie / `X-API-Key` / bearer token as every
  other MCP call — no new auth surface, no separate credential.

- **PII.** No PII beyond what the renderers already redact. The render call
  passes `suppress_pii=True` by default (matching `validation_renderer.py:39`
  and the REST default `files.py:222`); when a caller needs raw values it must
  opt out exactly as the CLI `--suppress-pii` / REST `suppress_pii=False` paths
  allow. The CSV sidecars `ValidationReporter` writes
  (`validation_renderer.py:56-58`) are governed by the same flag.

### 5. Lifecycle

- **Where written.** Reports are written into the dedicated reports dir
  (`_REPORTS_DIR`, `src/api/main.py:314-315`) — NOT `uploads/`. This corrects
  the REST endpoints' current habit of writing HTML into `uploads/` and
  returning `/uploads/...` (`files.py:300`, `:439`): the MCP path standardises
  on the `/reports` mount the app already exposes, so `report_url` is
  `/reports/<file>` and aligns with the static mount.

- **Naming.** Reports are named by run id: `<run_id>.html` for `validate_file`
  (the run id already lives in the run registry, ADR 0021), and by the tool's
  minted id for the compare / reconcile tools (`<comparison_id>.html`,
  `<reconcile_id>.html`). The `report://<run_id>` resolver and the on-disk name
  are thus the same id, so resolution is a direct lookup.

- **Cleanup.** Report retention ties to the existing retention machinery. The
  startup cleanup currently sweeps `uploads/` with `FILE_RETENTION_HOURS`
  (default 24, `main.py:38`, `:66-73`); the archive util already has a
  `REPORT_RETENTION_DAYS` purge for run directories (default 365,
  `archive.py:164-180`). S23-4 extends the startup sweep to also clean
  `_REPORTS_DIR` on the report retention window (reusing `cleanup_old_files`),
  so a `report://<run_id>` read of an expired report returns a clean
  "expired / not found" result. An expired report is a normal terminal state,
  not an error condition the agent must special-case beyond surfacing it.

## Implementation notes (what S23-2/3/4 build against this contract)

- **S23-4 (this contract's reference wiring):** add `include_report: bool = False`
  to `validate_file` and `compare_two_files` (params in `server.py` tool
  signatures + the `*_payload` functions); when set, render via
  `ValidationReporter` / `HTMLReporter` into `_REPORTS_DIR/<id>.html`; add
  `report_uri` / `report_url` / `report_path` to the responses. Register the
  `report://{run_id}` resource in `server.py` (mirroring
  `_etl_template_resource`, `server.py:392-406`) backed by
  `resolve_report_path` + a file read. Add `resolve_report_path` (the
  `_safe_upload_path`-style guard) and the reports-dir cleanup extension.
- **S23-2 (db-compare HTML):** build the db-compare HTML renderer; wire
  `db_compare`'s `include_report` to it; reuse the §2 response fields and the
  `report://` resource unchanged.
- **S23-3 (reconcile HTML):** build the reconcile / reconcile-all HTML renderer;
  wire `reconcile_mapping` and `reconcile_all` the same way.
- All renderer calls pass `suppress_pii=True` by default; expose the opt-out
  only where the existing CLI/REST surface already does.

## Consequences

### Positive

- An MCP agent can now hand a human a full Valdo HTML dashboard without leaving
  the MCP transport — closes the JSON-only gap.
- Reuses three proven primitives wholesale: the `templates://`/`taxonomy://`
  resource machinery, the `_safe_upload_path` traversal guard, and
  `MCPAuthMiddleware` — minimal new attack surface.
- Default behaviour is unchanged: opt-in means existing JSON-only callers see
  no contract change and pay no rendering cost.
- Standardises report storage on the `/reports` mount, fixing the REST
  endpoints' inconsistent `uploads/`-based report writing as a side benefit.

### Negative / risks

- Two retrieval surfaces (`report://` resource + `report_url`) mean two things
  to keep in sync; mitigated by minting both from one render call with one id.
- Reports persist on disk until the retention sweep; an opt-in HTML report with
  `suppress_pii=False` is a PII-at-rest artifact in the reports dir — operators
  must treat `_REPORTS_DIR` with the same care as `uploads/`.
- `db_compare` / `reconcile_*` cannot honour `include_report=True` until their
  renderers exist (S23-2/3); until then those tools should reject
  `include_report=True` with a clear "HTML report not yet available for this
  tool" tool error rather than silently ignoring the flag.
- Reports dir cleanup is a new responsibility added to the startup sweep; if the
  retention window is mis-tuned an agent may read a freshly-expired report and
  get a not-found.

### Follow-ups

- S23-2 — db-compare HTML renderer + tool wiring.
- S23-3 — reconcile / reconcile-all HTML renderer + tool wiring.
- S23-4 — `validate_file` / `compare_two_files` `include_report` wiring, the
  `report://{run_id}` resource, `resolve_report_path`, and reports-dir cleanup.
- Consider migrating the REST validate/compare endpoints off `uploads/` onto
  `_REPORTS_DIR` so REST and MCP return identically-rooted `report_url`s
  (tracked separately; out of scope for Sprint 23).
