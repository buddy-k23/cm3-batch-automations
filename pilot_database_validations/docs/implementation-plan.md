# Implementation Plan (Parallel Agent Tracks)

## Milestone 0: Planning (Current)
- [x] Charter pack
- [x] Workflow + governance docs
- [x] Initial schemas

## Milestone 1: Contract Freeze
### Track A - Contract Agent
- Finalize mapping/rules/report/telemetry schemas
- Add schema examples and negative cases
- Deliverable: `schemas/` + `docs/contracts.md`

### Track B - Architect Agent
- Finalize architecture and ADR-001..003
- Deliverable: `docs/architecture.md`, `docs/adrs/*`

**Gate:** user approval required before implementation.

## Milestone 1.5: Template Ingest Freeze
### Track C - Template Parser Agent
- Finalize XLSX/CSV template parser behavior
- Implement header alias normalization strategy
- Deliverable: parser spec + ingest tests

### Track D - Contract Generator Agent
- Define canonical ingest model -> mapping/rules contract conversion
- Produce deterministic generation outputs
- Deliverable: conversion design + sample generated artifacts

### Track E - Template QA Agent
- Define BA/QA-friendly validation errors and acceptance checklist
- Deliverable: template validation report format + examples

**Gate:** user approval required before core build.

## Milestone 2: Core Build
### Track F - Engine Agent
- Build rule execution pipeline (scope + priority)
- Deterministic evaluator for field/record/group/file

### Track G - Format Adapter Agent
- Implement fixed-width validator (position/length/overlap)
- Implement delimited validator (delimiter/quote/escape)

### Track H - Rule Authoring Agent
- Author baseline rule packs:
  - simple mapping checks
  - account relationship sequencing
  - header count reconciliation

**Gate:** all unit tests pass, golden smoke tests pass.

## Milestone 3: Integration + Reporting
### Track I - Reporting Agent
- Summary JSON report generation
- Detailed CSV violations export

### Track J - Telemetry Agent
- Phase timings, quality metrics, per-rule failures
- telemetry schema compliance checks

**Gate:** integration tests + artifact schema conformance.

## Milestone 4: Hardening
### Track K - QA/Adversarial Agent
- Golden datasets for complex scenarios
- Fuzz and mutation tests
- Determinism tests (repeat run hash)

**Gate:** quality thresholds met.

## Milestone 5: Release Readiness
### Track L - Governance/Release Agent
- Changelog, migration notes, runbook
- final checklist and rollback plan

**Gate:** user signoff for release.

---

## Worktree/Branch Strategy
- `database-validations` (integration trunk)
- per-agent branches:
  - `agent/contracts/*`
  - `agent/template-parser/*`
  - `agent/contract-generator/*`
  - `agent/template-qa/*`
  - `agent/engine/*`
  - `agent/adapters/*`
  - `agent/rules/*`
  - `agent/reporting/*`
  - `agent/telemetry/*`
  - `agent/qa/*`

## Mandatory PR Checklist
- schema/tests/docs updated
- step log entry added
- backward compatibility statement included
- approval checkbox for gate-relevant changes
