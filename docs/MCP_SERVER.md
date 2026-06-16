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

The MCP server exposes nine tools as of Sprint 5 (EF chain):

- `list_sources`, `get_source_spec`, `list_recent_runs` — read-only
  (EF-S2)
- `validate_file`, `get_run_status`, `get_violations` — action
  (EF-S4)
- `infer_mapping_from_sample`, `upload_workbook_as_spec`,
  `onboard_source_dry_run` — onboarding (EF-S5)

Each tool's input schema and description are surfaced via the standard
MCP `tools/list` discovery call.

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
