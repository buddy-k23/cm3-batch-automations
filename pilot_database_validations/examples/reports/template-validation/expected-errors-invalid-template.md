# Expected Error Output for Invalid Template Examples

This file shows expected, BA/QA-readable validation issues for:
- `examples/templates/invalid/mapping-template-invalid.csv`
- `examples/templates/invalid/rules-template-invalid.csv`
- `examples/templates/invalid/file-config-invalid.csv`

## Suggested summary
- status: `FAILED`
- error_count: `11`
- warning_count: `0`

## Expected errors

```text
[mapping-template-invalid.csv | Mapping Fields | row 2 | data_type | ENUM_INVALID]
Invalid data_type 'number'. Allowed: string, numeric, date, boolean.
Fix: Replace 'number' with one allowed value.

[mapping-template-invalid.csv | Mapping Fields | row 3 | required | ENUM_INVALID]
Invalid required value 'Maybe'. Allowed: Y, N, Conditional.
Fix: Use Y, N, or Conditional.

[mapping-template-invalid.csv | Mapping Fields | row 4 | length | INTEGER_RANGE_INVALID]
Invalid length '0'. Value must be >= 1.
Fix: Set a positive integer length.

[mapping-template-invalid.csv | Mapping Fields | row 5 | position_end | POSITION_INVALID]
position_end (200) cannot be less than position_start (210).
Fix: Ensure position_end >= position_start.

[rules-template-invalid.csv | Rules | row 2 | group_by | GROUPBY_REQUIRED]
group_by is required when scope='group'.
Fix: Provide one or more grouping fields, e.g. account_id.

[rules-template-invalid.csv | Rules | row 3 | rule_id | RULE_DUPLICATE_ID]
Duplicate rule_id 'GRP_PRIMARY_UNIQUENESS'.
Fix: Use a unique rule_id for each rule row.

[rules-template-invalid.csv | Rules | row 4 | severity | ENUM_INVALID]
Invalid severity 'FATAL'. Allowed: ERROR, WARN, INFO.
Fix: Use ERROR, WARN, or INFO.

[rules-template-invalid.csv | Rules | row 5 | priority | INTEGER_INVALID]
Invalid priority 'high'. Priority must be an integer >= 1.
Fix: Replace with a numeric priority such as 10.

[file-config-invalid.csv | File Config | row 2 | delimiter | DEPENDENCY_MISSING]
delimiter is required when format='delimited'.
Fix: Provide a single-character delimiter (for example ',').

[file-config-invalid.csv | File Config | row 2 | record_length | INTEGER_RANGE_INVALID]
Invalid record_length '0'. Value must be >= 1 when provided.
Fix: Use a positive integer or leave blank when format='delimited'.

[file-config-invalid.csv | File Config | row 2 | header_enabled | ENUM_INVALID]
Invalid header_enabled value 'Maybe'. Allowed: Y, N.
Fix: Use Y or N.
```

## Expected machine-friendly JSON shape (example)

```json
[
  {
    "file": "mapping-template-invalid.csv",
    "sheet": "Mapping Fields",
    "row": 2,
    "column": "data_type",
    "error_code": "ENUM_INVALID",
    "message": "Invalid data_type 'number'. Allowed: string, numeric, date, boolean.",
    "hint": "Replace 'number' with one allowed value."
  }
]
```
