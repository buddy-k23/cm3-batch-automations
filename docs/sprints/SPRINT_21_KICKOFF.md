# Sprint 21 — Kickoff (MCP Parity I — core data ops)

**Sprint goal:** Close the biggest MCP-coverage gaps so a BA/QA can drive the core data operations over MCP, not just validate/compare/reconcile-single. Add MCP tools for **db-compare, reconcile-all, mask, detect-drift, extract** — each a *thin wrapper* over the service that already exists (the `reconcile_mapping` tool, #407, is the established pattern). First of three sprints toward full MCP parity + HTML-everywhere (the user's 2026-06-17 ask).

**Duration:** 1–2 weeks · **Capacity:** 5 points (5 × S) · **Demo:** internal — call each new tool over MCP.

**Context:** A read-only audit confirmed MCP exposes only a subset; these five are the highest-value missing data operations. The business logic lives in services (`db_file_compare_service`, `reconcile_all_service` (S16-3), the mask service, detect-drift, the adapter-based `DataExtractor` (S15)) — so each story is a thin `@mcp_server.tool` + a `*_payload` wrapper + an MCP integration test + a tool-count/docs update. HTML over MCP is Sprint 23; these tools return structured JSON now.

---

## Stories

| ID | GitHub | Tool | Wraps | Size |
|---|---|---|---|---|
| **S21-1** | [#433](https://github.com/buddy-k23/valdo/issues/433) | `db_compare` | `db_file_compare_service` (honors DB_ADAPTER) | S |
| **S21-2** | [#434](https://github.com/buddy-k23/valdo/issues/434) | `reconcile_all` | `reconcile_all_service` (S16-3) | S |
| **S21-3** | [#435](https://github.com/buddy-k23/valdo/issues/435) | `mask_file` | the mask service (6 strategies) | S |
| **S21-4** | [#436](https://github.com/buddy-k23/valdo/issues/436) | `detect_drift` | detect-drift service | S |
| **S21-5** | [#437](https://github.com/buddy-k23/valdo/issues/437) | `extract_table` | adapter-based `DataExtractor` (S15) | S |

**Total: 5 pts.**

---

## Sequencing — sequential, commit-per-story

All five touch `src/mcp/server.py` (registration) + `docs/MCP_SERVER.md` (tool table/count) + the tool-count assertions in `tests/integration/test_mcp_*` — so run sequentially, one commit each, clean tree between agents.

| Day | Story | Notes |
|---|---|---|
| 1–2 | **S21-1** `db_compare` (#433) | Mirror `reconcile_tools.py`/`compare_tools.py`. Honors DB_ADAPTER (SQLite/Postgres/Oracle); SQLite end-to-end test. |
| 3 | **S21-2** `reconcile_all` (#434) | Wrap `reconcile_all_service`; aggregate + drift JSON. |
| 4 | **S21-3** `mask_file` (#435) | Wrap the mask service; return masked-file path + per-field strategy summary. PII-safe (no raw values leaked in the response). |
| 5 | **S21-4** `detect_drift` (#436) | Wrap detect-drift; drift report JSON. |
| 6 | **S21-5** `extract_table` (#437) | Wrap the adapter-based `DataExtractor`; **preserve the S13.5-4 SQL hardening** (bound limit, allow-listed identifiers, no raw where); SQLite test. |
| 7 | Buffer / review | Kickoff final commit; push; close #433–437. |

---

## Definition of Done (per tool)

- [ ] Thin `@mcp_server.tool` registered in `src/mcp/server.py`; logic delegates to the existing service (arch principle #1 — no business logic in the tool wrapper)
- [ ] An MCP integration test (mirror `test_mcp_reconcile_tool.py`) drives the tool over the transport and asserts a correct result; data-touching tools prove it on **SQLite**
- [ ] The MCP tool-count assertions (`test_mcp_onboarding_tools` / `test_mcp_action_tools` / `test_mcp_scaffold` / `/mcp/health` count) updated consistently to the new total
- [ ] `docs/MCP_SERVER.md` tool table updated
- [ ] **No secrets/PII** in tool responses (esp. `mask_file` and `extract_table`)
- [ ] `pytest tests/unit/ tests/integration/test_mcp_*.py` **green (0 failed)**; coverage ≥80% (enforced)
- [ ] Each story = one conventional commit `(S21-<m>, #<issue>)`; #433–437 closed
- [ ] Kickoff lands as the final commit; push

---

## Risks

| Risk | Mitigation |
|---|---|
| Tool-count assertions drift as each tool is added (several tests pin the count) | Each story updates ALL count assertions to the new true total (they were reconciled to 11 in #407; now climbing). Run the MCP integration suite per story. |
| `mask_file` / `extract_table` leak PII or secrets in the MCP response | Return paths + summaries, never raw masked values or credentials; assert in tests. |
| `extract_table` MCP path loses the S13.5-4 SQL hardening | It goes through the same `DataExtractor` — keep the injection tests green; add MCP-path coverage that a malicious table/limit is rejected. |
| Async vs sync semantics (db_compare/extract can be slow) | Match the existing tool conventions; if a tool is long-running, follow the `validate_file` async/run-id pattern or document it returns synchronously for pilot scale. |
| Shared-file contention (server.py, MCP_SERVER.md, count tests) | Strictly sequential, commit-per-story. |

## Out of scope
Parity II (parse/run-etl-pipeline/export-errors/submit-task/run-suite) → Sprint 22. HTML over MCP + db-compare/reconcile HTML → Sprint 23. These tools return JSON for now.

## Roles
Dev: `senior-fullstack-fintech-dev` per story (mirror the #407 reconcile_mapping tool pattern). Owner/PM: you.
