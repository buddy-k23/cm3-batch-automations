# ADR 0006: Fix BA-Friendly Rules Execution Path

- Status: Accepted — Implemented 2026-05-16
- Date: 2026-05-16
- Supersedes: none
- Related: [ADR 0005](0005-multi-record-pipeline-dispatch.md), issues #12 and #13,
  `docs/handover/SHAW_atoctran_smoke_findings.md`

## Context

SHAW ATOCTRAN became the first source to run end-to-end through Valdo's
BA-friendly rules path. A direct invocation of `MultiRecordValidator`
against `data/samples/atoctran_shaw_20260514.txt` (125,903 rows) using
`config/mappings/SHAW_ATOCTRAN.yaml` and the 7 per-record-type rules
JSONs produced by `BARulesTemplateConverter` exposed two pre-existing
Valdo internal bugs:

1. **`MultiRecordConfig.record_types` is declared as a bare `dict`.**
   The validator assumes each value is a `RecordTypeConfig` Pydantic
   model and does attribute access. Pydantic v2 does not coerce
   `dict` annotations, so the validator crashes with
   `AttributeError: 'dict' object has no attribute 'position'` on the
   first row evaluated. Tracked as **issue #12**.

2. **BA-friendly rule operators are unrecognized by the rule engine.**
   `BARulesTemplateConverter.RULE_TYPE_MAP` emits operators
   (`not_empty`, `numeric`, `date_format`, `valid_values`, `min_value`,
   `max_value`, `exact_length`, `min_length`) that
   `RuleEngine._validate_field` does not handle. Every rule in every
   BA-friendly JSON in the codebase fails to execute with
   `Unknown operator: <name>`. The converter docstring labels these
   "Valdo native rule types — passthrough"; nothing handles them
   downstream. Tracked as **issue #13**.

Neither bug was introduced by harness work. SHAW is simply the first
end-to-end exercise of the BA-friendly path; the converter and the
engine are independently unit-tested but never tested together.

The ATOCTRAN sample run confirmed the upstream work (multi-record
dispatch from ADR 0005, the strict-validator alignment, the
`bulk_convert_rules.py` converter-selection fix) is correct: the
umbrella loaded, the discriminator hit at positions 25–27 dispatched
all 125,903 rows to the right per-record group, zero cross-type
violations. The structural pipeline is sound. Only the rule execution
seam is broken.

AGENTS.md hard rule #1 forbids `src/` modifications from harness work.
Bug #12 is a one-line schema fix; bug #13 is an eight-predicate
additive engine extension. Together they are roughly 80 lines of `src/`
change with no behavioral risk to existing operators. This ADR
authorizes that scope.

## Decision

**Cross the `src/` line for both fixes, with full additive discipline
and no semantic changes to existing behavior.** Implement under this
single ADR as one MR per bug for clean review boundaries.

### Bug #12: schema coercion

```python
# src/config/multi_record_config.py
from typing import Dict

class MultiRecordConfig(BaseModel):
    discriminator: DiscriminatorConfig
    record_types: Dict[str, RecordTypeConfig]   # was: dict
    cross_type_rules: List[CrossTypeRule] = []
    default_action: str = "warn"
```

One-line change. Pydantic v2 coerces each value into a
`RecordTypeConfig` on construction — exactly what every downstream
consumer already assumes. No call-site changes anywhere.

### Bug #13: eight new field predicates

Extend `src/validators/field_validator.py` with eight methods:

| Predicate | Mask (True = violator) |
|---|---|
| `validate_not_empty(df, field)` | `~(s.notna() & (s.astype(str).str.strip() != ''))` |
| `validate_numeric_format(df, field)` | `pd.to_numeric(s, errors='coerce').isna() & s.notna()` |
| `validate_date_format(df, field, fmt)` | `~s.astype(str).str.fullmatch(_fmt_regex[fmt])` |
| `validate_valid_values(df, field, vs)` | `~s.astype(str).isin(vs)` |
| `validate_min_value(df, field, v)` | `pd.to_numeric(s, errors='coerce') < v` |
| `validate_max_value(df, field, v)` | `pd.to_numeric(s, errors='coerce') > v` |
| `validate_exact_length(df, field, n)` | `s.astype(str).str.len() != n` |
| `validate_min_length(df, field, n)` | `s.astype(str).str.len() < n` |

`_fmt_regex` mirrors the dict already built by
`BARulesTemplateConverter._convert_row` (`CCYYMMDD` →`^\d{8}$`,
`YYYY-MM-DD` → `^\d{4}-\d{2}-\d{2}$`, etc.) — extracted into a shared
constant module to avoid duplication.

Extend `src/validators/rule_engine.py::_validate_field` with eight
matching `elif` branches. Each reads the appropriate rule field:

| Operator | Reads rule field(s) |
|---|---|
| `not_empty` | (none) |
| `numeric` | (none) |
| `date_format` | `format` |
| `valid_values` | `values` |
| `min_value`, `max_value`, `exact_length`, `min_length` | `value` |

The existing operator branches (`>`, `<`, `>=`, `<=`, `==`, `!=`,
`in`, `not_in`, `regex`, `range`, `not_null`, `length`) are unchanged.

### Test plan

For #12:

- New unit test in `tests/unit/test_multi_record_config.py` (or
  equivalent): build a `MultiRecordConfig` from
  `config/mappings/SHAW_ATOCTRAN.yaml` and assert every
  `cfg.record_types[k]` is a `RecordTypeConfig` instance with
  attribute access working. Also assert a positional config
  (`position: "first"`) round-trips correctly.

For #13:

- One unit test per new predicate in
  `tests/unit/test_field_validator.py` covering: happy path (no
  violation), violation case, and an edge case for the type
  (e.g. whitespace-only for `not_empty`, leading zero for `numeric`,
  case-sensitivity for `valid_values`).
- One unit test per new operator branch in
  `tests/unit/test_rule_engine.py` mocking the predicate and
  asserting the right one is called with the right arguments.
- One integration test in
  `tests/integration/test_ba_rules_round_trip.py` (new file): write a
  small BA CSV → convert with `BARulesTemplateConverter` → execute
  with `RuleEngine` against a 3-row DataFrame → assert the expected
  violation set. This is the seam test the codebase has been missing.

### Acceptance criteria

A repeat of the SHAW ATOCTRAN smoke (see
`docs/handover/SHAW_atoctran_smoke_findings.md` reproduction recipe)
produces:

1. No `AttributeError` (no caller-side coercion workaround needed).
2. No `Unknown operator: <name>` lines on stderr.
3. A data-driven `valid: True | False` verdict with real per-rule
   violation counts in `record_type_results`.

### Rejected alternatives

- **Lower BA operators in the converter at write time.** Documented
  in issue #13 as "option b" and rejected there. Loses semantic
  fidelity in the JSON, forces every BA-JSON consumer (UI, reports,
  future tooling) to re-implement the lowering, and doesn't help
  BA-friendly JSONs already on disk.
- **Caller-side coercion for #12.** Used in
  `docs/handover/SHAW_atoctran_smoke_findings.md` to confirm #13.
  Not viable permanently — every multi-record call site would have
  to remember to do it. The validator's contract is that it consumes
  a `MultiRecordConfig`; making it consume "a MultiRecordConfig but
  also manually fix this field first" breaks the abstraction.
- **Wait for issues #12 / #13 to be picked up independently.** The
  SHAW work is the only thing currently exercising these paths, so
  fixing them inside the SHAW track preserves end-to-end velocity and
  surfaces the bugs to whoever else may be quietly impacted (anyone
  shipping a BA-friendly rules JSON today is shipping a rule set that
  doesn't execute).

## Consequences

### Positive

- ATOCTRAN's L1 gate becomes meaningful — verdicts driven by data,
  not by infrastructure.
- Every other Valdo source using a BA-friendly rules JSON suddenly
  starts producing real violation counts. This may surface latent
  problems in other sources' rules. Worth a small audit campaign as a
  follow-up.
- The seam test (`test_ba_rules_round_trip.py`) becomes a permanent
  guard against the converter and engine drifting apart again.
- The `prompts/generate-rules-csv.md` spec gains a true downstream
  implementation. The prompt has been describing rule types the engine
  couldn't run; that's now reconciled.

### Negative / accepted

- `src/` discipline relaxed twice in two ADRs (this one and ADR 0005).
  Both are documented exceptions; neither changes existing semantics.
  AGENTS.md hard rule #1's intent is preserved: harness work doesn't
  rewrite Valdo internals to suit itself; harness work surfaces
  Valdo-internal bugs and authorizes minimal fixes via ADR. If the
  pattern continues a third time, worth revisiting the rule's
  phrasing.
- Sources whose rules CSVs use rule types this ADR doesn't cover
  (e.g. anyone using `cross_field` or `cross_row:*` rules in
  BA-friendly templates) are not addressed here. The converter
  already lowers those to `cross_field` / `cross_row` rule **types**
  (not operators), which the rule engine does dispatch on. They
  should continue working, but no test currently proves it. Worth
  asserting in the round-trip test by adding one cross-field row.
- The eight new predicates are simple but each has edge cases.
  Numeric coercion of leading-zero strings, date-format regexes
  for non-ISO formats, case-sensitivity of `valid_values` — all
  decisions the BA converter already makes implicitly. The new
  predicates must match those choices exactly. Test plan covers this.

### Migration

- No data migration. No on-disk format change.
- Existing rules JSONs that happened to use the legacy synonyms
  (`required`, `allowed values`, `length`, `range`, `regex`) continue
  to work unchanged.
- Existing rules JSONs using "Valdo native" operators (every
  BA-friendly JSON in the repo today) stop crashing and start
  producing real violations. **Operationally this is a behavior
  change**: if those JSONs were silently shipping zero violations
  because every rule crashed, they may now report many. Worth a
  heads-up to anyone running existing pipelines.

## Implementation order

1. ~~Land this ADR.~~ **Done 2026-05-16.**
2. ~~**MR 1 — bug #12**~~ **Done 2026-05-16:**
   - `src/config/multi_record_config.py`: schema annotation changed
     to `Dict[str, RecordTypeConfig]`.
   - `tests/unit/test_multi_record_validator.py::TestRecordTypesDictCoercion`:
     3 new tests exercising the YAML → `MultiRecordConfig` path.
   - SHAW smoke recipe re-run without caller-side coercion: no
     `AttributeError`, proceeded to "Unknown operator" stderr from
     #13 as expected.
3. ~~**MR 2 — bug #13**~~ **Done 2026-05-16:**
   - `src/validators/_date_formats.py`: shared format regex table
     (`DATE_FORMAT_REGEX`, `regex_for_format()`).
   - `src/validators/field_validator.py`: 8 new predicates appended,
     all pre-existing predicates byte-identical.
   - `src/validators/rule_engine.py`: 8 new operator branches in
     `_validate_field`, all pre-existing branches unchanged. Unknown
     operators still raise `ValueError`.
   - `tests/unit/test_field_validator.py` (new, 25 tests).
   - `tests/unit/test_rule_engine.py` (new, 13 tests).
   - `tests/integration/test_ba_rules_round_trip.py` (new, 3 tests) —
     the seam test that was missing.
   - SHAW smoke recipe re-run: clean run, no `Unknown operator`
     lines, `valid= False`/`total_rows= 125903` driven by real
     mapping-side validation. All three acceptance criteria met.
   - **Surfaced a separate finding** in the clean run: per-record
     mapping JSONs contain `valid_values: ['100030.0']` numeric
     literals with a stray `.0` suffix. This is a CSV-generator /
     bulk-mapping-converter pandas float-coercion artifact, tracked
     in `docs/handover/SHAW_source_onboarding_session.md` item 4.9.
4. ~~Update `docs/handover/SHAW_source_onboarding_session.md`~~
   **Done** — status table 4.7/4.8/4.9, MR 2 completion section,
   renumbered "Still open" list.
5. ~~Update `docs/handover/SHAW_atoctran_smoke_findings.md`~~
   **Done** — header marked Resolved, "Resolution" section appended
   with after-state output, acceptance-criteria table, test coverage
   summary, and the new mapping-side finding.

## Open questions deferred

- Whether `valid_values` should be case-insensitive by default
  (BA-friendly templates often mix `'Active'` with raw values from
  files that may be `'ACTIVE'`). Default to case-sensitive for now;
  revisit if BA review surfaces a need.
- Whether `numeric` should accept signed values (`-123`) and decimals
  (`123.45`) by default. `pd.to_numeric` handles both; matches BA
  workbook semantics. Default to permissive.
- Whether `date_format` should accept additional formats beyond what
  `BARulesTemplateConverter` lists today. Out of scope; extend on
  demand as new sources need them.
