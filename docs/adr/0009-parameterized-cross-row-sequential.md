# ADR 0009: Parameterize the `cross_row:sequential` Check with `start`/`step`

- Status: Accepted
- Date: 2026-06-03
- Accepted: 2026-06-11 (landed on trunk feature/valdo-engine-v3, commit cbc4cda)
- Supersedes: none
- Related: [ADR 0006](0006-fix-ba-friendly-rules-execution.md),
  `src/validators/cross_row_validator.py`,
  `config/rules/SHAW_TRANERT_CUS_rules.json`

## Context

The SHAW TRANERT customer record (record type 32005) carries a field
`CIF-REF-NUM-CUS` (position 247, length 3) that is a **per-account contact
ordinal**: the first contact on an account is `998`, the second `997`, the
third `996`, and so on — a contiguous countdown from `998`, one value per
contact row.

This is a real business invariant, but **no validation layer caught a
violation of it**:

- The **mapping** (`config/mappings/SHAW_TRANERT_CUS_mapping.json`) declares
  the field as `string`, `length 3`, `required`, `not_null` — no value check.
- The **business rules** (`config/rules/SHAW_TRANERT_CUS_rules.json`) had only
  `not_empty` (R027) and `length 1–3` (R028). A wrong value like `994` is
  non-empty and 3 chars, so it passed.
- The **L2b SQL-truth gate** deliberately defers this field
  (`ignored_fields` in the reconciliation YAML, issue #18 §3): its *full*
  per-account value depends on a stateful Java contact-iteration ordinal that
  the SQL truth cannot reproduce without confirming the Java iteration order.
- **L3 baseline diff** would only flag it opportunistically as a byte
  difference against a pinned golden copy — not as a semantic rule.

The closest existing mechanism is the rules engine's `cross_row:sequential`
check (`CrossRowValidator._check_sequential`), which verifies that the *set*
of integer values within each key group equals `{1, 2, …, N}`. That is an
**ascending run starting at 1** — it cannot express a descending run from
`998`, so the invariant was inexpressible as configuration.

Note this is a **core engine capability gap** in `src/`, not E2E-harness work,
so AGENTS.md hard rule #1 ("harness = scripts + config, no `src/` changes")
does not apply; this change goes through the normal core MR flow with an ADR
and a unit test, exactly as for any rules-engine change.

## Decision

Extend `CrossRowValidator._check_sequential` to accept two **optional** rule
keys:

- `start` (int, default `1`) — the first value in the expected run.
- `step` (int, default `1`) — the increment between consecutive values; may be
  negative for a descending run. Must be a non-zero integer.

For a key group of `N` rows the check now compares the multiset of values to
the contiguous run `{start, start + step, …}` of length `N`. With the defaults
(`start=1, step=1`) the behaviour is **identical** to before, so every existing
`cross_row:sequential` rule is unaffected.

The SHAW TRANERT invariant is then expressed as a pure-config rule (R028B) in
`SHAW_TRANERT_CUS_rules.json`:

```json
{
  "id": "R028B",
  "type": "cross_row",
  "check": "sequential",
  "key_field": "LN-NUM-ERT",
  "sequence_field": "CIF-REF-NUM-CUS",
  "start": 998,
  "step": -1
}
```

The rule dict flows unchanged from the JSON loader through
`RuleEngine._validate_cross_row` into the validator (the engine passes the full
rule dict through, and `src/config/models.py` already allows extra keys), so no
loader, model, or template-converter change is required for JSON-authored
rules.

## Consequences

**Positive**
- A previously uncatchable business invariant is now enforced as **config**,
  running in the structural/rules gate across all four execution modes
  (ad-hoc, integration batch, UAT batch, CI/CD).
- The capability is **general**: any descending or non-1-based ordinal sequence
  (per group) is now expressible by other sources without further code change.
- Fully backward-compatible; the classic `1..N` behaviour is the default.

**Negative / limitations**
- Like the original implementation, the check asserts the *multiset* of values,
  **not their physical row order** — a group whose values are the correct set
  but shuffled passes. This matches the existing `sequential` semantics; a
  strictly order-sensitive variant is out of scope here and can be a follow-up
  if a source needs it.
- The CSV rule-template converters were **not** extended to author `start`/`step`
  (the SHAW rules are JSON-authored). A future CSV-driven source needing this
  would require a small converter addition; deferred until there is a consumer.
- This does **not** lift the L2b deferral of `CIF-REF-NUM-CUS`: the rules engine
  validates the *sequence shape* from the file alone, whereas L2b would need the
  Java-grounded per-account value. The two are complementary, not redundant.

## Alternatives considered

1. **A new check type** (`cross_row:countdown` / `:run`) — rejected: it would
   duplicate `_check_sequential` almost entirely; parameterizing the existing
   check is smaller and keeps one code path.
2. **A derived/transformed helper column** (`998 - CIF-REF-NUM-CUS`) feeding the
   existing ascending check — rejected: more moving parts (a transform plus a
   rule) for the same result, and harder for a BA to read.
3. **Leaving it to L3 baseline diff** — rejected: byte-diff is not a semantic
   rule, is fragile against baseline staleness, and gives no actionable message.
