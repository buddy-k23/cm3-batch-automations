# Arch-Review Story Handoff — R-06a (#36)

**Generated:** 2026-06-08
**Story:** Introduce a record-reader strategy seam (fixed-width default)
**Predecessor:** docs/handover/ARCH_REVIEW_R-09_HANDOFF.md
**Trunk:** feature/valdo-engine-v3 (commit c3175ea, pushed: yes)
**Safety snapshot:** arch-review-snapshot/R-06a @ aaff8c0 — deleted after green: yes
**Depends-on satisfied:** none (Wave 3 head)

## 1. What shipped (scope delivered)

- `src/validators/multi_record_reader.py` — introduced the field-strategy seam:
  - New `RecordFieldStrategy` `typing.Protocol` (single method
    `extract(line, disc) -> str`).
  - New `FixedWidthFieldStrategy` default implementation carrying the historical
    1-indexed `position`/`length` slice verbatim.
  - Module-level shared `_DEFAULT_FIELD_STRATEGY` instance (stateless).
  - `read_multi_record_file` gained an optional `field_strategy` parameter
    (defaults to the fixed-width strategy); threaded through `_iter_rows` and
    `_dispatch_row`, which now call `strategy.extract(...)`.
  - `_extract_discriminator` retained as a thin shim delegating to the default
    strategy (single implementation; no drift; back-compat for importers).
- `tests/unit/test_multi_record_reader.py` — added 7 seam tests:
  byte-identical SHA equivalence (default vs explicit fixed-width), `None`
  fall-back, shim equivalence, short-line empty, a custom strategy that proves
  the reader is format-agnostic below the seam, Protocol conformance, and a
  SHAW-fixture stability check (skips cleanly if the fixture is absent).
- `docs/adr/0014-record-reader-strategy-seam.md` — new ADR (Proposed).
- `CHANGELOG.md` — `[Unreleased] / Added` entry.

## 2. Out of scope / deferred (with follow-up note)

- The delimited `RecordFieldStrategy` implementation itself — **R-06b (#37)**.
- Generalising `scripts/e2e_lib/multi_record_file_parser._slice_fields` (the
  full per-record-type field-set slicer) for delimited input — deferred to #37
  (noted in ADR 0014 §Consequences and §Open questions).

## 3. AGENTS.md compliance

- `src/` touched? **Yes** — ADR 0014 authorizes it. This is a **core** change
  via the normal ADR flow (R-06a is classified core, like R-01x), not
  harness work, so hard rule #1's harness carve-out does not apply.
- Secrets: none added. Audit table: not mutated. Quick actions: none used.
- CRLF/BOM-safe edits used for the CRLF source/test/CHANGELOG files (per §9
  gotchas); ADR is a new LF file.

## 4. ADR(s)

- ADR 0014 — Record-Reader Field-Strategy Seam — **Proposed** (flip to Accepted
  on merge).

## 5. Verification (actual results)

- black: PASS (`src/validators/multi_record_reader.py`,
  `tests/unit/test_multi_record_reader.py`).
- flake8: PASS — the single E501 in the test file is **pre-existing at HEAD**
  (the `test_line_shorter_than_discriminator` docstring), not introduced here;
  the pre-existing `F401 'typing.List' unused` at HEAD is now resolved (the new
  tests use `List`).
- mypy: `multi_record_reader.py` clean (the 5 reported errors are pre-existing
  environmental: missing `pandas`/`azure` stubs and a `secrets.py` dict typing
  issue in unrelated files).
- pytest (full unit+integration, `--no-cov`): **43 failed, 2700 passed, 12
  skipped** — failure count equals the documented baseline (43), all in buckets
  A–G; **zero NEW failures**. Passed up from baseline 2664 (added tests across
  the series + this story's +7).
- Targeted: `test_multi_record_reader.py` + `test_multi_record_validator.py` →
  76 passed.
- Harness offline subset (+ reader/validator): 343 passed, 1 skipped.
- Live-Oracle tests: SKIPPED (no SIT) — expected.

## 6. Docs updated

- `docs/adr/0014-record-reader-strategy-seam.md` (new)
- `CHANGELOG.md`
- `docs/handover/ARCH_REVIEW_STATUS.md` (rolling pointer)
- This handoff.

## 7. Acceptance criteria

- [x] Reader behaviour byte-identical for existing SHAW fixtures (SHA-per-row
      equivalence test, default vs explicit fixed-width strategy; validator
      suite unchanged).
- [x] Strategy seam exists with fixed-width as default.
- [x] ADR added; suite green.

## 8. Next story

- #37 — R-06b — Delimited multi-record reader strategy (depends: #36, now DONE).
  No pending decision blocks it; it builds the delimited `RecordFieldStrategy`
  on top of this seam.
