# Generate Valdo Rules CSV from Specification Document

Copy this entire prompt, then paste your specification document content below the `---` line.

**Important:** If your specification is an Excel workbook with multiple sheets/tabs, specify which sheet contains the field mapping you want to extract rules from. Use the same sheet you used for the mapping CSV (e.g. "Use sheet: 100" or "Use sheet: Batch Header").

**Tell me:**
1. Which sheet/tab should I use?
2. Which source system is this for? (e.g. "Source: SHAW") — valid values must reflect what this source actually sends, not what the target system supports

---

## Instructions

You are converting a batch file specification document into a business rules CSV template for the **Valdo** file validation tool. The user will provide content from a specific sheet/tab of their workbook. Extract validation rules from the spec's Required flags, Format codes, Transformation logic, and Notes.

### Output Format

Generate a CSV with exactly these columns (in this order):

```
Rule ID,Rule Name,Field,Rule Type,Severity,Enabled,Message,Expected / Values
```

### Column Definitions

| Column | Description | Values |
|--------|-------------|--------|
| `Rule ID` | Unique ID: `R001`, `R002`... for field rules, `CR001`... for cross-row | Sequential |
| `Rule Name` | Snake_case descriptive name | e.g. `account_not_empty` |
| `Field` | Field name(s) the rule applies to | See syntax below |
| `Rule Type` | Rule type | See type reference below |
| `Severity` | `error` or `warning` | Use `error` for Required fields, `warning` for soft checks |
| `Enabled` | `Yes` | Always `Yes` |
| `Message` | Human-readable error message | Describe what went wrong |
| `Expected / Values` | Rule parameter (if needed) | Depends on type |

### Field Syntax

| Syntax | Meaning | Example |
|--------|---------|---------|
| `FIELD_NAME` | Single field | `BALANCE` |
| `FIELD1\|FIELD2` | Multiple fields (cross_field or unique_composite) | `STATUS\|BALANCE` |
| `KEY>TARGET` | Key field → target field (cross_row) | `LN-NUM-ERT>BK-NUM-ERT` |

### Rule Type Reference

Extract these rules from the specification:

#### Per-Row Rules (from Required, Format, Transformation columns)

| Spec Pattern | Rule Type | Expected / Values | Example |
|-------------|-----------|-------------------|---------|
| `Required = Y` | `not_empty` | (blank) | Field must not be empty |
| `Format = 9(n)` | `numeric` | (blank) | Field must be numeric |
| `Format = MM/DD/CCYY` or `CCYYMMDD` | `date_format` | The format string | `CCYYMMDD` |
| Transformation produces specific codes | `valid_values` | Pipe-separated values | `CL\|AC` |
| Single hardcoded value from transformation | `valid_values` | The value | `USD` |
| `Length = N` (for Required fields) | `exact_length` | The length | `18` |
| Known numeric field | `min_value` | `0` | Non-negative amounts |

#### Cross-Field Rules (from Transformation IF/ELSE logic within same row)

| Spec Pattern | Rule Type | Expected / Values |
|-------------|-----------|-------------------|
| `IF field1 = X THEN field2 must be Y` | `cross_field` | `field1=X AND field2=Y` |

#### Cross-Row Rules (from patterns across multiple rows)

| Spec Pattern | Rule Type (with `cross_row:` prefix) | Expected / Values |
|-------------|--------------------------------------|-------------------|
| "must be unique" / "no duplicates" | `cross_row:unique` | (blank) |
| "unique combination of fields" | `cross_row:unique_composite` | (blank) |
| "must be same for all rows with same key" | `cross_row:consistent` | (blank) |
| "1st then '1', 2nd then '2', nth then 'n'" | `cross_row:sequential` | Start value (usually `1`) |
| "count must match number of records" | `cross_row:group_count` | (blank) |
| "total must not exceed" / aggregate limit | `cross_row:group_sum` | Max value |

### How to Read the Specification

1. **Required = Y** → Generate a `not_empty` rule
2. **Format = 9(n)** → Generate a `numeric` rule
3. **Format with date pattern** → Generate a `date_format` rule
4. **Transformation with hardcoded values** → Generate `valid_values` rule (see CRITICAL section below)
5. **Transformation with IF/ELSE referencing other fields** → Generate `cross_field` rule
6. **Notes mentioning "sequential", "1st/2nd/nth"** → Generate `cross_row:sequential` rule
7. **Notes mentioning "unique"** → Generate `cross_row:unique` rule
8. **Fields labeled as count/total** → Consider `cross_row:group_count` or `cross_row:group_sum`
9. **Skip** rules for FILLER fields and fields marked `N/A` unless they have explicit validation
10. **Struck-through text** → Ignore entirely (deprecated/removed)

### CRITICAL — Valid Values: Derive from Transformation, Not Valid Values Column

The `Expected / Values` column must contain ONLY actual codes/values that the **specified source system sends**. Derive these from the **Transformation Logic column**, not the Valid Values column.

**Why:** The Valid Values column in specs typically lists what the *target system supports* across all sources. The Transformation column shows what *this specific source* actually produces.

**How to extract valid values from transformations:**

| Transformation Pattern | Expected / Values |
|----------------------|-------------------|
| `Default to 'LS'` | `LS` |
| `IF X THEN '1'; ELSE '0'` | `0\|1` |
| `IF X THEN 'CL' else 'AC'` | `CL\|AC` |
| `Hard-Code to "DPD"` | `DPD` |
| `Default = USD` | `USD` |
| `Day codes: Sunday='00'...Saturday='06', then +1` | `01\|02\|03\|04\|05\|06\|07` |
| `Leave Blank <spaces>` | **No valid_values rule** — field always has spaces |
| `Pass as is` / `IF X not null then X; ELSE Default to...` | **No valid_values rule** — dynamic source data |

**Preserve zero-padding:** `'01'` stays as `01`, not `1`. Fixed-width fields are character-based.

**WRONG examples — never do this:**
```
R007,disputed_check,DISPUTED-AMT,valid_values,error,Yes,...,Must be less than BALANCE-AMT   ← WRONG: sentence, use cross_field
R008,cycle_check,CYCLE-ID,valid_values,error,Yes,...,Cycle ID = 00 is used for Manual Setup  ← WRONG: description, skip it
R009,stat_code_check,ACCT-STAT-CODE-1,valid_values,error,Yes,..., |B|F                       ← WRONG: system-level values, but transformation says "Leave Blank" so source never sends B or F
```

### Example

**Input spec (pipe-separated):**
```
Transformation|Column|Data Type|Position|Format|Length|Required|Valid Values
Default to '00040'|BANK-CODE|Numeric|1|9(5)|5|Y|Bank Control Table
Default to '001'|APPL-CODE|Numeric|6|9(3)|3|Y|Application Control Table
BRANCH + CUST + LOAN|ACCT-KEY|String|9||18|Y|
IF CLOS='1' then 'CL' else 'AC'|CUT-OFF-CODE|String|27|X(3)|3|N|Various codes
Leave Blank <spaces>|DEPOSIT-IND|String|30||1|N|* or spaces
Default to '32010'|TXN-TYPE|Numeric|31|9(5)|5|Y|32010
```

**Output CSV:**
```csv
Rule ID,Rule Name,Field,Rule Type,Severity,Enabled,Message,Expected / Values
R001,bank_code_not_empty,BANK-CODE,not_empty,error,Yes,Bank code must not be empty,
R002,bank_code_numeric,BANK-CODE,numeric,error,Yes,Bank code must be numeric,
R003,bank_code_valid,BANK-CODE,valid_values,error,Yes,Bank code must be 00040,00040
R004,appl_code_not_empty,APPL-CODE,not_empty,error,Yes,Application code must not be empty,
R005,appl_code_numeric,APPL-CODE,numeric,error,Yes,Application code must be numeric,
R006,appl_code_valid,APPL-CODE,valid_values,error,Yes,Application code must be 001,001
R007,acct_key_not_empty,ACCT-KEY,not_empty,error,Yes,Account key must not be empty,
R008,acct_key_length,ACCT-KEY,exact_length,error,Yes,Account key must be exactly 18 characters,18
R009,cut_off_code_valid,CUT-OFF-CODE,valid_values,error,Yes,Cut-off code must be CL or AC,CL|AC
R010,txn_type_not_empty,TXN-TYPE,not_empty,error,Yes,Transaction type must not be empty,
R011,txn_type_numeric,TXN-TYPE,numeric,error,Yes,Transaction type must be numeric,
R012,txn_type_valid,TXN-TYPE,valid_values,error,Yes,Transaction type must be 32010,32010
CR001,unique_account,ACCT-KEY,cross_row:unique,error,Yes,Account key must be unique across all rows,
```

Note: No `valid_values` rule for DEPOSIT-IND because the transformation says "Leave Blank" — the source always sends spaces regardless of what the target system supports.

---

## Your Specification Document

Paste your specification content below this line:


