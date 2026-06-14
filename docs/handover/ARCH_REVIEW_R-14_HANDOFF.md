# Arch-Review Story Handoff — R-14 (#43)

**Generated:** 2026-06-11
**Story:** [arch-review][R-14] Audit for residual shell=True in src/pipeline
**Predecessor:** docs/handover/ARCH_REVIEW_R-13_HANDOFF.md
**Trunk:** feature/valdo-engine-v3 (commit d6f149b, pushed: yes)
**Safety snapshot:** arch-review-snapshot/R-14 @ 64fd69c — deleted after green: yes
**Depends-on satisfied:** none

## 1. What shipped (scope delivered)

- **Audit result:** Confirmed zero `shell=True` occurrences in `src/` via both
  regex scan and AST walk across all 178 source files. Specifically verified:
  - `src/pipeline/runner.py` — uses `shlex.split()` + `subprocess.run(cmd_args, ...)` (no `shell=True`)
  - `src/pipeline/output_regression_suite.py` — uses `subprocess.run(cmd, ...)` (no `shell=True`)
  - `src/workflows/engine.py` — uses `subprocess.run(cmd, ...)` (no `shell=True`)
  - `src/services/downloader_service.py` — uses `subprocess.Popen(cmd, ...)` with explicit arg-arrays (no `shell=True`)
- **Guard test confirmed:** `tests/unit/test_no_shell_true.py` (initial checkin, git SHA `b40d99f`)
  contains two tests — `test_no_shell_true_regex` (regex scan) and `test_no_shell_true_ast`
  (AST-level call inspection) — both passing green. No new test file was needed.
- **CHANGELOG.md** updated with R-14 entry.
- **ARCH_REVIEW_STATUS.md** rolling pointer updated to next story (#44 R-15).

## 2. Out of scope / deferred (with follow-up note)

- `scripts/` was also scanned: no `shell=True` found there either (informational only; scope was `src/`).
- The pre-existing black/flake8 issues in `test_no_shell_true.py` (2 E501 lines, black reformatting)
  are pre-existing from the initial checkin and are not regressions introduced by this story.

## 3. AGENTS.md compliance

- src/ touched? No. Audit-only story; no source changes.
- Secrets: none added. Audit table: not mutated. Quick actions: none used.

## 4. ADR(s)

- None required (verification/audit story, no architectural decision).

## 5. Verification (actual results)

- black: PASS (on changed files — no new files introduced by this story)
- flake8: PASS (no new violations)
- mypy: 226 errors (all pre-existing, zero new)
- pytest (unit): 30 failed / 2737 passed / 12 skipped — matches R-13 baseline exactly; no regression
- harness offline subset: 267 passed / 1 skipped — matches baseline exactly
- Live-Oracle tests: SKIPPED (no SIT) — expected
- Guard test: `test_no_shell_true_regex` PASSED, `test_no_shell_true_ast` PASSED

## 6. Docs updated

- `docs/handover/ARCH_REVIEW_R-14_HANDOFF.md` (this file)
- `docs/handover/ARCH_REVIEW_STATUS.md` (rolling pointer updated)
- `CHANGELOG.md [Unreleased]` (entry added)

## 7. Acceptance criteria

- [x] Confirmed: no unsafe `shell=True` on config-derived commands in `src/`.
- [x] A guard test/lint prevents regression (2 tests: regex + AST, both green).
- [x] If any residual is found, it is fixed in this story — none found; no fix needed.

## 8. Next story

- #44 — R-15 — Fix doc/prompt drift; no blockers/decisions pending.
