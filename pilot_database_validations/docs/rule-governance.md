# Rule Governance

## Governance Goals
- Keep rules auditable, deterministic, and reusable
- Prevent silent behavior drift
- Ensure complex business logic remains explainable

## Versioning
- Rule packs use semantic versioning: `MAJOR.MINOR.PATCH`
- **MAJOR**: breaking behavior/contract changes
- **MINOR**: backward-compatible new rules
- **PATCH**: bug fixes, wording, non-behavioral updates

## Promotion Levels
1. `draft`
2. `candidate`
3. `approved`
4. `deprecated`
5. `retired`

Only `approved` rules run in production mode.

## Wave 5 Review States (Extracted Candidate Governance)
Extracted candidates from `transform_logic` must pass explicit review states before pack promotion:
1. `extracted` (machine-produced candidate)
2. `in_review` (human review pending)
3. `approved` (eligible for promotion)
4. `rejected` (closed with rationale)
5. `deferred` (parked for later release)
6. `unresolved` (manual rule authoring required)

Rules in `in_review` or `unresolved` state block auto-promotion when governance checks are enabled.

## Required Artifacts per Rule Pack
- Rule JSON
- Schema validation result
- Test evidence (pass/fail snapshots)
- Compatibility assessment
- Change note entry

## Precedence Policy
- Explicit priority (low number = high priority)
- If priorities tie, deterministic tiebreaker by `ruleId`
- No implicit ordering by file location

## Conflict Resolution
When two rules disagree:
1. Compare scope specificity (group > record > field)
2. Compare severity (ERROR > WARN > INFO)
3. Compare priority
4. Escalate if still ambiguous

## Audit Log Fields
- `runId`
- `rulePackVersion`
- `ruleId`
- `ruleDecision`
- `inputFingerprint`
- `timestamp`
- `agent`

## Security/Privacy
- Mask sensitive values in violation outputs
- No raw SSN/account identifiers in telemetry
- Include reversible masking only if explicitly approved

## Deprecation Policy
- Mark deprecated rule at least 1 release before retirement
- Provide migration guidance and replacement rule IDs
