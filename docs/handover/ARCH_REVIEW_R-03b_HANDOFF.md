# Arch-Review Story Handoff — R-03b (#29)

**Generated:** 2026-06-04
**Story:** [arch-review][R-03b] Implement the L2-regeneration decision
**Predecessor:** docs/handover/ARCH_REVIEW_R-03a_HANDOFF.md
**Trunk:** feature/valdo-engine-v3 (commit 3b46c92, pushed: yes)
**Safety snapshot:** arch-review-snapshot/R-03b @ d6ff1f9 — deleted after green: yes
**Depends-on satisfied:** #28 / R-03a (ADR 0012 Accepted — Option B chosen by user)
**Option A backlog item:** #46

## 1. What shipped (scope delivered)
Implemented **Option B (retire the `L2_regeneration` gate)** per ADR 0012.

- `scripts/e2e_lib/run_source.py`: removed `L2_regeneration` from
  `_OUTPUT_PHASE_GATES`; deleted the `ENV_ENABLE_L2`/`VALDO_E2E_ENABLE_L2`
  constant and the run-time skip branch; removed the `L2` entries from
  `_layer_for_gate` and `_error_type_for_gate`; rewrote the module docstring
  ("L2 Valdo gap" + `TODO(valdo-gap)` → an "Output-truth gates" section).
- `scripts/generate_pipeline_yaml.py`: dropped `L2_regeneration` from
  `_VALDO_RUNNABLE_GATES` and `_build_gate`; deleted `_steps_l2_regeneration`;
  removed the now-orphaned `regenerated_path` field, `work_root_template`
  resolution, and `run_id_token` (output source-def `input_path` is now `''`).
- `scripts/build_rollup_index.py`: dropped the `L2_regeneration → L2` mapping.
- `scripts/e2e_lib/split_pipeline.py`: docstring updated (gate list, two→one
  output gate).
- `scripts/e2e_lib/failure_sink.py`: `_VALID_LAYERS` now `{None, L1, L2b, L3}` —
  dropped the retired `L2` and **added the live `L2b`** (latent gap: L2b failure
  rows would previously have been rejected; this fixes it).
- `scripts/run_e2e_source.sh`, `scripts/run_e2e_all.sh`: removed
  `VALDO_E2E_ENABLE_L2` documentation lines.
- `config/e2e/sources/{SHAW,SRC_A}.yml`: removed the `L2_regeneration` gate row.
- `config/e2e/pipelines/{ait/SHAW, sit/SHAW, sit/SRC_A}.pipeline.yaml`:
  regenerated (L2 gate + regenerated-file source-def paths gone).
- `tests/fixtures/e2e_pipelines/SRC_A.sit.golden.yaml`: regenerated to match.
- Tests updated: `test_generate_pipeline_yaml.py`, `test_e2e_run_source.py`
  (the skip test became `test_l2_regeneration_gate_is_retired`; removed the
  obsolete `TestL2OptIn`), `test_e2e_split_pipeline.py`,
  `test_e2e_build_rollup_index.py`, `test_e2e_promote_baseline.py`,
  `test_e2e_run_all_wrapper.py`, `test_e2e_watch.py`,
  `test_e2e_failure_sink.py` (sample layer `L2`→`L2b`).
- Docs: ADR 0012 (Proposed→Accepted, Option B + decision outcome), ADR 0008
  note, `prompts/e2e_batch_testing_README.md`, `prompts/e2e_batch_testing_prompt.md`,
  `docs/Valdo-Infographic.html` (L2 cards + gate-chain), `CHANGELOG.md`.

## 2. Out of scope / deferred (with follow-up note)
- **Option A (ship `valdo regenerate`)** is retained as a future backlog item
  per the user's request (a *mapping-driven* re-derivation and/or an ad-hoc CLI).
  Tracked in the backlog issue filed alongside this story; ADR 0012 §"Decision
  outcome" records the rationale and the sizing caveat.
- The generator also *could* emit `config/e2e/pipelines/ait/SRC_A.pipeline.yaml`
  (not previously tracked); deliberately **not** added here (out of scope — it
  would introduce a new committed artifact unrelated to the L2 retirement).
- Two historical infographic narrative mentions of `L2_regeneration` (the
  "direction taken" KT card and an "opt-in shape" aside) are left as historical
  record; broad infographic/prompt drift is owned by R-15 (#44).

## 3. AGENTS.md compliance
- src/ touched? **No.** Pure harness (`scripts/` + `config/`) + tests + docs.
  `oracle_expected_generator` (src/) is untouched.
- The `failure_sink._VALID_LAYERS` edit is a `scripts/e2e_lib/` change, not a
  `src/` change — no ADR carve-out needed.
- Secrets: none. Audit table: schema/rows not mutated (only the harness-side
  layer-validation set). Quick actions: none used. Explicit-path commit.

## 4. ADR(s)
- `docs/adr/0012-l2-regeneration-disposition.md` — **Accepted (Option B)**.

## 5. Verification (actual results)
- Full suite: **43 failed, 2676 passed, 12 skipped** — failure count + buckets
  match the documented baseline (api_files, api_system, alembic_install,
  chunked_validator_stats, downloader_service, strict_mode_parity, trend_service,
  web_ui, workflow_engine). No new failures; no e2e/L2 failures.
- Coverage: **80.23%** — "Required test coverage of 80% reached."
- Harness offline subset: **267 passed, 1 skipped**.
- black/flake8/mypy: every finding on the changed files is **pre-existing at
  HEAD** (verified per-file by diffing HEAD vs working tree); this story
  introduced **no new** black/flake8 findings, and `mypy src/` is unchanged
  (227 errors at HEAD and now — no `src/` edits). Degraded dev-box baseline per
  `docs/handover/ARCH_REVIEW_TEST_BASELINE_2026-06-03.md`.
- Live-Oracle (SIT) tests: SKIPPED (no SIT) — expected.

## 6. Docs updated
- `docs/adr/0012-l2-regeneration-disposition.md`, `docs/adr/0008-...md`,
  `prompts/e2e_batch_testing_README.md`, `prompts/e2e_batch_testing_prompt.md`,
  `docs/Valdo-Infographic.html`, `CHANGELOG.md`.

## 7. Acceptance criteria
- [x] The `TODO(valdo-gap)` for L2 is resolved (removed with the gate).
- [x] Orchestrator unit tests updated; suite green (no new failures vs baseline).
- [x] "Retire" path: no dangling **live** references to `L2_regeneration`
      (remaining mentions are ADR 0012, historical handoffs, and explicit
      "retired" notes / negative test assertions).

## 8. Next story
- #32 — R-05 — Empty/header-only-batch gate semantics. **DECISION story**
  (`<stop_and_ask_first>`): deliver the write-up/ADR, then STOP for the user's
  call on the empty/header-only-batch assertion semantics before implementing.
