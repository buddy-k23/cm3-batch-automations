# Generate Valdo Mapping CSV from Specification Document

Copy this entire prompt, then paste your specification document content below the `---` line.

---

## Instructions

You are converting a batch file specification document into a CSV mapping template for the **Valdo** file validation tool.

### Output Format

Generate a CSV with exactly these columns (in this order):

```
field_name,data_type,position,length,target_name,required,format,transformation,valid_values,description
```

### Column Definitions

| Column | Description | Values |
|--------|-------------|--------|
| `field_name` | Source field name from the spec (preserve original naming) | e.g. `BK-NUM-ERT` |
| `data_type` | One of: `String`, `Numeric`, `Date` | See type mapping below |
| `position` | Start position (1-indexed integer) | e.g. `1` |
| `length` | Field length in characters | e.g. `5` |
| `target_name` | Target/destination field name. Use snake_case if spec has a target column; otherwise lowercase source name with hyphens → underscores | e.g. `bk_num_ert` |
| `required` | `Yes` or `No` | `Y` → `Yes`, `N` or `N/A` or blank → `No` |
| `format` | Format pattern from spec | `9(5)`, `CCYYMMDD`, `MM/DD/CCYY`, `X(18)`, or blank |
| `transformation` | Transformation logic from spec (summarize IF/ELSE as single line) | e.g. `Default to '00040'`, `BR + CUS + LN` |
| `valid_values` | Valid values from spec (pipe-separated if multiple) | e.g. `A\|I\|C`, `32010` |
| `description` | Brief description from the Definition/Notes columns | Keep under 80 chars |

### Type Mapping Rules

Convert the spec's data types and format codes to Valdo types:

| Spec Format | Valdo Type | Notes |
|-------------|-----------|-------|
| `Numeric`, `9(n)`, `S9(n)`, `9(n)V9(m)` | `Numeric` | COBOL numeric picture |
| `String`, `X(n)`, `A(n)`, `Alpha` | `String` | COBOL string picture |
| `Date`, `MM/DD/CCYY`, `CCYYMMDD`, `YYYYMMDD` | `Date` | Any date format |
| `FILLER`, filler, spacer | `String` | Always String |
| Blank/missing format with numeric position+length | `String` | Default to String when ambiguous |

### Position Calculation

- If the spec provides positions, use them directly (convert to integer)
- If positions are missing, calculate from lengths: field N starts at (sum of lengths of fields 1..N-1) + 1
- First field always starts at position 1

### Special Handling

- **Duplicate field names**: If a field appears multiple times (e.g. for different record types), include all occurrences — add a suffix like `_2` to avoid collisions
- **FILLER fields**: Include them with `required=No` and description "Filler/reserved"
- **Transformation column**: Ignore transformation logic for mapping CSV (it goes into rules CSV)
- **Valid Values column**: Ignore for mapping CSV (it goes into rules CSV)

### Example

**Input spec (pipe-separated):**
```
Transformation|Column|Definition|Data Type|Position|Format|Length|Required|Valid Values|Notes
Default to '00040'|BANK-CODE|Bank identifier code|Numeric|1|9(5)|5|Y|Bank Control Table|
Default to '001'|APPL-CODE|Application identifier|Numeric|6|9(3)|3|Y|Application Control Table|
BRANCH + CUST + LOAN|ACCT-KEY|Account key (composite)|String|9||18|Y||
|TXN-DATE|Transaction effective date|Date|160|MM/DD/CCYY|10|Y||
Nullable|FILLER-01|Filler for alignment|String|30||130|N/A||Initialize to spaces
Default to '32010'|TXN-TYPE|Transaction type code|Numeric|170|9(5)|5|Y|32010|
```

**Output CSV:**
```csv
field_name,data_type,position,length,target_name,required,format,transformation,valid_values,description
BANK-CODE,Numeric,1,5,bank_code,Yes,9(5),Default to '00040',,Bank identifier code
APPL-CODE,Numeric,6,3,appl_code,Yes,9(3),Default to '001',,Application identifier
ACCT-KEY,String,9,18,acct_key,Yes,,BRANCH + CUST + LOAN,,Account key (composite)
FILLER-01,String,30,130,filler_01,No,,Initialize to spaces,,Filler for alignment
TXN-DATE,Date,160,10,txn_date,Yes,MM/DD/CCYY,,,Transaction effective date
TXN-TYPE,Numeric,170,5,txn_type,Yes,9(5),Default to '32010',32010,Transaction type code
```

---

## Your Specification Document

Paste your specification content below this line:


