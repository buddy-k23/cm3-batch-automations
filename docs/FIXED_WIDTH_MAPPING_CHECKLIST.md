# Fixed-Width Mapping Checklist & Failure Playbook

Use this before running strict validation on fixed-width files.

## Checklist

1. **Headers present**
   - Required: `Field Name`, `Data Type`
   - For fixed-width: `Position`, `Length`

2. **Field names are unique**
   - No duplicate `Field Name` values.

3. **Position/Length are complete and numeric**
   - If one is provided, both must be provided.
   - Both must be positive integers.

4. **No overlap in spans**
   - Ensure each field range does not overlap previous fields.
   - Typical pattern: sorted by `Position`, then `Position + Length - 1` strictly increases.

5. **Required fields are marked correctly**
   - `Required` should be `Y/N` (or equivalent accepted values).

6. **Format and valid values are coherent**
   - `Format` should match expected PIC/date pattern when provided.
   - `Valid Values` may be `A|B|C` (or comma-separated if quoted in CSV).

## Common Failure Playbook

### Error: missing required 'length'
- Cause: field row has blank `Length` in fixed-width mapping.
- Fix: populate `Length` (and verify `Position`).

### Error: duplicate field name
- Cause: repeated `Field Name` rows.
- Fix: make each field name unique.

### Error: overlapping or out-of-order fixed-width span
- Cause: current row starts before previous row end.
- Fix: reorder rows and correct `Position`/`Length` so spans do not overlap.

### Many format errors in strict mode
- Cause: wrong offsets/lengths or wrong `Format` pattern.
- Fix: validate record slicing first (`Position` + `Length`) before adjusting format strings.

### Required fields not showing as expected
- Verify:
  - `Required=Y` in mapping
  - strict mode enabled (`--strict-fixed-width --strict-level format|all`)
  - whitespace handling in source rows

## Recommended command sequence

```bash
# 1) Convert mapping templates with strict checks
valdo convert-mappings --input-dir mappings/csv --output-dir config/mappings --format fixed_width

# 2) Run strict validation (chunked)
valdo validate -f <data.txt> -m <mapping.json> --use-chunked --strict-fixed-width --strict-level format -o reports/validation.html
```
