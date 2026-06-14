# SHAW Source Onboarding — Session Handover

**Date**: 2026-05-15 (resumed)
**Status**: Local work complete. RHEL-side deployment + smoke test pending.
**Reference**: `prompts/e2e_batch_testing_README.md` (5-step add-a-source checklist)

---

## Goal

Add a new E2E batch testing source **SHAW** to the Valdo harness. Work is
config + scripts only per AGENTS.md hard rule #1.

---

## Status at end of session

| # | Item | Status |
|---|---|---|
| 1 | Identity, schema/output overrides, gates locked in | Done |
| 2 | `config/e2e/sources/SHAW.yml` written | Done |
| 3 | `path_resolver.py` + tests for F1/F1b overrides | Done — 30/30 path-resolver tests pass |
| 3.1 | `generate_pipeline_yaml.py` one-line fix (`staging_schema` resolution) | Done — SRC_A golden unchanged |
| 4 | Pipeline YAMLs generated for sit + ait | Done — both `--check` clean |
| 4.1 | Pipeline-YAML multi-record dispatch (ADR 0005) | Done — ATOCTRAN L1 emits `validate_multi_record` |
| 4.2 | `bulk_convert_rules.py` BA-converter dispatch fix | Done — `kind`-aware routing |
| 4.3 | Strict-validator alignment with `prompts/generate-rules-csv.md` | Done — `R`/`CR` IDs + conditional `Expected / Values` |
| 4.4 | ATOCTRAN BA review of 14 CSVs at `mappings/csv/shaw_atoctran/` | Done (operator-signed-off 2026-05-15) |
| 4.5 | ATOCTRAN CSV→JSON conversion (7 mapping + 7 rules JSONs) | Done — 57 fields / 112 rules, all referenced by umbrella |
| 4.6 | First ATOCTRAN smoke run against real sample (125,903 rows) | Done — exposed Valdo-internal issues #12 and #13; ADR 0006 approved |
| 4.7 | ADR 0006 MR 1 — schema fix for issue #12 | **Done** — schema annotated `Dict[str, RecordTypeConfig]`; 3 new YAML-path coercion tests pass; 51/51 in `test_multi_record_validator.py` + 238 across the focused suite; SHAW smoke recipe no longer raises `AttributeError` and proceeds to issue #13's `Unknown operator` stream as expected |
| 4.8 | ADR 0006 MR 2 — operator predicates for issue #13 | **Done** — 8 new predicates in `field_validator.py` + 8 operator branches in `rule_engine.py` + shared `_date_formats.py`; 41 new tests pass (25 field validator + 13 rule engine + 3 integration round-trip); 286 across the full focused suite; SHAW ATOCTRAN smoke runs clean with zero `Unknown operator` lines and produces a real data-driven verdict |
| 4.9 | ATOCTRAN mapping JSON numeric-literal `.0` suffix | **Done** — fixed under [ADR 0007](../adr/0007-template-converter-preserve-string-literals.md): `dtype=str` added to both `pd.read_csv`/`pd.read_excel` boundaries in `TemplateConverter` *and* (defensively) `BARulesTemplateConverter`. 5 new regression tests across `test_template_converter_valid_values.py` (3) and `test_ba_rules_converter.py` (2). 7 ATOCTRAN mapping JSONs regenerated; spot check confirms `valid_values: ["100030"]` (no `.0`). SHAW smoke re-run shows the 213,398 `FW_VAL_001` count is gone |
| 4.10 | ATOCTRAN string-field length rule semantics (Part A) | **Done** — `scripts/generate_shaw_atoctran_csvs.py` now emits `length` (min..max) rules for string fields instead of `exact_length`, plus a `STRING_FIELD_MIN_LENGTH_OVERRIDES` table for operator-confirmed minimums (ACCT-NUM=14 for SHAW). Numeric and Date fields keep `exact_length`. CSVs regenerated; 7 ATOCTRAN rules JSONs reconverted via `bulk_convert_rules.py`. **SHAW ATOCTRAN smoke now passes**: `valid=True`, all 125,903 rows valid across rt_060/rt_200/rt_300/rt_700/rt_900, zero errors |
| 4.11 | Multi-record HTML report set (Part B) | **Done** — `scripts/render_multi_record_html.py` walks a multi-record validation result and writes an umbrella `index.html` (verdict, summary, per-record-type table) plus one detail subpage per non-empty record-type group via the existing `ValidationReporter`. Strictly `scripts/`-side; no `src/` changes. Tested against the SHAW ATOCTRAN smoke — generated 1 umbrella + 5 detail pages + 10 CSV sidecars at `reports/smoke/SHAW_ATOCTRAN/` |
| 4.12 | Generalize the SHAW CSV generator (refactor) | **Done** — extracted reusable core from `scripts/generate_shaw_atoctran_csvs.py` into `scripts/shaw_csv_gen/` package (6 modules: `__init__`, `config`, `helpers`, `valid_values`, `csv_builders`, `runner`). ATOCTRAN wrapper now 37 lines (was 655); regenerated CSVs are byte-identical to the operator-signed-off baseline (`git diff` shows zero changes). 77 tests pass. Cleared the path for the TRANERT and CONTACT generators |
| 4.13 | TRANERT multi-record umbrella (step 2) | **Done** — `scripts/generate_shaw_tranert_csvs.py` (37 lines using `shaw_csv_gen`). 12 CSVs generated (258 fields / 357 rules) from 6 layout sheets. Batch Header hand-authored (23 fields / 63 rules) per operator direction (separate record type, position-based matching, multiple-header scenarios supported). `config/mappings/SHAW_TRANERT.yaml` umbrella with discriminator `TRN-COD-ERT` at position 170/length 5, 8 record types (rt_32000 / rt_32001 → NEW1 sharing JSONs; rt_32005 / rt_32010 / rt_32025 / rt_32040 / rt_32075; batch_header position-based), `header_trailer_count` cross-type rule on `ITM-CNT-BRT`. rt_32030 (VR) and rt_32070 (CON) commented out pending BA layout-sheet authoring. `SHAW.yml` TRANERT entry flipped to `multi_record: true`. **Smoke: 4 of 7 record types PASS** (batch_header, rt_32000, rt_32075, and rt_32005 after BSA review item 4.14); cross-type reconciliation passes |
| 4.14 | BSA review of TRANERT rules CSVs | **Done (2026-05-25)** — senior BSA review of CUS/ORI/COD/CBRS rules + mappings against the spec workbook. Identified 18 corrections: 1 enumeration expansion (ECOA codes), 12 generator false positives (IF-condition values mined as output codes), 2 length-rule fixes (variable-payload signed decimals), 3 spec gaps. Generator hardening: `if 1st`/`if nth`/`is equal to` dynamic markers, single-lowercase-letter rejection in `_is_clean_code`, sequential-counter / field-comparison guards in `valid_values.py`. Two additive `src/` fixes under ADR 0006 scope: `validate_valid_values` and `validate_date_format` now skip NaN cells. Error counts dropped: rt_32005 220→0 (PASS), rt_32010 333→173, rt_32025 254→63 (-75%), rt_32040 1249→67 (-94%). All remaining errors are genuine BA review items (open-ended Metro 2 lookups, day-of-month enumerations, FW_ALIGN_002/FW_REQ_001 spec-vs-data mismatches) — no more generator false positives. HTML report regenerated at `reports/smoke/SHAW_TRANERT/` |
| 5 | Mark wrapper executable on RHEL | **Pending (RHEL-side)** |
| 6 | Smoke test in SIT | **Local smoke passes (ATOCTRAN 2026-05-18; TRANERT 4-of-7 record types as of 2026-05-25 BSA review); RHEL SIT smoke still pending** — ATOCTRAN umbrella `valid=True`; TRANERT umbrella structurally exercisable end-to-end with cross-type reconciliation working. RHEL run still required to confirm wrapper resolution and audit-table behavior |
| 7 | Promote first baseline | **Pending (after CONTACT + TRANERT umbrellas land and Java outputs available)** |

Aggregate test status at end of ADR 0007: **352 passed** across the
expanded focused suite — the ADR 0006 set (286) plus the converter and
parser regressions (`test_template_converter_valid_values.py` +6,
`test_ba_rules_converter.py` +2 for ADR 0007, plus
`test_mapping_parser.py`, `test_infer_mapping.py`, `test_validator.py`,
`test_strict_fixed_width.py`, `test_fix1_valid_values_trimming.py`,
`test_fixed_width_parser.py` exercised end-to-end with the new
``dtype=str`` read boundary). Five of those 352 are new for ADR 0007;
the rest are pre-existing tests that continue to pass after the fix.

Test status after TRANERT + BSA review (item 4.14): **92 passed** in
the targeted regression suite (`test_field_validator.py`,
`test_rule_engine.py`, `test_multi_record_validator.py`,
`test_ba_rules_converter.py`, `test_template_converter_valid_values.py`,
`test_bulk_convert_rules_validation.py`,
`tests/integration/test_ba_rules_round_trip.py`). The two additional
NaN-skip fixes in `field_validator.py` (`validate_valid_values` and
`validate_date_format`) are strictly additive — they reduce false
positives on optional fields with empty cells and do not change
behavior on populated cells. No new test failures.
The pre-existing test count line at end of ADR 0006 MR 2 was **286
passed** across the
focused suite — `test_multi_record_validator.py` (51, incl. 3 new for
ADR 0006 MR 1), `test_generate_pipeline_yaml.py`,
`test_e2e_path_resolver.py`, `test_e2e_split_pipeline.py`,
`test_etl_pipeline_runner.py`,
`test_bulk_convert_rules_validation.py`, `test_ba_rules_converter.py`,
`test_fix2_rules_parsing.py`, `test_api_rules_upload.py`,
`test_mapping_upload.py`, `test_api_validate_multi_record.py`,
`test_multi_record_transform_engine.py`,
`test_multi_record_wizard_service.py`, `test_generate_multi_record.py`,
plus the ADR 0006 MR 2 additions: `test_field_validator.py` (25 new),
`test_rule_engine.py` (13 new), `test_rule_engine_conditions.py`,
`test_rules_engine_happy.py`, and
`tests/integration/test_ba_rules_round_trip.py` (3 new). Pre-existing
Oracle-gated skips and the two Windows-only tempfile flakes in
`test_chunked_validator_stats.py` are excluded — neither is caused by
or related to ADR 0006.

---

## Decisions locked in

### Identity
- `source: SHAW`
- `release_tag: "2026.M06"`
- `staging_schema: "APP_INT"` (source-level override; same for sit and ait)
- `output_root: "/app/software/APPS/ftp/input/shaw"` (source-level override; same for sit and ait)

### Per-gate policy (Question 6)

| Gate | `blocking` | `invoke_java` |
|---|---|---|
| `load_step` | true | true |
| `file_to_staging` | true | false |
| `generate_step` | false | false |
| `L1_structural` | true | false |
| `L2_regeneration` | false | false |
| `L3_baseline_diff` | true | false |

Notes:
- `generate_step` disabled because SHAW has no Java generate script in this
  iteration.
- `L3_baseline_diff` blocking by design — will fail every run until step 5
  (first baseline promotion) is complete. Accepted by operator.
- `file_to_staging` blocking — will fail every run until mapping JSONs land.
  Accepted by operator (option (a) of the file_to_staging question).

### Load step — wrapper approach (Option A)
- **Repo**: `scripts/wrappers/load_SHAW.sh` (17 sqlload scripts wired)
- **Deploy path**: `/app/software/APPS/valdo/scripts/wrappers/load_SHAW.sh`
- **Input dir**: `/app/software/ftp/input/shaw` (default; `--input-dir` overridable)
- **Scripts dir**: `/app/software/APPS/interfaces/shaw/scripts` (`--scripts-dir` overridable)
- **Semantics**: per-file conditional skip; run-all-aggregate-fail-at-end;
  exit 1 on any sqlload failure; exit 3 on wrapper-level errors.
- **JSONL logging** to stderr in harness style.
- TODO on RHEL: `chmod +x /app/software/APPS/valdo/scripts/wrappers/load_SHAW.sh`
  (or `git update-index --chmod=+x scripts/wrappers/load_SHAW.sh` before deploy).

### Input files — 6 entries (this iteration)

| `file_type` | `glob` | `target_staging_table` (resolves under `APP_INT`) |
|---|---|---|
| `COLLATERAL_MASTER` | `collateral-master_*.txt` | `SHAW_COLLATERAL` |
| `FEE_MASTER` | `fee-master_*.txt` | `SHAW_FEE_MASTER` |
| `LOAN_MASTER` | `loans-master_*.txt` | `SHAW_LOAN_MASTER` |
| `LOANS_NAME` | `loans-name_*.txt` | `SHAW_LOANS_NAME` |
| `POSTED_TRANS` | `posted-trans_*.txt` | `SHAW_TRANSACTIONS` |
| `TRANS_MASTER` | `history_*.txt` | `SHAW_TRANS_MASTER` |

Operator confirmed these are the only files currently delivered by SHAW.
The other 11 sqlload scripts in `load_SHAW.sh` (including `HSS_TRANS_DAILY`,
`USER_FIELDS`, METRO2, etc.) remain load-only — `file_to_staging` coverage
will be added in a follow-up MR when SHAW starts delivering those files.

Each input uses `thresholds.max_errors: 0`.

### Output files — 16 entries

Lowercase glob names (Question A option (b)). Three multi-record files with
uniform `discriminator_field: "RECORD_TYPE"`:

- `atoctran_shaw_*.txt` → `ATOCTRAN` (multi-record)
- `cdstrans_eac_collateral_shaw_*.txt` → `CDSTRANS_EAC_COLLATERAL`
- `cdstrans_efb_*.txt` → `CDSTRANS_EFB`
- `cdstrans_efi_*.txt` → `CDSTRANS_EFI`
- `cdstrans_efw_fee_waivers_*.txt` → `CDSTRANS_EFW_FEE_WAIVERS`
- `cdstrans_efx_*.txt` → `CDSTRANS_EFX`
- `cdstrans_esa_*.txt` → `CDSTRANS_ESA`
- `cdstrans_est_shaw_*.txt` → `CDSTRANS_EST`
- `cdstrans_hss_*.txt` → `CDSTRANS_HSS`
- `cdstrans_rlt_*.txt` → `CDSTRANS_RLT`
- `cdstrans_sec_shaw_*.txt` → `CDSTRANS_SEC`
- `cdstrans_xpr_*.txt` → `CDSTRANS_XPR`
- `contact_shaw_*.txt` → `CONTACT` (multi-record)
- `contact-account_shaw_*.txt` → `CONTACT_ACCOUNT`
- `p327_shaw_*.txt` → `P327`
- `tranert_shaw_*.txt` → `TRANERT` (multi-record, has header record)

Per-entry defaults: `strict_fixed_width: true`, `strict_level: all`,
`tolerance.ignore_fields: []` (refine when mappings land),
`tolerance.max_errors: 0`, `tolerance.max_error_pct: 0`.

### Mapping/rules paths
- Convention: `config/mappings/SHAW_<TOKEN>.json` and
  `config/rules/SHAW_<TOKEN>.json`.
- **Status: pending.** Operator is generating them separately.
- Each input/output entry in `SHAW.yml` carries `# TODO(mapping-pending)`
  and/or `# TODO(rules-pending)` markers.
- `file_to_staging`, L1, L2, and L3 gates will fail until mappings/rules
  exist — accepted.

---

## Forward-looking schema decisions — now adopted

| ID | Scope | Status |
|---|---|---|
| **F1** | Per-source `staging_schema` override | **Adopted** — honored by `path_resolver.py` |
| **F1b** | Per-source `output_root` override | **Adopted** — honored by `path_resolver.py` |
| **F2** | Per-table schema (qualified `SCHEMA.TABLE`) | Deferred |
| **F3** | Named-schema map in `paths.yml` | Deferred |

`path_resolver.py` allow/deny lists:
- `_SOURCE_OVERRIDABLE_KEYS = {"output_root", "staging_schema"}` — silently
  accepted; literal values only (braces rejected, non-strings rejected).
- `_SOURCE_OVERRIDE_FORBIDDEN_KEYS` covers all other env-level path keys —
  raises `PathResolverError` at `source_config()` load time (option (b),
  fail-fast policy).

---

## Files

### Created this session

- `config/e2e/sources/SHAW.yml` — full source config with `staging_schema`
  and `output_root` overrides, 6 inputs, 16 outputs, gates per Q6.
- `config/e2e/pipelines/sit/SHAW.pipeline.yaml` — generated.
- `config/e2e/pipelines/ait/SHAW.pipeline.yaml` — generated.

### Modified this session

- `scripts/e2e_lib/path_resolver.py` — added override hook in `resolve()`
  plus disallowed-key check in `source_config()`. Allow-list is at the top
  of the module.
- `scripts/generate_pipeline_yaml.py` — single-line change: pass `source=`
  to the `staging_schema` resolution so per-source overrides are honored.
- `tests/unit/test_e2e_path_resolver.py` — appended `TestSourceLevelOverrides`
  class (9 new tests).

### Created previous session (already on disk)

- `scripts/wrappers/load_SHAW.sh` — 17-entry SHAW load wrapper.

### ATOCTRAN mapping & rules CSV generation (this session)

Generated the per-record-type mapping and rules CSVs for the multi-record
ATOCTRAN output file:

- `scripts/excel_to_spec_text.py` — reusable Excel→struct reader that
  handles the SOURCE/TARGET banner-merged header layout common to Valdo
  spec workbooks. Honors strikethrough as deprecated. Designed to be
  reused for `ESA_AFS_M06.xlsx`, `P327_SHAW_M06.xlsx`, and future Excel
  specs without modification.
- `scripts/generate_shaw_atoctran_csvs.py` — one-shot generator that
  walks 7 record-type sheets (`100, 200, 300, 605, 700, 900, 060`) and
  emits 14 CSVs under `mappings/csv/shaw_atoctran/`.
  - Pattern coverage: `Default to 'X'`, `Hard-Code to "X"`,
    `Valid Values - X or Y`, `with following hard-coded values: a, b, c`,
    `IF/THEN/ELSE` (clause-scoped to avoid comparison-literal false
    positives), `set 'X'; set 'Y'`, leading bare quoted codes
    (`"700" - if ...`).
  - Length filter: drops candidate values whose length exceeds the
    field's declared `Length` (numerics zero-padded on the left at
    runtime, strings space-padded on the right).
  - Workbook Valid Values column: used only when the Transformation cell
    yields no concrete values, per the prompt's CRITICAL rule that
    Transformation wins (it represents what SHAW *actually sends*).
  - Sheet `500` skipped per operator (out of scope this iteration).

Final counts:

| Rec Type | Fields | Rules |
|---:|---:|---:|
| 100 | 5 | 12 |
| 200 | 8 | 15 |
| 300 | 15 | 23 |
| 605 | 7 | 19 |
| 700 | 13 | 18 |
| 900 | 5 | 14 |
| 060 | 4 | 11 |
| **Total** | **57** | **112** |

Operator-confirmed test cases that pass:

- Sheet 100 `LOCATION-CODE` → `100030|100040|200000` (mined from
  `with following hard-coded values: ...`)
- Sheet 200 `PREVIOUS-CCI` → `0|2` (union of `Default to '0'` and
  `Valid Values - 0 or 2` patterns)
- Sheet 300 `CATEGORY-CODE` → `2|3|1` (from three `set 'X'` constructs;
  the workbook column's spurious `4` is correctly suppressed)
- Sheet 300 `THIRD-PARTY-AMT-AFFECTED` → empty (transformation is
  `Leave Blank <spaces>`; workbook column values suppressed)
- Sheet 700 `TRANSACTION-CODE` → `700` (leading bare-quoted code)
- Sheet 900 `LOCATION-CODE` → `100030` (`Hard-code to 100030` mined
  even though the cell also contains `batch date`)
- Sheet 060 `ACCT-NUM` → empty (transformation is the derivation
  `ACCT-NUM = BR + CUS + LN`; no fixed value set)

### Pending — CSV-to-JSON conversion (downstream)

The 14 CSVs are inputs to the existing repo converters; the JSONs they
produce land at the paths already referenced by `SHAW.yml`:

```bash
# Mapping JSONs (use existing TemplateConverter)
for r in 100 200 300 605 700 900 060; do
  python -m src.config.template_converter \
    mappings/csv/shaw_atoctran/SHAW_ATOCTRAN_${r}_mapping.csv \
    config/mappings/SHAW_ATOCTRAN_${r}_mapping.json
done

# Rules JSONs (use BARulesTemplateConverter via the API or a thin wrapper)
```

### ATOCTRAN multi-record umbrella (this session)

Decision: use the existing `MultiRecordConfig` YAML schema
(`src/config/multi_record_config.py`) rather than inventing a new format.

Created `config/mappings/SHAW_ATOCTRAN.yaml` as the umbrella for the 7
ATOCTRAN record types:

- `discriminator: { field: TRANSACTION-CODE, position: 25, length: 3 }`
- 7 `record_types` keyed `rt_100` … `rt_060`, each with `match`,
  `mapping` (per-record JSON path), `rules` (per-record JSON path),
  `expect: any`
- `cross_type_rules: []` (operator-confirmed: ATOCTRAN has none;
  per-account creation gates are enforced inside per-record rules)
- `default_action: error` — unknown TRANSACTION-CODE values fail loudly
  (closed set of 7 known codes; an unknown value is a Java defect)

Validated by loading through `MultiRecordConfig(**yaml.safe_load(...))`
during the session — schema clean.

`SHAW.yml` updated:

- `output_files.ATOCTRAN.mapping`: `"config/mappings/SHAW_ATOCTRAN.yaml"`
  (was `SHAW_ATOCTRAN.json` placeholder)
- `output_files.ATOCTRAN.rules`: `""` (per-record rules embedded in
  umbrella; standalone path no longer applies)
- `discriminator_field`: `"TRANSACTION-CODE"` (was `"RECORD_TYPE"`)
- Comment block above `output_files:` updated to list per-file
  discriminator names; CONTACT and TRANERT still `"RECORD_TYPE"`
  pending their spec workbook reviews

Pipeline YAMLs regenerated for both envs; both `--check` clean. The 6
ATOCTRAN path references in `SHAW.pipeline.yaml` (sit + ait) now point
at the `.yaml` umbrella. **Crucially these references are not yet
executable** — see "Pipeline-YAML multi-record dispatch" in the open
follow-ups below.

### Open follow-ups

Resolved this session (kept here for trail):

- ~~Multi-record JSON wiring in `SHAW.yml`~~ — **resolved**: option (b),
  umbrella file using existing `MultiRecordConfig` schema. See section
  above.
- ~~Discriminator field name in `SHAW.yml`~~ — **resolved**: ATOCTRAN
  now uses `"TRANSACTION-CODE"`. CONTACT and TRANERT remain
  `"RECORD_TYPE"` placeholders pending their workbooks.

Resolved in follow-up session (2026-05-15):

- ~~**Pipeline-YAML multi-record dispatch**~~ — **resolved** per
  [ADR 0005](../adr/0005-multi-record-pipeline-dispatch.md). New
  `validate_multi_record` step type added to the runner dispatcher; the
  generator emits it whenever an output entry has `multi_record: true`,
  routing the umbrella YAML through
  `run_multi_record_validate_service`. L2 and L3 stay as plain
  `compare` steps. A fail-fast sanity check (`multi_record` flag must
  match the mapping file extension — `.yaml`/`.yml` for true, `.json`
  for false) prevents recurrence. Single `src/` touch: one additive
  branch in `ETLPipelineRunner._execute_step`. SRC_A golden YAML
  unchanged. SHAW pipelines regenerated for sit + ait; ATOCTRAN's L1
  step is now `type: validate_multi_record`.
- ~~**`bulk_convert_rules.py` converter-selection bug**~~ — **resolved**.
  `convert_file()` now takes the `kind` returned by
  `validate_template_strict()` and dispatches to
  `BARulesTemplateConverter` for `ba_friendly` templates and
  `RulesTemplateConverter` for `standard` ones. The previous code
  silently misrouted every BA-friendly template through the standard
  converter.
- ~~**Strict-validator alignment with `prompts/generate-rules-csv.md`**~~
  — **resolved**. `validate_template_strict()` in
  `scripts/bulk_convert_rules.py` was rejecting the rule-ID format
  (`R001` / `CR001`) and empty `Expected / Values` cells that the
  prompt explicitly mandates. Discovered during follow-up #3 work.
  Per AGENTS.md the prompt is the authoritative spec, so the validator
  was adjusted:
  - Rule-ID regex now accepts `R<digits>`, `BR<digits>`, `CR<digits>`.
  - `Expected / Values` is conditionally required based on `Rule Type`;
    blank is allowed for `not_empty`, `numeric`, `required`,
    `cross_row:unique`, `cross_row:unique_composite`,
    `cross_row:consistent`, `cross_row:group_count` (the rule types
    the prompt's reference table marks `(blank)`).
- ~~**ATOCTRAN BA review** (was #2)~~ — **complete** (operator signed
  off 2026-05-15). The 14 CSVs at `mappings/csv/shaw_atoctran/` are the
  canonical artifact; the derived JSONs reflect that review.
- ~~**CSV→JSON conversion for ATOCTRAN** (was #3)~~ — **complete**.
  Ran:
  ```bash
  PYTHONPATH=. python scripts/bulk_convert_mappings.py \
      --input-dir mappings/csv/shaw_atoctran \
      --output-dir config/mappings \
      --format fixed_width
  PYTHONPATH=. python scripts/bulk_convert_rules.py \
      --input-dir mappings/csv/shaw_atoctran \
      --output-dir config/rules
  ```
  (The bulk scripts log a "Validation failed" for every cross-kind
  file in a mixed-input directory — mapping converter sees rules CSVs
  as malformed mappings and vice versa. Symmetric noise; the
  correctly-kinded files in each run produce valid JSON.)

  Produced 7 mapping JSONs at
  `config/mappings/SHAW_ATOCTRAN_{060,100,200,300,605,700,900}_mapping.json`
  and 7 rules JSONs at the matching `config/rules/...` paths. Field and
  rule counts match the handover table exactly (57 fields / 112 rules).
  The umbrella `config/mappings/SHAW_ATOCTRAN.yaml` now loads cleanly
  through `MultiRecordConfig` with every referenced file present.

  Effect: with #1 + #3 both landed, ATOCTRAN's L1 path is end-to-end
  executable. Pipeline YAML → runner dispatch → multi-record service
  → umbrella YAML → 7 mapping JSONs + 7 rules JSONs. The SHAW SIT
  smoke test will now actually exercise the multi-record validator
  against ATOCTRAN; L1 will pass or fail on real validation logic
  rather than failing structurally.

Discovered during follow-up session smoke test (2026-05-16):

The first real end-to-end run of the ATOCTRAN umbrella against
`data/samples/atoctran_shaw_20260514.txt` (125,903 rows) exposed two
pre-existing Valdo internal bugs in the BA-friendly rules execution
path. Neither was caused by harness work; SHAW is just the first
source to exercise the seam end-to-end. The structural pipeline
(discriminator dispatch, per-record grouping, umbrella resolution) is
verified working. Full session record at
`docs/handover/SHAW_atoctran_smoke_findings.md`.

Tracked and authorized to fix:

- **Issue #12** — `MultiRecordConfig.record_types` annotated as bare
  `dict`; Pydantic v2 does not coerce, so the validator crashes with
  `AttributeError: 'dict' object has no attribute 'position'`. One-line
  schema fix.
- **Issue #13** — Eight BA-friendly rule operators (`not_empty`,
  `numeric`, `date_format`, `valid_values`, `min_value`, `max_value`,
  `exact_length`, `min_length`) are unrecognized by `RuleEngine`. Every
  BA-friendly rules JSON in the codebase is dead on arrival. Eight-
  predicate additive engine extension.
- **ADR 0006** approved (`docs/adr/0006-fix-ba-friendly-rules-execution.md`).
  Authorizes the `src/` crossing for both fixes. Two MRs planned:
  MR 1 fixes #12 standalone (unblocks every multi-record validator
  call); MR 2 fixes #13 (unblocks every BA-friendly JSON in the repo,
  not just SHAW's).

**MR 1 of ADR 0006 — complete (2026-05-16):**

- ✅ `src/config/multi_record_config.py`: `record_types` annotated
  `Dict[str, RecordTypeConfig]` with an inline ADR-0006 / issue-#12
  comment block. Single additive line.
- ✅ `tests/unit/test_multi_record_validator.py`: appended
  `TestRecordTypesDictCoercion` (3 new tests) exercising the
  YAML → `MultiRecordConfig` construction path that real callers
  use (the API service, the multi-record CLI, and the pipeline
  runner via `run_multi_record_validate_service`). The new tests
  assert that values in `record_types` are coerced to
  `RecordTypeConfig` instances on construction from raw dicts,
  including positional (`position: "first"`) configs. Without the
  schema fix all three would raise `AttributeError` on the very
  attribute access pattern that crashed the SHAW smoke.
- ✅ Existing `test_multi_record_validator.py` unchanged behavior:
  51/51 pass (was 48; +3 new). The schema change is strictly
  additive coercion, not behavioral.
- ✅ Focused suite: **238 passed** across
  `test_multi_record_validator.py`, `test_generate_pipeline_yaml.py`,
  `test_e2e_path_resolver.py`, `test_e2e_split_pipeline.py`,
  `test_etl_pipeline_runner.py`,
  `test_bulk_convert_rules_validation.py`,
  `test_ba_rules_converter.py`, `test_fix2_rules_parsing.py`,
  `test_api_rules_upload.py`, `test_mapping_upload.py`,
  `test_api_validate_multi_record.py`,
  `test_multi_record_transform_engine.py`,
  `test_multi_record_wizard_service.py`,
  `test_generate_multi_record.py`.
- ✅ SHAW smoke recipe (`docs/handover/SHAW_atoctran_smoke_findings.md`)
  re-run **without** the caller-side coercion workaround:
  - No `AttributeError`. Bug #12 is closed.
  - `total_rows= 125903` — discriminator dispatched all rows.
  - Stream proceeds to `Unknown operator: <name>` stderr from
    issue #13 (e.g. `not_empty`, `exact_length`, `valid_values`,
    `date_format`, `numeric`). This is the expected handoff point
    to MR 2.
  - `valid= False` — the structurally meaningless verdict from
    #13 that MR 2 resolves.

Resume at **MR 2 (issue #13)** per the ADR implementation order.

**MR 2 of ADR 0006 — complete (2026-05-16):**

- ✅ `src/validators/_date_formats.py`: new shared module exporting
  `DATE_FORMAT_REGEX` and `regex_for_format()`. Mirrors the lowering
  the BA converter already applies for the legacy `date format` rule
  type; adding a new format here makes it available to both halves
  without duplication. Required by ADR 0006's "extract into a shared
  constant module to avoid duplication" instruction.
- ✅ `src/validators/field_validator.py`: 8 new predicates appended
  to `FieldValidator` (`validate_not_empty`, `validate_numeric_format`,
  `validate_date_format`, `validate_valid_values`, `validate_min_value`,
  `validate_max_value`, `validate_exact_length`, `validate_min_length`).
  Strictly additive — every pre-existing predicate is byte-identical.
- ✅ `src/validators/rule_engine.py::_validate_field`: 8 new
  `elif operator == '<name>'` branches mapping the BA-native operator
  names to the new predicates. The existing `>`/`<`/`>=`/`<=`/`==`/
  `!=`/`in`/`not_in`/`regex`/`range`/`not_null`/`length` branches are
  unchanged. `Unknown operator` ValueError still fires for genuinely
  unknown operators (asserted by a regression test).
- ✅ `tests/unit/test_field_validator.py` (new): 25 tests, 3 cases per
  new predicate (happy path, violation case, edge case per predicate
  semantics).
- ✅ `tests/unit/test_rule_engine.py` (new): 13 tests — 9 mock-based
  dispatch assertions (one per operator + the comma-string fallback
  for `valid_values`), 3 end-to-end execution tests, 1 regression for
  the `Unknown operator` ValueError path.
- ✅ `tests/integration/test_ba_rules_round_trip.py` (new): 3 tests —
  full BA CSV → `BARulesTemplateConverter` → `RuleEngine` round-trip
  asserting the expected violation set per rule on a hand-built
  DataFrame; explicit assertion of zero `Unknown operator` output;
  legacy-operator regression. This is the seam test ADR 0006 calls out
  as missing.
- ✅ Focused suite: **286 passed** (was 238 after MR 1; +48 from new
  test files. 2 pre-existing Windows-only tempfile failures in
  `test_chunked_validator_stats.py` are unrelated to MR 2 — they fail
  on `os.unlink` of a still-open temp file).
- ✅ SHAW ATOCTRAN smoke re-run on the 125,903-row sample meets every
  ADR 0006 acceptance criterion:
  - No `AttributeError`.
  - No `Unknown operator: <name>` lines on stderr (run is silent).
  - Data-driven `valid= False`, `total_rows= 125903`.
  - All 7 record-type groups dispatched and validated.

**New issue surfaced by the clean MR 2 smoke (separate track):**

`record_type_results.rt_*.issue_code_summary` shows real fixed-width
validator findings dominated by `FW_VAL_001` (213,398 across rt_060
alone). Spot check:

```
"expected": "one of ['100030.0']",
"actual":   "100030"
```

The per-record mapping JSONs contain numeric-typed `valid_values`
entries with a stray `.0` suffix (`'100030.0'`) where the source data
has the unpadded integer string. **CSV-generator / type-coercion
artifact, not an ADR 0006 concern**:
`scripts/generate_shaw_atoctran_csvs.py` (and/or
`scripts/bulk_convert_mappings.py`) lifted numeric literals through
pandas which promoted them to floats. Resolved 2026-05-16 under
[ADR 0007](../adr/0007-template-converter-preserve-string-literals.md) —
root cause was the absence of ``dtype=str`` on the
``pd.read_csv``/``pd.read_excel`` calls inside
``src/config/template_converter.py``, not the CSV generator. Fix
applied at the read boundary in both ``TemplateConverter`` and
(defensively) ``BARulesTemplateConverter``. 7 ATOCTRAN mapping JSONs
regenerated; SHAW smoke shows the 213,398 ``FW_VAL_001`` count gone.

**ADR 0007 — complete (2026-05-16):**

- ✅ `src/config/template_converter.py`: `dtype=str` added to the
  two `pd.read_excel` calls in `from_excel` and the single
  `pd.read_csv` call in `from_csv`, with inline ADR-0007 comment
  blocks. Position/Length already do explicit `int(...)`; all other
  cells already pipe through `str(...).strip()`. Strictly additive.
- ✅ `src/config/ba_rules_template_converter.py`: same one-argument
  fix in `from_csv` and `from_excel`. SHAW's rules CSVs escaped this
  by accident (mixed-type column → `object` dtype); a tighter
  numeric-only rules CSV would surface the same corruption. Closed
  defensively.
- ✅ `tests/unit/test_template_converter_valid_values.py`: 3 new
  regression tests — numeric-only `Valid Values`, pipe-separated
  numerics, and a `Position`/`Length` sanity check that `dtype=str`
  doesn't break int coercion.
- ✅ `tests/unit/test_ba_rules_converter.py`: 2 new regression tests
  — numeric `valid_values` and numeric `exact_length`/`min_value`
  thresholds (the latter would crash `int('18.0')` without the fix).
- ✅ Mapping JSON regeneration: ran
  `scripts/bulk_convert_mappings.py --input-dir mappings/csv/shaw_atoctran
  --output-dir config/mappings --format fixed_width`. All 7 ATOCTRAN
  mapping JSONs rewritten; spot check `SHAW_ATOCTRAN_900_mapping.json`
  shows `LOCATION-CODE.valid_values == ["100030"]` and
  `TRANSACTION-CODE.valid_values == ["900"]`. No `.0` artifact
  anywhere.
- ✅ SHAW ATOCTRAN smoke (re-run via `scripts/_smoke_atoctran.py`):
  the per-rt `issue_code_summary` `FW_VAL_001` landslide (213,398 on
  rt_060 alone before the fix) is **gone**. Remaining
  `FW_ALIGN_000: 1` per group is a separate alignment-check seam,
  not ADR 0007 scope.

Still open (recommended priority order):

1. **BA confirmation on TRANERT remaining findings** — 3 record types
   still FAIL after the 2026-05-25 BSA review, with only genuine
   data-vs-spec discrepancies remaining (no more generator false
   positives). BA needs to confirm:
   - **rt_32010 `LN-TYP-ORI`** = `181` not in spec. Transformation
     references the Ledger Code Mapping tab — an open-ended lookup
     table. Options: (a) import the full lookup as valid_values, or
     (b) drop the rule entirely and rely on `numeric` + `exact_length: 3`.
   - **rt_32025 `DUE-DAT-DAY-COD`** = `09`, `13`, `31` (day-of-month).
     The mapping JSON still has an `in_list: [01, DD]` constraint
     inherited from the workbook's Valid Values column. Recommend
     clearing the mapping CSV's `Valid Values` cell for this field
     (`DD` is a format placeholder, not a code; `numeric` +
     `exact_length: 2` already covers it).
   - **rt_32040 `ACT-TYP-CBRS`** = `001`. Same open-ended Metro 2
     lookup as `LN-TYP-ORI`. Same options.
   - **rt_32010 `FW_REQ_001`** (4 rows): a required field is empty
     in 4 rows. Likely `OGL-CONTRACT-DAT-ORI` or `OGL-NTE-DAT-ORI`
     (conditional on `CHG-OFF-CD = '1'`). BA to confirm whether these
     should be `Required: No` (conditional) or whether the source
     data has a quality issue.
   - **FW_ALIGN_002** (1 per row in each failing detail group): one
     field per record type has a position/length mismatch between
     the spec and the actual data. The error CSV sidecars at
     `reports/smoke/SHAW_TRANERT/rt_*_errors.csv` show which field
     and offset. BA to reconcile against the actual file layout.
   - **CUS workbook gap**: BA to add "Default to '00040'" and
     "Default to '001'" transformation logic to the CUS sheet for
     `BK-NUM-ERT` and `APP-ERT` (other record types have these;
     CUS is inconsistent).

2. **CONTACT spec workbook → CSVs → umbrella** — blocked on the
   workbook arrival. Once available, follow the
   `docs/ADD_RECORD_TYPE_PLAYBOOK.md` pattern (extended to a new
   source file, not just a new record type). Same generator
   infrastructure (`scripts/shaw_csv_gen`); add a new
   `scripts/generate_shaw_contact_csvs.py` wrapper with the CONTACT
   sheet list and sheet→rectype mapping. No cross-type rules expected.

3. **TRANERT remaining record types (rt_32030 VR, rt_32070 CON)** —
   commented out in `config/mappings/SHAW_TRANERT.yaml` pending BA
   authoring of their layout sheets in the workbook. Uncomment and
   regenerate when the layout sheets exist.

4. **Valid Values column standardization** — proposal drafted in chat:
   prefer pipe-separated codes or `CODE - description` per line; ban
   sentences and control-table references in the Valid Values column.
   Worth capturing as `prompts/valid-values-column-standard.md` if the
   BA team agrees. Doc-only; not blocking anything. The BSA review
   surfaced concrete examples of the cost of *not* having this
   standard — every "IF X then Y" or "lookup from table Z"
   transformation became a generator false positive.

---

## Remaining work (RHEL host)

### Step 5 — deploy and mark wrapper executable

```bash
# On RHEL host, after deploying scripts/wrappers/load_SHAW.sh:
chmod +x /app/software/APPS/valdo/scripts/wrappers/load_SHAW.sh
```

Alternatively, before deploy, set the executable bit in git:
```bash
git update-index --chmod=+x scripts/wrappers/load_SHAW.sh
git commit -m "chore: mark load_SHAW.sh executable"
```

### Step 6 — smoke test in SIT

```bash
VALDO_E2E_DISABLE_JAVA=1 \
VALDO_E2E_DISABLE_FAILURE_SINK=1 \
  bash scripts/run_e2e_source.sh \
      --env sit --source SHAW \
      --run-id "smoke_$(date -u +%Y%m%d_%H%M%S)"
```

Expected: `file_to_staging`, L1, and L3 gates will fail (no mappings/rules,
no baselines). The point of this smoke is to confirm the harness invokes
the wrapper, resolves paths correctly under `APP_INT` and
`/app/software/APPS/ftp/input/shaw`, and writes the per-gate rollup to
`AUDIT.VALDO_RUN_FAILURES`.

### Step 7 — first baseline promotion

Run only after:
1. Mapping JSONs exist for all 16 output files.
2. Rules JSONs exist for all 16 output files.
3. At least one Java-generated output is available to promote.

Use `scripts/promote_baseline.sh` per the README's step 5.

---

## Out of scope for this iteration

- Generate wrapper (`scripts/wrappers/generate_SHAW.sh`) — `generate_step`
  is disabled. Add when SHAW gets a Java generate script.
- Mapping/rules JSONs for the 15 single-record outputs (everything
  except ATOCTRAN) — still pending. ATOCTRAN's 14 JSONs landed in the
  follow-up session.
- Mapping/rules JSONs for CONTACT and TRANERT — pending their spec
  workbooks. Both currently flagged `multi_record: false` in
  `SHAW.yml` with `TODO(multi-record-pending)` markers so the
  pipeline-YAML sanity check from ADR 0005 does not fail; flip back
  to `true` when their umbrella YAMLs land.
- `file_to_staging` coverage for the 11 load-only sqlload targets — follow-up
  MR after SHAW starts delivering those input files.

---

## Hard rules respected (AGENTS.md)

1. **`src/` discipline** — initial session: zero `src/` changes.
   Follow-up sessions cumulative: four additive `src/` changes, each
   documented and approved via ADR before the line was crossed:
   - [ADR 0005](../adr/0005-multi-record-pipeline-dispatch.md) — new
     dispatcher branch in `ETLPipelineRunner._execute_step` for
     `validate_multi_record`. Uses pre-existing
     `run_multi_record_validate_service`; no Valdo-internal semantics
     changed.
   - [ADR 0006](../adr/0006-fix-ba-friendly-rules-execution.md) MR 1 —
     one-line schema annotation in
     `src/config/multi_record_config.py` (`Dict[str, RecordTypeConfig]`).
     Closes a Pydantic v2 coercion gap; consumers already assumed the
     coerced type.
   - [ADR 0006](../adr/0006-fix-ba-friendly-rules-execution.md) MR 2 —
     additive predicate set in `src/validators/field_validator.py`
     (8 new methods) and additive `elif` branches in
     `src/validators/rule_engine.py::_validate_field` (8 new ops),
     plus a new shared `src/validators/_date_formats.py` constants
     module. Every pre-existing predicate and operator branch is
     byte-identical to before; the new operators correspond
     one-for-one to what `BARulesTemplateConverter.RULE_TYPE_MAP`
     emits, closing the converter↔engine vocabulary gap.
   - [ADR 0007](../adr/0007-template-converter-preserve-string-literals.md) —
     ``dtype=str`` added to ``pd.read_csv``/``pd.read_excel`` calls in
     `src/config/template_converter.py` and (defensively)
     `src/config/ba_rules_template_converter.py`. One argument per
     call site. Closes a silent data-corruption seam where pandas
     auto-inferred numeric columns and emitted ``'100030.0'`` into
     mapping JSON ``valid_values`` for integer-literal source cells.
   - Two additional NaN-skip fixes under ADR 0006 scope (same
     BA-friendly rules execution path, no new ADR required):
     `validate_valid_values` and `validate_date_format` in
     `src/validators/field_validator.py` now skip null/NaN cells.
     Without these fixes, optional fields that are legitimately
     blank in some rows produce spurious ``'nan'`` violations
     because ``astype(str)`` coerces NaN to the literal string
     ``"nan"``. Surfaced by SHAW TRANERT optional fields (e.g.
     `DAT-BKY-REC-CUS`, `RCF-DES-COD-REC`) during item 4.14.

   All four ADRs plus the two NaN-skip extensions are strictly
   additive and surface bugs that pre-existed
   the harness work — they were not introduced by SHAW onboarding,
   only first exercised by it. AGENTS.md hard rule #1's intent
   ("harness work doesn't rewrite Valdo internals to suit itself")
   is preserved: every crossing is a one-or-two-line bugfix that
   restores the documented or assumed contract. If the next
   milestone surfaces a fifth Valdo-internal bug, the rule's
   phrasing should be revisited to formalize the
   "harness work surfaces internal bugs → ADR-authorized minimal fix"
   pattern rather than treating each case as an exception.
2. **No secrets in code/config** — `USRPW` etc. inherited from `.env` by the
   load wrapper.
3. **No `UPDATE`/`DELETE` on `AUDIT.VALDO_RUN_FAILURES`** — not touched.
4. **Stopped at milestone boundary** — initial session: paused before
   RHEL deployment. Follow-up sessions: completed ATOCTRAN end-to-end
   (dispatch + conversion + BA sign-off; smoke `valid=True` on
   125,903 rows); completed TRANERT end-to-end (umbrella with
   cross-type rule, 8 record types including position-based Batch
   Header, BSA review 2026-05-25). Paused before CONTACT workbook
   arrival and RHEL smoke test. BSA confirmation of TRANERT
   remaining findings (item 1 in "Still open" list) is the next
   external dependency.
