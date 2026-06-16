# CSV-to-CSV Reconciliation Template

## What this template is for

You have two CSV (or TSV) files with the same column shape -- typically a legacy-system export and a new-platform export of the same business records -- and you need to know **which rows match, which rows differ field-by-field, and which rows exist on only one side.** Drop your join key in, point the template at the two files, and Valdo produces a per-field diff plus only-in-left / only-in-right lists. This is the simplest non-SHAW reconciliation shape and is the right starting point for any "compare two extracts" use case.

## 5 blanks to fill in

Open `csv_file_comparison.yml` and replace each `<FILL_IN_*>` marker:

- **`<FILL_IN_SOURCE_NAME>`** -- short identifier for the reconciliation (e.g., `CUSTOMER_RECON`).
- **`<FILL_IN_LEFT_GLOB>`** -- filename glob for the "expected" side (e.g., `*_legacy_*.csv`).
- **`<FILL_IN_RIGHT_GLOB>`** -- filename glob for the "actual" side (e.g., `*_new_*.csv`).
- **`<FILL_IN_PATH_TO_MAPPING_JSON>`** -- path to the column-name mapping JSON (see the sample under `csv_file_comparison_sample/mapping.json`).
- **`<FILL_IN_KEY_COLUMNS>`** -- the column(s) that uniquely identify a row (e.g., `[CUSTOMER_ID]` or `[ACCOUNT_ID, EFFECTIVE_DATE]`).

## How to run

From the repository root:

```bash
valdo compare \
  --file1 templates/etl/csv_file_comparison_sample/left.csv \
  --file2 templates/etl/csv_file_comparison_sample/right.csv \
  --keys CUSTOMER_ID \
  --mapping templates/etl/csv_file_comparison_sample/mapping.json \
  --output reports/csv_compare.html
```

**Exit code:** `0` on a successful run regardless of whether differences were found; non-zero only on a fatal error (file unreadable, mapping invalid, etc.). Use the comparison summary (printed to stdout) or the JSON / HTML report to decide whether the reconciliation passed. To gate a CI pipeline on differences, run `valdo compare` and apply a `--thresholds` config or chain it to a check on `rows_with_differences > 0` in the produced JSON.

> **Engine note.** Today the format detector routes both `.csv` and `.tsv` files through the pipe-delimited parser. The sample under `csv_file_comparison_sample/` is comma-separated and exercises the comparison engine directly (see `tests/unit/test_etl_templates.py`). For end-to-end CLI runs against comma-separated files, supply a mapping JSON or rename the files to use the pipe delimiter. Full comma-CSV routing is tracked as a follow-up.

## What you get back

The CLI prints a comparison summary -- total rows on each side, matching rows, only-in-file-1 / only-in-file-2 counts, and rows with field-level differences. When `--output` is supplied, you also get a full HTML report containing:

- **`only_in_file1`** -- rows whose join key appears only on the left side.
- **`only_in_file2`** -- rows whose join key appears only on the right side.
- **`differences`** -- rows where the join key matches but at least one non-key field differs. Each entry lists the keys, the per-field old/new values, and a difference type (`value_difference`, `null_to_value`, `value_to_null`, `type_mismatch`, `both_null`).
- **`field_statistics`** -- which fields differed most often, useful for spotting systemic transforms (e.g., trailing-whitespace drift on one field across most rows).

See `csv_file_comparison_sample/expected_report.json` for the exact shape produced against the sample data.

## When to use something else

This template assumes a flat file with one record type and a known join key. If your data doesn't fit that shape, consult the **[ETL shape decision tree](../../docs/etl/CHOOSE_YOUR_SHAPE.md)** -- multi-record fixed-width files, file-to-database loads, and database-to-file reconciliations each have a dedicated template (or are explicitly on the roadmap).
