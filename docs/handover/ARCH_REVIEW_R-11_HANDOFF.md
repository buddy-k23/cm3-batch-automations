# Arch-Review Story Handoff — R-11 (#40)

**Generated:** 2026-06-11
**Story:** Introduce registries for pipeline step types (and report formats)
**Predecessor:** docs/handover/ARCH_REVIEW_R-08_HANDOFF.md
**Trunk:** feature/valdo-engine-v3 (commit 524177a, pushed: yes)
**Safety snapshot:** arch-review-snapshot/R-11 @ 4c55185 — deleted after green: yes
**Depends-on satisfied:** none (no deps)

## 1. What shipped (scope delivered)
- `src/pipeline/etl_pipeline_runner.py`:
  - Replaced the `_execute_step` type-switch with a step-type → handler
    **registry** built by a new `ETLPipelineRunner._step_handlers()` method.
    `_execute_step` now does a dict lookup and raises the same `ValueError`
    (message enumerates the registry keys) for unknown types.
  - Extracted the four inline branches into bound handler methods
    (`_execute_validate_step`, `_execute_validate_multi_record_step`,
    `_execute_compare_step`, `_execute_db_compare_step`). The pre-existing
    `_execute_reconcile_step` is reused as-is (its signature already matched
    the handler shape). Per-handler bodies are byte-for-byte the prior logic.
  - Added `Callable` to the typing import for the registry's return annotation.
- `tests/unit/test_etl_pipeline_runner.py`: three new tests under
  `TestExecuteStep` —
  - `test_step_dispatch_goes_through_registry` (registry is the type source of
    truth; keys = the five documented types; values are callables),
  - `test_register_new_step_type_without_editing_switch` ("add, don't
    edit-the-switch": injecting a registry entry is enough to dispatch),
  - `test_unknown_step_type_message_lists_all_registered_types` (the
    `ValueError` advertises every registered type).

## 2. Out of scope / deferred (with follow-up note)
- **Report-format registry.** The issue *title* mentions report formats, but the
  issue **Scope** narrows this story to `_execute_step` only ("Keep it minimal;
  this is a refactor, not new step types"). Report-format selection was left
  untouched to honour the scope. Follow-up: if a report-format registry is still
  wanted, raise a separate small story; the runner refactor here is independent
  of it.

## 3. AGENTS.md compliance
- src/ touched? **Yes** — `src/pipeline/etl_pipeline_runner.py`. This is a
  **core** arch-review change driven by the review (R-11), via the normal story
  flow — **not** harness work — so hard rule #1 (no harness `src/` changes) does
  not apply and no carve-out/ADR is required. Pure refactor: no behaviour change
  for existing step types (all five dispatch identically; unknown-type still
  raises `ValueError`).
- Secrets: none added. Audit table (`AUDIT.VALDO_RUN_FAILURES`): not mutated.
  Quick actions: none used in any GitLab body. No `print()`; no
  `os.environ.get()` added.

## 4. ADR(s)
- None. R-11 is a small internal refactor with no new dependency, config format,
  or load-bearing decision (the review pre-authorises the registry direction).

## 5. Verification (actual results)
- black --check (changed files): no NEW deviations vs HEAD (HEAD already
  reports these two files as "would reformat"; the diff in findings is zero).
- flake8 (changed files): no NEW findings vs HEAD (identical finding set:
  pre-existing E501 at runner:11 + test:297/385 and the pre-existing unused
  `yaml` import in the test file).
- mypy src/: no NEW findings vs HEAD (pre-existing 106 errors across 34 files;
  the only runner line is the pre-existing PyYAML-stub note).
- Targeted: `pytest tests/unit/test_etl_pipeline_runner.py --no-cov` →
  **46 passed, 1 warning** (incl. the 3 new tests).
- Full unit+integration (`pytest tests/unit tests/integration --no-cov`) →
  **43 failed, 2743 passed, 12 skipped** — failure count == documented baseline
  (43), all in environmental buckets A–G; **zero** failures attributable to
  R-11.
- Harness offline subset: **267 passed, 1 skipped** — matches the documented
  baseline exactly.
- Live-Oracle tests: SKIPPED (no SIT) — expected.

## 6. Docs updated
- docs/architecture.md (mode-parity section: runner now notes the registry-based
  step dispatch + R-11).
- CHANGELOG.md ([Unreleased] / Changed — registry-driven step dispatch, R-11;
  CRLF-safe edit, verified clean CRLF).
- docs/handover/ARCH_REVIEW_STATUS.md (rolling pointer → next story).
- This handoff doc.

## 7. Acceptance criteria
- [x] Step dispatch goes through a registry; all existing step types behave
      identically.
- [x] Adding a step type no longer edits a switch body.
- [x] Suite green; `black`/`flake8`/`mypy` clean (no NEW findings vs HEAD on
      changed files; per the documented baseline policy).

## 8. Next story
- #41 — R-12 — Baseline-promotion policy + drift guard (Wave 3). **DECISION
  story** (`<stop_and_ask_first>`): deliver the baseline-promotion policy
  write-up/ADR and STOP for the human answer before implementing the drift
  guard. No code-dependency blockers otherwise.
