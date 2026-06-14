# Arch-Review Story Handoff — R-09 (#35)

**Generated:** 2026-06-08
**Story:** Document the two DB-to-file compare engines' division of labour
**Predecessor:** docs/handover/ARCH_REVIEW_R-10b_HANDOFF.md
**Trunk:** feature/valdo-engine-v3 (commit ad3d5d8, pushed: yes)
**Safety snapshot:** arch-review-snapshot/R-09 @ 2412d66 — deleted after green: yes
**Depends-on satisfied:** none (R-09 has no dependencies)

## 1. What shipped (scope delivered)

- `docs/DB_COMPARE_ENGINES.md` (new) — a single doc distinguishing the two
  DB-to-file comparison engines:
  - `compare_db_to_file` (`src/services/db_file_compare_service.py`) — the
    production-core `db_compare` pipeline step / `valdo db-compare` CLI / UI
    DB-compare tab engine. Truth source = a caller-named Oracle query/table;
    whole-file structural+row diff via `run_compare_service`.
  - `db_truth_comparator.reconcile` (`scripts/e2e_lib/db_truth_comparator.py`) —
    the L2b SQL-truth gate engine. Truth source = per-record-type
    `expected_*.sql` views driven by a checked-in `ReconciliationSpec`;
    multi-record, key-indexed, cardinality + cross-type-assertion aware;
    returns a frozen `ReconciliationReport`.
  - Includes an at-a-glance comparison table, per-engine purpose/inputs/truth-
    source/when-to-use, a "why two, not one" rationale, and an explicit
    convergence position (see §below) plus one open team question.
- `docs/architecture.md` — new "DB-to-file comparison engines" subsection
  cross-linking `docs/DB_COMPARE_ENGINES.md` (R-09).
- `CHANGELOG.md [Unreleased] > Added` — R-09 entry (CRLF-safe patch; file is
  CRLF/no-BOM).

## 2. Out of scope / deferred (with follow-up note)

- No code change to either engine. The doc records an **open question** (should
  the `db_compare` pipeline step ever consume a `ReconciliationSpec`?) as a
  future-ADR candidate rather than deciding it — R-09 is documentation only.

## 3. AGENTS.md compliance

- `src/` touched? **No.** Docs-only (`docs/` + `CHANGELOG.md`). Satisfies hard
  rule #1.
- Secrets: none added. Audit table: not mutated. Quick actions: none used.

## 4. ADR(s)

- None. Documentation-only; no new config format, dependency, or decision.

## 5. Verification (actual results)

- black/flake8/mypy: **no NEW findings on changed files** — R-09 changed zero
  Python files (docs + CHANGELOG only). (`black --check` reports a pre-existing
  whole-tree formatting drift of 371 files under black 26.x; unrelated to this
  story and not introduced by it.)
- pytest `tests/unit tests/integration --no-cov`: **42 failed, 2694 passed,
  12 skipped** — all failures are in the documented environmental buckets
  (trend_service, web_ui, workflow_engine, api_files, api_system); **zero NEW**
  failures vs the documented 43-failure baseline (one fewer; environmental
  jitter). A docs-only change cannot affect tests.
- harness offline subset: **267 passed, 1 skipped** — matches baseline exactly.
- Live-Oracle tests: SKIPPED (no SIT) — expected.

## 6. Docs updated

- docs/DB_COMPARE_ENGINES.md (new)
- docs/architecture.md
- CHANGELOG.md
- docs/handover/ARCH_REVIEW_STATUS.md (rolling pointer)
- docs/handover/ARCH_REVIEW_R-09_HANDOFF.md (this file)

## 7. Acceptance criteria

- [x] A single doc clearly distinguishes the two engines and their use cases
      (`docs/DB_COMPARE_ENGINES.md`).
- [x] Cross-linked from `docs/architecture.md`.

## 8. Next story

- End of Wave 2. Next backlog entry: **#36 — R-06a — Record-reader strategy
  seam (fixed-width default)** (Wave 3, depends: none). R-06a is a **core**
  (`src/`) change via the normal ADR flow, not harness work. No pending
  decision blocks it.
