# Sprint 22 — Kickoff (MCP Parity II — pipeline & admin ops)

**Sprint goal:** Finish MCP parity — add the remaining capabilities as MCP tools so the BA/QA can drive *everything* over MCP. Five more thin wrappers: **parse_file, run_etl_pipeline, export_failed_rows, submit_task, run_suite**. Second of the two parity sprints; after this, every user-facing capability is reachable over MCP (HTML reporting is Sprint 23).

**Duration:** 1–2 weeks · **Capacity:** 5 points (5 × S) · **Demo:** internal — call each new tool over MCP.

**Context:** Sprint 21 added the five core data-op tools (count 11→16). These five complete the surface. Same pattern: a `src/mcp/<x>_tools.py` `<x>_payload` thin wrapper over the existing service/command + `@mcp_server.tool` registration + an MCP integration test + tool-count/docs updates. The integration suite is now CI-gated — keep it 0-failed.

---

## Stories

| ID | GitHub | Tool | Wraps | Count |
|---|---|---|---|---|
| **S22-1** | [#438](https://github.com/buddy-k23/valdo/issues/438) | `parse_file` | parse command/service | 16→17 |
| **S22-2** | [#439](https://github.com/buddy-k23/valdo/issues/439) | `run_etl_pipeline` | ETL pipeline runner | 17→18 |
| **S22-3** | [#440](https://github.com/buddy-k23/valdo/issues/440) | `export_failed_rows` | validate --export-errors | 18→19 |
| **S22-4** | [#441](https://github.com/buddy-k23/valdo/issues/441) | `submit_task` | submit_task_command | 19→20 |
| **S22-5** | [#442](https://github.com/buddy-k23/valdo/issues/442) | `run_suite` | suite runner | 20→21 |

**Total: 5 pts.**

---

## Sequencing — sequential, commit-per-story

All touch `src/mcp/server.py` + `docs/MCP_SERVER.md` + the tool-count assertions (`test_mcp_onboarding_tools` / `test_mcp_action_tools` / `test_mcp_scaffold`). Run sequentially, one commit each.

| Day | Story | Notes |
|---|---|---|
| 1 | **S22-1** `parse_file` (#438) | Wrap parse; return parsed preview/contents JSON (bounded). |
| 2–3 | **S22-2** `run_etl_pipeline` (#439) | Wrap the ETL pipeline runner; per-gate results + overall pass/fail JSON; SQLite-friendly fixture for the test. |
| 4 | **S22-3** `export_failed_rows` (#440) | Wrap validate --export-errors; return the export path + count. PII-aware (path/counts, not raw rows). |
| 5 | **S22-4** `submit_task` (#441) | Wrap submit_task_command (idempotency-aware); task id/status JSON. |
| 6 | **S22-5** `run_suite` (#442) | Wrap the suite runner; suite result summary JSON. |
| 7 | Buffer / review | Kickoff final commit; push; close #438–442. After this MCP parity is complete (21 tools). |

---

## Definition of Done (per tool)

- [ ] Thin `@mcp_server.tool` delegating to the existing service (no business logic in the wrapper)
- [ ] MCP integration test over the transport asserting a correct result (mirror `test_mcp_db_compare_tool.py`)
- [ ] Tool-count assertions updated consistently to the new total; `/mcp/health` auto-derives
- [ ] `docs/MCP_SERVER.md` tool table updated
- [ ] No secrets/PII in tool responses (esp. `export_failed_rows`)
- [ ] `pytest tests/unit/` (coverage ≥80%) AND `pytest tests/integration/` both **0 failed** (now both CI-gated)
- [ ] Each story = one conventional commit `(S22-<m>, #<issue>)`; #438–442 closed
- [ ] Kickoff lands as the final commit; push

---

## Risks

| Risk | Mitigation |
|---|---|
| Tool-count assertions drift (now climbing 16→21) | Each story updates ALL count assertions to the new total; run the MCP integration suite per story. |
| `run_etl_pipeline` / `run_suite` are heavier (multi-gate) | Use a small SQLite-backed pipeline/suite fixture; if long-running, follow the validate_file async pattern or document synchronous-for-pilot. |
| `export_failed_rows` leaks PII | Return the export file path + count, never raw failed-row values. |
| Shared-file contention (server.py, MCP_SERVER.md, count tests) | Strictly sequential, commit-per-story. |

## Out of scope
HTML over MCP + db-compare/reconcile HTML → Sprint 23 (these tools return JSON for now).

## Roles
Dev: `senior-fullstack-fintech-dev` per story (mirror the Sprint-21 `*_tools.py` pattern). Owner/PM: you.
