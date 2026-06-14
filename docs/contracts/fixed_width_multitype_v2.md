# Fixed-Width Multi-Record-Type Contract v2

This document defines the **internal normalized contract** for validating fixed-width files that may contain one or more record types with different row widths.

## Goals
- Support both:
  - legacy single-layout fixed-width mappings
  - multi-record-type fixed-width mappings
- Keep deterministic classification and parity across standard/chunked validation.
- Keep header/trailer **optional by default** unless explicitly required.

## Backward Compatibility
Legacy single-layout mappings are normalized in-memory into this v2 contract as one record type (`DETAIL`) with `classification.kind=default`.

No required breaking changes to existing mapping files.

---

## Normalized Top-Level Shape

```json
{
  "version": "2.0",
  "mapping_name": "P327_full_in_sheet_order_strict",
  "format": "fixed_width",
  "record_types": [
    {
      "id": "DETAIL",
      "description": "Default transaction row",
      "classification": { "kind": "default" },
      "expected_total_width": 2809,
      "fields": []
    }
  ],
  "file_rules": {
    "required_record_types": [],
    "sequence_rules": [],
    "reconciliation_rules": [],
    "unknown_record_policy": "error"
  },
  "metadata": {
    "source_mapping_version": "legacy|v2",
    "created_date": "2026-02-22T00:00:00Z",
    "last_modified": "2026-02-22T00:00:00Z"
  }
}
```

## `record_types[]`
Required fields:
- `id` (string, unique)
- `classification` (object; one of supported kinds)
- `fields` (array)

Optional fields:
- `description` (string)
- `expected_total_width` (number)

### Field contract (`record_types[].fields[]`)
Required:
- `name` (string)
- `position` (number, 1-based)
- `length` (number > 0)
- `data_type` (string)
- `required` (boolean)

Optional:
- `target_name` (string)
- `format` (string|null)
- `valid_values` (array)
- `description` (string)

---

## Classification Contract

`record_types[].classification.kind` supports:

### 1) Discriminator
```json
{
  "kind": "discriminator",
  "position": 1,
  "length": 1,
  "allowed_values": ["H"]
}
```

### 2) Length
```json
{
  "kind": "length",
  "expected_total_width": 120
}
```

### 3) Default
```json
{
  "kind": "default"
}
```

### Deterministic precedence
1. discriminator match
2. length match
3. default match
4. unknown-record handling (`file_rules.unknown_record_policy`)

If multiple record types match the same precedence, emit a structural ambiguity error (do not guess).

---

## File Rules (Optional)

```json
{
  "file_rules": {
    "required_record_types": ["DETAIL"],
    "sequence_rules": [
      {
        "name": "header_before_detail",
        "kind": "must_precede",
        "first": "HEADER",
        "then": "DETAIL"
      }
    ],
    "reconciliation_rules": [
      {
        "name": "trailer_detail_count",
        "kind": "count_match",
        "source_record_type": "TRAILER",
        "source_field": "DETAIL-COUNT",
        "target_record_type": "DETAIL"
      }
    ],
    "unknown_record_policy": "error"
  }
}
```

### Defaults
- `required_record_types`: empty
- `sequence_rules`: empty
- `reconciliation_rules`: empty
- `unknown_record_policy`: `error`

### Header/Trailer policy
Header/trailer are optional by default.
They are only enforced when included in `required_record_types` and/or explicit sequence/reconciliation rules.

---

## Result Contract Additions (for v2 support)

Validation outputs should include:

```json
{
  "record_type_summary": {
    "DETAIL": { "rows": 25, "errors": 0, "warnings": 116 },
    "HEADER": { "rows": 0, "errors": 0, "warnings": 0 }
  },
  "classification_stats": {
    "unknown_rows": 1,
    "ambiguous_rows": 0
  },
  "structural_issues": [
    {
      "code": "UNKNOWN_RECORD_TYPE",
      "severity": "error",
      "line_number": 42,
      "row_length": 97,
      "message": "No matching record type for line 42 (length=97)."
    }
  ]
}
```

These additions should be produced equivalently in standard and chunked validation modes.

---

## Validation Expectations

### Single-record-type files
- Must validate successfully without header/trailer.
- Should run without multi-type routing overhead where possible.

### Multi-record-type files
- Each row must be classified deterministically and validated by selected type.
- Unknown/ambiguous rows must be reported with line number and row length.

### Structural checks
- Only enforced when configured in `file_rules`.

---

## Error Domains
Keep domains separate for reporting and troubleshooting:
- `mapping_errors` (invalid mapping contract)
- `data_errors` (row/field content validation)
- `structural_errors` (file-level sequence/classification/reconciliation)

This separation is required for clear diagnostics and KPI/report rollups.
