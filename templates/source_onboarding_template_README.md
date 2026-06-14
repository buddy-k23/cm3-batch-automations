# Source Onboarding Workbook — Column Reference (EC-S1)

This README is the canonical sheet-by-sheet column reference for the BA-facing
source-onboarding Excel workbook. Fill in a copy of
`templates/source_onboarding_template.xlsx`, then run (future EC-S6):

```bash
valdo onboard-source path/to/my_source.xlsx
```

The CLI will emit the full Valdo artefact tree:

```
config/e2e/sources/<SOURCE>.yml
config/mappings/<SOURCE>_<FILETYPE>.json|yaml
config/mappings/<SOURCE>_<FILETYPE>_<RECORD_TYPE>_mapping.json   (multi-record)
config/rules/<SOURCE>_<FILETYPE>_rules.json
config/rules/<SOURCE>_<FILETYPE>_<RECORD_TYPE>_rules.json        (multi-record)
config/e2e/sources/<SOURCE>/reconciliation/<filetype>.yml        (if reconciliation sheet present)
```

> When in doubt, see `templates/SHAW_onboarding.xlsx` — the worked SHAW example.
> It mirrors the existing `config/e2e/sources/SHAW.yml` + per-record mapping
> JSONs + the L2b reconciliation YAML using this workbook's sheet layout, and
> is the canonical regression fixture for EC-S2..EC-S6.

---

## How EC stories consume each sheet

| Sheet                       | Consumed by story         | What it drives                                                                                    |
| --------------------------- | ------------------------- | ------------------------------------------------------------------------------------------------- |
| `Source`                    | EC-S3 (source YAML emitter) | Top-level keys in `config/e2e/sources/<SOURCE>.yml` (gates, paths, scripts).                       |
| `InputFiles`                | EC-S3                     | `input_files[]` block in the source YAML.                                                          |
| `OutputFiles`               | EC-S3                     | `output_files[]` block in the source YAML.                                                         |
| `MultiRecord_<FILETYPE>`    | EC-S3 + EC-S4             | Umbrella `config/mappings/<SOURCE>_<FILETYPE>.yaml`; record-type discriminator + cardinality.      |
| `Reconciliation_<FILETYPE>` | Sprint-3 follow-up (L2b)  | `config/e2e/sources/<SOURCE>/reconciliation/<filetype>.yml` (driver for `db_truth_comparator`).    |
| `*_Mapping`                 | EC-S4 (mapping emitter)   | Per-layout `config/mappings/<SOURCE>_<...>_mapping.json` via the existing `TemplateConverter`.     |
| `*_Rules`                   | EC-S5 (rules emitter)     | Per-layout `config/rules/<SOURCE>_<...>_rules.json` via `BARulesTemplateConverter`.                |
| _(workbook entry-point)_    | EC-S6 (`valdo onboard-source` CLI) | Single command that fans out to the EC-S3/S4/S5 emitters in dependency order.             |

The schema validator at `src/onboarding/workbook_schema.py` is invoked first by
the EC-S2 reader; downstream stories trust that the workbook is already
schema-clean before consuming any cells.

---

## Conventions

- **DASH-style field names** (e.g. `LN-NUM-ERT`) are preserved verbatim through
  to the emitted SQL. Underscored variants (`LN_NUM_ERT`) are the table-column
  forms; both are needed for the L2b reconciliation join (see
  `config/e2e/sources/SHAW/reconciliation/tranert.yml`).
- **Sheet-name matching** is **case-sensitive** for the fixed sheets
  (`Source`, `InputFiles`, `OutputFiles`) and the dynamic prefixes/suffixes
  (`MultiRecord_`, `Reconciliation_`, `_Mapping`, `_Rules`). The schema
  validator surfaces a targeted "wrong case" hint when a near-miss is
  detected so the BA can fix the tab name immediately.
- **Column header matching** is **case-insensitive** (`Field Name` ==
  `field name` == `FIELD NAME`). BAs typically re-type headers when copying
  sheets and the validator absorbs that variation.
- **Multi-list cells** use `|` (pipe) as the list separator
  (`LN-NUM-ERT|CONTACT-ID`, `FILE_CREATE_TS|BATCH_SEQ_NO`). The pipe is
  consistent across `key_columns`, `ignored_fields`, `assertions`, and
  `tolerance_ignore_fields`.
- **Excel sheet-name 31-char limit**: when a sheet name would exceed 31
  chars (the Excel hard limit), the build script abbreviates it by
  truncating the stem and inserting a single `~` marker. The shortened
  name is what the BA sees on the tab AND what the `mapping_sheet` /
  `rules_sheet` reference cells contain — so the cross-references always
  resolve. Example: `CDSTRANS_EFW_FEE_WAIVERS_Mapping` (32 chars) becomes
  `CDSTRANS_E~AIVERS_Mapping` if needed. Most names fit unchanged.
- **TODO placeholder rows**: when a mapping or rules sheet refers to a
  source spec that has not yet been authored (many SHAW input/output specs
  fall into this bucket today), the build script seeds the sheet with a
  single `TODO_FIELD_1_<FILETYPE>` row so the schema validator stays
  green. The BA replaces the placeholder row with real fields before
  re-running the workbook through the onboarding CLI.

---

## Sheet: `Source` (single row)

One row of key/value pairs (column headers are the keys, row 2 has the values).
All columns are **required headers**, but `java_generate_script` is allowed to
be blank (meaning the source has no Java generate script and the `generate_step`
gate is disabled).

| Column                       | Type    | Default                              | Notes                                                                                                    |
| ---------------------------- | ------- | ------------------------------------ | -------------------------------------------------------------------------------------------------------- |
| `source_code`                | string  | _(required)_                         | Upper-case short identifier, e.g. `SHAW`. Used as the file-name prefix for every emitted artefact.        |
| `schema_version`             | int     | `1`                                  | Always `1` for now; bumped when the workbook schema itself changes (breaking change).                     |
| `release_tag`                | string  | _(required)_                         | e.g. `2026.M06`.                                                                                          |
| `description`                | string  | _(required)_                         | Free-text one-liner describing the source.                                                                |
| `staging_schema`             | string  | `APP_INT`                            | Oracle schema that holds the staging tables for this source.                                              |
| `output_root`                | string  | _(required)_                         | Absolute on-host directory where generated output files land, e.g. `/app/software/APPS/ftp/input/shaw`.    |
| `java_load_script`           | string  | _(required)_                         | Path to the deployed `load_<SOURCE>.sh` wrapper that the harness shells out to when `load.invoke_java=true`. |
| `java_generate_script`       | string  | _(blank)_                            | Path to the `generate_<SOURCE>.sh` wrapper; blank means no Java generate script this iteration.            |
| `gate_load_blocking`         | bool    | `true`                               | If true, load failures abort the pipeline.                                                                |
| `gate_load_invoke_java`      | bool    | `true`                               | If true, the load gate invokes the Java load wrapper.                                                     |
| `gate_f2s_blocking`          | bool    | `true`                               | If true, file-to-staging mismatch aborts the pipeline.                                                    |
| `gate_generate_blocking`     | bool    | `false`                              | Typically false until a Java generate script is delivered.                                                |
| `gate_generate_invoke_java`  | bool    | `false`                              | Same default.                                                                                             |
| `gate_l1_blocking`           | bool    | `true`                               | L1 structural validation.                                                                                 |
| `gate_l2b_blocking`          | bool    | `true`                               | L2b SQL-truth reconciliation (driven by the `Reconciliation_*` sheets).                                   |
| `gate_l3_blocking`           | bool    | `true`                               | L3 baseline diff.                                                                                         |
| `gate_mr_report_blocking`    | bool    | `false`                              | Multi-record HTML report is a report, not a gate.                                                          |

---

## Sheet: `InputFiles` (one row per upstream file)

| Column                  | Type    | Default      | Notes                                                                                                |
| ----------------------- | ------- | ------------ | ---------------------------------------------------------------------------------------------------- |
| `file_type`             | string  | _(required)_ | Logical name, e.g. `COLLATERAL_MASTER`. Drives the emitted artefact file names.                       |
| `glob`                  | string  | _(required)_ | Filename glob the watcher matches against, e.g. `collateral-master_*.txt`.                            |
| `mapping_sheet`         | string  | _(required)_ | Name of the `<FILETYPE>_Mapping` sheet in this workbook that defines the input layout.                |
| `target_staging_table`  | string  | _(required)_ | Bare table name; the `staging_schema` from the `Source` sheet qualifies it.                           |
| `thresholds_max_errors` | int     | `0`          | Optional override. Blank means use the default (zero tolerance).                                      |

---

## Sheet: `OutputFiles` (one row per generated output)

| Column                       | Type    | Default      | Notes                                                                                                                                                                       |
| ---------------------------- | ------- | ------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `file_type`                  | string  | _(required)_ | Logical name, e.g. `ATOCTRAN`, `CDSTRANS_EFB`.                                                                                                                              |
| `glob`                       | string  | _(required)_ | Filename glob matched directly under `output_root`.                                                                                                                          |
| `mapping_sheet`              | string  | _(required)_ | For flat files: the `<FILETYPE>_Mapping` sheet name. For multi-record files: the literal string `(umbrella)` — the umbrella YAML is built from the `MultiRecord_<FILETYPE>` sheet. |
| `rules_sheet`                | string  | _(blank)_    | For flat files: the `<FILETYPE>_Rules` sheet name. For multi-record files: `(umbrella)`. Blank means no rules JSON is emitted.                                                |
| `tolerance_max_errors`       | int     | `0`          | Optional override.                                                                                                                                                          |
| `tolerance_max_error_pct`    | float   | `0`          | Optional override.                                                                                                                                                          |
| `tolerance_ignore_fields`    | list    | `[]`         | Pipe-separated list of field names to exclude from L3 diffs, e.g. `FILE_CREATE_TS|BATCH_SEQ_NO`.                                                                            |

---

## Sheet: `MultiRecord_<FILETYPE>` (one sheet per multi-record output)

One row per record type in the umbrella. The discriminator field/position/length
are repeated on every row for human readability — emitters take the value from
the first row and validate that all subsequent rows agree.

| Column                     | Type    | Default      | Notes                                                                                                                                |
| -------------------------- | ------- | ------------ | ------------------------------------------------------------------------------------------------------------------------------------ |
| `record_type_name`         | string  | _(required)_ | Logical record-type name, e.g. `batch_header`, `rt_32000`, `rt_32005`. Becomes the umbrella YAML key + the `ParsedRow.record_type`.    |
| `discriminator_field`      | string  | _(required)_ | Field name (DASH form), e.g. `TRN-COD-ERT`.                                                                                          |
| `discriminator_position`   | int     | _(required)_ | 1-based start column.                                                                                                                |
| `discriminator_length`     | int     | _(required)_ | Width in chars.                                                                                                                      |
| `match_kind`               | enum    | _(required)_ | One of: `position_first` (batch headers matched by position rather than discriminator), `discriminator_equals`, `discriminator_in`.   |
| `match_value`              | string  | _(required)_ | The value (or comma-separated list when `match_kind=discriminator_in`). Use `first` when `match_kind=position_first`.                  |
| `mapping_sheet`            | string  | _(required)_ | Per-record `*_Mapping` sheet name.                                                                                                   |
| `rules_sheet`              | string  | _(blank)_    | Per-record `*_Rules` sheet name. Blank means no rules JSON for this record type.                                                     |
| `cardinality`              | enum    | _(required)_ | One of: `one_per_driver_row`, `many_per_driver_row`, `zero_or_one_per_driver_row`.                                                   |

---

## Sheet: `Reconciliation_<FILETYPE>` (one sheet per L2b-reconciled output)

Each row drives one record-type leg of the L2b SQL-truth reconciliation. For
flat (non-multi-record) outputs, use `(flat)` as the `record_type_name`.
Assertions are file-wide and are conventionally placed on the first row's
`assertions` cell.

| Column                  | Type    | Default      | Notes                                                                                                              |
| ----------------------- | ------- | ------------ | ------------------------------------------------------------------------------------------------------------------ |
| `record_type_name`      | string  | _(required)_ | Matches the `record_type_name` in the corresponding `MultiRecord_<FILETYPE>` sheet, or `(flat)`.                    |
| `key_columns`           | list    | _(required)_ | Pipe-separated DASH-form column names (e.g. `LN-NUM-ERT` or composite `BK-NUM-BRT|APP-BRT`).                       |
| `staging_table`         | string  | _(required)_ | Materialised expected table, e.g. `cm3_int.EXPECTED_BATCH_HEADER_TBL`.                                              |
| `predicate`             | string  | _(blank)_    | Optional WHERE clause (e.g. `BAT_TYP_BRT = 'A'`); blank means no predicate.                                         |
| `ignored_fields`        | list    | _(blank)_    | Pipe-separated; e.g. `DR_CR_AMT_BRT|FILE_CREATE_TS`. Excluded from the row-by-row reconciliation.                  |
| `assertions`            | list    | _(blank)_    | Pipe-separated DSL strings (e.g. `header.ITM-CNT-BRT == count(detail)`). Conventionally on row 1 only (file-wide). |
| `expected_sql_override` | string  | _(blank)_    | Optional verbatim SQL path overriding the auto-derived expected SQL.                                                |

---

## Sheets: `<FILETYPE>_Mapping` / `<FILETYPE>_<RECORD_TYPE>_Mapping`

One row per field. The column shape mirrors the existing CSV templates under
`mappings/csv/shaw_<filetype>/` that the `TemplateConverter` already understands.

| Column           | Type   | Required by validator | Notes                                                                                                  |
| ---------------- | ------ | --------------------- | ------------------------------------------------------------------------------------------------------ |
| `Field Name`     | string | Yes                   | DASH form preferred (`LN-NUM-ERT`).                                                                    |
| `Data Type`      | string | Yes                   | `String`, `Numeric`, `Date`, `Decimal`.                                                                |
| `Position`       | int    | Conditional (fixed-width) | 1-based start column. Required when the layout is fixed-width.                                    |
| `Length`         | int    | Conditional (fixed-width) | Width in chars. Required when the layout is fixed-width.                                          |
| `Target Name`    | string | No                    | DB column name (typically lower-snake of `Field Name`).                                                |
| `Required`       | string | No                    | `Yes` / `No`.                                                                                          |
| `Format`         | string | No                    | e.g. `9(5)`, `MM/DD/CCYY`.                                                                             |
| `Transformation` | string | No                    | Free text, e.g. `Hard-Code to BATCH`.                                                                  |
| `Valid Values`   | string | No                    | Pipe-separated valid values.                                                                           |
| `Description`    | string | No                    | Free text. Surfaces in generated docs.                                                                 |

---

## Sheets: `<FILETYPE>_Rules` / `<FILETYPE>_<RECORD_TYPE>_Rules`

One row per validation rule. Mirrors the existing BA-friendly rules CSVs under
`mappings/csv/shaw_<filetype>/*_rules.csv` that `BARulesTemplateConverter`
already understands.

| Column              | Type   | Required by validator | Notes                                                                                       |
| ------------------- | ------ | --------------------- | ------------------------------------------------------------------------------------------- |
| `Rule ID`           | string | Yes                   | Stable identifier, e.g. `R001`.                                                              |
| `Rule Name`         | string | No                    | Human label.                                                                                 |
| `Field`             | string | Yes                   | Field the rule applies to (DASH form).                                                       |
| `Rule Type`         | string | Yes                   | `not_empty`, `numeric`, `exact_length`, `valid_values`, `date_format`, `length`, etc.        |
| `Severity`          | string | No                    | `error` (default) or `warning`.                                                              |
| `Enabled`           | string | No                    | `Yes` (default) / `No`.                                                                      |
| `Message`           | string | No                    | Error message surfaced in the validation report.                                             |
| `Expected / Values` | string | No                    | Rule-type-specific value (e.g. `5` for `exact_length=5`, `BATCH` for `valid_values=BATCH`).  |

---

## Regenerating the worked SHAW example

The SHAW worked example is built deterministically from the live SHAW config:

```bash
python scripts/build_shaw_onboarding_workbook.py
```

This regenerates **both** `templates/source_onboarding_template.xlsx` and
`templates/SHAW_onboarding.xlsx`. Run it after editing the underlying SHAW
mapping JSONs, rules JSONs, or `SHAW.yml` to keep the worked example in sync.
The schema validator is invoked automatically at the end of the build.
