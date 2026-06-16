# Sprint 8 — Kickoff

**Sprint goal:** Honest semantics + paved road for future formats. Fix the two engine bugs Sprint 6 flagged (which Sprint 7 worked around), file the ADRs that scope JSON + DB-to-DB before any code is written, and add the CI drift-check so a typo'd template can't ship.

**Duration:** 2 weeks
**Capacity:** 5 points (5 × S)
**Demo:** Internal — no external audience. The two bug fixes restore honest CLI exit codes + correct CSV parsing. ADRs land for review; implementation lives in future sprints.

**Context:** Sprint 7 closed the BA UX loop end-to-end. Two known engine bugs were carried as workarounds (S7-4 has a TODO referencing them). This sprint pays that debt and scopes the next wave of format support so we don't blunder into JSON/DB-to-DB unscoped.

---

## Stories

| ID | GitHub | Title | Size | Owner | Track |
|---|---|---|---|---|---|
| **S8-1** | [#392](https://github.com/buddy-k23/valdo/issues/392) | Engine bug: `valdo compare` exits 0 even on differences | S | Dev A | Engine hygiene |
| **S8-2** | [#393](https://github.com/buddy-k23/valdo/issues/393) | Engine bug: `FormatDetector` routes `.csv` via `PipeDelimitedParser` | S | Dev A | Engine hygiene |
| **S8-3** | [#377](https://github.com/buddy-k23/valdo/issues/377) | ADR 0018 — JSON parser design | S | Architect | Engine breadth |
| **S8-4** | [#379](https://github.com/buddy-k23/valdo/issues/379) | ADR 0020 — DB-to-DB disposition (absorb/kill `pilot_database_validations/`) | S | Architect | Engine breadth |
| **S8-5** | [#384](https://github.com/buddy-k23/valdo/issues/384) | CI drift-check guardrail for `templates/etl/*.yml` | S | Dev A | Hygiene |

**Total: 5 pts.**

XML ADR (#378) deferred to a future hygiene mini-sprint — XML is less-asked-for than JSON for new integrations and we don't want to blow the sprint cap. Will be revisited after Sprint 9 or as a standalone story when the JSON pattern lands.

---

## Daily sequencing

| Day | Story | Why this order |
|---|---|---|
| 1 | **S8-1** (#392 compare exit code) | Quick narrow CLI fix. Affects no downstream code other than tests. |
| 2 | **S8-2** (#393 CSV routing) | Slightly larger because S7-4 workaround retirement is part of AC. Test the round-trip end-to-end. |
| 3–4 | **S8-3** (#377 JSON ADR) | Pure research + write. ADR pattern is well-established. |
| 5–6 | **S8-4** (#379 DB-to-DB ADR) | Requires reading the entire `pilot_database_validations/` sub-project end-to-end before deciding absorb/kill. |
| 7–8 | **S8-5** (#384 CI drift-check) | Workflow + local script. Pattern from EC-S11 `workbook-drift-check.yml` to mirror. |
| 9 | Buffer / cleanup | |
| 10 | Sprint review + plan Sprint 8.5 (XML ADR mini-sprint) or Sprint 9 (gated on prod) | |

---

## Definition of Ready

- [ ] AC is testable with a named test file + case
- [ ] Files touched ≤ 4 (or overage justified in commit body)
- [ ] Dependencies explicit
- [ ] Completable by one dev in ≤ 1 day

## Definition of Done

- [ ] `pytest tests/unit/` passes (narrow filter OK)
- [ ] `tests/unit/test_no_shell_true.py` still passes
- [ ] **Documentation updated** per backlog standard:
  - `docs/MCP_SERVER.md` if MCP surface changes (S8-2 may touch — verify the `compare_two_files` test still passes after workaround removal)
  - `docs/USAGE_AND_OPERATIONS_GUIDE.md` for any user-facing CLI/UI change
  - Relevant README section
  - One-line entry to `CHANGELOG.md` under `[Unreleased]`
- [ ] Conventional commit landed on `valdo-version-v4`
- [ ] Story-specific AC from the linked GitHub issue all checked
- [ ] **For ADRs:** status flipped to `Proposed` (or `Accepted` for #379 which requires a decision)

---

## Sprint risks

| Risk | L | I | Mitigation |
|---|---|---|---|
| #393 fix cascades into other format-detection callers we don't expect | M | M | Run full unit test suite (not just narrow filter) after the fix; check for any tests pinning `.csv → PipeDelimitedParser` behavior; update them to test the actual intended semantic |
| #379 DB-to-DB ADR needs a definitive verdict (Accepted, not Proposed) | M | M | Time-box reading `pilot_database_validations/` to 1.5 days; if quality assessment is ambiguous, default to "kill-and-defer" — the absorb path needs a real customer ask to justify it |
| #384 CI workflow fails on existing templates due to subtle schema drift | L | M | First run is informational only — `continue-on-error: true` on the new job until baseline is green; promote to required check in a follow-up commit once stable |
| S7-4 workaround removal (in S8-2) breaks the `compare_two_files` MCP tool tests | M | L | The MCP integration test passes against any working CSV parser; once the FormatDetector returns a comma-CSV parser, the workaround code can be replaced with a single `FormatDetector.get_parser_class()` call — no behavior change at the MCP layer |

---

## Out of scope — do not pull in

- XML ADR (#378) — deferred to a hygiene mini-sprint
- Implementation of JSON parser (#377 is ADR only)
- Implementation of DB-to-DB integration (#379 is ADR only; if "absorb" wins, follow-up M/L issue gets filed)
- `severity_filter` re-add for `compare_two_files` — needs comparator-level severity classification first; flag as a follow-up issue if not already filed
- Updating issue body for #375 (`ReconciliationConfig.model_validate()` typo + CLI flag example) — these are GH issue edits, can be done in <5 min outside sprint scope
- Production hardening (#387 #388 #389 #390 #391) — Sprint 9, gated

---

## Roles

- **Sprint owner / dev:** you (self-paced)
- **Architect (for ADRs):** delegated to `principal-enterprise-architect` agent
- **Reviewer:** you + Claude Code suggestions on every commit
- **Demo audience:** none for this sprint — internal hygiene
- **Environment:** local dev + CI

---

## After Sprint 8

| Sprint | Focus | Issues |
|---|---|---|
| **8.5** (mini) | XML ADR + any S8 follow-ups (severity classification, JSON parser implementation if scoped) | #378 + new issues filed during S8 |
| **9** | Production hardening (gate: prod greenlit) | #387 #388 #389 #390 #391 |
