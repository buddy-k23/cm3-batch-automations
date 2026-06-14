# Arch-Review Story Handoff — R-10b (#34)

**Generated:** 2026-06-08
**Story:** Add a mapping↔baseline version-pinning test
**Predecessor:** docs/handover/ARCH_REVIEW_R-10a_HANDOFF.md
**Trunk:** feature/valdo-engine-v3 (commit c6fb243, pushed: yes)
**Safety snapshot:** arch-review-snapshot/R-10b @ 661c66f — deleted after green: yes
**Depends-on satisfied:** #33 (R-10a) — DONE (handoff + issue closed)

## 1. What shipped (scope delivered)

- `scripts/e2e_lib/baseline_resolver.py`:
  - New module-level `read_mapping_version(mapping_path)` — dependency-free
    reader of the live mapping version (`version` or `mapping_version`,
    defaulting to `"unversioned"`); the read-side counterpart to
    `promote_baseline.resolve_mapping_version`, kept here so the resolver does
    not import the promote tool.
  - New `BaselineResolver.resolve_for_mapping(env, source, file_type,
    mapping_path)` — the enforcement point for the mapping↔baseline pinning
    contract. Reads the live mapping version, resolves the baseline pinned to
    *that* version via `find(...)`, then cross-checks the resolved entry and
    raises `BaselineResolverError` (actionable message pointing at
    `scripts/promote_baseline.sh`) if the pin disagrees. No more silent
    comparison against a stale baseline when a mapping is bumped without
    refreshing its baseline.
- `tests/unit/test_e2e_baseline_resolver.py`:
  - `TestReadMappingVersion` (7 cases: `version`, `mapping_version` alias,
    precedence, unversioned default, missing file, malformed JSON, non-object).
  - `TestResolveForMapping` (matching, matching-newer, mismatch fails fast,
    mismatch caught even if `find` is monkeypatched to relax its key,
    unversioned pin, missing mapping file).
- `docs/CONFIG_SCHEMA_REGISTRY.md`: ticked the R-10b follow-up checkbox and
  documented the delivered enforcement.
- `CHANGELOG.md [Unreleased] > Added`: R-10b entry.

## 2. Out of scope / deferred (with follow-up note)

- The other registry follow-ups (Source YAML / `paths.yml` `schema_version`
  checks, Umbrella/Pipeline `extra="forbid"`, routing `ConfigLoader.load_mapping`
  through `MappingConfig.from_file`) remain open checkboxes in the registry —
  they were never part of R-10b's scope (the issue scopes R-10b to the
  mapping↔baseline pin only). Left as-is.
- Wiring `resolve_for_mapping` into the live L3 compare call site is not part of
  this story (the issue asks for the enforceable contract + test); the existing
  `find()`-based L3 path already fails fast on a missing pin. Follow-up if a
  call site should adopt the stronger cross-check.

## 3. AGENTS.md compliance

- `src/` touched? **No.** Harness-only change (`scripts/e2e_lib/` + tests +
  docs), satisfying hard rule #1 (no `src/` changes for harness work).
- Secrets: none added. Audit table: not mutated. Quick actions: none used.

## 4. ADR(s)

- None. No new config format or dependency; pure additive primitive.

## 5. Verification (actual results)

- black: PASS (changed files) | flake8: PASS | mypy: PASS
  (`scripts/e2e_lib/baseline_resolver.py` clean; `mypy src/` unchanged
  pre-existing baseline — no `src/` files touched).
- pytest (targeted `test_e2e_baseline_resolver.py`): **33 passed** (was 23; +10).
- pytest `tests/unit tests/integration --no-cov`: **43 failed, 2693 passed,
  12 skipped** — failure count == documented baseline (43), all in buckets A–G;
  zero NEW failures attributable to this story.
- harness offline subset: PASS.
- Live-Oracle tests: SKIPPED (no SIT) — expected.

## 6. Docs updated

- docs/CONFIG_SCHEMA_REGISTRY.md
- CHANGELOG.md
- docs/handover/ARCH_REVIEW_STATUS.md (rolling pointer)
- docs/handover/ARCH_REVIEW_R-10b_HANDOFF.md (this file)

## 7. Acceptance criteria

- [x] A version mismatch produces a deterministic, actionable error (not a
      silent wrong-baseline comparison) — `resolve_for_mapping` raises
      `BaselineResolverError` with a `promote_baseline.sh` remediation hint.
- [x] Test covers both the matching and mismatching case —
      `TestResolveForMapping` covers both (plus edge cases).
- [x] `black`/`flake8`/`mypy` clean.

## 8. Next story

- #35 — R-09 — Document the two DB-compare engines (depends: none). No pending
  decision blocks it. (#34's only dependency, #33, is DONE.)
