# Sprint 23 — Kickoff (HTML Reporting Parity)

**Sprint goal:** Close the reporting gap the user flagged — HTML reports reachable **over MCP**, and HTML reports added to **db-compare** and **reconcile/reconcile-all** (which today emit JSON only). After this, every report-producing capability has an HTML report, reachable across CLI / REST / UI / MCP. Final sprint of the MCP-parity + HTML program.

**Duration:** 1–2 weeks · **Capacity:** ~5 points (ADR S + 3 × S) · **Demo:** internal — an MCP tool call returns a retrievable HTML report.

**Context:** Today HTML exists only for validate/compare/suite, only via CLI `--output *.html` / REST / UI — **never over MCP** (the MCP tools return JSON; `validate_file` even runs with `output=None`). And db-compare's `--output-format html` is "informational only" (not wired); reconcile has no HTML at all. This sprint fixes all three, starting with an ADR for the cross-cutting "how does an agent retrieve an HTML report over MCP" design.

---

## Stories

| ID | GitHub | Title | Size |
|---|---|---|---|
| **S23-1** | [#443](https://github.com/buddy-k23/valdo/issues/443) | ADR 0023 — HTML report retrieval over MCP | S |
| **S23-2** | [#444](https://github.com/buddy-k23/valdo/issues/444) | Wire db-compare HTML report (today "informational only") | S |
| **S23-3** | [#445](https://github.com/buddy-k23/valdo/issues/445) | HTML report for reconcile / reconcile-all | S |
| **S23-4** | [#446](https://github.com/buddy-k23/valdo/issues/446) | HTML report over MCP (validate_file/compare/db_compare/reconcile/reconcile_all) | S |

**Total: ~5 pts.**

---

## Sequencing — sequential, commit-per-story

| Day | Story | Notes |
|---|---|---|
| 1–2 | **S23-1** ADR 0023 (#443) | Architect decides the retrieval design: MCP tools generate the HTML to the reports dir and return BOTH a server path AND an agent-retrievable handle. Options to weigh: (a) a new `report://<run_id>` MCP **resource** that serves the HTML body; (b) return the existing REST `report_url` (`/api/.../report.html`) the agent/user can open; (c) return the HTML inline (rejected — response-budget). Pick one (or a primary + fallback), with security (no path traversal; reports dir only) + the response-shape contract. Status Accepted; defines S23-4. |
| 3 | **S23-2** db-compare HTML (#444) | Wire `db_file_compare_service` `output_format=html` to `comparison_renderer` (it's currently a no-op flag). CLI `--output *.html` + API produce a real HTML diff report; tests. |
| 4–5 | **S23-3** reconcile HTML (#445) | New `reconcile_renderer` (HTML) for the field-level verdict (matches / mismatches / advisories) + the reconcile-all aggregate; wire to CLI `--output *.html`, REST, and the reconcile/reconcile_all MCP tools (per ADR 0023); tests. |
| 6–7 | **S23-4** HTML over MCP (#446) | Per ADR 0023: `validate_file`, `compare_two_files`, `db_compare`, `reconcile_mapping`, `reconcile_all` optionally generate the HTML report and return the retrievable handle (path + URL/resource). `validate_file` stops forcing `output=None` when a report is requested. Tests over the transport prove an agent can get the report. |
| 8 | Buffer / review | Kickoff final commit; push; close #443–446. Program complete: full MCP parity + HTML everywhere. |

---

## Definition of Done

- [ ] **S23-1:** ADR 0023 (Accepted) — one concrete retrieval design (resource vs report_url), security + response-shape contract, defines S23-4
- [ ] **S23-2:** db-compare emits a real HTML report via CLI `--output *.html` + API (no longer "informational only"); test asserts the HTML file is produced
- [ ] **S23-3:** reconcile + reconcile-all emit an HTML verdict report (CLI/REST/MCP); test
- [ ] **S23-4:** the 5 report-producing MCP tools return a retrievable HTML report per ADR 0023; an integration test proves an agent can fetch it; opt-in (JSON-only stays the default if no report requested, to bound responses)
- [ ] Both CI gates **0 failed** (unit coverage ≥80%, integration); no path-traversal in report retrieval; no secrets/PII in reports beyond what the existing renderers already redact
- [ ] Each story = one conventional commit `(S23-<m>, #<issue>)`; #443–446 closed
- [ ] Kickoff lands as the final commit; push

---

## Risks

| Risk | Mitigation |
|---|---|
| Returning HTML over MCP blows the response budget | Don't inline large HTML — return a path + a retrievable handle (resource/URL); ADR 0023 makes this call. Inline only a small report, if ever. |
| A `report://` resource or report_url enables path traversal / reading arbitrary files | Serve ONLY from the configured reports dir; validate/resolve the id against that dir (reuse the `_safe_upload_path` pattern). Test a traversal attempt is rejected. |
| reconcile HTML renderer duplicates the validation/comparison renderer | Reuse the shared renderer scaffolding (`src/reports/renderers/`) + the established HTML shell; keep it a thin verdict renderer. |
| `validate_file` behavior change (output=None → optional report) regresses the async/run-id flow | Make HTML opt-in via a tool param; default behavior (JSON + run_id) unchanged; test both. |
| Shared-file contention (server.py, the tools, renderers, MCP_SERVER.md) | Strictly sequential, commit-per-story. |

## Roles
Architect: `principal-enterprise-architect` (ADR 0023). Dev: `senior-fullstack-fintech-dev` (S23-2..4). Owner/PM: you.
