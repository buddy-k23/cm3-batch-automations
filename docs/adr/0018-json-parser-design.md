# ADR 0018: JSON parser design

- Status: Proposed
- Date: 2026-06-15
- Sprint: 8 (S8-3, [#377](https://github.com/buddy-k23/valdo/issues/377))
- Related: [ADR 0003](0003-fixed-width-v2-normalization.md),
  [ADR 0005](0005-multi-record-pipeline-dispatch.md),
  [ADR 0007](0007-template-converter-preserve-string-literals.md),
  [ADR 0014](0014-record-reader-strategy-seam.md),
  `src/parsers/base_parser.py`,
  `src/parsers/format_detector.py`,
  `src/parsers/pipe_delimited_parser.py`,
  `src/parsers/fixed_width_parser.py`,
  `src/pipeline/etl_config.py` (`SourceConfig`, `OutputFileConfig`,
  `InputFileConfig`),
  `src/config/template_converter.py`,
  `src/validators/field_validator.py`,
  `templates/etl/csv_file_comparison.yml`,
  `templates/etl/fixed_width_single_record.yml`,
  `src/mcp/resources/etl_templates.py` (S7-2 auto-discovery).

## Context

Valdo today validates and compares **flat, row-oriented files only**:
fixed-width (`FixedWidthParser`), pipe / CSV / TSV (`PipeDelimitedParser`),
and multi-record fixed-width via the umbrella YAML pattern (ADR 0005). Every
parser conforms to the same minimal contract in `src/parsers/base_parser.py`
— `parse() -> pd.DataFrame` and `validate_format() -> bool` — and every
mapping the engine consumes is a *flat list of fields* with a per-field
`position` / `length` / `data_type` / `valid_values` / `required` shape
emitted by `TemplateConverter` from a BA-authored CSV/Excel template.

JSON is the next batch-output format the platform asks us to validate.
Internal fintech payloads (statement extracts, lending-decisioning outputs,
core-banking event feeds) are increasingly emitted as either an array of
records or as NDJSON (one record per line). The engine has **no JSON
parser**, **no JSON template under `templates/etl/`**, and **no nested-value
concept** in either the mapping format or `field_validator.py`. Standing
this up requires answering five interlocking questions before any code
lands; this ADR makes the calls so the implementation issue (a separate
M/L follow-up — scope sketched in §5) becomes a mechanical execution.

What JSON breaks that FW/CSV don't:

- **Hierarchy.** A FW or CSV row is a flat tuple of cells. A JSON record
  may carry nested objects (`customer.address.zip`) and arrays of objects
  (`transactions[*].amount`). The "row = list of cells" mental model in
  `pd.read_fwf` / `pd.read_csv` does not survive verbatim.
- **No positional anchors.** Fixed-width fields are located by 1-indexed
  `position` + `length`; CSV by column name or ordinal. JSON fields are
  located by a *path* (`$.customer.address.zip`). The mapping schema must
  carry that locator.
- **Array cardinality is data.** "How many transactions did this record
  carry?" is a first-class business rule for JSON in a way it never is for
  a CSV row (which has exactly one value per column by construction).
  `field_validator.py` has no predicate for it.

## Decision

### 1. Schema source — **Picked: A (JSONPath expressions in a CSV mapping template)**

The mapping template stays a **CSV/Excel workbook**, identical in shape to
the existing FW/CSV `TemplateConverter` input, with **one new column:
`JSON Path`**. The BA writes `$.customer.id`, `$.transactions[*].amount`,
`$.metadata.batch_date` next to each field row. `TemplateConverter` (already
the single ingestion point — `src/config/template_converter.py`, ADR 0007)
gains a `from_json_template()` branch that recognises a `JSON Path` column
and emits a mapping JSON whose per-field entries carry `json_path` instead
of `position` / `length`.

**Rejected — B (sample-driven inference):** BAs do not author from samples
in this shop; they author from a spec workbook. Inference would *generate*
a draft mapping but cannot encode required/optional, valid_values, or
business intent — the same reason `valdo infer-mapping` (which already
exists for CSV/FW) is a starting point, not the contract. Inference can
ship later as a JSON variant of that command; it is not the schema source.

**Rejected — C (JSON Schema as the mapping):** JSON Schema is a richer
contract but lives in a *different* mental model than the workbook BAs
already maintain (EC-S7's onboarding workbook). Adopting it would fork the
mapping ingestion pipeline (two parallel `TemplateConverter` shapes,
two reconciliation paths, two ADR-0006-style operator-vocabulary fights).
JSONPath columns reuse 100% of the existing FW/CSV plumbing — the only
new code is path resolution at parse time.

The BA workflow becomes: open the existing onboarding workbook, fill in
the `JSON Path` column for each field, upload through the same
`/api/v2/mappings/upload` endpoint. Zero new tooling.

### 2. Record boundary — **Picked: NDJSON (one JSON object per line)**

The parser treats the file as **newline-delimited JSON** — one
`json.loads(line)` per non-blank line. Empty lines are skipped (matching
the existing multi-record reader's contract per ADR 0014).

Defence against alternatives:

- **JSON array (`[{...}, {...}]`):** Forces the whole file into memory to
  parse, which is incompatible with the chunked validator pattern
  (`src/parsers/chunked_validator.py`) already used for large FW/CSV
  inputs. Also fragile against truncation — a missing trailing `]`
  invalidates the entire file.
- **Streaming JSON via `ijson`:** Adds a C-extension dependency for a
  problem we do not have. NDJSON streams cheaply on `stdlib json` (one
  `loads()` per line, O(1) memory per record). The 95th-percentile
  enterprise batch output we see internally is *already* NDJSON or
  array-of-objects, and the latter can be converted to the former by a
  one-line `jq` preprocess. Reserving `ijson` for a future "streaming
  array" mode is cheap; importing it on day one is not.

NDJSON also slots directly into the existing `__source_row__` convention
(`PipeDelimitedParser` and `FixedWidthParser` both inject a 1-indexed line
number as the first DataFrame column) — line N in the source maps to
DataFrame row N. Error reports stay line-addressable, which is the BA's
mental model.

### 3. Nested structure handling — **Picked: JSONPath selectors with eager flattening at parse time**

Each field's `json_path` is resolved against the per-record dict via a
JSONPath library (`jsonpath-ng`, pure Python, already a transitive
dependency of `pydantic-extra-types`) and the resolved value is written
into a flat DataFrame column named by the field's `Field Name`. The
DataFrame shape downstream of `JsonParser.parse()` is **identical** to
the FW/CSV case: one column per mapped field, plus `__source_row__`.

Why flatten:

- Every existing validator in `field_validator.py` operates on a `pd.Series`
  (one column). Preserving nested dicts in DataFrame cells would force a
  rewrite of every predicate. Flattening reuses 100% of the validator
  surface.
- The BA's mental model from FW/CSV is "one column per field." JSONPath
  is the bridge, not a new abstraction layer the BA has to think in.
- Arrays-of-objects collapse to a *count* column (`transactions[*]` ->
  `transactions_count: int`) plus optional per-index columns for the v2
  spec. For v1, the count is enough to drive the one new validator below.

Trade-off accepted: a single JSON record cannot validate per-element rules
on `transactions[*].amount` in v1 — that is a multi-record-style expansion
deferred to a follow-up (and probably reuses the ADR 0005 umbrella shape
where each array element becomes a "detail" record).

### 4. Validation rule applicability

**Work as-is (no changes):** every predicate in `field_validator.py`
already operates on the resolved scalar value once flattening is done —
`validate_not_empty`, `validate_numeric_format`, `validate_date_format`,
`validate_valid_values`, `validate_min_value` / `validate_max_value`,
`validate_exact_length`, `validate_min_length`, `validate_regex`,
`validate_range`, `validate_list`. They take `(df, field, …)` and a flat
column is a flat column.

**One new validator needed for v1:**

```python
# src/validators/field_validator.py
def validate_json_array_length(
    self, df: pd.DataFrame, field: str, min_len: int, max_len: int | None = None
) -> pd.Series:
    """Flag rows whose JSON array column has length outside [min_len, max_len]."""
```

This is the single load-bearing predicate for JSON v1. `field` resolves
to the count column emitted by the parser when the JSON path ends in
`[*]` (e.g. `transactions_count`). With this one method, "every statement
must have at least one transaction" and "no statement may carry more than
500 transactions" become BA-writable rules using the existing rules
template's operator vocabulary.

**One new validator deferred to a fast-follow (sized as part of the
implementation issue, not a separate sprint):**

```python
def validate_nested_required(
    self, df: pd.DataFrame, field: str
) -> pd.Series:
    """Flag rows where the JSON path did not resolve (key missing vs. value null)."""
```

Distinct from `validate_not_empty` because JSON has *three* states a flat
file does not: present-with-value, present-with-null, absent. BA needs the
ability to say "the `customer.id` key must be present" independently of
"the value at `customer.id` must not be empty." The parser writes a
sentinel (`pd.NA` for absent, `None` for present-null) so the predicate
can distinguish them.

**Deliberately not adding:** path-existence assertions beyond the two
above, JSON-schema-style `oneOf` / `anyOf`, per-element rules across an
array. The first two duplicate `validate_valid_values` + cross-field
rules; the third is the v2 follow-up flagged in §3.

### 5. Implementation scope

| File | Action | Estimated LOC |
|---|---|---|
| `src/parsers/json_parser.py` | **New** — `JsonParser(BaseParser)` with NDJSON streaming + JSONPath resolution + `__source_row__` injection. Mirrors `FixedWidthParser` (102 LOC) and `PipeDelimitedParser` (118 LOC); JSON is slightly heavier due to path resolution. | **~180** |
| `src/parsers/format_detector.py` | Extend `_DELIMITED_EXTENSIONS` routing with `.ndjson` / `.jsonl` -> `JsonParser`; add `_score_json` (single bracket / brace check on first non-blank line); extend `FileFormat` enum with `JSON`. | **~25** |
| `src/parsers/__init__.py` | Export `JsonParser`. | **~3** |
| `src/config/template_converter.py` | Extend column recognition: add `JSON Path` to `OPTIONAL_COLUMNS`; new `from_json_template()` entrypoint; `_convert_row_to_field` emits `json_path` when the column is populated. | **~60** |
| `src/validators/field_validator.py` | Add `validate_json_array_length` + `validate_nested_required` (see §4). | **~40** |
| `src/validators/rule_engine.py` | Dispatch the two new operators (`json_array_length`, `nested_required`). | **~15** |
| `src/pipeline/etl_config.py` | Extend `OutputFileConfig` / `InputFileConfig` with no schema break — the `mapping: "*.json"` path covers JSON mappings already (they are flat per ADR 0005). One-line note in the `is_multi_record` docstring that JSON arrays use the same umbrella shape when expanded in v2. | **~5** |
| `templates/etl/json_single_record.yml` | **New** BA-facing template mirroring `fixed_width_single_record.yml`. Top-level `source` / `schema_version` / `input_files` block; `strict_json: true`; `strict_level: all`. Auto-discovered by `templates://etl/list` (S7-2) with zero MCP code changes. | **~70** |
| `templates/etl/json_single_record_sample/` | **New** — worked sample: `input.ndjson` (10 records), `mapping.json` (hand-curated), `expected_report.json`, `build_sample.py`. Mirrors the FW sample directory pattern. | **~150** (mostly fixture data) |
| `templates/etl/json_single_record_README.md` | **New** — one-page README in the established shape. | **~50** |
| `tests/unit/test_json_parser.py` | **New** — happy-path NDJSON parse, JSONPath resolution (scalar / nested / array), `__source_row__` correctness, malformed-line handling, empty-line skipping. | **~140** |
| `tests/unit/test_template_converter_json.py` | **New** — round-trip a CSV template with a `JSON Path` column; assert emitted mapping JSON carries `json_path`; assert backward compatibility (templates without the column still emit fixed-width mappings). | **~80** |
| `tests/unit/test_field_validator_json.py` | **New** — coverage for `validate_json_array_length` + `validate_nested_required`, including the absent-vs-null distinction. | **~70** |
| `tests/integration/test_mcp_template_resources.py` | Extend — assert `json_single_record` appears in `templates://etl/list` (zero-code MCP integration is the AC). | **~15** |
| `docs/MCP_SERVER.md` | Add JSON template to the discovered-templates table. | **~10** |
| `docs/USAGE_AND_OPERATIONS_GUIDE.md` | Add "Validating JSON files" section mirroring the FW section. | **~80** |
| **Total** | | **~990 LOC** |

**Estimated effort:** 4 dev-days (one M-sized issue), broken as:
1. Parser + format detector + tests (1.5 days)
2. `TemplateConverter` extension + tests (1 day)
3. New validators + rule engine dispatch + tests (0.5 day)
4. Template + sample + README + docs (1 day)

**`SourceConfig` / `TemplateConverter` plumbing that needs extension:**

- `TemplateConverter.OPTIONAL_COLUMNS` (`src/config/template_converter.py`):
  add `"JSON Path"` to the recognised set. The converter's existing
  `dtype=str` contract (ADR 0007) carries through unchanged.
- `OutputFileConfig.is_multi_record` (`src/pipeline/etl_config.py`):
  no code change for v1 — JSON mappings are flat JSON files, so the
  existing `mapping: "*.json"` -> single-record inference is correct.
  The docstring grows one line clarifying that JSON-shape mappings (as
  distinguished from FW/CSV-shape mappings) are also flat.
- `FormatDetector._DELIMITED_EXTENSIONS` and `get_parser_class`
  (`src/parsers/format_detector.py`): the same extension-first routing
  pattern S8-2 just landed for `.csv` / `.tsv` / `.psv` extends to
  `.ndjson` / `.jsonl` -> `JsonParser`. Content sniffing for plain
  `.json` is the harder case and is **explicitly deferred** — v1 requires
  one of the two NDJSON extensions, and the BA workbook documents that.
- S7-2 auto-discovery (`src/mcp/resources/etl_templates.py`): zero code
  change. Dropping `templates/etl/json_single_record.yml` into the
  directory is enough — the resource walks the directory at every read.

## Consequences

### Positive

- The BA workflow is unchanged in shape: same workbook, one new column,
  same upload endpoint, same `TemplateConverter` ingestion. Adoption cost
  is ~zero.
- Every existing per-field validator works as-is on flattened JSON
  columns. The two new predicates are tightly scoped and dispatch through
  the existing `rule_engine.py` operator map.
- NDJSON-first means no `ijson` dependency, no large-file memory cliff,
  and `__source_row__` line addressability is preserved end-to-end.
- The umbrella-YAML pattern (ADR 0005) and the record-reader strategy
  seam (ADR 0014) are *not* touched by v1 — the JSON parser is a flat,
  single-record parser. When v2 needs per-element rules on
  `transactions[*]`, it slots in as a new umbrella variant rather than a
  rewrite.
- The MCP `templates://etl/*` surface (S7-2) picks the new template up
  automatically. No agent-facing code changes.

### Negative / risks

- **Plain `.json` (array-of-objects) is not supported in v1.** Files
  produced by systems that emit a single top-level array must be
  pre-converted to NDJSON (`jq -c '.[]' in.json > in.ndjson`). The
  USAGE_AND_OPERATIONS_GUIDE entry will call this out with the one-line
  recipe. Risk: a BA tries to validate a 200MB array file and gets a
  confused error. Mitigation: the format detector's `_score_json` flags
  array-shaped JSON with a clear "convert to NDJSON first" message.
- **Per-array-element validation is deferred to v2.** A rule like "every
  transaction must have a non-empty `amount`" cannot be expressed in v1
  — only "every record must carry at least 1 transaction." This is the
  single biggest gap, and is the most likely follow-up driver. The
  trade-off is intentional: shipping the array-of-objects fan-out in v1
  doubles the scope and would block the simpler use-cases that 80% of
  internal JSON feeds need.
- **JSONPath has dialect ambiguity.** `jsonpath-ng` follows the Goessner
  draft, not the (newer) RFC 9535. If the BA workbook surfaces filter
  expressions (`$.transactions[?(@.amount > 100)]`) we will be locked
  into `jsonpath-ng`'s dialect. Mitigation: v1 supports only the
  "dotted path + `[*]`" subset; filter expressions are documented as
  out-of-scope and explicitly rejected at template-conversion time with
  a clear error.
- **No XML overlap planned.** XML is the next nested-format ADR
  (#378, deferred from Sprint 8 — see SPRINT_8_KICKOFF.md). The
  patterns chosen here (template column with locator, flatten-at-parse,
  one or two new validators) are reusable for XML with XPath substituted
  for JSONPath. The XML ADR should explicitly cite this one.

### Follow-ups

- **Implementation issue (M/L):** scoped above (~990 LOC, 4 dev-days).
  Title: `feat(parsers): NDJSON JSON parser + JSON Path template column`.
  Acceptance: parser + converter extension + 2 validators + 1 template +
  full sample + docs + MCP auto-discovery pickup proven by integration
  test. **File this after ADR review, do not include in Sprint 8.**
- **v2 array-of-objects fan-out:** size as L; reuses the ADR 0005
  umbrella pattern with one detail-record type per array path. Defer
  to a sprint where a concrete use-case forces it.
- **XML ADR (#378):** explicitly model after this ADR — template column
  rename to `XML XPath`, parser swap, same flatten-at-parse + same two
  validators (`xml_array_length`, `nested_required`). Estimated half the
  net-new code because the validator + template-converter work is
  already done here.
- **Plain `.json` (single top-level array) support:** add as a
  follow-up only if the conversion recipe proves operationally painful.
  Adds `ijson` as a dependency and a streaming-array mode to the parser
  (~80 LOC extra).
