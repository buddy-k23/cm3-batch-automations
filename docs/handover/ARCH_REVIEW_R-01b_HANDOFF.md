# Arch-Review Story Handoff — R-01b (#24)

**Generated:** 2026-06-03
**Story:** Rewire L2b connection opener through OracleTruthSource
**Predecessor:** docs/handover/ARCH_REVIEW_R-01a_HANDOFF.md
**Trunk:** feature/valdo-engine-v3 (commit <set on push>, pushed: yes)
**Safety snapshot:** arch-review-snapshot/R-01b @ cbc4cda — deleted after green: yes
**Depends-on satisfied:** #23 (R-01a)

## 1. What shipped (scope delivered)
- `scripts/e2e_lib/run_source.py::_open_l2b_connection` — internals replaced:
  it now builds an `OracleTruthSource(secret_lookup=resolver)` (ADR 0010) and
  calls `.connect()`, instead of importing `oracledb` and calling
  `oracledb.connect(...)` directly. The orchestrator no longer imports
  `oracledb`.
  - Behaviour preserved: same `ORACLE_DSN`/`ORACLE_USER`/`ORACLE_PASSWORD`
    secret convention via the run's `SecretResolver`; same degrade-to-`None`
    (→ `infra_error`) on any failure (no raise); the historical `l2b_*` log
    events (`l2b_secret_unresolved` / `l2b_oracledb_missing` /
    `l2b_connect_failed`) are retained by mapping the adapter's
    `TruthSourceError` message.
  - Function signature unchanged → the single call site (line ~828) is untouched.
- `tests/unit/test_e2e_run_source.py` — new `TestOpenL2bConnection` (3 tests):
  success returns the connection (real adapter + injected `connect_fn`),
  missing-secret degrades to `None` with `l2b_secret_unresolved`, connect
  failure degrades to `None` with `l2b_connect_failed`. Added a module-import
  alias `run_source_mod` (the existing `from ... import run_source` binds the
  *function* name, so the private helper needs the module).

## 2. Out of scope / deferred (with follow-up note)
- SQL-dialect decoupling / `dialect` consumption → **R-01c (#25)**, next story.

## 3. AGENTS.md compliance
- src/ touched? **No.** This story edits `scripts/e2e_lib/run_source.py`
  (harness) only; it consumes the `src/` primitive added under ADR 0010 in
  R-01a — exactly the intended direction (harness depends on the core seam).
  No new `src/` change, so hard rule #1 is satisfied.
- Secrets: convention unchanged; values never logged (adapter guarantees it).
- Audit table: not touched. Quick actions: none.
- Note: black reformatted one pre-existing `GateResult(...)` line in
  `run_source.py` (cosmetic, single line). The pre-existing F401/F841 lint in
  `run_source.py` (shlex/shutil/f2s_blocking) is untouched and remains owned by
  **R-16 (#45)**.

## 4. ADR(s)
- None new. Implements ADR 0010 step 2.

## 5. Verification (actual results, baseline policy)
- Targeted: `test_e2e_run_source.py` (20) + `test_truth_source.py` (8) =
  **28 passed**.
- black --check (changed files): PASS. flake8: only the documented pre-existing
  items (`run_source.py` F401 shlex/shutil, F841 f2s_blocking;
  `test_e2e_run_source.py:36` GateResult unused) — no NEW items.
- Full unit+integration failure count unchanged vs. baseline (43, buckets A–G);
  this story adds **0** new failures.
- Live-Oracle path: still degrades cleanly without a DB (tested via injection).

## 6. Docs updated
- `CHANGELOG.md` (updated the R-01 entry to cover the rewire).

## 7. Acceptance criteria
- [x] `run_source.py` no longer imports `oracledb` directly; depends only on the
      `TruthSource` abstraction.
- [x] Connection-failure path still produces an `infra_error` gate result +
      failure-sink row (degrade-to-`None` → caller emits infra_error, unchanged).
- [x] L2b tests pass with a fake/injected TruthSource (no live Oracle needed).
- [x] black/flake8/mypy clean (no NEW lint vs. the documented baseline).

## 8. Next story
- #25 — R-01c — Decouple expected-SQL dialect (spike + seam). Depends on #24
  (now satisfied). No pending decisions.
