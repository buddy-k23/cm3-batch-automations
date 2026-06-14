# Arch-Review Story Handoff — R-01a (#23)

**Generated:** 2026-06-03
**Story:** Define TruthSource interface (no behaviour change)
**Predecessor:** first in series (baseline:
`docs/handover/ARCH_REVIEW_TEST_BASELINE_2026-06-03.md`)
**Trunk:** feature/valdo-engine-v3 (commit <set on push>, pushed: yes)
**Safety snapshot:** arch-review-snapshot/R-01a @ 88673f9 — deleted after green: yes
**Depends-on satisfied:** none

## 1. What shipped (scope delivered)
- `src/database/truth_source.py` — new module:
  - `TruthSource` ABC: `connect() -> <PEP-249 conn>`, `close()`, `dialect`
    property, context-manager sugar.
  - `SqlDialect` frozen dataclass + `ORACLE_DIALECT` sentinel (placeholder seam
    for R-01c).
  - `OracleTruthSource` adapter mirroring the legacy
    `run_source._open_l2b_connection` credential convention
    (`ORACLE_DSN`/`ORACLE_USER`/`ORACLE_PASSWORD`), lazy `oracledb` import,
    injectable `connect_fn`, single `TruthSourceError` on failure.
- `tests/unit/test_truth_source.py` — 8 tests (interface contract via a fake
  impl, context manager, Oracle adapter credential pass-through, idempotent
  close, missing-secret + connect-failure error wrapping with no secret leak).
- `docs/adr/0010-truthsource-backend-abstraction.md` — ADR, status **Proposed**.
- `CHANGELOG.md` — `[Unreleased] > Added` entry.

## 2. Out of scope / deferred (with follow-up note)
- Rewiring `run_source._open_l2b_connection` to use `OracleTruthSource` →
  **R-01b (#24)**, next story.
- SQL-dialect decoupling / `dialect` consumption → **R-01c (#25)**.

## 3. AGENTS.md compliance
- src/ touched? **Yes** — `src/database/truth_source.py`. This is a **core**
  change (not harness work), authorized by ADR 0010 via the normal MR+ADR flow;
  hard rule #1 does not apply.
- Secrets: none added; secret *values* never logged or put in error messages.
- Audit table: not touched. Quick actions: none used in any GitLab body.

## 4. ADR(s)
- ADR 0010 — Proposed (flip to Accepted on merge per R-16).

## 5. Verification (actual results, under the baseline policy)
- Targeted suite `test_truth_source.py` + `test_e2e_run_source.py` +
  `test_e2e_db_truth_comparator.py`: **49 passed**.
- black --check (changed files): PASS | flake8 (changed files): PASS
- mypy `src/database/truth_source.py` (isolated): PASS (no issues).
- Full unit+integration failure count: unchanged at the documented baseline
  (43, all environmental buckets A–G); this story adds **0** new failures.
- Coverage 78% < 80% is the pre-existing condition owned by R-02 (#26); not a
  regression from this story (new module is outside the gate's `--cov` scope).
- Live-Oracle / e2e-server tests: SKIPPED/errored for env reasons (no DB, no
  server) — expected, see baseline doc.

## 6. Docs updated
- `docs/adr/0010-truthsource-backend-abstraction.md` (new)
- `CHANGELOG.md`
- `docs/handover/ARCH_REVIEW_TEST_BASELINE_2026-06-03.md` (gate policy, written
  during triage)

## 7. Acceptance criteria
- [x] `TruthSource` interface + `OracleTruthSource` adapter land with no change
      to existing L2b behaviour.
- [x] ADR 0010 added as Proposed.
- [x] New unit tests pass; no new failures vs. baseline.
- [x] black/flake8/mypy clean on changed files.

## 8. Next story
- #24 — R-01b — Rewire L2b opener through OracleTruthSource. Depends on #23
  (now satisfied). No pending decisions.
