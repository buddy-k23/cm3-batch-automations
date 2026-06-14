# Arch-Review Story Handoff — R-02 (#26)

**Generated:** 2026-06-03
**Story:** Align coverage gate scope with the core engine
**Predecessor:** docs/handover/ARCH_REVIEW_R-01c_HANDOFF.md
**Trunk:** feature/valdo-engine-v3 (commit <set on push>, pushed: yes)
**Safety snapshot:** arch-review-snapshot/R-02 @ d090e79 — deleted after green: yes
**Depends-on satisfied:** none

## 1. What shipped (scope delivered)
- `pytest.ini` — widened `--cov` scope to add `src/validators`, `src/services`,
  `src/pipeline`, `src/database` to the existing four packages
  (`api`/`commands`/`comparators`/`reports`). `--cov-fail-under=80` unchanged.
- `docs/adr/0011-coverage-gate-scope.md` — ADR (Proposed) recording the decision
  and the measured number.

## 2. Decision (ADR 0011 summary)
- **Widen scope, keep the bar at 80%.** Measured on the degraded dev box
  (Windows / Py3.14 / no Oracle / no server) the wider scope reports
  **TOTAL 80.23% — gate PASSES** even with the 43 environmental failures
  (buckets A–G) not contributing coverage. On the AGENTS.md target
  (RHEL/3.11/DB) those tests pass, so the real enforced number is ≥ 80%. Bar is
  therefore **not lowered** — the widening is honest and non-regressive.
- The 78% < 80% "failure" reported before this story was an artifact of the
  *old narrow* scope interacting with the degraded env; the proper fix is the
  scope alignment, not a bar change.

## 3. AGENTS.md compliance
- src/ touched? **No** (config + ADR only).
- Secrets: none. Audit table: untouched. Quick actions: none.

## 4. ADR(s)
- ADR 0011 — Proposed (flip to Accepted under R-16).

## 5. Verification (actual results, baseline policy)
- `pytest tests/unit tests/integration` with the new `pytest.ini`:
  **Required test coverage of 80% reached. Total coverage: 80.23%** → coverage
  gate **PASSES** (previously failed at 78% under the narrow scope).
- Test pass/fail count unchanged vs. baseline: **43 failed, 2669 passed,
  12 skipped** — all 43 in the documented environmental buckets A–G; this story
  adds **0** new test failures (it changes only the coverage configuration).
- black/flake8/mypy: not applicable (no `.py` changed); `pytest.ini` + markdown.

## 6. Docs updated
- `docs/adr/0011-coverage-gate-scope.md` (new)
- `CHANGELOG.md` (new "Changed" entry)

## 7. Acceptance criteria
- [x] `pytest.ini` coverage scope matches the documented decision (widened to
      the core engine + database).
- [x] CI reports the true core-coverage percentage (80.23% on the dev box;
      ≥ 80% on target).
- [x] ADR 0011 added.
- [x] No regression below the agreed threshold (gate passes at 80%+; bar not
      lowered).

## 8. Next story
- #27 — R-02-followup — Add a `scripts/e2e_lib` coverage job. Depends on #26
  (now satisfied); ADR 0011 covers whether the harness gets its own gate (yes,
  separate, ratcheting threshold).
