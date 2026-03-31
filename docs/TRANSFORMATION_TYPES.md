# Transformation Types Reference

## Overview

Transformations are parsed from free-text mapping spreadsheet cells by
`parse_transform()` in `src/transforms/transform_parser.py`.  Each text
pattern produces a typed Python dataclass from `src/transforms/models.py`.
The engine in `src/transforms/transform_engine.py` applies those objects to
source field values at run time.

This document covers all transform types in the Phase 1–4 system.

---

## 1. `Transform` (noop / pass-through)

The base class.  Produced when the text is empty, `None`, or unrecognised.
Downstream code treats it as a direct copy of the source field value.

**Parsed from:** empty cell, `None`, or any unrecognised text

**Mapping JSON:**
```json
{ "transform": { "type": "noop" } }
```

**Example:** `"ABC"` → `"ABC"` (unchanged)

---

## 2. `DefaultTransform`

Returns the source value when it is present and non-blank; otherwise returns
the configured default string.

**Parsed from:**
- `Default to 'VALUE'`
- `Default to VALUE`
- `Default = VALUE`

**Mapping JSON:**
```json
{ "transform": { "type": "default", "value": "100030" } }
```

**Example:**
- Source `""` + default `"100030"` → `"100030"`
- Source `"200000"` + default `"100030"` → `"200000"`

---

## 3. `BlankTransform`

Always outputs a blank (space or custom fill) value, ignoring the source.

**Parsed from:**
- `Leave Blank`
- `Leave blank <spaces>`
- `Pass Blank <spaces>`
- `Initialize to spaces`
- `Nullable --> Leave Blank`
- `Nullable --> 'FILL'` (sets `fill_value`)

**Mapping JSON:**
```json
{ "transform": { "type": "blank" } }
```

**Example:** any source → `""` (or spaces when `fill_char` is applied)

---

## 4. `ConstantTransform`

Always outputs a fixed constant string, ignoring the source value entirely.

**Parsed from:**
- `Pass 'VALUE'`
- `Hard-code to 'VALUE'`
- `Hard-Code to 'VALUE'`
- `Hardcode to 'VALUE'`

**Mapping JSON:**
```json
{ "transform": { "type": "constant", "value": "000" } }
```

**Example:** any source → `"000"`

---

## 5. `ConcatTransform` + `ConcatPart`

Concatenates multiple source fields in order, with optional per-field LPAD.

`ConcatPart` describes one field reference:

| Attribute | Description |
|-----------|-------------|
| `field_name` | Source row field to read |
| `lpad_width` | Left-pad to this width before concatenating (`0` = no pad) |
| `lpad_char` | Pad character (default `' '`) |

**Parsed from:**
- `FIELD1 + FIELD2 + FIELD3`
- `LPAD(FIELD,N) + FIELD2`
- `LPAD(FIELD,N,'C') + FIELD2`

**Mapping JSON:**
```json
{
  "transform": {
    "type": "concat",
    "parts": [
      { "field_name": "PREFIX", "lpad_width": 0, "lpad_char": " " },
      { "field_name": "ACCOUNT", "lpad_width": 10, "lpad_char": "0" }
    ]
  }
}
```

**Example:** `PREFIX="ACT"`, `ACCOUNT="123"` → `"ACT0000000123"`

---

## 6. `FieldMapTransform`

Maps a named source field directly to the output, equivalent to a column
rename.

**Parsed from:** A bare uppercase identifier with 2+ characters, e.g. `ACCOUNT_NUM`

**Mapping JSON:**
```json
{ "transform": { "type": "field_map", "source_field": "ACCOUNT_NUM" } }
```

**Example:** row `{"ACCOUNT_NUM": "9876"}` → `"9876"`

---

## 7. `DateFormatTransform`

Converts a date string from one `strptime` format to another `strftime`
format.  Returns `default_value` when the source is absent or unparseable.

Supported format tokens:

| Input text token | Input format | Output format |
|------------------|--------------|---------------|
| `CCYYMMDD` / `YYYYMMDD` | `%Y-%m-%d` | `%Y%m%d` |
| `MM/DD/CCYY` / `MM/DD/YYYY` | `%Y-%m-%d` | `%m/%d/%Y` |
| `MMDDCCYY` / `MMDDYYYY` | `%m%d%Y` | `%Y%m%d` |

**Parsed from:**
- `Convert to CCYYMMDD`
- `Date format CCYYMMDD`
- `Format as YYYYMMDD`
- `Reformat date to CCYYMMDD`
- `Convert date MMDDYYYY`
- `CCYYMMDD format`

**Mapping JSON:**
```json
{
  "transform": {
    "type": "date_format",
    "input_format": "%Y-%m-%d",
    "output_format": "%Y%m%d",
    "default_value": ""
  }
}
```

**Example:** `"2025-06-15"` → `"20250615"`; `""` → `""`

---

## 8. `NumericFormatTransform`

Zero-pads a numeric source value to a fixed width, with optional sign prefix
and implied decimal-place scaling.

| Attribute | Description |
|-----------|-------------|
| `length` | Total output width (includes sign char when `signed=True`) |
| `signed` | Prepend `'+'` or `'-'` to the value |
| `decimal_places` | Multiply source by `10 ** decimal_places` before padding |
| `default_value` | Returned for absent or non-numeric source |

**Parsed from:**
- `+9(N)` — signed COBOL picture (length = N+1)
- `9(N)` — unsigned COBOL picture
- `Signed numeric, length N`
- `Zero-pad to N`
- `Pad to N digits`
- `N-digit zero-filled` / `N-digit zero-fill`
- `Zero-fill to N positions`
- `Signed N-digit`

**Mapping JSON:**
```json
{
  "transform": {
    "type": "numeric_format",
    "length": 13,
    "signed": true,
    "decimal_places": 0
  }
}
```

**Example:** `"12345"` with `length=13, signed=True` → `"+000000012345"`

---

## 9. `ScaleTransform`

Multiplies (or divides) the numeric source value by a fixed factor and
returns the result as a string.

| Attribute | Description |
|-----------|-------------|
| `factor` | Multiplier (use `< 1` for division, e.g. `0.01` = divide by 100) |
| `decimal_places` | Fixed decimal places in output (`-1` = auto via `str()`) |
| `default_value` | Returned for absent or non-numeric source |

**Parsed from:**
- `Multiply by N`
- `Divide by N`
- `Scale by N`
- `Times by N`
- `Times N`
- `Divide result by N`

**Mapping JSON:**
```json
{ "transform": { "type": "scale", "factor": 100.0, "decimal_places": 0 } }
```

**Example:** `"123.45"` with `factor=100, decimal_places=0` → `"12345"`

---

## 10. `PadTransform`

Pads a source value to a target width without truncating.

| Attribute | Description |
|-----------|-------------|
| `length` | Target character width |
| `pad_char` | Fill character (default `' '`) |
| `direction` | `'left'` (LPAD) or `'right'` (RPAD) |

**Parsed from:**
- `Left pad to N with 'C'` / `LPAD to N with 'C'`
- `Right pad to N` / `Right pad to N with 'C'`
- `Pad to N with spaces` / `Pad to N`
- `Space-pad to N`
- `Zero-fill left to N`
- `Pad left N zeros`

**Mapping JSON:**
```json
{
  "transform": {
    "type": "pad",
    "length": 10,
    "pad_char": "0",
    "direction": "left"
  }
}
```

**Example:** `"42"` with `length=5, pad_char='0', direction='left'` → `"00042"`

---

## 11. `TruncateTransform`

Truncates a source value to at most `length` characters.

| Attribute | Description |
|-----------|-------------|
| `length` | Maximum characters to keep |
| `from_end` | When `True`, keep the last N chars instead of the first |

**Parsed from:**
- `Truncate to N`
- `Truncate to N chars` / `Truncate to N characters`
- `Truncate decimal places` (produces `length=0`, acts as annotation)

**Mapping JSON:**
```json
{ "transform": { "type": "truncate", "length": 8, "from_end": false } }
```

**Example:** `"ABCDEFGHIJ"` with `length=5` → `"ABCDE"`

---

## 12. Conditions

Conditions are used inside `ConditionalTransform`.  They are never produced
directly by `parse_transform()` but are embedded in conditional objects.

### `NullCheckCondition`

Tests whether a field is null (absent, `None`, or whitespace-only).

| `negate` | Semantics |
|----------|-----------|
| `False` (default) | IS NULL |
| `True` | IS NOT NULL |

**Parsed from (within IF expression):**
- `IF FIELD IS NULL THEN ...`
- `IF FIELD IS NOT NULL THEN ...`
- `IF FIELD not null THEN ...`

### `EqualityCondition`

Tests whether a field equals (or not-equals) a given value (case-sensitive,
whitespace-stripped).

**Parsed from (within IF expression):**
- `IF FIELD = 'VALUE' THEN ...`
- `IF FIELD != 'VALUE' THEN ...`
- `IF FIELD <> 'VALUE' THEN ...`

### `InCondition`

Tests whether a field value is a member of a list.

**Parsed from (within IF expression):**
- `IF FIELD IN ('A','B','C') THEN ...`
- `IF FIELD = 'A' or 'B' THEN ...`

---

## 13. `ConditionalTransform`

Dispatches to one of two child transforms depending on whether a condition
holds against the current row.

| Attribute | Description |
|-----------|-------------|
| `condition` | A `NullCheckCondition`, `EqualityCondition`, or `InCondition` |
| `then_transform` | Applied when condition is `True` |
| `else_transform` | Applied when condition is `False` (defaults to noop) |

**Parsed from:** `IF <condition> THEN <branch> [ELSE <branch>]`

Branch values can be:
- A quoted literal `'VALUE'` → `ConstantTransform`
- A bare uppercase field name → `FieldMapTransform`
- Any other recognised phrase (e.g. `Default to 'X'`, `Leave Blank`) → recursive `parse_transform()`

**Mapping JSON:**
```json
{
  "transform": {
    "type": "conditional",
    "condition": { "type": "null_check", "field": "AMOUNT", "negate": false },
    "then_transform": { "type": "constant", "value": "0" },
    "else_transform": { "type": "field_map", "source_field": "AMOUNT" }
  }
}
```

**Examples:**
- `IF AMOUNT IS NULL THEN '0' ELSE AMOUNT`
  - row `{"AMOUNT": ""}` → `"0"`
  - row `{"AMOUNT": "99"}` → `"99"`
- `IF STATUS = 'A' THEN 'ACTIVE' ELSE 'INACTIVE'`

---

## 14. `SequentialNumberTransform` + `SequentialCounter`

Assigns an incrementing sequence number to each processed record.

| Attribute | Description |
|-----------|-------------|
| `start` | Value emitted for the first record (default `1`) |
| `step` | Increment per record (default `1`) |
| `pad_length` | Zero-pad to this width (default `None` = no padding) |

`SequentialCounter` (`src/transforms/sequential_counter.py`) is the stateful
manager that tracks the current value across records.  When no counter is
provided to `apply_transform()` the transform falls back to `str(start)`.

**Parsed from:** `Sequential`, `sequential number`, `sequence`

**Mapping JSON:**
```json
{
  "transform": {
    "type": "sequential",
    "start": 1,
    "step": 1,
    "pad_length": 5
  }
}
```

**Example:** records 1, 2, 3 → `"00001"`, `"00002"`, `"00003"`

---

## Related Source Files

| File | Purpose |
|------|---------|
| `src/transforms/models.py` | All transform and condition dataclasses |
| `src/transforms/transform_parser.py` | `parse_transform()` — text → dataclass |
| `src/transforms/transform_engine.py` | `apply_transform()` — dataclass → string |
| `src/transforms/condition_evaluator.py` | `evaluate_condition()` — condition → bool |
| `src/transforms/sequential_counter.py` | Stateful counter for sequential transforms |
| `src/transforms/transform_orchestrator.py` | `TransformEngine` — high-level orchestration |
| `src/transforms/multi_record_transform_engine.py` | Multi-record-type transform engine |
| `src/transforms/transform_mismatch_reporter.py` | Mismatch reporting for transform output |

## Related Documentation

- [Mapping Quick Start](MAPPING_QUICKSTART.md)
- [Universal Mapping Guide](UNIVERSAL_MAPPING_GUIDE.md)
- [Usage and Operations Guide](USAGE_AND_OPERATIONS_GUIDE.md)
