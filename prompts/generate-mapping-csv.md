# Generate Valdo Mapping CSV from Specification Document

Copy this entire prompt, then paste your specification document content below the `---` line.

**Important:** If your specification is an Excel workbook with multiple sheets/tabs, specify which sheet contains the field mapping you want to convert. Common mapping sheets include record type names (e.g. "100", "200", "ORI", "CUS", "Batch Header"), while sheets like "Revision History", "Overview", "Filter Criteria", and lookup tables should be skipped.

**Tell me:**
1. Which sheet/tab should I use? (e.g. "Use sheet: 100" or "Use sheet: Batch Header")
2. Which source system is this for? (e.g. "Source: SHAW", "Source: FDR") — this determines which transformation branch to follow for valid values

---

## Instructions

You are converting a batch file specification document into a CSV mapping template for the **Valdo** file validation tool. The user will provide content from a specific sheet/tab of their workbook — focus only on that data.

### Output Format

Generate a CSV with exactly these columns (in this order, **Title Case headers required**):

```
Field Name,Data Type,Position,Length,Target Name,Required,Format,Transformation,Valid Values,Description
```

### Column Definitions

| Column | Description | Values |
|--------|-------------|--------|
| `Field Name` | Source field name from the spec (preserve original naming) | e.g. `BK-NUM-ERT` |
| `Data Type` | One of: `String`, `Numeric`, `Date` | See type mapping below |
| `Position` | Start position (1-indexed integer) | e.g. `1` |
| `Length` | Field length in characters | e.g. `5` |
| `Target Name` | Target/destination field name. Use snake_case: lowercase with hyphens converted to underscores | e.g. `bk_num_ert` |
| `Required` | `Yes` or `No` | `Y` → `Yes`, `N` or `N/A` or blank → `No` |
| `Format` | Format pattern from spec | `9(5)`, `CCYYMMDD`, `MM/DD/CCYY`, `X(18)`, or blank |
| `Transformation` | Transformation logic from spec (summarize IF/ELSE as single line) | e.g. `Default to '00040'`, `BR + CUS + LN` |
| `Valid Values` | Valid values the source actually sends (pipe-separated if multiple) | e.g. `CL\|AC`, `USD` |
| `Description` | Brief description from the Definition/Notes columns | Keep under 80 chars |

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
- **FILLER fields**: Include them with `Required=No` and description "Filler/reserved"
- **Transformation column**: Summarize briefly in the `Transformation` column (single line, no newlines)
- **Strip spaces**: Trim all trailing/leading spaces from field names, valid values, and descriptions
- **Struck-through text**: If any rows or values in the spec are struck through (strikethrough formatting), ignore them entirely — they represent deprecated or removed fields/values

### CRITICAL — Length for Fixed-Width

Every field MUST have a `Length` value. If the spec doesn't provide it:
  - Derive from format: `9(5)` → length 5, `X(18)` → length 18, `+9(12)V9(6)` → length 19 (12+6+1 for sign)
  - Derive from position: next field's position - this field's position
  - If neither available, estimate from the data type (String=10, Numeric=8, Date=8) and add a comment in description

### CRITICAL — Valid Values: Source-Specific, Not System-Level

Specs often contain TWO types of value lists in different columns:
1. **System-level values** (in the Valid Values column): What the target system *supports* across all source systems
2. **Source-specific values** (in the Transformation Logic column): What this specific source system *actually sends*

**Always derive valid values from the Transformation column for the specified source system.** The Valid Values column in the spec often lists all possible values the target system accepts, but the source may only ever produce a subset.

**How to extract valid values from transformations:**

| Transformation Pattern | Valid Values to Extract |
|----------------------|----------------------|
| `Default to '100030'` (single hardcoded value) | `100030` |
| `IF X THEN '1'; ELSE '0'` | `0\|1` |
| `IF X THEN 'CL' else 'AC'` | `CL\|AC` |
| `Hard-Code to "DPD"` | `DPD` |
| `Default = USD` | `USD` |
| `Day of Week: Sunday='00', Monday='01'...Saturday='06'` | `00\|01\|02\|03\|04\|05\|06` |
| `Leave Blank <spaces>` | **Leave Valid Values empty** — field always has spaces |
| `Pass as is` / `IF X not null then X; ELSE Default to...` | **Leave Valid Values empty** — value comes from source data |
| `IF X not null then X; ELSE Leave Blank` | **Leave Valid Values empty** — dynamic source data |

**What to SKIP (never put in Valid Values):**
- Sentences: "Must be less than or equal to BALANCE-AMT" → skip (this is a cross_field rule, not a valid value)
- References: "Bank Control Table", "Application Control Table" → skip (can't validate without the table)
- Descriptions: "Each digit represents a month", "Cycle ID = 00 is used for..." → skip
- System-level lists when transformation says "Leave Blank" → skip (source doesn't send those values)
- Values from the Valid Values column that describe what the *target system* supports but the *source* never sends

**Preserve zero-padding:** If the spec shows `'01'`, `'02'`, etc., keep them as `01|02|03`, not `1|2|3`. Fixed-width fields are character-based and padding matters.

### Example

**Input spec (pipe-separated):**
```
Transformation|Column|Definition|Data Type|Position|Format|Length|Required|Valid Values|Notes
Default to '00040'|BANK-CODE|Bank identifier code|Numeric|1|9(5)|5|Y|Bank Control Table|
Default to '001'|APPL-CODE|Application identifier|Numeric|6|9(3)|3|Y|Application Control Table|
BRANCH + CUST + LOAN|ACCT-KEY|Account key (composite)|String|9||18|Y||
IF CLOS='1' then 'CL' else 'AC'|CUT-OFF-CODE|Cut-off reason|String|27|X(3)|3|N|Various codes|
Leave Blank <spaces>|DEPOSIT-IND|Deposit indicator|String|30||1|N|* or spaces|Asterisk=deposit
Default to '32010'|TXN-TYPE|Transaction type code|Numeric|31|9(5)|5|Y|32010|
```

**Output CSV:**
```csv
Field Name,Data Type,Position,Length,Target Name,Required,Format,Transformation,Valid Values,Description
BANK-CODE,Numeric,1,5,bank_code,Yes,9(5),Default to '00040',00040,Bank identifier code
APPL-CODE,Numeric,6,3,appl_code,Yes,9(3),Default to '001',001,Application identifier
ACCT-KEY,String,9,18,acct_key,Yes,,BRANCH + CUST + LOAN,,Account key (composite)
CUT-OFF-CODE,String,27,3,cut_off_code,No,X(3),IF CLOS='1' then 'CL' else 'AC',CL|AC,Cut-off reason
DEPOSIT-IND,String,30,1,deposit_ind,No,,Leave Blank <spaces>,,Deposit indicator
TXN-TYPE,Numeric,31,5,txn_type,Yes,9(5),Default to '32010',32010,Transaction type code
```

Note: DEPOSIT-IND has no valid values because the transformation says "Leave Blank" — the source always sends spaces, so the system-level values (`*` or spaces) from the spec are irrelevant for this source.

---

## Your Specification Document

Paste your specification content below this line:


