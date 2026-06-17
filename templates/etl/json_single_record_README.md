# JSON (NDJSON) single-record validation — worked example

Validates a **newline-delimited JSON** file (one JSON object per line) against a
mapping whose fields are located by **JSONPath**, per [ADR 0018](../../docs/adr/0018-json-parser-design.md).
This is the JSON counterpart to `fixed_width_single_record` — same engine, same
report shape, fields located by `$.path` instead of position/length.

## When to use

- Your file is **NDJSON / JSON Lines** (`.ndjson` / `.jsonl`): one JSON object per line.
- A plain `.json` file that is a single top-level array is **not** supported in v1 —
  convert it first: `jq -c '.[]' in.json > in.ndjson`. (The format detector tells you this.)
- Each line has the same shape (one record type per file).

## Files in this sample

| File | What it is |
|---|---|
| `input.ndjson` | 10 customer records (regenerate with `python3 build_sample.py`). Lines 6/8/9/10 carry seeded defects. |
| `mapping.json` | The contract — each field carries a `json_path`. `TXN_COUNT` maps `$.transactions[*]` → an integer count column `TXN_COUNT_count`. |
| `rules.json` | The business rules: `nested_required` (CUSTOMER_ID present), `valid_values` (STATUS), `numeric` (AGE), `json_array_length` (≥1 transaction). |
| `expected_report.json` | The **real** output of running the validation below (committed for the test to compare against). |

## How a BA fills the mapping

Each `fields[]` entry needs a `name`, a `data_type`, and a **`json_path`** (the
JSONPath selector). For an array path, end it in `[*]` — the parser collapses it
to a `<name>_count` integer column you can rule against:

```json
{ "name": "CUSTOMER_ID", "data_type": "string", "json_path": "$.customer.id", "required": true }
{ "name": "TXN_COUNT",   "data_type": "decimal", "json_path": "$.transactions[*]" }
```

Two JSON-specific rule operators (ADR 0018 §4):

- **`nested_required`** — flags rows where the JSON path is **absent** (the key is
  missing), distinct from a present-but-`null` value.
- **`json_array_length`** — flags rows whose array-count column is outside
  `[min_len, max_len]` (e.g. "every record must carry ≥1 transaction").

## Run it

```bash
valdo validate \
  --file templates/etl/json_single_record_sample/input.ndjson \
  --mapping templates/etl/json_single_record_sample/mapping.json \
  --rules templates/etl/json_single_record_sample/rules.json \
  --output reports/json_validate.html
```

The seeded sample reports **`valid: false`, 4 errors** — `CUSTOMER_ID` missing
(line 9), `STATUS` not in the allowlist (line 8), non-numeric `AGE` (line 10), and
an empty `transactions` array (line 6). That matches `expected_report.json`.
