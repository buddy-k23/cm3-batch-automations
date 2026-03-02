# Source Lineage Specification (Wave 4 / W4-B)

## Goal
Harden `sourceField` derivation so generated artifacts carry auditable, deterministic lineage and confidence tags.

## Lineage model
Each canonical `mappingRows[]` item now includes `sourceLineage`:

- `lineageId` — stable deterministic ID (`sha1(file|sheet|row|targetField)` truncated)
- `origin` — provenance origin (`column:source_field`, `column:source`, `definition_alias`, `target_mirror`, `placeholder`)
- `transforms` — transform expressions applied on top of source
- `derivationFlag` — `true` when source was inferred, `false` for direct explicit mapping
- `confidence` — `high|medium|low|none`
- `strategy` — active derivation mode
- `inputsUsed` — ordered set of source inputs used for decision
- `placeholderUsed` — marker for unresolved placeholders

## Deterministic derivation strategy (`lineage_hardening`)
When `source_field` is missing and derivation is enabled:

1. **Explicit source columns** (`source_field`, `source`, `src`, etc.) → confidence `high`
2. **Definition alias extraction** (e.g., `source: SRC_COL`, `from SRC_COL`) → confidence `medium`
3. **Target mirror fallback** (`target_field`) → confidence `low`
4. **Placeholder fallback** (`UNRESOLVED_SOURCE::<target>`) → confidence `none`

Tie-break is deterministic by fixed column priority and regex order.

## Lineage report artifact
`parse_template_with_report` now emits `conversion.lineage` and the e2e runner persists:

- `generated/.../lineage-report.json`

Report structure:

- `summary` (total, direct/derived, placeholder/low-confidence counts + ratios)
- `thresholds` (`maxPlaceholderRatio`, `maxLowConfidenceRatio`)
- `gates` (`PASS|FAIL` per threshold)
- `anomalies` (`placeholder`, `lowConfidence` arrays)
- `status` (`PASS|WARN`)

## Threshold defaults
- `lineage_max_placeholder_ratio`: **0.02**
- `lineage_max_low_confidence_ratio`: **0.15**

These are configurable via parser/e2e CLI flags and passed through `DerivationConfig`.
