# Arch-Review Story Handoff — R-01c (#25)

**Generated:** 2026-06-03
**Story:** Decouple expected-SQL dialect from the comparator (spike + seam)
**Predecessor:** docs/handover/ARCH_REVIEW_R-01b_HANDOFF.md
**Trunk:** feature/valdo-engine-v3 (commit <set on push>, pushed: yes)
**Safety snapshot:** arch-review-snapshot/R-01c @ 8e544e7 — deleted after green: yes
**Depends-on satisfied:** #23 (R-01a), #24 (R-01b)

## 1. What shipped (scope delivered)
- **Dialect-coupling inventory** added to `docs/adr/0010-...md` (new "R-01c"
  section): a table of every Oracle-dialect assumption in the L2b SQL
  (quoted identifiers `AS "LN-NUM-ERT"`, `TRIM`/`LPAD`/`TO_CHAR`, CTAS-not-view
  materialization, `q'[...]'` quoting, `ORA-00955` PL/SQL traps, `app_int.`
  schema qualifier) and where each lives. Key finding: the comparator **engine**
  is already dialect-neutral; all coupling is in the checked-in `.sql`.
- **Minimal seam** in `scripts/e2e_lib/db_truth_comparator.py::reconcile` — new
  optional `dialect: Optional[SqlDialect] = None` parameter (imported from
  `src.database.truth_source`). Resolves `effective_dialect = dialect or
  ORACLE_DIALECT`. **Behaviour-neutral**: nothing branches on it yet; it is the
  single documented consumption point a future backend's dialect flows into.
- **Tests** (`tests/unit/test_e2e_db_truth_comparator.py::TestDialectSeam`):
  - default vs explicit `ORACLE_DIALECT` produce identical reports;
  - a custom `SqlDialect(name="postgres")` is accepted without changing the
    Oracle-shaped result;
  - a `@pytest.mark.skip` placeholder documenting the second-backend extension
    point (P3) — replace with a real fixture when a concrete backend lands.

## 2. Out of scope / deferred (with follow-up note)
- A real non-Oracle adapter (PostgreSQL/Parquet) + backend-dialect
  `expected_*.sql` — deferred until a concrete consumer exists (ADR 0010 open
  question). This story only establishes the seam + inventory. **R-01 (R-01a/b/c)
  is now complete.**

## 3. AGENTS.md compliance
- src/ touched? **No.** Edits are in `scripts/e2e_lib/db_truth_comparator.py`
  (harness) + ADR/tests; it consumes the R-01a `src/` seam. Hard rule #1 OK.
- Secrets: none. Audit table: untouched. Quick actions: none.
- CRLF/BOM (§9): `db_truth_comparator.py` is CRLF and the test file is BOM+CRLF;
  both were patched with CRLF/BOM-safe scripts (not `edit_file`), then black;
  BOM + CRLF verified preserved post-format.

## 4. ADR(s)
- ADR 0010 updated (R-01c section). Status remains **Proposed** (flips to
  Accepted with the others under R-16).

## 5. Verification (actual results, baseline policy)
- Targeted: `test_e2e_db_truth_comparator.py` (26 passed, 1 skipped — the
  documented extension placeholder) + `test_truth_source.py` (8) = **34 passed,
  1 skipped**.
- black --check (changed files): PASS. flake8 (changed files): only the
  documented **pre-existing** F401s in `db_truth_comparator.py`
  (`typing.Sequence`, `reconciliation_spec.FieldSpec`) — confirmed present on
  the snapshot, owned by R-16; the test file is flake8-clean. No NEW lint.
- mypy: the added `Optional[SqlDialect]`/`effective_dialect` are well-typed; the
  only mypy output is the pre-existing `yaml` stub + the `scripts.e2e_lib`
  double-module-name resolution note (baseline).
- Full unit+integration failure count unchanged vs. baseline (43, buckets A–G);
  this story adds **0** new failures.

## 6. Docs updated
- `docs/adr/0010-truthsource-backend-abstraction.md` (R-01c inventory + seam).
- `CHANGELOG.md`.

## 7. Acceptance criteria
- [x] ADR 0010 updated with the dialect-coupling inventory.
- [x] `dialect` hint exposed on the interface (`reconcile`'s param); Oracle path
      unchanged and green.
- [x] A skipped test documents the second-backend extension point.
- [x] black/flake8/mypy clean (no NEW issues vs. the documented baseline).

## 8. Next story
- #26 — R-02 — Align coverage gate scope with the core engine. No depends-on.
  Note: this is the story that addresses the pre-existing 78% < 80% coverage
  condition recorded in the baseline doc.
