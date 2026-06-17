# MCP Server — Operations Reference

Valdo ships a Model Context Protocol (MCP) server at `/mcp/` that lets
AI agents (Claude Desktop, VSCode, GitLab Duo, etc.) drive Valdo's
validation, comparison, and onboarding surface via JSON-RPC over HTTP
Streamable or stdio transports.

This document covers **operational concerns** — auth, persistence, table
shape, and dev-mode bypass. For the **client-side setup** (how to wire
VSCode / GitLab Duo / Claude Desktop to a running server) see
[`docs/MCP_CLIENTS.md`](MCP_CLIENTS.md) and the per-client guides under
[`docs/mcp-clients/`](mcp-clients/). For the agent-facing tool catalogue
see the `tools/list` response or
[`docs/USAGE_AND_OPERATIONS_GUIDE.md`](USAGE_AND_OPERATIONS_GUIDE.md).

---

## Tools

The MCP server exposes fourteen tools:

- `list_sources`, `get_source_spec`, `list_recent_runs` — read-only
  (EF-S2)
- `validate_file`, `get_run_status`, `get_violations` — action
  (EF-S4)
- `infer_mapping_from_sample`, `upload_workbook_as_spec`,
  `onboard_source_dry_run` — onboarding (EF-S5)
- `compare_two_files` — ad-hoc file diff (S7-4)
- `reconcile_mapping` — adapter-agnostic mapping-vs-table reconcile (#407)
- `db_compare` — adapter-agnostic DB-extract-vs-file compare (S21-1)
- `reconcile_all` — adapter-agnostic bulk mapping reconcile + baseline drift (S21-2)
- `mask_file` — PII masking of a batch file with 6 strategies (S21-3)

### Tool: `reconcile_mapping` (#407)

Reconciles a Valdo mapping's declared fields against an actual database
table on whichever backend `DB_ADAPTER` selects (`sqlite` | `postgresql` |
`oracle`), per ADR 0022. It wraps the shared service seam
[`src/services/reconcile_service.py::reconcile_mapping_service`](../src/services/reconcile_service.py)
— the same code path the `valdo reconcile` CLI and the `POST /api/v2/reconcile`
REST endpoint call.

**Input parameters**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `mapping` | string | yes | Mapping path or bare mapping id (under `config/mappings/`). |
| `table` | string | no | Target table override; defaults to the mapping's `target.table_name`. |
| `schema` | string | no | Schema / owner, prepended to the table. |

**Verdict shape** — `status` (`clean` \| `advisories` \| `mismatch` \| `error`),
`valid`, `summary` counts, and the field-level `errors` (missing table /
required column), `mismatches` (genuine type conflicts), and `advisories`
(non-blocking notes, e.g. a boolean stored as integer on a backend with no
native boolean) lists. A type conflict is reported in the verdict — it does
NOT raise a tool error. Tool errors are reserved for caller-fixable problems
(mapping not found, no resolvable table, bad adapter name).

Each tool's input schema and description are surfaced via the standard
MCP `tools/list` discovery call.

### Tool: `db_compare` (S21-1)

Extracts rows from a database table or SQL query on whichever backend
`DB_ADAPTER` selects (`sqlite` | `postgresql` | `oracle`), writes them to a
temp file, and diffs that against an actual batch file. It wraps the existing
db-compare service
[`src/services/db_file_compare_service.py::compare_db_to_file`](../src/services/db_file_compare_service.py)
— the same code path the `valdo db-compare` CLI and the
`POST /api/v1/files/db-compare` REST endpoint call.

**Input parameters**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `mapping` | string | yes | Path to a mapping JSON whose `fields` list names the columns. |
| `actual_file` | string | yes | Path to the actual batch file to compare the DB extract against. |
| `table` | string | one-of | Bare table name to extract from. Mutually exclusive with `query`. |
| `query` | string | one-of | A SQL `SELECT` statement to extract with. Mutually exclusive with `table`. |
| `key_columns` | list[string] | no | Column name(s) used as join keys; omit for row-by-row comparison. |
| `db_adapter` | string | no | Adapter override (`sqlite` \| `postgresql` \| `oracle`); defaults to env `DB_ADAPTER`. |
| `connection` | object | no | Per-request connection values (`db_host`, `db_user`, `db_password`, `db_schema`, `db_path`). Never echoed in the response. |

**Verdict shape** — `workflow` (`status` `passed` \| `failed`,
`db_rows_extracted`, `query_or_table`) and `compare` (`structure_compatible`,
`matching_rows`, `only_in_file1` / `only_in_file2`, `differences`, row counts).
A genuine comparison difference is reported in the verdict (`status=failed`) —
it does NOT raise a tool error. Tool errors are reserved for caller-fixable
problems (mapping or actual file not found, neither/both of `table`/`query`
supplied, or a bad adapter name). Connection credentials are never included in
the tool response.

### Tool: `reconcile_all` (S21-2)

Bulk-reconciles every Valdo mapping in a directory against a live database on
whichever backend `DB_ADAPTER` selects (`sqlite` | `postgresql` | `oracle`),
per ADR 0022, and aggregates a summary — adding a baseline drift-diff when a
prior report is supplied. It wraps the shared bulk-reconcile service seam
[`src/services/reconcile_all_service.py::reconcile_all_service`](../src/services/reconcile_all_service.py)
— the same code path the `valdo reconcile-all` CLI calls.

**Input parameters**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `mappings_dir` | string | no | Directory of mapping JSON files. Defaults to `config/mappings`. |
| `pattern` | string | no | Glob for mapping files within `mappings_dir`. Defaults to `*.json`. |
| `baseline` | string | no | Path to a prior reconcile-all JSON report; enables the `drift` block. |
| `db_adapter` | string | no | Adapter override (`sqlite` \| `postgresql` \| `oracle`); defaults to env `DB_ADAPTER`. |

**Verdict shape** — `total_mappings`, `valid_mappings`, `invalid_mappings`,
`total_errors`, `total_warnings`, and a per-mapping `results` list (each entry
carrying the field-level reconcile verdict). When `baseline` is supplied, a
`drift` block reports `added_files`, `removed_files`, `changed`, `new_errors`,
and `new_warnings`. A single mapping that fails to process is recorded as an
invalid `results` entry — it does NOT raise a tool error. Tool errors are
reserved for caller-fixable problems (a bad adapter name or an unreadable
baseline report). No connection credentials are echoed in the response.

### Tool: `mask_file` (S21-3)

Masks the PII in a batch file (fixed-width or pipe-delimited) and writes a
masked copy. It wraps the existing masking service
[`src/services/masking_service.py::MaskingService.mask_file`](../src/services/masking_service.py)
— the same code path the `valdo mask` CLI command drives. The original input
file is never modified. Six strategies are supported: `preserve`,
`preserve_format`, `deterministic_hash`, `random_range`, `redact`, `fake_name`.

**Input parameters**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `file` | string | yes | Path to the input batch file. Never modified. |
| `mapping` | string | yes | Path to the mapping JSON; its `fields` list names the columns and (for fixed-width) their positions/lengths. |
| `masking_config` | string | yes | Path to the masking-rules JSON; its `fields` object maps each field name to a rule carrying a `strategy`. Unmapped fields are preserved. |
| `output` | string | yes | Destination path for the masked output file. Parent directories are created if needed. |

**Response shape** — `output_path` (where the masked file was written),
`records_masked` (count), and `field_strategies` (a per-field list of
`{field, strategy}` — strategy and field NAMES only).

**PII safety (critical)** — the response NEVER contains raw field values:
neither the unmasked originals nor the masked replacements. To inspect the
masked rows, read the output file directly. Tool errors are reserved for
caller-fixable problems: a missing/blank argument, a mapping or masking-config
file that cannot be found or parsed, an input file that does not exist, an
unsupported mapping format, or an unrecognised masking strategy.

### Tool: `compare_two_files` (S7-4)

Ad-hoc row-by-row comparison of two files by a declared set of key
columns. The tool wraps
[`src/comparators/file_comparator.py::FileComparator`](../src/comparators/file_comparator.py)
directly so an agent can diff two arbitrary files without first
registering them as a Valdo source.

**Input parameters**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `left_path` | string | yes | Path to the left-hand file. Extension drives parsing. |
| `right_path` | string | yes | Path to the right-hand file. Extension drives parsing. |
| `key_columns` | list[string] | yes | Non-empty list of column names to join on. Every entry must exist in both files' headers. |
| `mapping_path` | string | no | Path to a mapping JSON. Required when either file is `.txt` (fixed-width). |

**Auto-detection by extension**

| Extension | Behaviour |
|-----------|-----------|
| `.csv` | Comma-separated, header row required. Parsed with `pandas.read_csv(sep=",")`. |
| `.tsv` | Tab-separated, header row required. Parsed with `pandas.read_csv(sep="\t")`. |
| `.txt` | Fixed-width. `mapping_path` is REQUIRED — the mapping's `fields` array provides field names + lengths. |

The `.csv` branch deliberately bypasses
[`FormatDetector`](../src/parsers/format_detector.py) because of a known
routing bug (tracked as S6-2): the detector still funnels `.csv` files
through `PipeDelimitedParser` with a hardcoded `sep="|"`. When S6-2
ships, the workaround in `compare_tools.py` can be retired.

**Response shape**

```json
{
  "comparison_id": "ab12cd34...",
  "summary": {
    "matched": 4,
    "differing": 1,
    "only_in_left": 0,
    "only_in_right": 0
  },
  "top_differences": [
    {
      "keys": {"id": "3"},
      "differences": {
        "balance": {"left": "300.00", "right": "350.00", "type": "value_difference"}
      },
      "difference_count": 1
    }
  ]
}
```

- `comparison_id` is **ephemeral** — minted per call (UUID hex), never
  persisted. The S7-4 story explicitly placed run history out of scope
  for ad-hoc compares; clients that need durable history should use
  `validate_file` + `get_run_status` instead.
- `top_differences` is capped at 10 entries (`TOP_DIFFERENCES_LIMIT` in
  [`src/mcp/compare_tools.py`](../src/mcp/compare_tools.py)) to bound the
  response budget. Larger diffs are still reported via the summary
  counts; agents needing the full diff should use the CLI / API surface.

**Deviation from the original issue spec**

The S7-4 issue draft includes a `severity_filter` parameter. The
underlying `FileComparator` does not classify per-row severity — it
returns a flat list of row-level differences with field-level
before/after values but no severity bucket. Rather than ship a parameter
that silently never matches anything, `severity_filter` was dropped from
the public tool signature. This is documented in
[`src/mcp/compare_tools.py`](../src/mcp/compare_tools.py)'s module
docstring.

**Errors**

All failures surface as MCP `ToolError`:

- Missing `left_path` / `right_path`
- Unsupported extension
- `.txt` without `mapping_path`
- Empty / non-list `key_columns`
- Key column missing from either file's header
- Malformed mapping JSON (no `fields`, missing `name`/`length`)
- pandas / `FixedWidthParser` parse failures

---

## Resources

The MCP server exposes the following resource URIs, discoverable via the
standard `resources/list` call:

### `taxonomy://violations` and `taxonomy://rules` (EF-S3)

Live introspection of the engine's violation kinds (`taxonomy://violations`)
and rule check names (`taxonomy://rules`). Each entry has `{name,
description}` (violations) or `{name, category, description}` (rules).
See [`src/mcp/taxonomy.py`](../src/mcp/taxonomy.py) for the
introspection sources.

### `templates://etl/*` (S7-2)

Three resources backed by [`src/mcp/resources/etl_templates.py`](../src/mcp/resources/etl_templates.py)
let agents browse the committed ETL templates under
[`templates/etl/`](../templates/etl/) without scraping the filesystem.
The shape list is **auto-discovered on every read** — no template name
is hardcoded in the server.

| URI                                             | MIME              | Returns                                                                                          |
|-------------------------------------------------|-------------------|--------------------------------------------------------------------------------------------------|
| `templates://etl/list`                          | `application/json`| List of `{shape, description}` entries — one per discovered template.                            |
| `templates://etl/<shape>`                       | `text/yaml`       | The raw YAML body of one template, verbatim (comments and `<FILL_IN_*>` placeholders preserved). |
| `templates://etl/<shape>/sample`                | `application/json`| Manifest of the paired `<shape>_sample/` directory — file paths, sizes, 200-char text previews.  |

**Description sourcing.** For each discovered template the one-line
description is resolved in this order: (1) the top-level
`description:` field in the YAML (the canonical source; modelled by
`SourceConfig`), (2) the first prose paragraph after the H1 in the
paired `<shape>_README.md`, (3) the literal placeholder
`(no description available)` so a missing description is visible
rather than silently omitted.

**Failure handling.** A template whose YAML fails to validate through
`src.pipeline.etl_config.SourceConfig.model_validate()` is omitted
from `templates://etl/list` (and a `WARNING` is logged) so an agent
never picks up a half-broken shape.

#### Example — list templates

Request (JSON-RPC):

```json
{"jsonrpc": "2.0", "id": 1, "method": "resources/read",
 "params": {"uri": "templates://etl/list"}}
```

Response body (abbreviated):

```json
[
  {"shape": "csv_file_comparison",
   "description": "Brief one-line description of the comparison"},
  {"shape": "db_to_file_reconciliation",
   "description": "Brief one-line description of what this reconciliation proves"},
  {"shape": "fixed_width_single_record",
   "description": "Brief one-line description of the file being validated"},
  {"shape": "json_single_record",
   "description": "Brief one-line description of the NDJSON file being validated"},
  {"shape": "xml_single_record",
   "description": "Brief one-line description of the XML file being validated"}
]
```

> The `json_single_record` shape (ADR 0018) validates newline-delimited JSON;
> its mapping locates fields by JSONPath (`$.customer.id`) and an array path
> `$.transactions[*]` becomes a `<field>_count` column. The `xml_single_record`
> shape (ADR 0019) validates repeated-record XML with a **hardened** parser
> (rejects DOCTYPE / XXE); fields are located by XPath (`customer/@id` reads an
> attribute, `customer/name` reads element text) and a repeated child becomes a
> `<field>_count` column. Both are auto-discovered — dropping the template into
> `templates/etl/` required no MCP code change.

#### Example — fetch one template body

Request: `resources/read` with `uri = templates://etl/csv_file_comparison`.

Response: the verbatim text of
[`templates/etl/csv_file_comparison.yml`](../templates/etl/csv_file_comparison.yml)
as a single `TextResourceContents.text` field. The agent can paste
this straight into a working spec.

#### Example — fetch a sample manifest

Request: `resources/read` with `uri = templates://etl/csv_file_comparison/sample`.

Response body:

```json
{
  "sample_dir": "templates/etl/csv_file_comparison_sample",
  "files": [
    {"path": "expected_report.json", "size_bytes": 1834, "preview": "{\n  \"total_rows_file1\": 5, ..."},
    {"path": "left.csv",             "size_bytes":  291, "preview": "CUSTOMER_ID,NAME,EMAIL,..."},
    {"path": "mapping.json",         "size_bytes":  812, "preview": "{\n  \"mapping_name\": \"csv_..."},
    {"path": "right.csv",            "size_bytes":  293, "preview": "CUSTOMER_ID,NAME,EMAIL,..."}
  ]
}
```

The manifest is intentionally **not** a tarball — large fixtures
(Excel workbooks, multi-MB CSVs) are listed but not inlined, keeping
the response budget bounded. Binary files have `"preview": null`.

### `formats://supported` (S7-3)

A single static-URI resource backed by
[`src/mcp/resources/formats.py`](../src/mcp/resources/formats.py) that
enumerates every input format Valdo's engine can validate today (each
with its sprint of introduction and, when one exists, a pointer to
the matching `templates://etl/<shape>` template) plus the formats
tracked by open ADR issues.

Source-of-truth is two module-level constants — `SUPPORTED_TODAY` and
`PLANNED` — at the top of `src/mcp/resources/formats.py`. Adding a
new format when an ADR closes is a one-line edit there; the resource
handler does no I/O at request time and needs no other change.

**Response shape:**

```json
{
  "supported_today": [
    {"format": "fixed_width_single", "since": "Sprint 1",
     "template": "templates://etl/fixed_width_single_record"},
    {"format": "fixed_width_multi_record", "since": "Sprint 2",
     "template": null},
    {"format": "csv", "since": "Sprint 1",
     "template": "templates://etl/csv_file_comparison"},
    {"format": "pipe_delimited", "since": "Sprint 1",
     "template": null},
    {"format": "db_to_file", "since": "Sprint 3",
     "template": "templates://etl/db_to_file_reconciliation"}
  ],
  "planned": [
    {"format": "json",
     "issue": "https://github.com/buddy-k23/valdo/issues/377"},
    {"format": "xml",
     "issue": "https://github.com/buddy-k23/valdo/issues/378"},
    {"format": "db_to_db",
     "issue": "https://github.com/buddy-k23/valdo/issues/379"}
  ]
}
```

**`template: null` semantics.** A `null` template means the engine
supports the format (so an agent CAN ask for a validation run) but no
public `templates/etl/` shape covers it yet — the agent should fall
back to the inline mapping/rules workflow or to
`infer_mapping_from_sample`. `pipe_delimited` is the canonical
worked example: the parser shipped in Sprint 1, but no template
exists for it.

**Cross-checked at test time.** The integration test
`tests/integration/test_mcp_formats_resource.py` asserts that every
non-null `template` URI resolves through `templates://etl/list` and
that every `planned.issue` URL matches the
`https://github.com/buddy-k23/valdo/issues/<digits>` shape — a
placeholder or a typo'd issue number cannot ship without breaking CI.

---

## Prompts

The MCP server exposes four workflow prompts via the standard
`prompts/list` discovery call. Each prompt is a templated free-text
instruction set that surfaces in MCP clients' prompt pickers (Claude
Desktop, mcp-cli, VSCode, etc.) and guides the agent through the
correct tool-call sequence:

- `onboard_new_source` — sandbox an Excel workbook, dry-run the
  artefact tree, summarise drift, gate on user confirmation before
  any write (EF-S6).
- `diagnose_validation_failure` — triage a failed run by joining
  `get_run_status` + `get_violations` + the `taxonomy://violations`
  resource into a top-5 severity-grouped summary (EF-S6).
- `infer_field_map` — bootstrap a draft mapping from a sample file
  via `infer_mapping_from_sample`, flagging low-confidence
  `FIELD_NNN` placeholders (EF-S6).
- `pick_etl_shape` — BA-facing capstone (S7-5). Takes a free-text
  problem description and walks the agent through the catalogue →
  clarifying questions → template recommendation → fill-in → dry-run
  loop.

### Prompt: `pick_etl_shape` (S7-5)

The BA-facing entry-point for the Sprint 7 demo. Given a free-text
description of the BA's data problem, the rendered messages instruct
the agent to:

1. Fetch `templates://etl/list` to see every available shape (the
   S7-2 auto-discovered catalogue).
2. Ask 2-3 clarifying questions before recommending — file vs
   database source? single record type per row or
   header/detail/trailer? key column(s) known? The BA-readable
   decision tree is [`docs/etl/CHOOSE_YOUR_SHAPE.md`](etl/CHOOSE_YOUR_SHAPE.md)
   (deep-linked from the prompt body, not re-encoded inline).
3. Recommend ONE template with one-line reasoning tied to the BA's
   answers — not a menu of every shape.
4. Fetch `templates://etl/<shape>` for the recommended shape and walk
   the BA through the `<FILL_IN_*>` placeholders verbatim.
5. Validate the filled-in spec with `onboard_source_dry_run` (or
   `compare_two_files` for ad-hoc file diffs — the S7-4 path) BEFORE
   any commit. Stop and confirm after the dry-run.

**Body length budget.** The rendered prompt body stays under 1000
characters by design — agents read briefly and the LLM does the
reasoning. The drift-prevention strings (`templates://etl/list`,
`templates://etl/`, `onboard_source_dry_run`, `compare_two_files`,
`docs/etl/CHOOSE_YOUR_SHAPE.md`) are kept as module-level constants in
[`src/mcp/prompts.py`](../src/mcp/prompts.py) so a rename in
`src/mcp/server.py` only needs touching one constant; the integration
tests fail fast on any mismatch.

**Invocation example (Claude Desktop / mcp-cli).** "Use the
`pick_etl_shape` prompt with description = 'I need to compare two
CSV exports'." The agent fetches the catalogue, asks about key
columns, and lands on the CSV template — closing the demo loop end
to end.

---

## Health probe (S9-2, #390)

`GET /mcp/health` is the MCP-aware load-balancer / readiness probe. It is a
new MCP surface distinct from the FastAPI process-liveness endpoint
(`/api/v1/system/health`): the FastAPI process can be up while the MCP
transport is wedged, and this probe detects exactly that.

**No auth.** The route is registered *outside* the MCP token-auth gate
(`MCPAuthMiddleware` short-circuits the `/mcp/health` path before any
credential check) so load balancers — which never authenticate — can poll it.
Every other `/mcp/` path still requires a session cookie, `X-API-Key`, or
bearer token.

**What it exercises (in-process, no DB round-trip, <100 ms target):**

1. **Session-manager liveness** — the FastMCP Streamable-HTTP session manager
   has entered `run()` (the in-process analogue of a healthy `initialize`
   handshake).
2. **Resource read** — reads `taxonomy://violations` through the registered
   handler.
3. **Registry enumeration** — counts registered tools / resources / prompts
   from the live registry (no hardcoded expected literal).

The handler is thin — all check logic lives in
[`src/mcp/health.py`](../src/mcp/health.py) (`check_mcp_health`); the route in
`src/mcp/server.py` only maps the structured result onto a 200/503 status.

**Success (200):**

```json
{
  "status": "healthy",
  "mcp_protocol_version": "2025-11-25",
  "tool_count": 10,
  "resource_count": 4,
  "prompt_count": 4,
  "uptime_seconds": 137
}
```

**Failure (503):** any failed check yields a structured body with
`status: "unhealthy"`, a `failed_check` token (`session_manager` |
`resource_read` | `registry`), and an operator-facing `reason` (never a raw
stack trace).

Operational detail — nginx probe wiring, Kubernetes `readinessProbe` example,
and the 200/503 LB action table — lives in
[`docs/PRODUCTION_DEPLOYMENT.md`](PRODUCTION_DEPLOYMENT.md), section
"Health probe (S9-2)".

---

## Rate limiting (S9-3, #388)

The `/mcp/` route applies an in-process **sliding-window** rate limiter
([`src/mcp/rate_limit.py`](../src/mcp/rate_limit.py)) to billable tool calls.
A client that exceeds its budget receives an **HTTP 429** at the transport
layer (before the JSON-RPC tool runs), carrying a `Retry-After` header — this
is a client-visible MCP surface behaviour every agent integration should
handle.

**What counts against the limit.** Only `tools/call` requests are metered.
These are **exempt** (never throttled):

- `resources/read` — `taxonomy://…`, `templates://etl/…`, `formats://supported`
  (cheap, idempotent);
- the handshake / discovery methods `initialize`, `tools/list`,
  `resources/list`, `prompts/list`, `ping`.

**Caps (defaults; all env-configurable — see the deployment runbook):**

| Budget | Default / min | Notes |
|---|---|---|
| Per **token** (authenticated principal) | 30 | The normal working rate for one agent. |
| Per **IP** (proxy-corrected client IP) | 60 | Defence-in-depth vs token theft; wider than per-token. |
| Per-token `get_run_status` polling | 240 | **Elevated** separate counter so polling a long run is not self-DOSed by the normal cap. |

**429 response shape:**

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 12
Content-Type: application/json

{"error": "Rate limit exceeded", "scope": "per_token", "retry_after_seconds": 12}
```

- `Retry-After` (and the mirrored `retry_after_seconds`) is the whole number
  of seconds until a slot frees (1–60). Clients should back off for that long
  before retrying.
- `scope` is `per_token` or `per_ip`, telling the caller which budget it hit.

**Client guidance.** Treat a 429 as a transient backpressure signal: wait
`Retry-After` seconds, then retry. Long-poll loops should prefer
`get_run_status` (elevated cap) over re-issuing heavyweight tools, and should
honour `Retry-After` rather than tight-looping.

Operational tuning (env vars, the in-memory-vs-Redis state model, the
multi-worker caveat, and the per-token-runs-first attribution rule) lives in
[`docs/PRODUCTION_DEPLOYMENT.md`](PRODUCTION_DEPLOYMENT.md), section
"Rate limiting (S9-3)".

---

## Token revocation (S9-4, #389)

MCP bearer tokens (EF-S7) are HMAC-signed and self-contained. S9-4 adds a
**per-token revocation blocklist** so a single leaked token can be killed
without rotating the signing key (which would invalidate every token).

**New token field — `jti`.** Minted tokens now carry an opaque `jti` (16
random bytes, hex-encoded) bound into the signature. It is the handle used to
revoke one specific token. Legacy tokens without a `jti` still validate during
a **24-hour grace window**, then are rejected (re-login mints a `jti`-bearing
token).

**New MCP-adjacent surface — `POST /api/v2/mcp/revoke`.** Admin-only
(LDAPS group `valdo-admins`). Body:

```json
{"username": "<admin>", "password": "<admin-pw>",
 "token_id": "<jti-to-revoke>", "reason": "laptop stolen — INC-12345"}
```

Responses: `200 {"revoked": true, "token_id": "...", "revoked_by": "CN=..."}`;
`401` bad credentials; **`403`** authenticated but not an admin; `503` LDAP or
the blocklist table unavailable. This sits alongside `POST /api/v2/mcp/login`
on the FastAPI app (not inside the `/mcp/` JSON-RPC sub-app).

**Enforcement on the auth path.** `src/mcp/auth.py` `verify_token` checks the
blocklist **after** signature + expiry validation — a forged token never
reaches the lookup. The check is backed by a **60-second in-memory cache** over
the `MCP_REVOKED_TOKENS` Oracle table, so the hot-path lookup is
sub-millisecond and DB-free within the TTL. A revocation is enforced on the
issuing node immediately; other nodes converge within the TTL.

CLI (`valdo mcp-revoke <token_id> --reason "..."`), the `jti` grace-window
mechanism, cache/latency model, fail-soft behaviour, and the incident-response
flow are documented in
[`docs/PRODUCTION_DEPLOYMENT.md`](PRODUCTION_DEPLOYMENT.md), section
"Token revocation (S9-4)".

---

## Run state persistence (S6-1, #386)

**Background.** EF-S4 introduced the `validate_file` tool, which starts
a validation run and returns a `run_id` the agent uses to poll
`get_run_status` and `get_violations`. The first release stored run
state in a process-local Python dict.

**Problem.** The dict was per-worker and in-memory only. Run state
was lost on:

- FastAPI / gunicorn restart
- Worker rotation
- Graceful reload

A BA who started a long validation and tried to poll `get_run_status`
after a restart would see `Unknown run_id` even though the run had
completed.

**Sprint 6 fix.** The MCP server now persists run state to the
`APP_MCP_RUN_REGISTRY` table via the shared SQLAlchemy engine. Each
status transition (`queued` → `running` → `completed`/`failed`) is
written through to the database, so a poll from a fresh worker sees
the latest state.

### Behaviour

- **Database-backed (default when reachable):** Runs survive restarts.
  `get_run_status` and `get_violations` work identically across worker
  rotations.
- **In-memory fallback:** When the database engine cannot be
  constructed (missing DSN, auth failure) or the table is missing,
  the server logs a `WARNING` and falls back to a process-local
  registry. This is the same fail-soft posture used by the baseline
  store — the server never refuses to boot. Runs started during the
  fallback window will not survive a restart.

The fallback path is logged at startup. Operators can grep the logs
for `MCP run registry:` to confirm which backend is active:

```
INFO  src.mcp.run_registry: MCP run registry: using database backend at table APP_INT.APP_MCP_RUN_REGISTRY
```

or

```
WARNING src.mcp.run_registry: MCP run registry: database backend unavailable,
falling back to in-memory store. Runs will not survive a restart. Underlying
error: <details>
```

### Configuring durable persistence

Persistence requires:

1. **A reachable database.** Set the standard Valdo DB env vars
   (`DB_ADAPTER`, `ORACLE_DSN`, `ORACLE_USER`, `ORACLE_PASSWORD`, or
   the PostgreSQL / SQLite equivalents). See
   [`docs/USAGE_AND_OPERATIONS_GUIDE.md`](USAGE_AND_OPERATIONS_GUIDE.md)
   §13 for the full env-var matrix.
2. **The migration applied.** Run `alembic upgrade head` to create
   `APP_MCP_RUN_REGISTRY`:

   ```
   alembic upgrade head
   ```

   The migration (revision `0004`) is idempotent — re-running is
   safe. The down-migration drops the table.

### Table shape (`APP_MCP_RUN_REGISTRY`)

| Column            | Type           | Notes                                               |
|-------------------|----------------|-----------------------------------------------------|
| `run_id`          | VARCHAR(64) PK | UUID4 hex string                                    |
| `source`          | VARCHAR(100)   | Canonical source name; used in idempotency lookup   |
| `file_path`       | VARCHAR(1000)  | File path; used in idempotency lookup               |
| `status`          | VARCHAR(20)    | queued / running / completed / failed               |
| `started_at`      | TIMESTAMP      | Run start time                                      |
| `finished_at`     | TIMESTAMP      | Set on terminal status                              |
| `violation_count` | NUMBER         | Set on terminal status                              |
| `payload`         | CLOB / TEXT    | Full JSON of the run record (incl. violations list) |
| `created_ts`      | TIMESTAMP      | Row insert time (diagnostic)                        |

Indexes:

- `IDX_MCP_RUN_INFLIGHT` on `(source, file_path, status)` — backs
  the `validate_file` idempotency-guard query.
- `IDX_MCP_RUN_STATUS` on `(status)` — covers ad-hoc diagnostic
  queries.

### Operational notes

- The registry does **not** include an expiry sweep. The
  `created_ts` column is in place for a future scheduled cleanup
  job; for now, completed rows accumulate. For the INT demo this is
  fine. Production deployment should add a TTL job.
- Idempotency is `(source, file_path)` only — re-triggering the same
  file while a previous run is still in flight returns the existing
  `run_id` instead of starting a second run.
- The payload column carries the canonicalised violation list, so
  `get_violations` reads only this table — no second hop to the
  validation engine or report files.

---

## Background validation worker (S9-5/S10 — ADR 0021)

**Contract change.** `validate_file` no longer necessarily runs the engine
inside the MCP request. Per **ADR 0021** (Option A — the run registry table
*is* the job queue), the tool always writes the durable `queued` row, then
decides how to run it. A separate `valdo run-job-worker` process claims the
row (`queued` → `running` via an atomic guarded `UPDATE`) and runs the
validation out of band. The agent-visible contract is unchanged — you still
get a `run_id` back and poll `get_run_status` / `get_violations` — but the run
now genuinely transitions `queued` → `running` → terminal rather than being
terminal by the time you hold the id.

**Async-by-default with a live-worker fallback (S10-2, #397).**
`VALDO_MCP_ASYNC_VALIDATE` is now **ON by default**. To avoid ever stranding a
run where no worker is deployed, `validate_file` is **liveness-aware**: it
consults the dedicated `MCP_WORKERS` table (Alembic 0007) and

* if a worker has heartbeated within the liveness window
  (`VALDO_MCP_WORKER_LIVENESS_SECONDS`, default 60s) → **enqueues only** and
  returns within ~100ms; the worker runs it;
* if **no** worker is live → falls back to a **synchronous inline run** so the
  call always completes.

Set `VALDO_MCP_ASYNC_VALIDATE=0` to force the legacy always-inline path. The
atomic `claim_next()` primitive makes the dequeue safe for multiple workers
(proven under concurrency in S10-2). See
[`docs/PRODUCTION_DEPLOYMENT.md`](PRODUCTION_DEPLOYMENT.md) §"Background job
worker" for the systemd unit (`valdo-run-job-worker.service`, shipped in the
RPM), the liveness marker, and the end-to-end async flow.
