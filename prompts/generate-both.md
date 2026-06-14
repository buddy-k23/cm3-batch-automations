# Generate Valdo Mapping + Rules CSV from Specification Document

Copy this entire prompt, then paste your specification document content below the `---` line.

This generates **both** the mapping CSV and rules CSV in one pass.

**Important:** If your specification is an Excel workbook with multiple sheets/tabs, specify which sheet contains the field mapping you want to convert. Common mapping sheets include record type names (e.g. "100", "200", "ORI", "CUS", "Batch Header"), while sheets like "Revision History", "Overview", "Filter Criteria", and lookup tables should be skipped.

**Tell me:**
1. Which sheet/tab should I use? (e.g. "Use sheet: ORI" or "Use sheet: Batch Header")
2. Which source system is this for? (e.g. "Source: SHAW", "Source: FDR") — this determines which transformation branch to follow for valid values

---

## Instructions

You are converting a batch file specification document into two CSV files for the **Valdo** file validation tool. The user will provide content from a specific sheet/tab — focus only on that data:

1. **Mapping CSV** — defines the file structure (fields, positions, types, lengths)
2. **Rules CSV** — defines validation rules (required checks, format checks, cross-row checks)

**General rules for both outputs:**
- **Struck-through text**: If any rows or values in the spec are struck through (strikethrough formatting), ignore them entirely — they represent deprecated or removed fields/values
- **Strip spaces**: Trim all trailing/leading spaces from field names, valid values, and descriptions

---

### OUTPUT 1: Mapping CSV

Generate a CSV with these columns (**Title Case headers required**):

```
Field Name,Data Type,Position,Length,Target Name,Required,Format,Transformation,Valid Values,Description
```

**Column rules:**
- `Field Name`: Source field name from spec (preserve original naming)
- `Data Type`: `String`, `Numeric`, or `Date` (map from COBOL: `9(n)` → Numeric, `X(n)` → String, date formats → Date)
- `Position`: Start position (1-indexed integer from spec)
- `Length`: Field length in characters
- `Target Name`: Snake_case lowercase (hyphens → underscores)
- `Required`: `Yes` if spec says `Y`/`Required`, else `No`
- `Format`: Format pattern from spec (`9(5)`, `CCYYMMDD`, `X(18)`, or blank)
- `Transformation`: Transformation logic from spec (summarize IF/ELSE as single line)
- `Valid Values`: Source-specific valid values derived from Transformation column (see CRITICAL section)
- `Description`: Brief description (under 80 chars)
- Include FILLER fields with `Required=No`
- Deduplicate field names by adding `_2` suffix if repeated

**CRITICAL — Length for fixed-width:** Every field MUST have a `Length` value. Derive from format (`9(5)`→5, `+9(12)V9(6)`→19), position gaps, or estimate.

---

### OUTPUT 2: Rules CSV

Generate a CSV with these columns:

```
Rule ID,Rule Name,Field,Rule Type,Severity,Enabled,Message,Expected / Values
```

**Extract rules from:**

| Spec Pattern | Rule Type | Severity |
|-------------|-----------|----------|
| `Required = Y` | `not_empty` | `error` |
| `Format = 9(n)` | `numeric` | `error` |
| `Format = MM/DD/CCYY` or date | `date_format` | `error` |
| Transformation produces specific codes | `valid_values` | `error` |
| `Length` on required fields | `exact_length` | `error` |
| Numeric amounts | `min_value` (value: `0`) | `warning` |
| IF/ELSE referencing other fields | `cross_field` | `warning` |
| "must be unique" | `cross_row:unique` | `error` |
| "1st then 1, 2nd then 2, nth then n" | `cross_row:sequential` (field syntax: `KEY>SEQ`) | `error` |
| "same for all rows with same key" | `cross_row:consistent` (field syntax: `KEY>TARGET`) | `error` |
| Count/total fields | `cross_row:group_count` (field syntax: `KEY>COUNT`) | `error` |
| Aggregate limits | `cross_row:group_sum` (field syntax: `KEY>SUM`, value: max) | `warning` |
| Unique combination | `cross_row:unique_composite` (field syntax: `F1\|F2`) | `error` |

**Field syntax for cross-row rules:**
- `FIELD` — single field (unique, not_empty, numeric, etc.)
- `FIELD1\|FIELD2` — multiple fields (unique_composite, cross_field)
- `KEY>TARGET` — key field → target field (consistent, sequential, group_count, group_sum)

**Skip rules for:** FILLER fields, fields marked `N/A`, fields where transformation says "Leave Blank"

**Rule IDs:** `R001`-`R999` for per-row rules, `CR001`-`CR999` for cross-row rules

---

### CRITICAL — Valid Values: Source-Specific, Not System-Level

This is the most important rule for generating correct CSVs. Specs typically have TWO sources of value information:

1. **Valid Values column**: Lists what the *target system supports* across ALL source systems
2. **Transformation Logic column**: Shows what *this specific source system* actually sends

**Always derive valid values from the Transformation column for the specified source.** The Valid Values column is often misleading because it includes codes from other sources.

**How to extract valid values from transformations:**

| Transformation Pattern | Valid Values | Example |
|----------------------|-------------|---------|
| `Default to '100030'` | `100030` | Single hardcoded value |
| `IF X THEN '1'; ELSE '0'` | `0\|1` | Binary from IF/ELSE |
| `IF X THEN 'CL' else 'AC'` | `CL\|AC` | Codes from IF/ELSE |
| `Hard-Code to "DPD"` | `DPD` | Hardcoded constant |
| `Default = USD` | `USD` | Default constant |
| `Day codes: Sun='00'...Sat='06', field = code+1` | `01\|02\|03\|04\|05\|06\|07` | Computed codes (preserve zero-padding!) |
| `Transformation: 0-NA, 1-WK, 3-MO, 4-QT, 5-SA, 6-AN, 8-BW` | `MO\|QT\|SA\|AN\|BW\|WK\| ` | Map source codes to output values |
| `Leave Blank <spaces>` | **(empty — no valid_values rule)** | Source always sends spaces |
| `Pass as is` / `IF X not null then X; ELSE Default to...` | **(empty — no valid_values rule)** | Dynamic source data |
| `IF X not null then X; ELSE Leave Blank` | **(empty — no valid_values rule)** | Dynamic source data |

**What to SKIP (never put in Valid Values or Expected / Values):**
- Sentences: "Must be less than or equal to BALANCE-AMT" → use `cross_field`, not `valid_values`
- References: "Bank Control Table" → skip (can't validate without the table)
- Descriptions: "Each digit represents a month" → skip
- System-level values when transformation says "Leave Blank" → skip (source doesn't send those)
- Values from the Valid Values column that the source never produces

**Preserve zero-padding:** `'01'` stays as `01`, not `1`. Fixed-width fields are character-based.

**Real-world example of the distinction:**

A spec might show:
- **Valid Values column**: `S|C|D|T|E` (all format types the target system supports)
- **Transformation column**: `Default to 'I'` (this source always sends `I`)

The correct valid value is `I`, not `S|C|D|T|E`.

Another example:
- **Valid Values column**: `spaces|B-Blocked|F-Frozen` (target system supports these)
- **Transformation column**: `Leave Blank <spaces>` (this source always sends spaces)

The correct action is to **skip** this field entirely — no valid_values rule needed.

---

### Output Format

Output both CSVs clearly separated:

```
=== MAPPING CSV ===
Field Name,Data Type,Position,Length,Target Name,Required,Format,Transformation,Valid Values,Description
...

=== RULES CSV ===
Rule ID,Rule Name,Field,Rule Type,Severity,Enabled,Message,Expected / Values
...
```

---

## Your Specification Document

Paste your specification content below this line:


