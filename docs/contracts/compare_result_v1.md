# Compare Result Contract v1

This document defines the normalized compare result payload consumed by CLI/API surfaces.

Authoritative schema file: `docs/contracts/compare_result_v1.schema.json`

## Top-level required keys
- `total_rows_file1` (integer)
- `total_rows_file2` (integer)
- `matching_rows` (integer)

## Recommended keys
- `only_in_file1` (integer)
- `only_in_file2` (integer)
- `differences` (integer)
- `report_url` (string|null)
- `field_statistics` (object|null)
