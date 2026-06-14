# Fixed-Width Multi-Type Implementation Checklist (Architect-Gated)

Use this checklist to implement variable-width fixed-width validation safely.

## Definition of Done (applies to **every** task)
A task is not complete until all three are done:
1. ✅ **Tests** added/updated and passing
2. ✅ **Architect review note** recorded
3. ✅ **Documentation** updated

---

## Task 1 — Contract + Normalizer

### Build
- [ ] Add normalized v2 internal contract (`record_types`, `file_rules`)
- [ ] Add legacy->v2 in-memory normalizer (`DETAIL` + `classification.kind=default`)
- [ ] Keep external backward compatibility for existing mappings

### Tests (required)
- [ ] Normalizer converts legacy mapping to valid v2 shape
- [ ] Legacy mapping behavior unchanged in validation outcomes
- [ ] Invalid v2 contract fails with actionable mapping errors

### Architect review (required)
- [ ] Boundary review: parser/validator/report contracts remain separated
- [ ] Compatibility review: no breaking change for current users
- [ ] Risk note: migration and rollout plan

### Docs (required)
- [ ] Update `docs/contracts/fixed_width_multitype_v2.md`
- [ ] Add migration notes in `docs/UNIVERSAL_MAPPING_GUIDE.md`

---

## Task 2 — Record Classifier

### Build
- [ ] Implement deterministic classifier (discriminator -> length -> default)
- [ ] Add ambiguity detection and explicit structural errors
- [ ] Add unknown-row policy handling (`error` default)

### Tests (required)
- [ ] Discriminator match path
- [ ] Length fallback path
- [ ] Default fallback path
- [ ] Unknown row path (line number + length)
- [ ] Ambiguous match path

### Architect review (required)
- [ ] Determinism review: no heuristic guessing
- [ ] Performance review: no repeated schema scans in hot loop

### Docs (required)
- [ ] Classification precedence + examples in contract doc
- [ ] Troubleshooting section for unknown/ambiguous rows

---

## Task 3 — Per-Type Row Validation

### Build
- [ ] Validate row against selected record type schema
- [ ] Enforce `expected_total_width` only when configured for that type
- [ ] Preserve actionable field-level errors

### Tests (required)
- [ ] Width mismatch by record type
- [ ] Required/format/valid-values by record type
- [ ] Guardrails for overlapping/out-of-bounds fields
- [ ] Parity tests: chunked vs non-chunked outcomes

### Architect review (required)
- [ ] Shared validator path review for parity
- [ ] Error domain separation review (data vs structural)

### Docs (required)
- [ ] Update `docs/VALIDATION_RULES.md` with per-type behavior
- [ ] Add examples to `docs/FIXED_WIDTH_MAPPING_CHECKLIST.md`

---

## Task 4 — File-Level Structural Rules

### Build
- [ ] Implement optional `required_record_types`
- [ ] Implement optional `sequence_rules`
- [ ] Implement optional `reconciliation_rules`
- [ ] Keep header/trailer optional unless configured

### Tests (required)
- [ ] File with only one record type passes by default
- [ ] Missing required record type fails with structural error
- [ ] Sequence rule violations detected
- [ ] Reconciliation count mismatch detected

### Architect review (required)
- [ ] Strictness model review (opt-in policy checks)
- [ ] Structural error semantics review

### Docs (required)
- [ ] Add structural rules section to `docs/UNIVERSAL_MAPPING_GUIDE.md`
- [ ] Add examples to `docs/contracts/fixed_width_multitype_v2.md`

---

## Task 5 — Reporting + Adapters

### Build
- [ ] Add `record_type_summary` and `classification_stats` to adapter outputs
- [ ] Add `structural_issues` section in HTML report
- [ ] Keep KPI-first page; collapse heavy multi-type details by default

### Tests (required)
- [ ] Renderer regression tests for new sections
- [ ] Adapter parity tests (standard/chunked)
- [ ] No regression in existing KPI/dashboard behavior

### Architect review (required)
- [ ] UX review: no redundant bloat, keep scanability
- [ ] Contract conformance review across pipeline boundaries

### Docs (required)
- [ ] Update `docs/contracts/validation_result_v1.md` (or v2 successor) with new keys
- [ ] Update `docs/USAGE_GUIDE.md` for new report interpretation

---

## Suggested Merge Sequence
1. Task 1 (contract/normalizer)
2. Task 2 (classifier)
3. Task 3 (row validation)
4. Task 4 (file rules)
5. Task 5 (reporting/adapters)

Each task should be merged only after Tests + Architect Review + Docs are complete.
