# Cross-dialect reconcile — end-to-end demo

Demonstrates Valdo's **adapter-agnostic schema reconciliation** (ADR 0022, Sprint 12;
exposed via MCP + REST + UI in #407) running against a **SQLite** database — the same
reconcile that runs on Oracle now runs on any supported backend.

The demo seeds a SQLite `CUSTOMER` table deliberately shaped to produce an interesting
verdict, then reconciles the `reconcile_demo` mapping against it through **two surfaces**:

1. **mcp-valdo** — a real JSON-RPC `tools/call` to the `reconcile_mapping` MCP tool over
   the Streamable-HTTP transport.
2. **Web UI** — Playwright drives the "Reconcile mapping vs table" panel in the DB Compare
   tab and screenshots the rendered field-level verdict.

## What the verdict shows (identical in both surfaces)

| Field | Outcome |
|---|---|
| `AGE` | **Type mismatch** — mapping expects `integer`, DB column is `VARCHAR(10)` |
| `IS_ACTIVE` | **Advisory** — declared `boolean`, stored as `INTEGER` (no native boolean on this backend; not an error) |
| `NOTES` | **Advisory** — typeless SQLite column; mapping `string` accepted without a type assertion |

`status: mismatch` — the boolean and typeless cases are dialect-neutral **advisories**, not
errors (the Sprint 12 design call). A genuine type conflict (`AGE`) is still flagged.

## Run it

```bash
bash demo/reconcile_e2e/run_demo.sh
```

This seeds the DB (`seed/seed_db.py`, gitignored runtime file), runs the MCP segment
(`run_mcp_demo.py`) and the Playwright UI segment (`run_ui_demo.py`), writing artifacts to
`artifacts/`:

- `mcp_reconcile_transcript.json` / `.txt` — the MCP request + verdict
- `ui_reconcile_form.png` — the reconcile panel, filled
- `ui_reconcile_result.png` — the rendered verdict

Requires `playwright` + a cached Chromium (`pip install playwright && playwright install chromium`).
The server runs with `DB_ADAPTER=sqlite` pointed at the seeded demo DB.
