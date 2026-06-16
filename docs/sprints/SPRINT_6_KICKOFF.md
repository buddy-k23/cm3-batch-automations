# Sprint 6 — Kickoff

**Sprint goal:** Make Valdo's integration-environment demo land cleanly — reliable run state across restarts, BA-ready templates for the most common non-SHAW shapes, and multi-client setup docs so the demo isn't tied to one MCP client.

**Duration:** 2 weeks
**Capacity:** 5 points (5 × S)
**Demo:** End of Week 2 — a BA in the demo room uploads a CSV file and Claude (or VSCode / Duo) walks them through reconciling it against another CSV, end-to-end against the INT environment.

**Context:** Enterprise restrictions block a production demo for now. The INT pilot is the path to wider rollout. Production-hardening track (TLS-with-PKI, /mcp/health, rate limits, revocation, background queue) is **gated on prod greenlight** and parked for Sprint 9.

---

## Stories

| ID | GitHub | Title | Size | Owner | Track |
|---|---|---|---|---|---|
| **S6-1** | [#386](https://github.com/buddy-k23/valdo/issues/386) | MCP run registry → persistent backend (survive restarts) | S | Dev A | Demo reliability |
| **S6-2** | [#373](https://github.com/buddy-k23/valdo/issues/373) | CSV-to-CSV file comparison template + sample + README | S | Dev A | BA UX |
| **S6-3** | [#374](https://github.com/buddy-k23/valdo/issues/374) | Fixed-width single-record validation template + sample + README | S | Dev A | BA UX |
| **S6-4** | [#376](https://github.com/buddy-k23/valdo/issues/376) | ETL shape decision tree doc — "which template do I pick?" | S | Dev A | BA UX |
| **S6-5** | [#385](https://github.com/buddy-k23/valdo/issues/385) | VSCode + GitLab Duo MCP setup docs + config files | S | Dev A | Multi-client |

**Total: 5 pts** (single-track since this is self-paced).

---

## Daily sequencing

| Day | Story | Why this order |
|---|---|---|
| 1–2 | **S6-1** (#386 persistence) | Load-bearing for everything else. If runs don't survive a restart, the demo can fail silently. Land it first so subsequent stories build on a known-reliable foundation. |
| 3 | **S6-4** (#376 decision tree) | Pure docs, no code dependency. Lands as scaffolding the next two templates plug into. |
| 4 | **S6-2** (#373 CSV template) | Most-asked-for non-SHAW shape. First concrete BA win. |
| 5 | **S6-3** (#374 FW single template) | Pair to #373. Together they cover the majority of simple batch files. |
| 6–7 | **S6-5** (#385 VSCode + Duo) | Independent track. Schedule alongside templates if extra capacity opens. |
| 8 | Integration test on a synthetic demo scenario | End-to-end smoke against the seeded Oracle |
| 9 | Demo prep + buffer | |
| 10 | Demo | |

---

## Demo (end of sprint)

A BA scenario, scripted end-to-end:

1. BA logs into the INT Web UI (or opens Claude Desktop / VSCode with the MCP server configured)
2. BA says *"I have two CSV exports of our customer table — one from the legacy system, one from the new platform. Compare them on CUSTOMER_ID."*
3. Agent calls `infer_mapping_from_sample`, then `upload_workbook_as_spec` against the CSV template
4. Agent calls `onboard_source_dry_run` — drift report comes back
5. Agent calls `compare_two_files` (or the equivalent existing tool) — returns summary + top differences
6. Agent suggests opening a PR with the reconciliation config so this comparison runs on every release
7. BA confirms; PR opens (via the existing EE-S3 endpoint) targeting the `valdo-edits/<user>` branch

If the demo audience asks "what if we want to use this from VSCode instead?" — open `docs/MCP_CLIENTS.md` and walk through the 30-second setup.

---

## Definition of Ready

- [ ] AC is testable with a named test file + case
- [ ] Files touched ≤ 4 (or overage justified in the commit body)
- [ ] Dependencies explicit (predecessor commits referenced)
- [ ] Completable by one dev in ≤ 1 day

## Definition of Done

- [ ] `pytest tests/unit/` passes (narrow filter OK per env constraints)
- [ ] `tests/unit/test_no_shell_true.py` still passes
- [ ] **Documentation updated** per the backlog standard (see each story's checklist):
  - `docs/MCP_SERVER.md` if MCP surface changes
  - `docs/USAGE_AND_OPERATIONS_GUIDE.md` for user-facing changes
  - Relevant README section
  - One-line entry to `CHANGELOG.md` under `[Unreleased]`
- [ ] Conventional commit landed on `valdo-version-v4`
- [ ] Story-specific AC from the linked GitHub issue all checked

---

## Sprint risks

| Risk | L | I | Mitigation |
|---|---|---|---|
| #386 Oracle table design slows the persistence work | M | M | Time-box the design phase to half a day; if Alembic migration design exceeds that, ship in-memory + DB fallback first, defer schema work |
| Template Pydantic-model fit | M | M | Land the simplest possible template first (just enough to pass `SourceConfig.model_validate()`); iterate on richness only if it lands fast |
| VSCode/Duo MCP support state moves under us | L | M | Doc states "as of <date>" and links to upstream issue; falls back to documented manual config if native MCP isn't GA |
| Demo scenario depends on an unbuilt MCP tool | M | M | Sprint 6's stories specifically lean on tools that already ship (validate_file, get_violations, upload_workbook_as_spec); avoid demo paths that need future Sprint 7 tools |

---

## Out of scope — do not pull in

- TLS + nginx (#387) — prod-only, gated
- `/mcp/health` (#390) — prod load-balancer concern, gated
- Rate limiting (#388) / Token revocation (#389) — prod-only, gated
- Background job queue (#391) — performance hardening, defer
- CI drift-check (#384) — hygiene; Sprint 8
- DB-to-file template (#375) — Sprint 7 (avoid Oracle dependency in the first BA template)
- All engine ADRs (#377/378/379) — Sprint 8
- All MCP enrichment (#380/381/382/383) — Sprint 7 (these depend on templates landing first)

If a PR introduces any of the above, send it back as scope creep.

---

## Roles

- **Sprint owner / dev:** you (self-paced, on your own time with Claude Code)
- **Reviewer:** you + Claude Code suggestions on every commit
- **Demo audience:** TBD — leadership review or BA stakeholders
- **Demo environment:** integration region only (no prod data, no prod access)

---

## After Sprint 6

| Sprint | Focus | Issues |
|---|---|---|
| **7** | BA UX completion + MCP polish | #375 #380 #381 #382 #383 |
| **8** | Engine breadth ADRs + hygiene | #377 #378 #379 #384 |
| **9** | Production hardening (gate: prod greenlit) | #387 #388 #389 #390 #391 |
