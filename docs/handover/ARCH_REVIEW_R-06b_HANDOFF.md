# Arch-Review Story Handoff — R-06b (#37)

**Generated:** 2026-06-09
**Story:** Implement a delimited (CSV/pipe) multi-record reader strategy
**Predecessor:** docs/handover/ARCH_REVIEW_R-06a_HANDOFF.md
**Trunk:** feature/valdo-engine-v3 (commit fe9f898, pushed: yes)
**Safety snapshot:** arch-review-snapshot/R-06b @ dd2cfe6 — deleted after green: yes
**Depends-on satisfied:** #36 (R-06a, seam) — DONE

## 1. What shipped (scope delivered)

- `src/config/multi_record_config.py` — extended `DiscriminatorConfig`
  additively for delimited mode:
  - `position`/`length` are now `Optional[int]` (required only in fixed-width
    mode); new optional `delimiter: Optional[str]`, `column: Optional[int|str]`,
    and `columns: List[str]`.
  - New `is_delimited` property (`delimiter is not None`).
  - A `model_validator(mode="after")` enforces mode consistency: fixed-width
    requires `position` + `length`; delimited requires `column` (a 1-indexed int
    or a name present in `columns`). Empty delimiter and zero/negative column
    index are rejected.
- `src/validators/multi_record_reader.py` — built the delimited strategy on the
  R-06a seam:
  - New `DelimitedFieldStrategy(delimiter, column_index)` implementing the
    `RecordFieldStrategy` protocol; splits the line and returns the trimmed
    selected field, with the same permissive short-line contract (empty string,
    no raise) as `FixedWidthFieldStrategy`.
  - New `field_strategy_for(config)` factory — the config-only selection point:
    returns the shared `FixedWidthFieldStrategy` for fixed-width configs and a
    freshly-built `DelimitedFieldStrategy` (column name resolved to a 0-indexed
    position) for delimited configs.
  - `read_multi_record_file` now auto-selects via `field_strategy_for(config)`
    when no explicit `field_strategy` is passed. An explicit strategy still
    overrides. The dispatch logic (first/last/match priority, blank-line skip,
    BOM/CRLF handling, O(1) look-ahead) is untouched and format-agnostic.
- `tests/unit/test_multi_record_reader.py` — added 30 tests: delimited dispatch
  by integer column, by named column, pipe delimiter, value trimming, unknown
  value, short-line, positional first/last in delimited mode, CRLF+BOM in
  delimited mode; `DelimitedFieldStrategy` protocol/unit behaviour; factory
  selection (fixed-width vs delimited, named-column resolution, explicit
  override); config-validation cases; and a fixed-width byte-identical
  regression guard with the seam present.

## 2. Out of scope / deferred (with follow-up note)

- Generalising `scripts/e2e_lib/multi_record_file_parser._slice_fields` (the
  full per-record-type *field-set* slicer, distinct from the discriminator
  extractor) for delimited per-type validation. R-06b generalises only the
  discriminator extraction (which record type a line is); the field-set slicer
  for delimited input remains a follow-up. Noted in ADR 0014 "Open questions".
- No changes to the multi-record wizard/CLI (`generate_multi_record`,
  `detect_discriminator`) to author delimited configs interactively — config is
  authored in YAML today; a wizard affordance is a separate enhancement.

## 3. AGENTS.md compliance

- `src/` touched? **Yes** — ADR 0014 (R-06a/R-06b) authorizes it as a **core**
  change via the normal ADR flow, not harness work (hard rule #1's harness
  carve-out does not apply).
- Secrets: none added. Audit table: not mutated. Quick actions: none used.
- CRLF/BOM-safe edits used for the CRLF files (`multi_record_reader.py`,
  `test_multi_record_reader.py`, `CHANGELOG.md`); `multi_record_config.py` and
  ADR 0014 are LF.

## 4. ADR(s)

- ADR 0014 — Record-Reader Field-Strategy Seam — **Proposed** (unchanged
  status; R-16/#45 owns ADR-status flips). Its "Open questions deferred" section
  updated to record that the `DiscriminatorConfig` delimited shape is resolved
  by R-06b and the field-set slicer remains a follow-up.

## 5. Verification (actual results)

- black: PASS (`multi_record_reader.py`, `multi_record_config.py`,
  `test_multi_record_reader.py`).
- flake8: PASS — the single E501 in the test file (line 258,
  `test_line_shorter_than_discriminator` docstring) is **pre-existing at HEAD**,
  not introduced here.
- mypy: clean on `multi_record_reader.py` and `multi_record_config.py` (the 5
  reported errors are pre-existing environmental: missing pandas/azure stubs and
  a `secrets.py` dict typing issue in unrelated files).
- pytest `tests/unit tests/integration --no-cov`: **43 failed, 2722 passed, 12
  skipped** — failure count equals the documented baseline (43), all in buckets
  A–G; **zero NEW failures** (none touch multi_record/config). Passed up from
  baseline 2664 (series + this story's +30).
- Targeted: `test_multi_record_reader.py` + `test_multi_record_validator.py` →
  98 passed.
- Harness offline subset: **267 passed, 1 skipped** — matches baseline.
- Live-Oracle tests: SKIPPED (no SIT) — expected.

## 6. Docs updated

- `docs/adr/0014-record-reader-strategy-seam.md` (open questions)
- `CHANGELOG.md` ([Unreleased] / Added)
- `docs/handover/ARCH_REVIEW_STATUS.md` (rolling pointer)
- This handoff.

## 7. Acceptance criteria

- [x] A delimited multi-record file validates + reconciles through the same
      `MultiRecordValidator` path, config-only selection (auto-selected via
      `field_strategy_for`; validator suite passes unchanged).
- [x] Fixed-width path unaffected (byte-identical regression test green;
      `test_multi_record_validator.py` unchanged).
- [x] `black`/`flake8`/`mypy` clean (on changed files; pre-existing baseline
      findings excepted).

## 8. Next story

- #38 — R-07 — Generalise trigger-file / path naming away from CA-ESP specifics.
  **Decision story** (`<stop_and_ask_first>`): trigger-file naming must be
  confirmed with the user before encoding. Deliverable for #38 is the
  producer-naming audit + open-question write-up, then STOP for the human
  decision before any implementation.
