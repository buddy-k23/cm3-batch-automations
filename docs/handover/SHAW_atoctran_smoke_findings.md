# SHAW ATOCTRAN — first end-to-end smoke findings

- **Date**: 2026-05-16
- **Sample file**: `data/samples/atoctran_shaw_20260514.txt` (125,903 rows)
- **Umbrella**: `config/mappings/SHAW_ATOCTRAN.yaml`
- **Mode**: direct invocation of
  `src.validators.multi_record_validator.MultiRecordValidator` (Option 1
  from the chat — no pipeline runner, no API, no Java load step)
- **Status**: **Resolved** — both bugs documented below were fixed under
  [ADR 0006](../adr/0006-fix-ba-friendly-rules-execution.md) (MR 1 +
  MR 2). See the "Resolution" section at the bottom for the after-state
  smoke output and the new ATOCTRAN finding the clean run surfaced
  (mapping-side `LOCATION-CODE` expected-values mismatch — a CSV
  generator artifact, separate track from ADR 0006).

## Headline

The harness work landed cleanly: dispatch, grouping, and discriminator
matching all behave as designed. The validator successfully read the
file, identified every row's record type from `TRANSACTION-CODE`, and
routed each group to its per-record rules JSON.

But **every field-level rule fails to execute** with `Unknown operator:
<name>`. The cause is a vocabulary mismatch between
`BARulesTemplateConverter` (which emits the operators
`not_empty`/`exact_length`/`valid_values`/`date_format`/`numeric` into
the rules JSONs) and `RuleEngine._validate_field` (which only knows
`>`, `<`, `>=`, `<=`, `==`, `!=`, `in`, `not_in`, `regex`, `range`,
`not_null`, `length`). The two halves of the codebase speak different
vocabularies for the BA-friendly path. Zero overlap.

Net effect: the L1 gate for ATOCTRAN will report `valid: False` no
matter what the file contains, until one of the two bugs below is
fixed. The verdict is structurally meaningless today.

Worth being clear: **these are pre-existing Valdo internal gaps that
SHAW work is the first thing to actually exercise.** Neither was caused
by the ADR 0005 / `bulk_convert_rules.py` / strict-validator work. The
SHAW path simply ran every layer in order for the first time, and the
broken seam between converter and rule engine surfaced.

## Per-record-type results from the actual run

The good half of the run:

| Record type | Rows | Discriminator match | Cross-type violations |
|---|---:|:---:|:---:|
| rt_100 | (rolled in) | ✓ | 0 |
| rt_200 | 10,359 | ✓ | 0 |
| rt_300 | 8,400  | ✓ | 0 |
| rt_605 | (rolled in) | ✓ | 0 |
| rt_700 | 427    | ✓ | 0 |
| rt_900 | 18     | ✓ | 0 |
| rt_060 | 106,699 | ✓ | 0 |
| **Total** | **125,903** | — | **0** |

Note: rt_100 and rt_605 rows are present (the file's `TRANSACTION-CODE`
distribution naturally rolls some types into others) — `error_count`
shows as `None` because zero rules ran cleanly enough to produce a
count. The total still reconciles to 125,903.

The `error_count` figures for rt_700/rt_900/rt_060 (1,909 / 55 /
213,599) are noise: each is just `row_count × rules_in_record_type`
because every single rule blew up before evaluating the data.

The bad half: rules-engine errors below.

## Bug 1 — `MultiRecordConfig.record_types` not coerced to `RecordTypeConfig`

### Symptom

```
AttributeError: 'dict' object has no attribute 'position'
  at src/validators/multi_record_validator.py:253
```

### Root cause

`src/config/multi_record_config.py::MultiRecordConfig` declares
`record_types: dict`. The validator assumes each value is a
`RecordTypeConfig` Pydantic model and does attribute access
(`type_config.position`, `type_config.match`). With the bare `dict`
annotation Pydantic v2 does not coerce — the values stay raw dicts and
attribute access crashes.

### Reproduction

```python
import yaml
from src.config.multi_record_config import MultiRecordConfig
from src.validators.multi_record_validator import MultiRecordValidator

raw = yaml.safe_load(open('config/mappings/SHAW_ATOCTRAN.yaml').read())
cfg = MultiRecordConfig(**raw)
MultiRecordValidator().validate('any-file.txt', cfg)
# AttributeError on first row evaluated
```

The API path (`src.services.multi_record_validate_service.run_multi_record_validate_service`)
hits the same crash for any non-trivial multi-record YAML that uses
positional record types or `match`-based dispatch.

### Workaround (caller-side, no `src/` change)

```python
from src.config.multi_record_config import MultiRecordConfig, RecordTypeConfig
cfg = MultiRecordConfig(**raw)
cfg.record_types = {k: RecordTypeConfig(**v) for k, v in cfg.record_types.items()}
```

This is what I used to confirm Bug 2. Not viable as a permanent fix —
it pollutes every call site and breaks the abstraction.

### Proper fix (one-line schema change)

```python
# src/config/multi_record_config.py
class MultiRecordConfig(BaseModel):
    discriminator: DiscriminatorConfig
    record_types: Dict[str, RecordTypeConfig]   # was: dict
    cross_type_rules: List[CrossTypeRule] = []
    default_action: str = "warn"
```

Pydantic v2 will coerce each value into a `RecordTypeConfig` on
construction, exactly what the validator already assumes. No call-site
changes anywhere else.

### Risk

Low. The validator and any other downstream code already treat values
as `RecordTypeConfig`; today they only work by accident on YAMLs where
the dict shape happens to support `.attr` access (it doesn't, hence
this bug). Existing unit tests that build `MultiRecordConfig` from a
parsed YAML are likely the ones that should have caught this — worth
auditing for "do we ever exercise the validator with a real
`MultiRecordConfig`?"

### Test gap

`tests/unit/test_multi_record_validator.py` (if it exists) probably
constructs the config object manually, bypassing the YAML → Pydantic
path. Worth a follow-up test: build a config from a real YAML string
and run it through `validate()`.

## Bug 2 — BA-friendly rule operators not recognized by `RuleEngine`

### Symptom

```
Error executing rule R001: Unknown operator: not_empty
Error executing rule R002: Unknown operator: exact_length
Error executing rule R003: Unknown operator: valid_values
Error executing rule R010: Unknown operator: date_format
Error executing rule R013: Unknown operator: numeric
...
```

Repeated for every rule in every record type. 112 ATOCTRAN rules ×
~125,903 rows = many thousands of stderr lines per run. No violations
are emitted because no rule executes.

### Root cause

The two halves of the codebase use disjoint operator vocabularies for
the BA-friendly path:

| Source | File | Operators emitted/accepted |
|---|---|---|
| **Emitter** | `src/config/ba_rules_template_converter.py` `RULE_TYPE_MAP` | `not_empty`, `numeric`, `date_format`, `valid_values`, `min_value`, `max_value`, `exact_length`, `min_length`, plus legacy synonyms `required` → `not_null`, `allowed values` → `in`, `length` → `length`, `regex` → `regex`, `range` → `range` |
| **Consumer** | `src/validators/rule_engine.py` `_validate_field` | `>`, `<`, `>=`, `<=`, `==`, `!=`, `in`, `not_in`, `regex`, `range`, `not_null`, `length` |

Overlap: `in`, `not_in`, `regex`, `range`, `not_null`, `length`. These
are the *legacy* operator names (`required` / `allowed values` /
`length` / `range` / `regex`) that the BA converter aliases to the
rule engine's vocabulary.

Gap: every operator the BA converter calls "Valdo native" — `not_empty`,
`numeric`, `date_format`, `valid_values`, `min_value`, `max_value`,
`exact_length`, `min_length` — is **unrecognized** by the rule engine.
The converter's docstring labels them "Valdo native rule types —
passthrough"; in reality nothing handles them downstream.

### Reproduction

Any BA-friendly rules CSV using any of the unrecognized operators. Our
SHAW ATOCTRAN CSVs use exclusively `not_empty`, `exact_length`,
`valid_values`, `date_format`, `numeric` — all five are unrecognized.

```bash
PYTHONPATH=. python -c "from src.validators.rule_engine import RuleEngine; \
  import pandas as pd, json; \
  rules = json.load(open('config/rules/SHAW_ATOCTRAN_100_rules.json')); \
  df = pd.DataFrame([{'LOCATION-CODE': '100030', 'ACCT-NUM': 'X'*18}]); \
  print(RuleEngine().execute_rules(rules['rules'], df))"
# Prints "Unknown operator: not_empty" etc. for every rule.
```

### Why neither side is obviously wrong

- The **BA converter's "native" operators are the right names** for
  what they mean. `not_empty` is clearer than `not_null`,
  `exact_length` is clearer than `length: N..N`, `valid_values` is
  clearer than `in`. The BA team writes these in the spec
  workbook, and the prompt at `prompts/generate-rules-csv.md`
  explicitly documents them as the canonical rule types.
- The **rule engine's operators are the right primitives** for what
  they do. `>=` and `<` are operator semantics; `not_empty` and
  `exact_length` are field-level predicates layered on top.

So either side can move:

- (a) Extend `RuleEngine._validate_field` (and `FieldValidator`) with
  the missing predicates: `not_empty`, `numeric`, `date_format`,
  `valid_values`, `min_value`, `max_value`, `exact_length`,
  `min_length`. Each is a small wrapper over either `not_null` plus a
  whitespace check, a regex, or an arithmetic comparison.
- (b) Have `BARulesTemplateConverter` lower its "native" operators to
  the engine's vocabulary at write time (e.g. emit
  `operator: 'length', min_length: N, max_length: N` instead of
  `operator: 'exact_length', value: N`).

(a) is the better fix because the rule engine should accept what the
BA template documents. (b) loses semantic fidelity in the JSON.

### Proper fix (a)

Extend `FieldValidator` with eight methods and `RuleEngine._validate_field`
with eight matching `elif` branches. Surface ~80 lines. Each predicate
is independently simple:

- `not_empty(s)` → `s.notna() & (s.astype(str).str.strip() != '')`
- `numeric(s)` → `pd.to_numeric(s, errors='coerce').notna()` (passes
  when convertible)
- `date_format(s, fmt)` → regex per format string (CCYYMMDD, YYYY-MM-DD,
  etc.); the BA converter already builds this regex in `_convert_row`
- `valid_values(s, vs)` → `~s.astype(str).isin(vs)` (mask = "violator")
- `min_value(s, v)` → `pd.to_numeric(s, errors='coerce') < v`
- `max_value(s, v)` → `pd.to_numeric(s, errors='coerce') > v`
- `exact_length(s, n)` → `s.astype(str).str.len() != n`
- `min_length(s, n)` → `s.astype(str).str.len() < n`

### Risk

Low. All eight predicates are additive — no change to the existing
operator branches. Existing tests pass unchanged.

### Test gap

There should be an integration test that runs a real BA-friendly rules
JSON (produced by `BARulesTemplateConverter`) through
`RuleEngine.execute_rules` and asserts at least one row produces a
violation. Today no test exercises this seam — the converter and the
engine are individually tested, never together. That is exactly why
the bug shipped.

## Combined fix scope

| Bug | File | Lines | New tests |
|---|---|---:|---:|
| 1 | `src/config/multi_record_config.py` | 1 | 1 |
| 2 | `src/validators/field_validator.py` + `src/validators/rule_engine.py` | ~80 | 8–10 |

Both fall under AGENTS.md hard rule #1 ("no modifications under
`src/`") for harness work. Either an ADR exception is granted (as for
ADR 0005), or the work moves into a separate "fix Valdo internals
discovered by SHAW" track. Both are pre-existing bugs that SHAW work
just happened to be the first to exercise.

## What this does not block

- The ADR 0005 multi-record dispatch is sound. The pipeline YAML's
  `validate_multi_record` step would have called the same crashing
  validator; the crash is upstream of it.
- The BA review (open #2) is still valid. The rules captured in the
  CSVs are semantically correct; they just can't execute today.
- The umbrella YAML, mapping JSONs, and discriminator dispatch all
  work. ATOCTRAN's structural shape is verified by this run.

## What this does block

- Any meaningful L1 verdict for ATOCTRAN.
- The same L1 path for CONTACT, TRANERT, and every other multi-record
  source that uses BA-friendly rule operators (i.e. all of them,
  per the prompt).
- Any meaningful L1 verdict for **every other Valdo source using a
  BA-friendly rules JSON**, not just SHAW. This is not a SHAW-specific
  problem.

## Next steps recommended

1. Open both as GitLab issues against this project (one issue per bug).
2. Draft an ADR proposing the scope of `src/` changes needed (one-line
   schema fix + eight-predicate engine extension), with the
   compensating test plan.
3. Implement under the ADR.
4. Re-run this smoke test against the same sample file. Expect to see
   real per-rule violation counts and a `valid: True` or `valid: False`
   verdict driven by the data, not by missing engine support.

## Reproduction recipe (for whoever picks this up)

```bash
# From repo root, on Windows PowerShell or cmd:
set PYTHONIOENCODING=utf-8
set PYTHONPATH=.
python -c "import yaml; \
  from src.config.multi_record_config import MultiRecordConfig, RecordTypeConfig; \
  from src.validators.multi_record_validator import MultiRecordValidator; \
  raw = yaml.safe_load(open('config/mappings/SHAW_ATOCTRAN.yaml', encoding='utf-8').read()); \
  cfg = MultiRecordConfig(**raw); \
  cfg.record_types = {k: RecordTypeConfig(**v) for k, v in cfg.record_types.items()}; \
  r = MultiRecordValidator().validate('data/samples/atoctran_shaw_20260514.txt', cfg); \
  print('valid=', r['valid'], 'total_rows=', r['total_rows'])"
```

Without the `record_types = {…}` coercion line: AttributeError (Bug 1).
With the coercion: ~125,903 "Unknown operator" lines on stderr, then
`valid= False`, `total_rows= 125903` (Bug 2).

## Resolution (2026-05-16, ADR 0006)

Both bugs were fixed under
[ADR 0006](../adr/0006-fix-ba-friendly-rules-execution.md) in two MRs:

- **MR 1 (bug #12)** — `src/config/multi_record_config.py`:
  `record_types: Dict[str, RecordTypeConfig]`. Pydantic v2 now coerces
  YAML-loaded `record_types` values to `RecordTypeConfig` models. No
  caller-side workaround needed.
- **MR 2 (bug #13)** — `src/validators/field_validator.py` +
  `src/validators/rule_engine.py`: eight new predicates
  (`not_empty`, `numeric`, `date_format`, `valid_values`, `min_value`,
  `max_value`, `exact_length`, `min_length`) and matching operator
  branches in `RuleEngine._validate_field`. Shared format regex table
  extracted to `src/validators/_date_formats.py`. Strictly additive —
  every pre-existing operator branch untouched.

### After-state smoke output

Reproduction recipe **without** the `record_types = {…}` workaround:

```bash
set PYTHONIOENCODING=utf-8
set PYTHONPATH=.
python -c "import yaml; \
  from src.config.multi_record_config import MultiRecordConfig; \
  from src.validators.multi_record_validator import MultiRecordValidator; \
  raw = yaml.safe_load(open('config/mappings/SHAW_ATOCTRAN.yaml', encoding='utf-8').read()); \
  cfg = MultiRecordConfig(**raw); \
  r = MultiRecordValidator().validate('data/samples/atoctran_shaw_20260514.txt', cfg); \
  print('valid=', r['valid'], 'total_rows=', r['total_rows'])"
```

Output:

```
valid= False total_rows= 125903
```

Acceptance criteria from ADR 0006:

| # | Criterion | Status |
|---|---|---|
| 1 | No `AttributeError` (no caller-side coercion workaround) | ✅ |
| 2 | No `Unknown operator: <name>` lines on stderr | ✅ |
| 3 | Data-driven `valid: True/False` verdict with real per-rule violation counts in `record_type_results` | ✅ |

### Test coverage added

- `tests/unit/test_multi_record_validator.py::TestRecordTypesDictCoercion`
  (3 tests, MR 1) — YAML → `MultiRecordConfig` → validator round-trip.
- `tests/unit/test_field_validator.py` (25 tests, MR 2) — 3 cases per
  new predicate.
- `tests/unit/test_rule_engine.py` (13 tests, MR 2) — operator-dispatch
  assertions for each new operator.
- `tests/integration/test_ba_rules_round_trip.py` (3 tests, MR 2) — the
  seam test ADR 0006 calls out: BA CSV → converter → `RuleEngine` →
  asserted violation set.

### New issue surfaced by the clean run — resolved under ADR 0007

`record_type_results.rt_*.issue_code_summary` initially showed real
fixed-width validator findings dominated by `FW_VAL_001` (213,398
across rt_060 alone). Spot check of the violations:

```
"expected": "one of ['100030.0']",
"actual":   "100030"
```

The per-record mapping JSONs contained numeric-typed `valid_values`
entries with a stray `.0` suffix (`'100030.0'`) where the source data
naturally contains the unpadded integer string (`'100030'`).

**Root cause**: `src/config/template_converter.py::from_csv` called
``pd.read_csv(csv_path)`` *without* ``dtype=str``. Pandas auto-inferred
the ``Valid Values`` column to ``float64`` (the column had only
integer literals plus blanks, so the nullable type ended up float).
``str(100030.0)`` later produced ``'100030.0'`` in the JSON. The
mapping CSVs themselves are correct.

**Resolution**: [ADR 0007](../adr/0007-template-converter-preserve-string-literals.md).
``dtype=str`` added to ``pd.read_csv``/``pd.read_excel`` calls in both
``TemplateConverter`` and (defensively) ``BARulesTemplateConverter``.
5 new regression tests across
``tests/unit/test_template_converter_valid_values.py`` (3) and
``tests/unit/test_ba_rules_converter.py`` (2).

**After-state smoke** (re-run via `scripts/_smoke_atoctran.py` against
the same 125,903-row sample after regenerating the 7 ATOCTRAN mapping
JSONs):

```
valid=False  total_rows=125903
total_violations=0
  rt_060: issue_codes={'FW_ALIGN_000': 1}
  rt_100: issue_codes={}
  rt_200: issue_codes={'FW_ALIGN_000': 1}
  rt_300: issue_codes={'FW_ALIGN_000': 1}
  rt_605: issue_codes={}
  rt_700: issue_codes={'FW_ALIGN_000': 1}
  rt_900: issue_codes={'FW_ALIGN_000': 1}
```

The 213,398 ``FW_VAL_001`` count is gone. The remaining
``FW_ALIGN_000: 1`` per group is a separate alignment-check seam
(length/offset structural sanity), not within ADR 0007's scope.
