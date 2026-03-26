# Generate Valdo Rules CSV from Specification Document

Copy this entire prompt, then paste your specification document content below the `---` line.

---

## Instructions

You are converting a batch file specification document into a business rules CSV template for the **Valdo** file validation tool. Extract validation rules from the spec's Required flags, Valid Values, Format codes, Transformation logic, and Notes.

### Output Format

Generate a CSV with exactly these columns (in this order):

```
Rule ID,Rule Name,Field,Type,Severity,Enabled,Message,Value
```

### Column Definitions

| Column | Description | Values |
|--------|-------------|--------|
| `Rule ID` | Unique ID: `R001`, `R002`... for field rules, `CR001`... for cross-row | Sequential |
| `Rule Name` | Snake_case descriptive name | e.g. `account_not_empty` |
| `Field` | Field name(s) the rule applies to | See syntax below |
| `Type` | Rule type | See type reference below |
| `Severity` | `error` or `warning` | Use `error` for Required fields, `warning` for soft checks |
| `Enabled` | `Yes` | Always `Yes` |
| `Message` | Human-readable error message | Describe what went wrong |
| `Value` | Rule parameter (if needed) | Depends on type |

### Field Syntax

| Syntax | Meaning | Example |
|--------|---------|---------|
| `FIELD_NAME` | Single field | `BALANCE` |
| `FIELD1\|FIELD2` | Multiple fields (cross_field or unique_composite) | `STATUS\|BALANCE` |
| `KEY>TARGET` | Key field → target field (cross_row) | `LN-NUM-ERT>BK-NUM-ERT` |

### Rule Type Reference

Extract these rules from the specification:

#### Per-Row Rules (from Required, Format, Valid Values columns)

| Spec Pattern | Rule Type | Value | Example |
|-------------|-----------|-------|---------|
| `Required = Y` | `not_empty` | (blank) | Field must not be empty |
| `Format = 9(n)` | `numeric` | (blank) | Field must be numeric |
| `Format = MM/DD/CCYY` or `CCYYMMDD` | `date_format` | The format string | `CCYYMMDD` |
| `Valid Values = X` (single value) | `valid_values` | The value | `32010` |
| `Valid Values = A\|B\|C` (multiple) | `valid_values` | Pipe-separated values | `A\|I\|C` |
| `Length = N` (for Required fields) | `exact_length` | The length | `18` |
| Known numeric field | `min_value` | `0` | Non-negative amounts |
| Known numeric field | `max_value` | Upper limit | `999999999` |

#### Cross-Field Rules (from Transformation IF/ELSE logic within same row)

| Spec Pattern | Rule Type | Value |
|-------------|-----------|-------|
| `IF field1 = X THEN field2 must be Y` | `cross_field` | `field1=X AND field2=Y` |
| `IF TYPE = '7' THEN field = 'C' ELSE 'I'` | `cross_field` | Condition expression |

#### Cross-Row Rules (from patterns across multiple rows)

| Spec Pattern | Rule Type (with `cross_row:` prefix) | Value |
|-------------|--------------------------------------|-------|
| "must be unique" / "no duplicates" | `cross_row:unique` | (blank) |
| "unique combination of fields" | `cross_row:unique_composite` | (blank) |
| "must be same for all rows with same key" | `cross_row:consistent` | (blank) |
| "1st then '1', 2nd then '2', nth then 'n'" / sequential | `cross_row:sequential` | Start value (usually `1`) |
| "count must match number of records" | `cross_row:group_count` | (blank) |
| "total must not exceed" / aggregate limit | `cross_row:group_sum` | Max value |

### How to Read the Specification

1. **Required = Y** → Generate a `not_empty` rule
2. **Format = 9(n)** → Generate a `numeric` rule
3. **Format with date pattern** → Generate a `date_format` rule
4. **Valid Values column** → Generate `valid_values` rule (skip vague entries like "Bank Control Table")
5. **Transformation with IF/ELSE** → Generate `cross_field` rule
6. **Notes mentioning "sequential", "1st/2nd/nth"** → Generate `cross_row:sequential` rule
7. **Notes mentioning "unique"** → Generate `cross_row:unique` rule
8. **Fields with same name appearing in multiple rows** → Consider `cross_row:consistent` for key fields
9. **Fields labeled as count/total** → Consider `cross_row:group_count` or `cross_row:group_sum`
10. **Skip** rules for FILLER fields and fields marked `N/A` unless they have explicit validation

### Example

**Input spec (pipe-separated):**
```
Transformation|Column|Definition|Data Type|Position|Format|Length|Required|Valid Values|Notes
Default to '00040'|BANK-CODE|Bank identifier code|Numeric|1|9(5)|5|Y|Bank Control Table|
Default to '001'|APPL-CODE|Application identifier|Numeric|6|9(3)|3|Y|Application Control Table|
BRANCH + CUST + LOAN|ACCT-KEY|Account key (composite)|String|9||18|Y||
Default to '32010'|TXN-TYPE|Transaction type code|Numeric|170|9(5)|5|Y|32010|
if 1st in batch then '1'; if nth then 'n'|BATCH-SEQ|Sequential batch item|Numeric|175|9(9)|9|Y||
|TXN-COUNT|Transaction count|Numeric|187|9(4)|4|Y||
```

**Output CSV:**
```csv
Rule ID,Rule Name,Field,Type,Severity,Enabled,Message,Value
R001,bank_code_not_empty,BANK-CODE,not_empty,error,Yes,Bank code must not be empty,
R002,bank_code_numeric,BANK-CODE,numeric,error,Yes,Bank code must be numeric,
R003,appl_code_not_empty,APPL-CODE,not_empty,error,Yes,Application code must not be empty,
R004,appl_code_numeric,APPL-CODE,numeric,error,Yes,Application code must be numeric,
R005,acct_key_not_empty,ACCT-KEY,not_empty,error,Yes,Account key must not be empty,
R006,acct_key_length,ACCT-KEY,exact_length,error,Yes,Account key must be exactly 18 characters,18
R007,txn_type_not_empty,TXN-TYPE,not_empty,error,Yes,Transaction type must not be empty,
R008,txn_type_valid,TXN-TYPE,valid_values,error,Yes,Transaction type must be 32010,32010
R009,batch_seq_not_empty,BATCH-SEQ,not_empty,error,Yes,Batch sequence must not be empty,
R010,batch_seq_numeric,BATCH-SEQ,numeric,error,Yes,Batch sequence must be numeric,
R011,txn_count_not_empty,TXN-COUNT,not_empty,error,Yes,Transaction count must not be empty,
R012,txn_count_numeric,TXN-COUNT,numeric,error,Yes,Transaction count must be numeric,
CR001,unique_account,ACCT-KEY,cross_row:unique,error,Yes,Account key must be unique across all rows,
CR002,sequential_batch,ACCT-KEY>BATCH-SEQ,cross_row:sequential,error,Yes,Batch sequence must be 1 2 3... per account,1
CR003,txn_count_matches,ACCT-KEY>TXN-COUNT,cross_row:group_count,error,Yes,Transaction count must match actual records per account,
CR004,consistent_bank,ACCT-KEY>BANK-CODE,cross_row:consistent,error,Yes,Bank code must be consistent for same account,
```

---

## Your Specification Document

Paste your specification content below this line:


