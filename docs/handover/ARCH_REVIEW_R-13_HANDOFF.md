# Arch-Review Story Handoff — R-13 (#42)

**Generated:** 2026-06-11
**Story:** [arch-review][R-13] Link multi-record reports into the global rollup index
**Predecessor:** docs/handover/ARCH_REVIEW_R-12_HANDOFF.md
**Trunk:** feature/valdo-engine-v3 (commit cf05c54, pushed: yes)
**Safety snapshot:** arch-review-snapshot/R-13 @ f1bbee6 — deleted after green: yes
**Depends-on satisfied:** none

## 1. What shipped (scope delivered)

- `scripts/build_rollup_index.py` — added `MultiRecordLink` dataclass (file_type + index_html path).
- `scripts/build_rollup_index.py` — added `discover_multi_record_reports(source_dir)` function that
  scans `<source_dir>/multi_record/<file_type>/index.html` for every file-type subdirectory that
  contains a rendered `index.html`, returning results sorted by file_type.
- `scripts/build_rollup_index.py` — `SourceSummary` extended with `multi_record_reports` field
  (populated automatically by `load_source_summary` via `discover_multi_record_reports`).
- `scripts/build_rollup_index.py` — `to_summary_dict()` extended: each source entry now includes
  a `multi_record_reports` list of `{file_type, index_html}` dicts with relative paths.
- `scripts/build_rollup_index.py` — `render_index_html()` extended: Sources table gains a
  "Multi-record reports" column with relative hyperlinks per file type, or `&mdash;` when none.
- `tests/unit/test_e2e_build_rollup_index.py` — 17 new tests across two new classes:
  `TestDiscoverMultiRecordReports` (6 tests) and `TestMultiRecordLinkageIntegration` (11 tests).

## 2. Out of scope / deferred (with follow-up note)

- No changes to `run_source.py` or `render_multi_record_html.py` — the report paths are
  discovered from the filesystem, not from `summary.json`, so no schema change to the
  per-source summary is needed.
- No changes to `run_e2e_all.sh` — the rollup is invoked the same way.

## 3. AGENTS.md compliance

- src/ touched? No. Scripts-only change (AGENTS.md harness rule #1 satisfied).
- Secrets: none added. Audit table: not mutated. Quick actions: none used.

## 4. ADR(s)

- None required (scripts-only, no architectural decision needed).

## 5. Verification (actual results)

- black: PASS | flake8: PASS (2 pre-existing violations unchanged) | mypy: PASS (226 pre-existing errors, none new)
- pytest (unit only): 2737 passed, 12 skipped, 30 failed (all pre-existing environmental failures)
- harness offline subset: 267 passed / 1 skipped (matches baseline exactly)
- Live-Oracle tests: SKIPPED (no SIT) — expected

## 6. Docs updated

- `docs/handover/ARCH_REVIEW_R-13_HANDOFF.md` (this file)
- `docs/handover/ARCH_REVIEW_STATUS.md` (rolling pointer updated)
- `CHANGELOG.md [Unreleased]` (entry added)

## 7. Acceptance criteria

- [x] Global rollup links to every rendered multi-record report.
- [x] Graceful when a source has no multi-record report (no broken links — shows `&mdash;`).
- [x] Unit test for the linkage; `black`/`flake8` clean.

## 8. Next story

- #43 — R-14 — Audit for residual shell=True; no blockers/decisions pending.
