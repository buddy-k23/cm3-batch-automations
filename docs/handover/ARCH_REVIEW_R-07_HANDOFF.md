# Arch-Review Story Handoff — R-07 (#38)

**Generated:** 2026-06-09
**Story:** Generalise trigger-file / path naming away from CA-ESP specifics
**Predecessor:** docs/handover/ARCH_REVIEW_R-06b_HANDOFF.md
**Trunk:** feature/valdo-engine-v3 (commit 8c63c23, pushed: yes)
**Safety snapshot:** arch-review-snapshot/R-07 @ 70f076e — deleted after green: yes
**Depends-on satisfied:** none

## 0. Decision recorded (this was a `<stop_and_ask_first>` story)

Trigger-file naming must be confirmed with the user before encoding (AGENTS.md).
The audit + open questions were delivered and the user approved the proposed
defaults:
- **Trigger contract default unchanged:** `<datafile>.trigger`, data filename on
  line 1; per-source override of `suffix` / `data_file_line` added.
- **`filename_patterns`:** global default in `paths.yml`, **per-source override
  replaces** the global list (no merge).
- **Scope:** both per-source `filename_patterns` and trigger `suffix`/`data_file_line`.
- **Schema:** per-source pattern block enforces the same named-group contract,
  is load/use-validated, and is registered in `docs/CONFIG_SCHEMA_REGISTRY.md`.

## 1. What shipped (scope delivered)

- `scripts/e2e_lib/path_resolver.py`:
  - Extracted the pattern compile/validate loop into a shared module-level
    `_compile_patterns(patterns, context)` (used by both the global block and
    per-source overrides — identical contract, no drift).
  - `classify_filename(filename, *, source=None)` — when `source` declares a
    `filename_patterns` overlay, those patterns **replace** the global set for
    that source; otherwise the global patterns apply (unchanged default).
    Per-source compiled patterns are cached.
  - New `trigger_config(source=None)` — resolves `{suffix, data_file_line}` from
    the global `trigger_file` block with optional per-source override (per-key
    fallback). Validates non-empty string suffix and integer `data_file_line` ≥ 1.
- `scripts/e2e_lib/watch.py`:
  - `poll_once` now discovers triggers **per owning source**: globs each source's
    trigger dir with that source's `suffix` and classifies with that source's
    patterns (`classify_triggers(..., source=...)`).
  - `_trigger_dirs_for_known_sources` → `_trigger_dirs_by_source` returning
    `(source, dir)` pairs.
  - `TriggerEvent` gained `owning_source` + a `run_source` property; routing /
    `deduplicate_by_source` now key on `run_source` so a custom pattern that
    captures a producer-specific `source` token still runs the correct harness
    source.
  - `from_paths` global trigger defaults now come from `resolver.trigger_config()`.
- `config/e2e/paths.yml`: documented the per-source override for both
  `filename_patterns` and `trigger_file` (comments only; no behaviour change).
- Tests: `tests/unit/test_e2e_path_resolver.py` (+14: per-source patterns
  replace/cache/validation, trigger_config global+override+partial+bad) and
  `tests/unit/test_e2e_watch.py` (+4: custom suffix+pattern routes a run, wrong
  suffix ignored, global pattern rejected under override, existing sources
  unaffected).

## 2. Out of scope / deferred (with follow-up note)

- SHAW was deliberately **not** given an override — it keeps the global patterns
  and `.trigger` contract, so its behaviour is byte-identical. Authoring an
  example non-CA-ESP source overlay is left for when a real one is onboarded.
- No CLI/wizard affordance to author per-source `filename_patterns`; config is
  hand-authored YAML today.

## 3. AGENTS.md compliance

- `src/` touched? **No.** This is harness work: `scripts/e2e_lib/` + `config/`
  only (hard rule #1 satisfied without a carve-out).
- Secrets: none. Audit table: not mutated. Quick actions: none.
- Decision story: trigger naming was confirmed with the user before encoding
  (hard rule "do not guess on trigger-file naming").
- CRLF/BOM-safe edits used for the CRLF test file (`test_e2e_watch.py`); the
  other touched files are LF.

## 4. ADR(s)

- None. This is a config-surface generalisation within the existing
  `path_resolver` schema; no new architectural decision beyond the documented
  override semantics (captured in `docs/CONFIG_SCHEMA_REGISTRY.md`).

## 5. Verification (actual results)

- black: PASS (`path_resolver.py`, `watch.py`, both test files).
- flake8: PASS on `path_resolver.py` / `watch.py` (zero findings). The
  `test_e2e_watch.py` F401/W605 and `test_e2e_path_resolver.py:70` E501 are
  **pre-existing at HEAD** (verified via `git show HEAD:`), not introduced here.
- mypy (`--follow-imports=silent --ignore-missing-imports`): `watch.py` clean;
  the lone `path_resolver.py` yaml-stub error is pre-existing/environmental
  (`types-PyYAML` not installed).
- pytest `tests/unit tests/integration --no-cov`: **43 failed, 2740 passed, 12
  skipped** — failure count equals the documented baseline (43), all buckets
  A–G, **zero NEW failures** (none in path_resolver/watch). Passed up from the
  prior story (added +18 R-07 tests).
- Targeted: `test_e2e_path_resolver.py` + `test_e2e_watch.py` → 80 passed.
- Harness offline subset: **267 passed, 1 skipped** — matches baseline.
- Live-Oracle tests: SKIPPED (no SIT) — expected.

## 6. Docs updated

- `docs/CONFIG_SCHEMA_REGISTRY.md` (Source YAML + paths.yml rows: R-07 overrides)
- `config/e2e/paths.yml` (override documentation comments)
- `CHANGELOG.md` ([Unreleased] / Added)
- `docs/handover/ARCH_REVIEW_STATUS.md` (rolling pointer)
- This handoff.

## 7. Acceptance criteria

- [x] Producer-specific naming is config-driven, not hard-coded (per-source
      `filename_patterns` + `trigger_file` overrides; global `paths.yml`
      patterns remain the default).
- [x] SHAW behaviour unchanged (no override declared; 80 targeted tests +
      full suite green at baseline).
- [x] New config keys documented; the trigger-naming open question was raised
      and confirmed with the user before encoding.

## 8. Next story

- #39 — R-08 — Periodic AGENTS.md carve-out audit (no deps; Wave 3). Not a
  decision story; no blocker pending.
