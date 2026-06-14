# Scenario: Account Relationship Sequencing (Primary/Secondary/Tertiary)

## Problem
For a single account, multiple customer/contact transactions may exist. Output requires deterministic relationship sequencing and positional encoding in the target file.

## Business Behavior (as understood)
- Account-level records and contact-level records can coexist in same output file.
- Contact records must be ordered per relationship priority.
- Sequence/reference value (e.g., 998, 997, 996...) must be assigned per account.
- If no primary exists, first secondary may be promoted based on rule.
- Sequence value must be placed at exact mapped position/length.

## Canonical Grouping Key
- `account_id` (or mapped equivalent such as `LN-NUM-ERT`/`ACCT-NUM`)

## Ordering Strategy (default)
1. Primary
2. Secondary (by source stable order)
3. Tertiary / others (by source stable order)

## Derived Sequence Rule (example pack behavior)
- Primary -> `998`
- Secondary #1 -> `997`
- Secondary #2 -> `996`
- ... decrement by one per additional relationship
- left-pad when target length requires it

## Validation Rules

### Group-level
- Exactly one effective primary after transformation rules
- No duplicate `contact_id` for same `account_id`
- Sequence numbers unique within account group
- Sequence numbers monotonic descending in emitted order

### Record-level
- Relationship code maps to allowed output code set
- Sequence field length/format matches mapping

### File-level
- Header detail count equals emitted records
- Transaction code distribution matches expected by scenario

## Failure Examples
- Two primaries in one account group
- Missing sequence for a secondary
- Sequence not at required position in fixed-width output
- Header count mismatch after filtering/transform

## Test Data Requirements
- account with only primary
- account with primary + multiple secondaries
- account with no primary but secondary present (promotion case)
- account with duplicate relationships/contact IDs
- mixed transaction types for same account

## Expected Artifacts
- summary validation report
- record-level CSV with rule IDs and observed/expected values
- telemetry with per-rule fail counts and phase timings
