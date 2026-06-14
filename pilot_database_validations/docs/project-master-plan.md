# Project Master Plan

## Program Goal
Deliver a generic, mapping/rules-driven source-to-target validation framework that supports BA/QA template inputs (XLSX/CSV) and validates simple to complex output scenarios.

## Agent 0: Planning Agent (Program Manager)
### Responsibilities
- Own end-to-end roadmap and dependency sequencing
- Maintain milestone scope, dates, and acceptance criteria
- Resolve cross-agent conflicts and reprioritize work
- Maintain risk register and mitigation tracking

### Outputs
- `docs/project-master-plan.md`
- `docs/dependency-map.md`
- `docs/risk-register.md`
- `docs/stage-gates-checklist.md`

---

## Delivery Phases

## Phase 1: Planning + Contracts
- Finalize architecture, schemas, and template spec
- Freeze DSL and template ingest model
- Exit criteria: all contracts approved

## Phase 2: Template Ingestion Track
- Parse XLSX/CSV templates
- Build canonical ingest model
- Generate mapping/rules JSON contracts
- Exit criteria: successful conversion on sample templates

## Phase 3: Core Validation Engine Track
- Implement scope pipeline (field/record/group/file)
- Enforce deterministic ordering/priority
- Exit criteria: deterministic run behavior on smoke datasets

## Phase 4: Format + Reconciliation Track
- Fixed-width and delimited validation adapters
- Header/detail count and control total checks
- Exit criteria: layout + reconciliation tests green

## Phase 5: Reporting + Telemetry Track
- Summary report + detailed CSV
- Runtime/per-rule telemetry emission
- Exit criteria: artifact schema conformance green

## Phase 6: Hardening + Release Readiness
- Adversarial tests, regressions, and performance baseline
- Release checklist and rollback plan
- Exit criteria: user approval for release

---

## Milestone Cadence (execution order)
1. M0 Planning setup
2. M1 Contract freeze
3. M1.5 Template ingest freeze
4. M2 Core build
5. M3 Integration
6. M4 Hardening
7. M5 Release gate

## Governance Rule
No milestone progresses without stage-gate evidence and user approval where required.
