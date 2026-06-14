# Arch-Review Story Handoff — R-10a (#33)

**Generated:** 2026-06-07
**Story:** [arch-review][R-10a] Publish a config-schema registry document
**Predecessor:** docs/handover/ARCH_REVIEW_R-05_HANDOFF.md
**Trunk:** feature/valdo-engine-v3 (commit 6703c8d, pushed: yes)
**Safety snapshot:** arch-review-snapshot/R-10a @ 77fd4d2 — deleted after green: yes
**Depends-on satisfied:** none (R-10a has no dependencies; R-10b/#34 depends on this)

## 1. What shipped (scope delivered)
- `docs/CONFIG_SCHEMA_REGISTRY.md` — the single authority enumerating every
  config artifact → schema authority (module/class) → load-validated status:
  - Mapping JSON (`MappingConfig`), Rules JSON (`RulesConfig`), Rules CSV/XLSX
    (`BARulesTemplateConverter`/`RulesTemplateConverter`), umbrella YAML
    (`MultiRecordConfig`), reconcile YAML (`reconciliation_spec.load_spec`),
    source YAML (`path_resolver.source_config`), `paths.yml`
    (`PathResolver._validate_and_compile`), pipeline YAML (`PipelineDefinition`).
  - Adjacent config (suites, pipeline profiles, GE CSVs, `ui.yml`) listed for
    completeness.
  - A validation-strength legend and a §3 Follow-ups checklist flagging every
    artifact lacking a fail-fast loader (feeds R-10b / #34).
- `docs/architecture.md` — new "Configuration plane" section cross-linking the
  registry.
- `AGENTS.md` — "When in doubt" now points at the registry for any config work.
- `CHANGELOG.md` [Unreleased] → Added: registry entry.

## 2. Out of scope / deferred (with follow-up note)
- Implementing the missing validators / tightening loaders is **R-10b (#34)**.
  Flagged items (in the registry §3): source YAML + `paths.yml` ignore their
  declared `schema_version: 1`; umbrella + pipeline YAML use pydantic default
  `extra="ignore"` (silent typo drop); `ConfigLoader.load_mapping` bypasses
  `MappingConfig`; mapping↔baseline version-pinning test.

## 3. AGENTS.md compliance
- src/ touched? **No** — docs only (`docs/`, `AGENTS.md`, `CHANGELOG.md`).
- Secrets: none added. Audit table: not mutated. Quick actions: none used.
- CRLF-safe edits used for `AGENTS.md` and `CHANGELOG.md` (both CRLF); LF
  `edit_file` used for the new doc and `docs/architecture.md` (LF).

## 4. ADR(s)
- none (documentation story, no decision).

## 5. Verification (actual results)
- Docs-only change (no `.py` touched), so black/flake8/mypy have nothing to
  evaluate in the diff and cannot introduce new findings. Pre-existing tree-wide
  black/flake8/mypy findings at HEAD are unchanged (per session-2 baseline).
- harness offline subset: **267 passed, 1 skipped** (matches baseline).
- Full suite not re-run: docs-only change cannot affect test outcomes; baseline
  remains 43 pre-existing environmental failures (no regression).
- Live-Oracle tests: SKIPPED (no SIT) — expected.

## 6. Docs updated
- docs/CONFIG_SCHEMA_REGISTRY.md (new)
- docs/architecture.md (Configuration plane cross-link)
- AGENTS.md (When in doubt cross-link)
- CHANGELOG.md ([Unreleased] Added)

## 7. Acceptance criteria
- [x] Registry doc lists every config artifact with its schema authority and validation status.
- [x] Cross-linked from `docs/architecture.md` and AGENTS.md "When in doubt".

## 8. Next story
- #34 — R-10b — mapping↔baseline version-pinning test (depends #33, now satisfied).
  No decision pending.
