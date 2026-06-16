# DB-to-File Reconciliation Template

## What this template is for

You have a generated output file (CSV, TSV, pipe-delimited, or fixed-width) and a SQL query against the system-of-record database that produces the rows that **should** be in that file. Drop the SQL in, point the template at the file, declare the join key, and Valdo extracts the expected rows from the database, diffs them against the file row-by-row, and reports the violations. This is the right template for "did our batch generate exactly what the database says it should?" and is the natural next step after the CSV-to-CSV reconciliation pattern.

## 5 blanks to fill in

Open `db_to_file_reconciliation.yml` and replace each `<FILL_IN_*>` marker:

- **`<FILL_IN_SOURCE_NAME>`** -- short identifier for the reconciliation (e.g., `CUSTOMER_EXPORT_RECON`).
- **`<FILL_IN_PATH_TO_EXTRACT_SQL>`** -- path to your `.sql` file containing the expected-rows `SELECT` (see the sample under `db_to_file_reconciliation_sample/extract.oracle.sql`).
- **`<FILL_IN_OUTPUT_GLOB>`** -- filename glob for the generated output file (e.g., `*_customer_export_*.txt`).
- **`<FILL_IN_PATH_TO_MAPPING_JSON>`** -- path to the column-name + data-type mapping JSON (see `db_to_file_reconciliation_sample/mapping.json`).
- **`<FILL_IN_KEY_COLUMNS>`** -- the column(s) that uniquely identify a row on both sides (e.g., `[CUSTOMER_ID]` or `[ACCOUNT_ID, EFFECTIVE_DATE]`).

## Connection setup

`valdo db-compare` reads Oracle credentials from the environment, the same way every other DB-touching Valdo command does. Export the three vars before invoking the command:

```bash
export ORACLE_USER=app_int
export ORACLE_PASSWORD=<secret>
export ORACLE_DSN=localhost:1521/FREEPDB1
```

For local prototyping without Oracle, the worked example under `db_to_file_reconciliation_sample/` ships a SQLite path: run `python templates/etl/db_to_file_reconciliation_sample/build_sample.py` and it seeds a temporary SQLite DB, runs `extract.sqlite.sql`, and writes the reconciliation report to `expected_report.json`. The Oracle SELECT is in `extract.oracle.sql` for parity.

## Expected SQL shape

The expected-rows `SELECT` must produce:

- **One row per record that should appear in the output file.** No driver-query gymnastics, no per-row sub-selects -- the result set IS the expected file content.
- **Column names that match the file mapping exactly.** The mapping JSON's `fields[].name` values bind the SQL columns to file columns. Use Oracle-quoted aliases (`SELECT FOO AS "LN-NUM-ERT"`) when the file uses punctuation Oracle would otherwise fold.
- **Column order that matches the mapping order.** The comparator joins on the declared key, so order is not load-bearing for matching, but ordered SELECTs make manual eyeballing easier.
- **Deterministic ordering** (`ORDER BY <key>`). Helpful for diff stability when the report is committed as a baseline.

The sample's `extract.sqlite.sql`:

```sql
SELECT CUSTOMER_ID,
       NAME,
       EMAIL,
       BALANCE
  FROM SAMPLE_CUSTOMER
 ORDER BY CUSTOMER_ID;
```

## Key column declaration

The `key_columns` list inside the `reconciliation:` block names the column(s) used to join DB rows to file rows. Both sides must carry the same values for the join to land:

```yaml
reconciliation:
  key_columns: [CUSTOMER_ID]
```

For composite keys (e.g. one row per account-per-effective-date), list all key columns in order:

```yaml
reconciliation:
  key_columns: [ACCOUNT_ID, EFFECTIVE_DATE]
```

A row that exists on only one side surfaces in the report as `only_in_file1` (DB-only) or `only_in_file2` (file-only); a row whose key matches but whose non-key fields differ surfaces under `differences` with a per-field old/new pair.

## Ignored fields

Use `ignored_fields:` to skip columns that legitimately differ between the DB and the file -- load timestamps, run IDs, audit columns generated at file-write time:

```yaml
reconciliation:
  ignored_fields: [LOAD_TIMESTAMP, RUN_ID, FILE_GENERATED_AT]
```

For floating-point or rounded amounts where sub-cent drift is acceptable, use the `tolerance.numeric_columns` map instead -- it preserves the field in the diff but applies a per-field epsilon:

```yaml
tolerance:
  numeric_columns: {BALANCE: 0.01, INTEREST_RATE: 0.0001}
```

## How to run

From the repository root, against production Oracle:

```bash
valdo db-compare \
  --query-or-table "$(cat templates/etl/db_to_file_reconciliation_sample/extract.oracle.sql)" \
  --mapping        templates/etl/db_to_file_reconciliation_sample/mapping.json \
  --actual-file    templates/etl/db_to_file_reconciliation_sample/output.txt \
  --key-columns    CUSTOMER_ID \
  --output         reports/db_recon.json
```

For Oracle-free local prototyping, invoke the sample driver:

```bash
python templates/etl/db_to_file_reconciliation_sample/build_sample.py
```

The driver seeds a temp SQLite database, runs `extract.sqlite.sql`, compares against `output.txt`, and writes the result to `expected_report.json`. The committed sample is designed to produce **zero violations** -- it's the green-path contract pinned by `tests/unit/test_etl_templates.py`.

## When to use something else

This template assumes one record type per file and a known join key. For multi-record fixed-width files (header + interleaved detail types + trailer), the heavyweight `scripts/e2e_lib/reconciliation_spec.py` spec under `config/e2e/sources/<SOURCE>/reconciliation/` is the right contract. For pure file-to-file diffs, use [`csv_file_comparison.yml`](csv_file_comparison.yml). For single-record fixed-width file validation (no DB), use [`fixed_width_single_record.yml`](fixed_width_single_record.yml). The full decision tree lives in [`docs/etl/CHOOSE_YOUR_SHAPE.md`](../../docs/etl/CHOOSE_YOUR_SHAPE.md).
