# Validation Result Contract v1

This document defines the normalized validation result payload consumed by reporting and downstream automation.

## Scope
Applies to JSON outputs from `validate` (standard and chunked), with optional strict fixed-width and business rule sections.

## Top-level required keys
- `valid` (boolean)
- `errors` (array)
- `warnings` (array)
- `total_rows` (number)

## Recommended keys (mode-dependent)
- `file_path` (string)
- `timestamp` (string)
- `issue_code_summary` (object)
- `strict_fixed_width` (object)
- `business_rules` (object)
- `statistics` (object; especially in chunked mode)

## `strict_fixed_width` section
When strict mode is enabled, include:
- `enabled` (boolean)
- `strict_level` (basic|format|all)
- `invalid_rows` (array)
- `errors_total` (number)
- `errors_truncated` (boolean)
- `errors_file` (string, optional)

## `business_rules` section
When business rules are executed or referenced:
- `enabled` (boolean)
- `violations` (array)
- `statistics` (object)
- `error` (string, optional)

## `statistics` (chunked mode)
Expected keys:
- `null_counts` (object)
- `empty_string_counts` (object)
- `duplicate_count` (number)
- `duplicate_check_limited` (boolean)
- `elapsed_seconds` (number)
- `rows_per_second` (number)
- `chunk_size` (number)
