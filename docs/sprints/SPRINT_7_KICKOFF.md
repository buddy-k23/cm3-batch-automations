# Sprint 7 — Kickoff

**Sprint goal:** Close the BA UX loop. The CSV + FW-single templates from Sprint 6 are useful, but a BA can't *discover* them without docs. This sprint exposes the templates over MCP so an agent can fetch them on demand, adds the third BA-facing template (DB-to-file), and ships the `pick_etl_shape` prompt that walks a BA from a free-text problem statement to the right template.

**Duration:** 2 weeks
**Capacity:** 5 points (5 × S)
**Demo:** End of Week 2 — a BA in the demo room types *"I have an Oracle table and a daily extract file — verify they match."* Agent calls `pick_etl_shape` → recommends DB-to-file template → fetches it over `templates://etl/db_to_file_reconciliation` → walks the BA through filling it in → runs `db-compare`.

**Context:** Sprint 6 landed the foundation: persistence (S6-1), two templates (S6-2/S6-3), decision tree (S6-4), client setup docs (S6-5). What's missing is *discoverability over MCP* — an agent can't see what templates exist. This sprint closes that gap and adds the third must-have template.

---

## Stories

| ID | GitHub | Title | Size | Owner | Track |
|---|---|---|---|---|---|
| **S7-1** | [#375](https://github.com/buddy-k23/valdo/issues/375) | DB-to-file reconciliation template + sample + README (generic) | S | Dev A | BA UX |
| **S7-2** | [#380](https://github.com/buddy-k23/valdo/issues/380) | MCP resource `templates://etl/<shape>` — serve templates over MCP | S | Dev A | MCP surface |
| **S7-3** | [#381](https://github.com/buddy-k23/valdo/issues/381) | MCP resource `formats://supported` — enumerate formats | S | Dev A | MCP surface |
| **S7-4** | [#382](https://github.com/buddy-k23/valdo/issues/382) | MCP tool `compare_two_files(left, right, key_columns)` | S | Dev A | MCP surface |
| **S7-5** | [#383](https://github.com/buddy-k23/valdo/issues/383) | MCP prompt `pick_etl_shape(description)` | S | Dev A | BA UX |

**Total: 5 pts** (single-track, self-paced).

---

## Daily sequencing

| Day | Story | Why this order |
|---|---|---|
| 1–2 | **S7-1** (#375 DB-to-file template) | Lands the third template *before* S7-2 wires the resource. S7-2's auto-discovery test then naturally picks up all three. |
| 3 | **S7-2** (#380 templates resource) | First MCP surface story. Auto-discovers from `templates/etl/` so it picks up CSV (S6-2), FW-single (S6-3), and DB-to-file (S7-1) automatically. |
| 4 | **S7-3** (#381 formats resource) | Independent module — static constants. Quick win that closes the "what does Valdo support?" question for any agent. |
| 5 | **S7-4** (#382 compare_two_files tool) | Wraps existing `file_comparator.py`. The fast-feedback tool BAs will reach for first. |
| 6 | **S7-5** (#383 pick_etl_shape prompt) | Capstone — references the resource URIs from S7-2 and S7-3. Lands last because it points at everything else. |
| 7 | Integration test on full demo flow | End-to-end against Oracle docker + MCP server. |
| 8 | Demo prep + buffer | |
| 9–10 | Demo | |

---

## Demo (end of sprint)

Scripted BA scenario:

1. BA opens Claude Desktop / VSCode (MCP server attached)
2. BA types: *"I have an Oracle CUSTOMER table and we ship a daily extract file. I want to verify the file matches the table."*
3. Agent calls **prompt** `pick_etl_shape(description=...)`
4. Prompt instructs agent to fetch **resource** `templates://etl/list`
5. Agent reads list, asks BA 2 clarifying questions (key column? ignored fields?)
6. Agent recommends `db_to_file_reconciliation` template
7. Agent fetches **resource** `templates://etl/db_to_file_reconciliation` — renders the YAML inline
8. Agent helps BA fill in connection DSN, query, key column
9. Agent calls **tool** `onboard_source_dry_run` — drift report comes back clean
10. Agent calls **tool** `validate_file` (or `compare_two_files` for the ad-hoc path) — returns matched/differing counts
11. Agent offers to open a PR with the config — uses existing EE-S3 endpoint

If the audience asks *"what does Valdo not support yet?"* — agent fetches `formats://supported` and reads the `planned` array.

---

## Definition of Ready

- [ ] AC is testable with a named test file + case
- [ ] Files touched ≤ 4 (or overage justified in the commit body)
- [ ] Dependencies explicit (predecessor commits referenced)
- [ ] Completable by one dev in ≤ 1 day

## Definition of Done

- [ ] `pytest tests/unit/` passes (narrow filter OK per env constraints)
- [ ] `tests/unit/test_no_shell_true.py` still passes
- [ ] **Documentation updated** per the backlog standard:
  - `docs/MCP_SERVER.md` for any MCP surface change (every story in this sprint touches MCP except S7-1)
  - `docs/USAGE_AND_OPERATIONS_GUIDE.md` if a user-facing CLI/UI/config surface changes
  - Relevant README section
  - One-line entry to `CHANGELOG.md` under `[Unreleased]`
- [ ] Conventional commit landed on `valdo-version-v4`
- [ ] Story-specific AC from the linked GitHub issue all checked

---

## Sprint risks

| Risk | L | I | Mitigation |
|---|---|---|---|
| #375 DB-to-file template needs Oracle for the sample to "really run" | M | M | Provide SQLite fallback path in the README (`tests/manual/seed_db.py` already handles both); sample test runs against whichever backend is available |
| #380 auto-discovery picks up half-built / WIP templates | L | M | List + serve only files matching `templates/etl/*.yml` that load through `SourceConfig.model_validate()`; skip + log on failure |
| #382 `compare_two_files` exposes the existing exit-code-0 bug (flagged in S6-2) | M | L | Tool layer returns explicit summary dict — exit-code semantics don't leak through MCP. File the underlying CLI bug as separate issue in Sprint 8 hygiene. |
| #383 prompt body drifts from actual resource URIs | M | M | Test asserts the prompt text contains the literal URI strings; regression catch on rename |

---

## Out of scope — do not pull in

- Production hardening (#387 #388 #389 #390 #391) — Sprint 9 (gated on prod greenlight)
- Engine ADRs (#377 #378 #379) — Sprint 8
- CI drift-check (#384) — Sprint 8 hygiene
- Engine bug fixes flagged in Sprint 6 (`valdo compare` exit code; CSV-via-PipeDelimitedParser routing) — Sprint 8 hygiene
- Per-shape prompts beyond `pick_etl_shape` — overkill, LLM does the reasoning
- Streaming `compare_two_files` for huge files — keep the CLI path for >10M rows

If a PR introduces any of the above, send it back as scope creep.

---

## Roles

- **Sprint owner / dev:** you (self-paced, on your own time with Claude Code)
- **Reviewer:** you + Claude Code suggestions on every commit
- **Demo audience:** TBD — leadership review or BA stakeholders
- **Demo environment:** integration region only

---

## After Sprint 7

| Sprint | Focus | Issues |
|---|---|---|
| **8** | Engine breadth ADRs + hygiene | #377 #378 #379 #384 + two flagged engine bugs |
| **9** | Production hardening (gate: prod greenlit) | #387 #388 #389 #390 #391 |
