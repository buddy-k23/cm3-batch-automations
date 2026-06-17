# Sprint 18 — Kickoff (Structure Cleanup)

**Sprint goal:** Pay down the structural debt from the architecture review — a central UI fetch layer, thinner routers, dead-code removal, and the set-based chunked comparator that lets large compares actually scale. Third of four sprints to "100% functional."

**Duration:** 1–2 weeks · **Capacity:** ~5 points (5 × S) · **Demo:** internal.

**Context:** With correctness (S16) and multi-worker/quality (S17) done, this sprint addresses maintainability + the one remaining scale ceiling. The coverage gate is now enforced at 80% — keep it green.

---

## Stories

| ID | GitHub | Title | Size | Track |
|---|---|---|---|---|
| **S18-1** | [#427](https://github.com/buddy-k23/valdo/issues/427) | Central `apiFetch()` wrapper in `ui.js` | S | UI |
| **S18-2** | [#428](https://github.com/buddy-k23/valdo/issues/428) | Thin `files.py` — response-shaping + connection-resolution → services | S | Structure |
| **S18-3** | [#429](https://github.com/buddy-k23/valdo/issues/429) | `onboarding.py` git/gh orchestration → service | S | Structure |
| **S18-4** | [#431](https://github.com/buddy-k23/valdo/issues/431) | Remove dead Oracle-only `transaction.py` | S | Cleanup |
| **S18-5** | [#423](https://github.com/buddy-k23/valdo/issues/423) | Set-based chunked comparator (no per-row SELECT; CLI auto-route; complete diffs) | S* | Scale |

**Total: ~5 pts.** `*` #423 is the borderline-M item — scoped to the JOIN + auto-route + complete-diffs, not a rewrite.

---

## Sequencing — sequential, commit-per-story

| Day | Story | Notes |
|---|---|---|
| 1–2 | **S18-1** (#427) | One `apiFetch(path, opts)` in `ui.js`: auth headers, timeout, uniform 401/403/5xx handling; migrate the ~45 raw `fetch()` calls. Keep behavior; add a tiny test if the e2e harness allows, else manual-verify the UI loads. |
| 3 | **S18-2** (#428) | Move `files.py` cross-type violation reshaping + named-connection resolution into the validate/db-compare services; router delegates. Parity (same responses). |
| 4 | **S18-3** (#429) | Move `onboarding.py` git/gh subprocess orchestration into an onboarding service; router thin. |
| 5 | **S18-4** (#431) | Remove `src/database/transaction.py` (Oracle-only, zero functional `src/` consumers) + `tests/unit/test_transaction.py` + the `__init__` re-export; drop its `.coveragerc` omit entry. Confirm suite green + coverage still ≥80%. |
| 6–8 | **S18-5** (#423) | Replace `ChunkedFileComparator`'s per-row SELECT with a set-based JOIN (load file1 keys into SQLite, JOIN file2 chunks); CLI auto-routes to chunked by file size (as the API already does); return complete `only_in_file1/2` (make the 1000-cap configurable). Keep all comparator tests green; add a large-input + completeness test. |
| 9 | Buffer / review | Kickoff final commit; push; close. |

---

## Definition of Done

- [ ] **S18-1:** all UI API calls go through `apiFetch()`; consistent auth-failure/timeout/error UX; UI still loads + functions
- [ ] **S18-2:** `files.py` endpoints delegate; shaping/connection logic in services (shared with CLI); responses unchanged (parity test)
- [ ] **S18-3:** git/gh orchestration in an onboarding service; router thin
- [ ] **S18-4:** `transaction.py` + its test + re-export removed; `.coveragerc` omit entry dropped; suite green; coverage ≥80%
- [ ] **S18-5:** no per-row SELECT in the chunked comparator; CLI auto-routes by size; complete `only_in_file1/2` returned (cap configurable); comparator tests green + a completeness/large-input test added
- [ ] `pytest tests/unit/` **green (0 failed)** and **coverage ≥80%** (the gate is now enforced); no new required deps; no secrets/fixture DBs committed
- [ ] Each story = one conventional commit `(S18-<m>, #<issue>)`; #427/#428/#429/#431/#423 closed
- [ ] Kickoff lands as final commit; push

---

## Risks

| Risk | Mitigation |
|---|---|
| `apiFetch` migration changes a call's behavior (headers/error handling) and breaks a tab | Migrate mechanically; preserve each call's semantics; spot-check the UI loads + a couple of flows; the e2e tests (if runnable) are the net. |
| Thinning `files.py`/`onboarding.py` regresses an endpoint response | Parity tests: same request → identical response before/after; move logic, don't rewrite it. |
| Removing `transaction.py` breaks an import we missed | grep all of `src/` + `tests/` first; remove the `__init__` re-export too; run full suite + coverage. |
| Set-based comparator changes diff output / ordering vs the per-row path | Keep the result schema identical; assert the same diffs (now complete) on existing fixtures; test a value that was previously truncated/omitted is now present. |
| Coverage dips below 80% after removing transaction.py / adding code | transaction.py was omitted in `.coveragerc` (removing it is neutral); new comparator code needs its own tests to stay ≥80%. |
| Shared-file contention (ui.js, files.py, CHANGELOG) | Strictly sequential, commit-per-story. |

## Out of scope
JSON/XML parsers (#395/#396) — Sprint 19. ui.js full ES-module split — only the `apiFetch` slice here. The >500-line file splits (validation_renderer, enhanced_validator) — larger refactors, not this sprint.

## Roles
Dev: `senior-fullstack-fintech-dev` per story. Owner/PM: you.
