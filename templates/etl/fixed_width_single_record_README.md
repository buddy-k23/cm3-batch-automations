# Fixed-Width Single-Record Validation Template

## What this template is for

You have a fixed-width file -- each line is exactly the same width, and every line carries the **same record shape** (one record type per file, no header/detail/trailer). Common examples: a daily DDA-accounts export, a customer-master snapshot, a flat GL extract. Drop in your field positions and lengths via a mapping JSON, point the template at the file, and Valdo enforces field-level validation: required-field checks, fixed-width position/length, format pictures (e.g. `9(10)` numeric, `MM/DD/CCYY` date), and per-field `valid_values` allowlists. This is the right starting point for any single-record-type fixed-width validation. For multi-record fixed-width (header + detail rows + trailer with cross-type checks), use the [SHAW TRANERT umbrella shape](../../config/mappings/SHAW_TRANERT.yaml) instead.

## 3 blanks to fill in

Open `fixed_width_single_record.yml` and replace each `<FILL_IN_*>` marker:

- **`<FILL_IN_SOURCE_NAME>`** -- short identifier for the source (e.g., `ACCOUNT_DAILY`, `CUSTOMER_MASTER`).
- **`<FILL_IN_FILE_TYPE>`** and **`<FILL_IN_GLOB>`** -- a logical label for the file (e.g., `ACCOUNTS`) and the filename glob (e.g., `accounts_*.txt`).
- **`<FILL_IN_PATH_TO_MAPPING_JSON>`** -- path to the per-field mapping JSON (see the sample under `fixed_width_single_record_sample/mapping.json`).

## Authoring the mapping JSON

The mapping JSON is the contract. Each entry in `fields[]` declares one field:

- **`name`** -- the column identifier used in reports and the strict-validation engine.
- **`position`** (1-indexed) and **`length`** -- the character offsets the parser reads. `position: 1, length: 5` reads characters 1-5 of every line.
- **`data_type`** -- `string`, `decimal`, or `date` (informational; the format picture below drives actual validation).
- **`format`** (optional) -- a picture clause. `9(5)` means "exactly 5 digits"; `S9(8)` means signed 8 digits; `MM/DD/CCYY` is treated as a 10-char date.
- **`valid_values`** (optional) -- an allowlist (e.g. `["AC", "CL", "SU"]`); values outside the list raise `FW_VAL_001`.
- **`required`** (optional, defaults false) -- when true, empty/blank values raise `FW_REQ_001`.

For a worked example see [`config/mappings/SHAW_TRANERT_BATCH_HEADER_mapping.json`](../../config/mappings/SHAW_TRANERT_BATCH_HEADER_mapping.json) -- a 21-field, 158-char fixed-width header with formats, `valid_values`, and required-field declarations.

## How to run

From the repository root:

```bash
valdo validate \
  --file templates/etl/fixed_width_single_record_sample/input.txt \
  --mapping templates/etl/fixed_width_single_record_sample/mapping.json \
  --strict-fixed-width \
  --strict-level all \
  --output reports/fw_validate.html
```

**Exit code:** `0` when the file is valid; non-zero when any error-severity violation is recorded. Suitable for CI gating.

## What you get back

`valdo validate` against the sample produces 3 primary violations -- one per defect deliberately seeded into the worked example:

| Row | Field | Code | Defect |
|-----|-------|------|--------|
| 8 | `BALANCE` | `FW_FMT_001` | Non-numeric value in a `9(10)` field (type mismatch) |
| 9 | `ACCT_STATUS` | `FW_VAL_001` | Value `XX` is not in `valid_values=['AC','CL','SU']` |
| 10 | (whole row) | `FW_LEN_001` | Row is 75 chars long; mapping expects 80 |

See `fixed_width_single_record_sample/expected_report.json` for the exact contract these violations satisfy. The HTML report also includes derived rollup messages (`FW_ALIGN_002` first-misalignment diagnostics, `FW_LEN_002` truncation impact) -- those are derivative of the three primary codes above.

## When to use something else

This template assumes a fixed-width file with **one record type per line**. If your data doesn't fit that shape, consult the **[ETL shape decision tree](../../docs/etl/CHOOSE_YOUR_SHAPE.md)** -- multi-record fixed-width (SHAW TRANERT), CSV/TSV reconciliation, and DB-to-file flows each have a dedicated template (or are explicitly on the roadmap).
