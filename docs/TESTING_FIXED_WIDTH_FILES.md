# Testing Fixed-Width Files — Step-by-Step Guide

This guide walks through every step needed to validate fixed-width batch files using Valdo, from initial setup through advanced scenarios like multi-record-type files, strict validation, chunked processing, and business rules.

---

## Prerequisites

Before starting, ensure the following are in place:

1. **Python 3.9+** installed (`python --version`)
2. **Virtual environment** activated:
   ```powershell
   # Windows PowerShell
   .\.venv\Scripts\Activate.ps1
   ```
   ```bash
   # macOS / Linux
   source .venv/bin/activate
   ```
3. **Dependencies** installed:
   ```bash
   pip install -r requirements.txt
   pip install -e .
   ```
4. **Verify CLI** is available:
   ```bash
   valdo --help
   ```

---

## Step 1: Understand the Fixed-Width File Layout

Fixed-width files have no delimiters. Each field occupies a specific character range on every line. Every line must be the same length.

**Example** — a 47-character record:

```
LOC001ACCT-00000000001000000000000020260415
LOC002ACCT-00000000002500000000000020261231
```

| Field            | Position | Length | Type    | Format   |
|------------------|----------|--------|---------|----------|
| LOCATION-CODE    | 1        | 6      | String  |          |
| ACCT-NUM         | 7        | 18     | String  |          |
| CREDIT-LIMIT-AMT | 25       | 13     | Numeric | 9(12)    |
| EXPIRATION-DATE  | 38       | 8      | Date    | CCYYMMDD |

> **Tip:** Use `valdo detect -f <file>` to auto-detect the file format before writing a mapping.

---

## Step 2: Detect the File Format

Run format detection to confirm Valdo recognizes the file as fixed-width:

```powershell
valdo detect -f data\files\p327_sample_errored.txt
```

Expected output:
```
Detected format: fixed_width
```

If the file is not detected correctly, you can force the format via the mapping JSON (see Step 3).

---

## Step 3: Create or Obtain a Mapping JSON

The mapping JSON defines every field's name, position, length, data type, and format. Valdo uses this to slice each line into fields.

#### Option A — Use an Existing Mapping

The project ships with sample mappings in `config/mappings/`. For example, `p327_mapping.json` defines 252 fields with a total record length of 2,809 characters.

#### Option B — Generate from a CSV Template

1. Create a CSV file in `mappings/csv/` using the standard template format:

   ```csv
   Field Name,Data Type,Target Name,Required,Position,Length,Format,Description,Default Value,Valid Values
   LOCATION-CODE,string,LOCATION_CODE,Y,1,6,,,, 
   ACCT-NUM,string,ACCT_NUM,Y,7,18,,account number,,
   CREDIT-LIMIT-AMT,decimal,CREDIT_LIMIT_AMT,N,25,13,9(12),,,
   EXPIRATION-DATE,date,EXPIRATION_DATE,N,38,8,CCYYMMDD,,,
   ```

2. Convert to JSON:
   ```bash
   ./scripts/run_convert_mappings.sh "mappings/csv" "config/mappings" "fixed_width"
   ```

   On Windows:
   ```powershell
   python scripts/generate_from_csv_templates.py `
     --mapping-csv mappings\csv\my_mapping.csv `
     --mapping-out config\mappings\my_mapping.json `
     --mapping-name my_mapping `
     --mapping-format fixed_width
   ```

#### Option C — Generate from Excel

```bash
python src/config/template_converter.py ^
  data\mappings\my_template.xlsx ^
  config\mappings\my_mapping.json ^
  my_mapping ^
  fixed_width
```

#### Option D — Auto-Infer from a Sample File

```bash
valdo infer-mapping -f data\files\my_sample.txt -o config\mappings\inferred_mapping.json
```

> Review and adjust the inferred mapping before using it in production.

---

## Step 4: Parse the File (Dry Run)

Before validating, parse the file to inspect how Valdo reads it:

```powershell
valdo parse -f data\files\p327_sample_errored.txt
```

This prints the first few rows as a table. Verify that:
- [ ] Column names match your mapping field names
- [ ] Values are sliced at the correct positions
- [ ] No unexpected truncation or overflow

---

## Step 5: Run Basic Validation

Validate the file against the mapping and generate an HTML report:

```powershell
valdo validate `
  -f data\files\p327_sample_errored.txt `
  -m config\mappings\p327_mapping.json `
  -o reports\p327_validation.html `
  --detailed
```

**What this checks:**
- Every line matches the expected record length (2,809 chars for P327)
- Required fields (`LOCATION-CODE`, `ACCT-NUM`, `BASE-CURRENCY`) are not empty
- Data types match (numeric fields contain only digits, dates match `CCYYMMDD`)
- Field lengths are correct

**Interpreting results:**
- ✓ **File is valid** — all checks passed
- ✗ **File validation failed** — open the HTML report to see per-field error details

---

## Step 6: Run Strict Fixed-Width Validation

Strict mode adds position-level and length-level checks for every field on every row:

```powershell
valdo validate `
  -f data\files\p327_sample_errored.txt `
  -m config\mappings\p327_mapping.json `
  --strict-fixed-width --strict-level format `
  --detailed `
  -o reports\p327_strict_validation.html
```

**Strict levels:**
| Level    | What it checks                                                |
|----------|---------------------------------------------------------------|
| `format` | Line length, field position boundaries, basic type checks     |
| `all`    | Everything in `format` plus content-level rules per field     |

---

## Step 7: Add Business Rules

Business rules add domain-specific checks on top of structural validation.

#### 7a. Create Rules from CSV Template

1. Create `rules/csv/my_rules.csv`:

   ```csv
   Rule ID,Rule Name,Description,Type,Severity,Operator,Field,Value,Values,Pattern,Min,Max,Left Field,Right Field,Enabled,Min Length,Max Length
   BR001,Account Number Format,Must be 10 digits,field_validation,error,regex,ACCT-NUM,,,,,,,,TRUE,,
   BR002,Credit Limit Positive,Must be > 0,field_validation,error,>,CREDIT-LIMIT-AMT,0,,,,,,,TRUE,,
   BR003,Valid Status,Must be ACTIVE or CLOSED,field_validation,warning,in,ACCT-STATUS,,ACTIVE;CLOSED,,,,,,TRUE,,
   BR004,Expiry After Open,Expiry must be after open date,cross_field,error,>,,,,,,,EXPIRATION-DATE,ACCT-OPEN-DATE,TRUE,,
   ```

2. Convert to JSON:
   ```bash
   ./scripts/run_convert_rules.sh
   ```

#### 7b. Run Validation with Rules

```powershell
valdo validate `
  -f data\files\p327_sample_errored.txt `
  -m config\mappings\p327_mapping.json `
  -r config\rules\p327_business_rules.json `
  -o reports\p327_with_rules.html `
  --detailed
```

**Rule types available:**
| Type               | Description                                          |
|--------------------|------------------------------------------------------|
| `field_validation` | Single-field checks (regex, range, not_null, in, etc.) |
| `cross_field`      | Compare two fields in the same row                   |
| `cross_row`        | Validate across rows (unique, sequential, group_sum) |

---

## Step 8: Export Failed Rows

After validation, extract only the rows that failed into a separate file:

```powershell
valdo validate `
  -f data\files\p327_sample_errored.txt `
  -m config\mappings\p327_mapping.json `
  -r config\rules\p327_business_rules.json `
  --export-errors reports\p327_failed_rows.txt `
  -o reports\p327_validation.html
```

The exported file preserves the original fixed-width format so it can be re-processed after corrections.

---

## Step 9: Chunked Validation (Large Files)

For files with millions of rows, use chunked processing to avoid memory issues:

```powershell
valdo validate `
  -f data\files\large_batch.txt `
  -m config\mappings\p327_mapping.json `
  --use-chunked --chunk-size 50000 `
  --progress `
  -o reports\large_batch_validation.html
```

#### Parallel Chunked Validation (Multi-Core)

```powershell
valdo validate `
  -f data\files\large_batch.txt `
  -m config\mappings\p327_mapping.json `
  --use-chunked --chunk-size 50000 --workers 4 `
  --strict-fixed-width --strict-level format `
  --progress `
  -o reports\large_batch_parallel.html
```

---

## Step 10: Multi-Record-Type File Validation

Some fixed-width files contain interleaved record types (header, detail, trailer) identified by a discriminator field.

#### 10a. Create a Multi-Record YAML Config

Use the interactive wizard:
```bash
valdo generate-multi-record
```

Or create manually (see `config/multi-record/example_atoctran.yaml`):

```yaml
discriminator:
  field: REC_TYPE
  position: 1        # 1-indexed
  length: 3

record_types:
  header:
    match: "HDR"
    mapping: "config/mappings/header_mapping.json"
    expect: exactly_one
  detail:
    match: "DTL"
    mapping: "config/mappings/detail_mapping.json"
    expect: at_least_one
  trailer:
    match: "TRL"
    mapping: "config/mappings/trailer_mapping.json"
    expect: exactly_one

cross_type_rules:
  - check: header_trailer_count
    record_type: trailer
    trailer_field: RECORD_COUNT
    count_of: detail
    severity: error
  - check: header_trailer_match
    header_field: BATCH_ID
    trailer_field: BATCH_ID
    severity: error
  - check: type_sequence
    expected_order: [header, detail, trailer]
    severity: error

default_action: warn
```

#### 10b. Run Multi-Record Validation

```powershell
valdo validate `
  --file data\files\my_multi_record.txt `
  --multi-record config\multi-record\example_atoctran.yaml `
  -o reports\multi_record_validation.html
```

**Cross-type checks available:**
| Check                      | Description                                        |
|----------------------------|----------------------------------------------------|
| `required_companion`       | Header present requires detail records             |
| `header_trailer_count`     | Trailer count field matches actual detail rows. Set `allow_empty_batch: true` to treat a legitimately empty batch (count `0` and `0` rows) as valid; a truncated file (count `>0`, `0` rows) still fails (ADR 0013). |
| `header_trailer_sum`       | Trailer total matches sum of detail amounts        |
| `header_trailer_match`     | Header and trailer field values must match         |
| `header_detail_consistent` | Detail rows must match header field value          |
| `type_sequence`            | Record types appear in expected order              |
| `expect_count`             | Cardinality constraints (exactly_one, at_least_one)|

---

## Step 11: Schema Drift Detection

Detect if the file structure has drifted from the mapping definition:

```powershell
valdo detect-drift `
  --file data\files\p327_sample_errored.txt `
  --mapping config\mappings\p327_mapping.json `
  --output reports\drift_report.json
```

---

## Step 12: PII Scrubbing in Reports

By default, field values are redacted in HTML reports. To include raw values (non-production only):

```powershell
valdo validate `
  -f data\files\p327_sample_errored.txt `
  -m config\mappings\p327_mapping.json `
  --no-suppress-pii `
  -o reports\p327_with_values.html
```

---

## Step 13: Batch Validation via Manifest

Validate multiple files in one run using a CSV manifest:

1. Create `config/validation_manifest.csv`:

   ```csv
   data_file,mapping_file,rules_file,report_file,chunked,chunk_size
   data/files/batch_01.txt,config/mappings/p327_mapping.json,config/rules/p327_business_rules.json,batch_01.json,true,100000
   data/files/batch_02.txt,config/mappings/p327_mapping.json,,batch_02.json,false,
   ```

2. Run:
   ```bash
   ./scripts/run_validate_all.sh config/validation_manifest.csv
   ```

---

## Step 14: Web UI Testing

Start the server and use the browser-based Quick Test tab:

```powershell
valdo serve
# or
uvicorn src.api.main:app --reload --port 8000
```

1. Open `http://localhost:8000/ui`
2. Go to the **Quick Test** tab
3. Upload your fixed-width file and mapping JSON
4. Optionally upload a rules JSON
5. Toggle **Redact PII in report** as needed
6. Click **Validate**
7. Review metric cards and download the HTML report

---

## Step 15: CI/CD Integration

Add validation as a build gate in your pipeline:

#### GitLab CI

```yaml
validate-batch:
  extends: .valdo-validate
  variables:
    VALDO_FILE: data/files/p327_output.txt
    VALDO_MAPPING: config/mappings/p327_mapping.json
    VALDO_RULES: config/rules/p327_business_rules.json
```

The job exits with a non-zero code when validation fails, blocking the pipeline.

---

## Quick Reference — Common Commands

| Task                          | Command                                                                                   |
|-------------------------------|-------------------------------------------------------------------------------------------|
| Detect format                 | `valdo detect -f <file>`                                                                  |
| Parse and inspect             | `valdo parse -f <file>`                                                                   |
| Basic validation              | `valdo validate -f <file> -m <mapping> -o report.html --detailed`                         |
| Strict validation             | `valdo validate -f <file> -m <mapping> --strict-fixed-width --strict-level format`        |
| With business rules           | `valdo validate -f <file> -m <mapping> -r <rules> -o report.html`                         |
| Chunked (large files)         | `valdo validate -f <file> -m <mapping> --use-chunked --chunk-size 50000 --progress`       |
| Parallel chunked              | `valdo validate -f <file> -m <mapping> --use-chunked --workers 4 --progress`              |
| Multi-record                  | `valdo validate --file <file> --multi-record <config.yaml>`                               |
| Export failed rows            | `valdo validate -f <file> -m <mapping> --export-errors failed.txt`                        |
| Drift detection               | `valdo detect-drift --file <file> --mapping <mapping> --output drift.json`                |
| Infer mapping                 | `valdo infer-mapping -f <file> -o mapping.json`                                           |

---

## Troubleshooting

| Symptom                                    | Cause                                          | Fix                                                        |
|--------------------------------------------|-------------------------------------------------|------------------------------------------------------------|
| `Invalid mapping: field 'X' missing length`| Mapping CSV has blank Length column              | Fill in the Length for every fixed-width field              |
| Line length mismatches in report           | File has trailing spaces or mixed line endings   | Normalize line endings; check `total_record_length` in JSON |
| `Failed to parse fixed-width file`         | Mapping positions don't match actual file layout | Use `valdo parse` to inspect; adjust positions             |
| 0% coverage when running single test file  | Coverage gate requires full `tests/unit/` run    | Run `pytest tests/unit/ --cov=src`                         |
