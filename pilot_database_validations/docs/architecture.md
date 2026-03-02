# Architecture (Phase 1)

## Objective
Build a generic, metadata-driven source-to-target validation framework for Oracle -> fixed-width/delimited outputs.

## Principles
1. **Config over code**: mapping + rules drive behavior
2. **Deterministic execution**: same input/config => same output
3. **Layered validation**: field, record, group, file
4. **Explainable failures**: every violation tied to ruleId + evidence
5. **Auditability**: versioned mapping/rules + telemetry + run artifacts

## High-Level Components

1. **Ingestion Layer**
- Reads source query results from Oracle extract
- Normalizes datatypes into canonical row model

2. **Mapping Compiler**
- Validates mapping against `schemas/mapping.schema.json`
- Builds execution plan (field projections, transforms, layout positions)

3. **Rule Engine**
- Validates rule pack against `schemas/rules.schema.json`
- Executes rules by scope:
  - field -> record -> group -> file
- Supports severity and deterministic priority order

4. **Format Adapters**
- Fixed-width adapter: position/length/padding checks
- Delimited adapter: delimiter/quote/escape checks

5. **Reconciliation Layer**
- Header count checks
- Control totals (counts/sums/hashes)
- Transaction distribution checks

6. **Reporting + Telemetry**
- Summary report (`schemas/report.schema.json`)
- Detailed record-level CSV
- Telemetry (`schemas/telemetry.schema.json`)

## Canonical Data Model (concept)
- `run`: metadata and version references
- `record`: normalized source row + derived fields + transactionCode
- `group`: records grouped by account key(s)
- `violation`: ruleId, scope, severity, expected, actual, location

## Execution Pipeline (Phase 1)
1. Load mapping + rules
2. Schema validation
3. Compile mapping plan
4. Extract/normalize source rows
5. Apply transforms
6. Run validation scopes (field, record, group, file)
7. Emit reports + telemetry

## Complex Scenario Support
For account/contact sequencing:
- Group key: account identifier
- Ordering: primary -> secondary -> tertiary (stable)
- Sequence derivation and checks as rules (not hardcoded)
- Fixed-width positional validations for sequence field

## Non-Functional Requirements
- Throughput target: configurable; baseline measured in telemetry
- Deterministic output hash for regression confidence
- Masked PII in logs/reports

## ADR placeholders
- ADR-001 Rule DSL and precedence
- ADR-002 Group scope semantics
- ADR-003 Header/trailer reconciliation strategy
