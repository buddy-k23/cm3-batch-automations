# Dependency Map

## Critical Path
1. Planning Agent outputs approved
2. Contract schemas frozen
3. Template ingest model frozen
4. Converter prototype functional
5. Core engine supports DSL scopes
6. Format adapters integrated
7. Reporting/telemetry integrated
8. Hardening complete
9. Release approval

## Track Dependencies

### Track A: Contracts
- No dependencies
- Blocks: all other build tracks

### Track B: Template Parser
- Depends on: template spec + ingest schema
- Blocks: Contract Generator

### Track C: Contract Generator
- Depends on: Template Parser + mapping/rules schemas
- Blocks: end-to-end integration

### Track D: Engine
- Depends on: rules schema + DSL precedence ADR
- Blocks: QA hardening

### Track E: Format Adapters
- Depends on: mapping schema
- Blocks: reconciliation and integration tests

### Track F: Reporting/Telemetry
- Depends on: engine outputs + artifact schemas
- Blocks: release readiness

### Track G: QA/Adversarial
- Depends on: integrated pipeline
- Blocks: release approval

## Parallelizable Work
- Template Parser and Engine can start in parallel after contract freeze
- Reporting and Telemetry can progress in parallel after engine interfaces stabilize
