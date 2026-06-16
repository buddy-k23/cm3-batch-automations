# Sprint 8.5 — Kickoff (mini)

**Sprint goal:** Close out the Sprint 8 hygiene debt. Land the XML parser ADR that Sprint 8 deferred (#378), execute the `pilot_database_validations/` deletion that ADR 0020 ruled on (kill-and-defer), and file the two deferred parser-implementation issues (JSON, XML) so the breadth roadmap is queued and unambiguous. No engine behaviour changes ship this sprint — it is pure scoping + repo hygiene.

**Duration:** 1 week (mini-sprint)
**Capacity:** ~3 points (3 × S)
**Demo:** Internal — no external audience. ADR 0019 lands for review; the repo shrinks by ~4.7k LOC of dead prototype; the implementation backlog gets two concrete M-issues.

**Context:** Sprint 8 fixed the two engine bugs, landed the JSON ADR (0018) and the DB-to-DB disposition ADR (0020), and added the CI drift-check. Two follow-ups were explicitly deferred to this mini-sprint per the Sprint 8 kickoff "After Sprint 8" table: the XML ADR (#378) and the pilot deletion PR. Both ADR 0018 and ADR 0020 also instruct that the implementation issues be filed *after* their ADR review — i.e. now.

---

## Stories

| ID | GitHub | Title | Size | Owner | Track |
|---|---|---|---|---|---|
| **S8.5-1** | [#378](https://github.com/buddy-k23/valdo/issues/378) | ADR 0019 — XML parser design | S | Architect | Engine breadth |
| **S8.5-2** | [#394](https://github.com/buddy-k23/valdo/issues/394) | Remove `pilot_database_validations/` per ADR 0020 | S | Dev A | Hygiene |
| **S8.5-3** | (files new issues) | File deferred parser-implementation issues (JSON M, XML M) | S | Sprint owner | Backlog grooming |

**Total: ~3 pts.**

---

## Daily sequencing

| Day | Story | Why this order |
|---|---|---|
| 1–2 | **S8.5-1** (#378 XML ADR) | Pure research + write. Model directly on ADR 0018 (JSON) — its §Follow-ups already sketches the XML pattern (XPath column, parser swap, same two validators). The net-new design decision is namespaces + attribute-vs-text + the untrusted-XML security surface (XXE / billion-laughs), which JSON did not have. |
| 2 | **S8.5-2** (#394 pilot deletion) | Mechanical. Reference-scan first, then `rm -rf`, CHANGELOG line, confirm `pytest tests/unit/` unaffected. Runs in parallel with the ADR. |
| 3 | **S8.5-3** (file impl issues) | JSON impl M-issue (per ADR 0018 §Follow-ups: "file after ADR review, do not include in Sprint 8") + XML impl M-issue (per ADR 0019 §Follow-ups, once written). |
| 4 | Buffer / sprint review | Plan Sprint 9 (gated on prod greenlight) or a further hygiene mini-sprint. |

---

## Definition of Ready

- [x] AC is testable / verifiable (ADR proposes ONE design + LOC table; deletion verified by grep + pytest)
- [x] Files touched scoped (ADR: 1 new file; deletion: 1 dir + CHANGELOG)
- [x] Dependencies explicit (S8.5-3 XML impl issue depends on S8.5-1 landing)
- [x] Completable by one owner in ≤ 1 day each

## Definition of Done

- [ ] `pytest tests/unit/` passes (deletion must not regress the suite)
- [ ] `tests/unit/test_no_shell_true.py` still passes
- [ ] **Documentation updated** per backlog standard:
  - One-line entry to `CHANGELOG.md` under `[Unreleased]` for the deletion
  - No `docs/MCP_SERVER.md` / USAGE guide change expected (no user-facing surface changes this sprint)
- [ ] Conventional commits landed on `valdo-version-v4`, each referencing `(S8.5-<m>, #<issue>)`
- [ ] **For ADR 0019:** status `Proposed`; cites ADR 0018; implementation issue filed as M follow-up
- [ ] Kickoff doc lands as the **final commit** of the sprint

---

## Sprint risks

| Risk | L | I | Mitigation |
|---|---|---|---|
| XML ADR drifts from the JSON ADR's established pattern, creating two divergent designs to implement | M | M | ADR 0019 must explicitly cite ADR 0018 and reuse its flatten-at-parse + two-validator decisions; only namespaces, attributes, and the security surface are genuinely net-new |
| `pilot_database_validations/` is referenced somewhere we didn't expect (CI, sphinx, an import) and deletion breaks a build | L | M | Reference-scan (`grep -rn pilot_database_validations`) before deleting; only ADR 0020 + sprint docs may legitimately mention it; run full `pytest tests/unit/` after |
| Untrusted-XML security surface (XXE, billion-laughs) under-addressed in the ADR, leaking into implementation | M | H | ADR 0019 must make an explicit `lxml` vs stdlib `ElementTree` recommendation and address external-entity / entity-expansion defenses — fintech batch files may be untrusted |
| Scope creep — pulling JSON/XML *implementation* into a mini-sprint | L | M | Implementation is explicitly out of scope; this sprint only files the M-issues. See below. |

---

## Out of scope — do not pull in

- **Implementation of the JSON parser** (ADR 0018) — filed as an M-issue this sprint, built in a future sprint
- **Implementation of the XML parser** (ADR 0019) — filed as an M-issue this sprint, built in a future sprint
- **DB-to-DB comparator** — ADR 0020 ruled kill-and-defer; the conditional M-issue is filed only when a concrete BA request lands (not speculatively)
- **ADR-0015 salvage delta** (deterministic run IDs, promotion-gate vocabulary from the deleted pilot) — ADR 0020 flagged this as an optional S follow-up; defer unless time permits
- **`severity_filter` re-add for `compare_two_files`** — still needs comparator-level severity classification first; remains a future follow-up
- **Production hardening** (#387 #388 #389 #390 #391) — Sprint 9, gated on prod greenlight

---

## Roles

- **Sprint owner / PM:** you (self-paced)
- **Architect (ADR 0019):** delegated to `principal-enterprise-architect` agent
- **Dev (deletion):** delegated to `senior-fullstack-fintech-dev` agent
- **Reviewer:** you + Claude Code suggestions on every commit
- **Demo audience:** none — internal hygiene
- **Environment:** local dev + CI

---

## After Sprint 8.5

| Sprint | Focus | Issues |
|---|---|---|
| **9** | Production hardening (gate: prod greenlit) | #387 #388 #389 #390 #391 |
| **(unscheduled)** | JSON parser implementation | new M-issue filed in S8.5-3 |
| **(unscheduled)** | XML parser implementation | new M-issue filed in S8.5-3 |
| **(conditional)** | DB-to-DB comparator | filed only on a concrete BA request, per ADR 0020 |
