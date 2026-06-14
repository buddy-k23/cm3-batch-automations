# ADR 0003: Fixed-Width v2 Normalization (Task 1)

- Status: Accepted
- Date: 2026-02-23

## Context
We need to support variable-width fixed-width files with multiple record types, while preserving behavior for existing single-layout mappings.

## Decision
- Introduce a normalized internal v2 contract for fixed-width mappings (`record_types` + `file_rules`).
- Add a legacy normalizer that converts existing fixed-width mappings to a single `DETAIL` record type (`classification.kind=default`).
- Keep non-fixed-width mappings unchanged.
- Add minimal v2 contract validation helper for early structural errors (duplicate record type ids, invalid classification kind, multiple defaults).

## Consequences
- Backward compatibility is preserved for current mappings.
- Future classifier/validator work can target one canonical shape.
- Validation/reporting can distinguish structural issues from data issues in later tasks.
