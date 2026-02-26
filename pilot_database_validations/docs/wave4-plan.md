# Wave 4 Execution Plan (Post-Feedback Loop)

Date: 2026-02-24  
Scope focus: **rules extraction**, **source lineage hardening**, **promotion gates from WARN -> PASS/FAIL**

## Objectives
1. Convert implicit/worksheet-level business checks into governed, testable rule contracts.
2. Make source lineage explicit and auditable from template cell/header -> canonical ingest -> generated mapping/rules artifacts.
3. Replace ad-hoc WARN handling with deterministic promotion gates and measurable exit criteria.

---

## Agent Tracks

### Track W4-A: Rules Extraction & Normalization Agent
**Mission:** Extract rule intent from BA/QA templates and trial findings into normalized rule packs with clear precedence and ownership.

**Entry criteria**
- Wave 3 trial artifacts are available (`generated/trials/wave3/*`).
- Current rule governance and DSL constraints are baseline-approved (`docs/rule-governance.md`, `schemas/rules.schema.json`).

**Core deliverables**
- `docs/rules-extraction-catalog.md` (source rule inventory with confidence and owner).
- `generated/trials/wave4/rules-extracted.json` (candidate normalized rules).
- `docs/rules-extraction-decisions.md` (accepted/rejected/parked rules with rationale).
- tests: `tests/test_rules_extraction.py` (deterministic extraction and normalization checks).

**Exit criteria**
- >=90% of identified business checks in sampled workbook(s) are represented as explicit rule candidates.
- Each rule candidate has: stable `rule_id`, scope, severity, precedence, and source reference.
- No duplicate/conflicting `rule_id` collisions in extraction output.

**Risk mitigations**
- Enforce naming convention + collision pre-check before serialization.
- Tag ambiguous rules with `confidence=low` and block auto-promotion.
- Keep a reject list to prevent reintroducing discarded heuristics.

---

### Track W4-B: Source Lineage Hardening Agent
**Mission:** Establish end-to-end lineage from raw template location to generated contract fields and derived values.

**Entry criteria**
- Parser normalization/derivation paths from Wave 3 are in place.
- Schema validation layer is active (strict/fallback validator present).

**Core deliverables**
- `docs/source-lineage-spec.md` (lineage model, required provenance attributes, retention rules).
- `generated/trials/wave4/lineage-map.json` (field-level lineage graph for trial input).
- `generated/trials/wave4/lineage-anomalies.json` (missing/ambiguous lineage findings).
- tests: `tests/test_lineage_hardening.py` (coverage and integrity assertions).

**Exit criteria**
- 100% of generated mapping fields include lineage metadata (`origin`, `transforms`, `derivation_flag`).
- All derived values (`transaction_code`, `source_field`, others) include deterministic derivation reason + mode.
- Zero orphan generated fields (generated target with no source lineage node).

**Risk mitigations**
- Fail-fast gate for lineage-null on required fields.
- Explicit differentiation between direct-map vs derived-map nodes.
- Stable lineage IDs to support reproducible diffing across reruns.

---

### Track W4-C: Promotion Gates Agent (WARN -> PASS/FAIL)
**Mission:** Define and enforce deterministic promotion policy for conversion/report statuses with transparent thresholds.

**Entry criteria**
- WARN-producing scenarios from Wave 3 are documented.
- Rule extraction + lineage anomaly signals available as structured inputs.

**Core deliverables**
- `docs/promotion-gates-policy.md` (gate definitions, thresholds, escalation matrix).
- `tools/promotion_gate.py` (or equivalent module integrated in runner).
- `generated/trials/wave4/promotion-evidence.json` (per-run gate decision artifact).
- tests: `tests/test_promotion_gates.py` (threshold and edge-case coverage).

**Exit criteria**
- Every run yields one deterministic terminal state: `PASS` or `FAIL` (`WARN` allowed only as transient pre-gate signal).
- Gate decision includes machine-readable reasons and blocking categories.
- Repeated runs with same inputs produce byte-identical gate outcomes.

**Risk mitigations**
- Prioritize blocking categories: schema error > lineage break > high-severity rule failure > soft warning.
- Add dry-run mode to compare old vs new gate behavior before default enablement.
- Keep policy versioned and embedded in artifacts for auditability.

---

## Cross-Track Dependencies
- W4-A output (normalized rule candidates) feeds W4-C severity/threshold scoring.
- W4-B lineage anomalies feed W4-C blocker classification.
- W4-C policy references W4-A confidence tiers and W4-B lineage completeness metrics.

---

## Wave 4 Entry/Exit (Program Level)

### Program entry criteria
- Wave 3 parser hardening complete with documented trial artifacts.
- Baseline test suite green.
- Feedback-loop issues triaged into P0/P1/P2 backlog.

### Program exit criteria
- Rules extraction catalog signed off by BA/QA owner.
- Lineage coverage >= 99% on Wave 4 trial dataset(s), with all residual gaps dispositioned.
- Promotion gates active in e2e path and producing deterministic PASS/FAIL.
- Updated docs + tests + trial evidence merged with no open P0 risk.

---

## Compact Task Board (Prioritized)

| Pri | ID | Track | Task | Owner | Status | Exit Signal |
|---|---|---|---|---|---|---|
| P0 | W4-A1 | Rules | Build rule inventory extractor from workbook/template rows | Rules Agent | TODO | `rules-extraction-catalog.md` generated |
| P0 | W4-B1 | Lineage | Add lineage metadata model to canonical ingest + mapping generation | Lineage Agent | TODO | required lineage attrs present in artifacts |
| P0 | W4-C1 | Gates | Implement deterministic gate engine (WARN -> PASS/FAIL) | Gates Agent | TODO | `promotion-evidence.json` includes final state |
| P0 | W4-C2 | Gates | Encode blocker precedence and threshold config versioning | Gates Agent | TODO | policy version emitted in reports |
| P1 | W4-A2 | Rules | Normalize/explain ambiguous extracted rules with confidence flags | Rules Agent | TODO | low-confidence set isolated + non-promotable |
| P1 | W4-B2 | Lineage | Add lineage anomaly report and fail-fast checks | Lineage Agent | TODO | orphan/missing lineage = explicit failure reason |
| P1 | W4-C3 | Gates | Backtest new gate policy against Wave 2/3 trial artifacts | Gates Agent | TODO | diff report with expected deltas |
| P2 | W4-A3 | Rules | Author extraction decision log (accept/reject/park) | Rules Agent | TODO | decision doc linked to rule IDs |
| P2 | W4-B3 | Lineage | Add lineage visualization snapshot for review | Lineage Agent | TODO | lineage map reviewed with BA/QA |
| P2 | W4-C4 | Gates | Add operator-facing runbook for gate triage | Gates Agent | TODO | runbook published + linked in docs |

---

## Validation & Reporting Cadence
- Daily: run unit suite + targeted Wave 4 trials; append evidence paths.
- At each P0 completion: execute deterministic rerun check (2x run hash compare).
- End of Wave 4: publish consolidated trial report with PASS/FAIL gate rationale and residual risks.

## Open Risks to Track During Wave 4
1. Overfitting extraction logic to one workbook shape.
2. Lineage payload bloat affecting artifact readability/performance.
3. Promotion policy too strict for incremental adoption.

Mitigation pattern: feature flags + evidence-driven thresholds + staged rollout (observe -> enforce).