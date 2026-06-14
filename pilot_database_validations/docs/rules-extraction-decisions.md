# Rules Extraction Decisions (Wave 4 / W4-A)

## Accepted
1. **Opt-in extraction path** (`--extract-rules-from-transform-logic`) to avoid contract-breaking default behavior.
2. **Deterministic rule ID generation** using transaction code + target field + rule kind.
3. **Conservative normalization**: only high/medium-confidence patterns auto-normalize.
4. **Explicit unresolved queue** with warnings and markers for manual follow-up.

## Rejected
1. Auto-converting all IF/THEN prose into executable rule expressions.
   - Reason: high false-positive risk and non-deterministic parse quality.
2. Auto-appending extracted candidates into canonical `ruleRows` by default.
   - Reason: contract drift + hidden semantics changes.

## Parked
1. Natural language parser for multi-branch conditionals using formal grammar / LLM-assisted extraction.
2. Rule precedence inference from free text (currently fixed extraction priorities in 900+ range).
3. Owner/confidence routing automation into governance workflows.
