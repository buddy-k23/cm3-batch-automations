# Generate Valdo Mapping + Rules CSV from Specification Document

Copy this entire prompt, then paste your specification document content below the `---` line.

This generates **both** the mapping CSV and rules CSV in one pass.

---

## Instructions

You are converting a batch file specification document into two CSV files for the **Valdo** file validation tool:

1. **Mapping CSV** — defines the file structure (fields, positions, types, lengths)
2. **Rules CSV** — defines validation rules (required checks, format checks, cross-row checks)

---

### OUTPUT 1: Mapping CSV

Generate a CSV with these columns:

```
field_name,data_type,position,length,target_name,required,format,transformation,valid_values,description
```

**Rules:**
- `field_name`: Source field name from spec (preserve original naming)
- `data_type`: `String`, `Numeric`, or `Date` (map from COBOL: `9(n)` → Numeric, `X(n)` → String, date formats → Date)
- `position`: Start position (1-indexed integer from spec)
- `length`: Field length in characters
- `target_name`: Target/destination field name. Snake_case lowercase if no target column in spec
- `required`: `Yes` if spec says `Y`/`Required`, else `No`
- `format`: Format pattern from spec (`9(5)`, `CCYYMMDD`, `X(18)`, or blank)
- `transformation`: Transformation logic from spec (summarize IF/ELSE as single line, e.g. `Default to '00040'`, `BR + CUS + LN`)
- `valid_values`: Valid values (pipe-separated if multiple, e.g. `A|I|C`)
- `description`: Brief description (under 80 chars)
- Include FILLER fields with `required=No`
- Deduplicate field names by adding `_2` suffix if repeated

---

### OUTPUT 2: Rules CSV

Generate a CSV with these columns:

```
Rule ID,Rule Name,Field,Type,Severity,Enabled,Message,Value
```

**Extract rules from:**

| Spec Pattern | Rule Type | Severity |
|-------------|-----------|----------|
| `Required = Y` | `not_empty` | `error` |
| `Format = 9(n)` | `numeric` | `error` |
| `Format = MM/DD/CCYY` or date | `date_format` | `error` |
| `Valid Values = specific value(s)` | `valid_values` | `error` |
| `Length` on required fields | `exact_length` | `error` |
| Numeric amounts | `min_value` (value: `0`) | `warning` |
| IF/ELSE transformation logic | `cross_field` | `warning` |
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

**Skip rules for:** FILLER fields, fields marked `N/A`, vague valid values like "Bank Control Table"

**Rule IDs:** `R001`-`R999` for per-row rules, `CR001`-`CR999` for cross-row rules

---

### Output Format

Output both CSVs clearly separated:

```
=== MAPPING CSV ===
field_name,data_type,position,length,target_name,required,description
...

=== RULES CSV ===
Rule ID,Rule Name,Field,Type,Severity,Enabled,Message,Value
...
```

---

## Your Specification Document

Paste your specification content below this line:


