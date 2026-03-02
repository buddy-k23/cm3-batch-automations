# Risk Register

## R1: Ambiguous BA/QA template columns
- Impact: high
- Probability: high
- Mitigation: header alias map + strict template spec + row-level error reports
- Owner: Template QA Agent

## R2: Rule DSL ambiguity for complex group logic
- Impact: high
- Probability: medium
- Mitigation: ADR for precedence + explicit scope semantics + conflict checks
- Owner: Contract Agent + Architect Agent

## R3: Non-deterministic output ordering
- Impact: high
- Probability: medium
- Mitigation: stable sort keys + deterministic execution contract + output hash tests
- Owner: Engine Agent

## R4: Fixed-width positional defects
- Impact: high
- Probability: medium
- Mitigation: overlap/length checks + golden file byte-level tests
- Owner: Format Adapter Agent

## R5: PII leakage in reports/telemetry
- Impact: high
- Probability: medium
- Mitigation: masking policy + automated redaction checks in CI
- Owner: Reporting + Telemetry Agent

## R6: Scope creep during implementation
- Impact: medium
- Probability: high
- Mitigation: stage gates + planning agent reprioritization + explicit out-of-scope list
- Owner: Planning Agent

## R7: Performance regressions on large files
- Impact: medium
- Probability: medium
- Mitigation: baseline benchmarks + regression threshold alerts
- Owner: QA/Adversarial Agent
