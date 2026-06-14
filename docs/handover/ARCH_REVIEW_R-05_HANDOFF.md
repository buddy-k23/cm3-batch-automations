# Arch-Review Story Handoff — R-05 (#32)

**Generated:** 2026-06-05
**Story:** [arch-review][R-05] Decide and implement empty/header-only-batch gate semantics
**Predecessor:** docs/handover/ARCH_REVIEW_R-03b_HANDOFF.md
**Trunk:** feature/valdo-engine-v3 (decision `2435396`; implementation `c3ff262`, pushed: yes)
**Safety snapshot:** decision `arch-review-snapshot/R-05` (retired); implementation `arch-review-snapshot/R-05-impl` @ 396a44e — deleted after green: yes
**Depends-on satisfied:** none (R-05 has no dependency)
**Status:** DONE — user chose **Option A** (2026-06-05); implemented (see §9).

## 1. What shipped (scope delivered)
This is a **decision story** (`<stop_and_ask_first>`): the deliverable is the
ADR + the questions for the user. **No behaviour change implemented yet.**

- `docs/adr/0013-empty-header-only-batch-gate-semantics.md` — ADR (Proposed)
  framing the L1-vs-L2b disagreement on a header-only batch and weighing:
  - **Option A (recommended)** — treat "`ITM-CNT-BRT == 0` and 0 detail rows" as
    valid; truncation (`N>0` header, 0 details) and malformed headers still fail.
  - **Option B** — header-only batches always fail L1 (status-quo intent).
  - **Option C** — make the L1 item-count axis non-blocking (downgrade severity).
  - **Option D** — upstream emptiness gate that skips the item-count axis.
- `CHANGELOG.md` — ADR-0013 decision note. **Also corrected a stale R-03b entry**
  (see §2).

## 2. Out of scope / deferred (with follow-up note)
- The implementation slice (the conditional in
  `cross_type_validator._check_header_trailer_count`, possibly a narrow R058
  interaction, plus a clean-empty test and a truncation test) is **blocked** on
  the user's answer to the two questions in the ADR.
- **CHANGELOG correction:** the R-03b commit (`3b46c92`) shipped with a *stale*
  CHANGELOG entry — it still read "L2 regeneration disposition decided … blocked
  on the user's choice" (the R-03a wording) because the R-03b CHANGELOG edit had
  silently failed to persist at the time. This story replaces it with the
  correct "L2 regeneration gate retired (Option B)" entry. No other R-03b artifact
  was affected (ADR 0012 status, infographic, READMEs all verified committed).

## 3. AGENTS.md compliance
- src/ touched? **No.** Docs-only (one ADR + CHANGELOG note).
- Secrets: none. Audit table: not mutated. Quick actions: none used.
- Do-not-guess honored: gate blocking semantics and the source's zero-item
  representation are posed as explicit questions; the ADR proposes, the user
  disposes.

## 4. ADR(s)
- `docs/adr/0013-empty-header-only-batch-gate-semantics.md` — **Proposed**
  (awaiting user decision).

## 5. Verification (actual results)
- Docs-only change; no source/test/config touched, so the code gate is
  unaffected (baseline carried from R-03b: 43 env-failures / 2676 passed /
  coverage 80.23%). No Python changed → black/flake8/mypy N/A for this delta.

## 6. Docs updated
- `docs/adr/0013-empty-header-only-batch-gate-semantics.md` (new)
- `CHANGELOG.md` (ADR-0013 note + R-03b correction)

## 7. Acceptance criteria
- [x] ADR 0013 records the decision (options + recommendation + the two
      do-not-guess questions).
- [ ] Empty/header-only batch behaves per the decision, with a test. *(blocked:
      pending user choice)*
- [ ] No change to non-empty-batch behaviour. *(blocked: pending user choice)*

## 8. Next story
- After R-05, the runner order continues to **#33 — R-10a — Config-schema
  registry document**.

## 9. Implementation update (Option A — 2026-06-05)

The user chose **Option A**. Implemented as a config-driven, opt-in allowance
(core `src/` change governed by ADR 0013):

- `src/config/multi_record_config.py`: `CrossTypeRule` gained
  `allow_empty_batch: bool = False` (opt-in; default off → no existing rule or
  source changes behaviour).
- `src/validators/cross_type_validator.py::_check_header_trailer_count`: when
  `allow_empty_batch` is set **and** declared count == 0 **and** counted rows ==
  0, the check passes. Truncation (`declared > 0`, 0 rows) and any non-zero
  mismatch still fail.
- `config/mappings/SHAW_TRANERT.yaml`: the `header_trailer_count` rule opts in
  (`allow_empty_batch: true`).
- Tests (`tests/unit/test_multi_record_validator.py::TestCrossTypeValidator`):
  clean-empty passes, truncation still fails, non-empty unchanged (match +
  mismatch), and a no-opt-in zero/zero case.
- Docs: ADR 0013 → Accepted (Option A) with a decision-outcome + Q1/Q2 scope
  note; `docs/TESTING_FIXED_WIDTH_FILES.md` cross-type table; `CHANGELOG.md`.

**Q1/Q2 scope note:** the change is in the **L1 item-count axis** R-05 names and
is correct regardless of the source's zero representation. A *blank* (vs
zero-filled) `ITM-CNT-BRT` is already skipped by the count check; only the
separate per-field `not_empty` rule **R058** would flag a blank field — that is a
distinct field-format rule, intentionally left untouched. Relaxing R058 on an
empty batch (if SHAW emits blank-for-zero and the team wants it) is a small
separate follow-up, not part of the item-count decision.

### Verification (actual)
- Full suite: **43 failed / 2680 passed / 12 skipped** — failure count + buckets
  match the documented baseline (no new failures; +4 from the new tests).
- Coverage: **80.23%** — "Required test coverage of 80% reached."
- Harness offline subset: **267 passed / 1 skipped**.
- black/flake8 on changed files: all findings pre-existing at HEAD (verified
  per-file); zero new introduced (the new `src/` files are black+flake8 clean;
  the test file's findings are the documented baseline). `mypy src/`: **224**
  errors (was 227 — no new errors; net −3).
- Safety snapshot `arch-review-snapshot/R-05-impl` deleted after green + push.
