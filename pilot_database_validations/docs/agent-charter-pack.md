# Agent Charter Pack

## 1) Mission
Build a **generic, mapping + rules driven source-to-target validation framework** that supports:
- simple outputs (single transaction / flat checks)
- complex outputs (multi-transaction per account, relationship sequencing, header/trailer reconciliation)
- fixed-width and delimited target files

**Core principle:** engine is generic, behavior is declarative.

---

## 2) Scope

### In scope
- Mapping schema validation
- Rule schema validation
- Field, cross-field, cross-record, and file-level validations
- Record-level and run-level reporting
- Telemetry for performance + quality metrics
- Rule governance, versioning, and approvals
- Template ingestion from BA/QA-provided XLSX/CSV
- Auto-generation of normalized JSON contracts from templates

### Out of scope (for initial release)
- Production job scheduling/orchestration
- Auto-remediation (framework reports; does not auto-correct source)
- UI dashboard (export-ready artifacts only)

---

## 3) Team of Agents and Charters

## 0. Planning Agent (Owner: Program planning and coordination)
**Responsibilities**
- Own end-to-end roadmap, sequencing, and milestone definitions
- Maintain dependency map, risk register, and stage-gate readiness
- Coordinate cross-agent priorities and resolve scheduling conflicts
- Keep the execution plan aligned to approved scope

**Outputs**
- `docs/project-master-plan.md`
- `docs/dependency-map.md`
- `docs/risk-register.md`
- `docs/stage-gates-checklist.md`

**Cannot do alone**
- Approve milestone gate transitions without user signoff

---

## A. Architect Agent (Owner: System design)
**Responsibilities**
- Define module boundaries and contracts
- Publish architecture decisions (ADR)
- Define milestone plan and dependency graph

**Outputs**
- `docs/architecture.md`
- `docs/adrs/ADR-*.md`
- milestone board

**Cannot do alone**
- Merge contract-breaking changes without approval

---

## B. Rule Authoring Agent (Owner: rule definitions)
**Responsibilities**
- Write generic rules using DSL/JSON (not code hardcoding)
- Author reusable rule packs (simple → complex)
- Document assumptions and edge cases

**Outputs**
- `rules/*.json`
- scenario docs in `docs/rule-scenarios/`

**Cannot do alone**
- Introduce new DSL features without Contract Agent + approval

---

## C. Contract/Schema Agent (Owner: schemas and compatibility)
**Responsibilities**
- Define and version schemas for mapping/rules/telemetry/reports
- Define and version template-ingest schema (XLSX/CSV normalized model)
- Enforce backward compatibility checks

**Outputs**
- `schemas/*.json`
- compatibility report

---

## D. Validation Engine Agent (Owner: runtime execution)
**Responsibilities**
- Implement deterministic execution pipeline
- Ensure stateless + stateful rule support (group/window/account scope)

**Outputs**
- core evaluator modules
- unit and integration tests

---

## E. Format Adapter Agent (Owner: fixed-width + delimited)
**Responsibilities**
- Implement layout adapters and positional validations
- Guarantee exactness for position/length/padding/quoting/escaping

**Outputs**
- adapter modules
- layout conformance tests

---

## F. Reporting + Telemetry Agent (Owner: observability)
**Responsibilities**
- Generate run summary, detailed CSV, and telemetry artifacts
- Track per-rule failures, phase timings, and throughput

**Outputs**
- `reports/*.json|csv|md`
- telemetry schema + samples

---

## G. QA/Adversarial Agent (Owner: failure discovery)
**Responsibilities**
- Build golden datasets and adversarial cases
- Run mutation/property tests and regression suites

**Outputs**
- test datasets and expected outputs
- failure classification report

---

## H. Release/Governance Agent (Owner: quality gates)
**Responsibilities**
- Enforce stage gates and release checklist
- Maintain changelog and migration notes

**Outputs**
- release checklist
- version tags and release notes

---

## I. Template Parser Agent (Owner: XLSX/CSV ingestion)
**Responsibilities**
- Parse BA/QA input templates (xlsx/csv)
- Normalize variant headers to canonical fields via alias map
- Emit canonical intermediate model with row/cell traceability

**Outputs**
- `artifacts/canonical/*.json`
- parse error report with file/sheet/row/column references

**Cannot do alone**
- Change template contract without Contract Agent + approval

---

## J. Contract Generator Agent (Owner: JSON generation)
**Responsibilities**
- Convert canonical model into `mapping.json`, `rules.json`, optional `run-config.json`
- Ensure generated contracts validate against schemas
- Generate conversion summary + unresolved placeholder list

**Outputs**
- `generated/mapping.json`
- `generated/rules.json`
- `generated/run-config.json` (optional)
- conversion report

---

## K. Template QA Agent (Owner: BA/QA-friendly validation feedback)
**Responsibilities**
- Validate template completeness and semantic consistency
- Produce plain-language, actionable errors for BA/QA
- Maintain template examples and acceptance checklist

**Outputs**
- `reports/template-validation/*.md|csv`
- `examples/templates/*`

---

## 4) Stage-Gated Workflow (Approval-based)

1. **PLANNING** → architecture + high-level plan
2. **CONTRACT_FREEZE** → schemas + DSL accepted
3. **TEMPLATE_INGEST_FREEZE** → input template spec + alias maps accepted
4. **BUILD** → agents implement in isolated branches/worktrees
5. **INTEGRATE** → merge + full test matrix
6. **HARDEN** → adversarial/performance/security checks
7. **READY_FOR_RELEASE** → final package + signoff
8. **RELEASED**

**Advancement rule:** user approval required to move from PLANNING, CONTRACT_FREEZE, TEMPLATE_INGEST_FREEZE, and READY_FOR_RELEASE.

---

## 5) Required Quality Gates
- Schema validation passes
- Template-to-JSON conversion validation passes
- Lint/type checks pass
- Unit + integration tests pass
- Golden dataset reconciliation pass
- No PII leakage in logs/reports (masking checks)
- Deterministic output check (same input = same output)

---

## 6) Definition of Done (DoD) per task
A task is complete only if it includes:
1. Code/config update
2. Tests (positive + negative)
3. Documentation update
4. Example run artifact
5. Entry in agentic development log

---

## 7) Escalation Conditions (must pause and ask)
- Breaking schema change
- Ambiguous business rule without source authority
- Performance degradation >20% from baseline
- New external dependency introduction
- Conflicting rule priority with no explicit precedence
- Template header change that impacts BA/QA onboarding

---

## 8) Repository Conventions
- `docs/` for design, process, and decisions
- `rules/` for rule packs
- `schemas/` for contracts
- `examples/templates/` for BA/QA template samples
- `tests/golden/` for canonical input/output
- `logs/agentic-development/` for step-by-step history

---

## 9) Success Criteria
- Add a new file type or business scenario by **config/rules only** (minimal/no engine changes)
- Generate valid JSON contracts from BA/QA XLSX/CSV without manual coding
- Framework supports both simple and complex transaction relationships
- Reports and telemetry are sufficient for root-cause analysis without rerun
