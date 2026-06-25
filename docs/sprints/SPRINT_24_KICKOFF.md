# Sprint 24 — Kickoff (Excel ↔ DB Comparison)

**Sprint goal:** Deliver a net-new **Excel ↔ DB comparison** capability — compare an Excel sheet against a DB table/query (and the reverse), reconciling row-by-row through Valdo's existing comparison contract + HTML reporter, reachable across **CLI / REST / MCP / Web UI**. Per **ADR 0024** this is the #1 value-for-effort DuckDB-adjacent slice — but built **pandas-first** (no new dependency); DuckDB is a later large-workbook accelerator, explicitly out of scope here.

**Duration:** 1–2 weeks · **Capacity:** ~5 points (5 × S) · **Demo:** internal — `valdo excel-compare` and the UI tab both reconcile an Excel sheet against a seeded DB table and produce an HTML diff report.

**Context:** Excel is **spec-ingestion only** in Valdo today (mapping/rules templates → config JSON); there is **no Excel-as-data comparison path** (ADR 0024 §3). This sprint adds one, as a new `excel_db_compare_service` **parallel to** the working `db_file_compare_service`, reusing `run_compare_service`'s result contract + `HTMLReporter`. Net-new + additive ⇒ minimal regression surface (the ~3,850 tests pinning validation/rules are untouched). The DB side is extracted via the existing `DataExtractor`/`get_database_adapter()` (Oracle/Postgres/SQLite) exactly as db-compare does — the Oracle-not-native caveat in ADR 0024 does not bite because extraction already goes through `oracledb`.

---

## Stories

| ID | GitHub | Title | Size |
|---|---|---|---|
| **S24-1** | [#447](https://github.com/buddy-k23/valdo/issues/447) | Excel data reader (sheet/header selection + type coercion) | S |
| **S24-2** | [#448](https://github.com/buddy-k23/valdo/issues/448) | `excel_db_compare_service` + CLI `excel-compare` (both directions) | S |
| **S24-3** | [#449](https://github.com/buddy-k23/valdo/issues/449) | REST endpoint for Excel↔DB compare | S |
| **S24-4** | [#450](https://github.com/buddy-k23/valdo/issues/450) | `excel_db_compare` MCP tool (21→22) | S |
| **S24-5** | [#451](https://github.com/buddy-k23/valdo/issues/451) | Web UI tab (mirror DB Compare split-panel) | S |

**Total: ~5 pts.**

---

## Sequencing — sequential, commit-per-story

The seams to MIRROR (from db-compare): CLI `src/main.py:241`; command `src/commands/db_compare.py`; service `src/services/db_file_compare_service.py`; REST `src/api/routers/files.py:634`; MCP `src/mcp/db_compare_tools.py`; UI `src/reports/static/ui.{html,css,js}`. Result contract: `run_compare_service` (`src/services/compare_service.py:134`). Excel-reader patterns to reuse: `src/config/*converter.py` (openpyxl / `pd.read_excel`).

| Day | Story | Notes |
|---|---|---|
| 1–2 | **S24-1** Excel reader (#447) | New reader: xlsx sheet → DataFrame. Sheet name/index selection, header-row handling, and string/type coercion so Excel cells line up with DB column types for the join. Reuse the `pd.read_excel(dtype=str)` + openpyxl patterns from the converters. Unit tests (multi-sheet, header offset, type coercion, empty/odd cells). |
| 2–3 | **S24-2** service + CLI (#448) | `excel_db_compare_service`: read Excel (S24-1) + extract DB via `DataExtractor` → feed `run_compare_service` (same result dict the renderers consume) → `HTMLReporter`. **Both directions** via a `direction` flag (Excel-as-actual vs DB-as-actual), mirroring db-compare's swap. CLI `valdo excel-compare` (key_columns, sheet, direction, `--output *.html/.json`). Tests incl. a round-trip against a SQLite/seeded table. |
| 4 | **S24-3** REST (#449) | `POST /api/v2/.../excel-compare` mirroring `files.py:634` db-compare; reuse the `DbCompareResult`-style response model (Excel-variant). Upload Excel + DB connection/query in the request. Tests. |
| 5 | **S24-4** MCP tool (#450) | Thin `src/mcp/excel_db_compare_tools.py` `excel_db_compare_payload` wrapping the service + `ToolError`; register in `server.py`. Tool count **21→22** — update assertions in `test_mcp_{onboarding,action,scaffold}`; `/mcp/health` auto-derives. MCP integration test over the transport (mirror `test_mcp_db_compare_tool.py`). No secrets/PII in the response. |
| 6–7 | **S24-5** UI tab (#451) | New tab mirroring DB Compare split-panel: Excel upload + DB connection/query, direction swap, metric cards + HTML report link, client-side diff CSV. Connection password in sessionStorage only (never persisted), as DB Compare does. CSS in `ui.css`, JS in `ui.js`, markup in `ui.html` — never recombine (CLAUDE.md file-size rule). |
| 8 | Buffer / review | Kickoff final commit; push; close #447–451. Excel↔DB compare live across all four surfaces. |

---

## Definition of Done

- [ ] **S24-1:** Excel reader returns a DataFrame with selectable sheet + header, coercing types to align with DB columns; unit tests cover multi-sheet/header-offset/coercion/edge cells
- [ ] **S24-2:** `excel_db_compare_service` reconciles Excel ↔ DB through `run_compare_service` + `HTMLReporter`, **both directions**; `valdo excel-compare` produces `.json` and `.html` outputs; round-trip test green
- [ ] **S24-3:** REST endpoint compares an uploaded Excel against a DB table/query; test asserts the result contract
- [ ] **S24-4:** `excel_db_compare` MCP tool returns a correct result over the transport; tool count 21→22 consistently; integration test; no PII/secrets in response
- [ ] **S24-5:** UI tab reconciles Excel vs DB with direction swap + HTML report + diff download; password never persisted
- [ ] Both CI gates **0 failed** (unit coverage ≥80%, integration); pandas-only (no new dependency); no regression to db-compare/compare/validation paths
- [ ] Each story = one conventional commit `(S24-<m>, #<issue>)`; #447–451 closed
- [ ] Kickoff lands as the final commit; push

---

## Risks

| Risk | Mitigation |
|---|---|
| Excel type/format drift vs DB column types breaks the join (e.g. Excel numeric → DB string, dates) | S24-1 coerces to a canonical string form (as db-compare reads `dtype=str`); document the coercion; test the mismatch cases |
| Duplicating db-compare instead of reusing the contract | Reuse `run_compare_service` result dict + `HTMLReporter` + the `DbCompareResult` model shape; the service only swaps the Excel side in for one input |
| Shared-file contention (`files.py`, `ui.js/html`, `server.py`, MCP count tests) | Strictly sequential, commit-per-story |
| Scope creep into DuckDB | DuckDB is explicitly OUT (ADR 0024 §3 — later accelerator); pandas-only this sprint |
| MCP response leaking row data/PII | Return summary/diff counts + a retrievable report handle, not raw rows; mirror the existing db_compare tool's redaction posture |

## Out of scope
DuckDB join backend (ADR 0024 — later, benchmark-gated); the DB↔file DuckDB pilot (ADR 0024 §2); Excel↔Excel compare; writing back to Excel.

## Roles
Dev: `senior-fullstack-fintech-dev` per story (mirror db-compare's `*_tools.py`/service/router/UI patterns). Owner/PM: you.
