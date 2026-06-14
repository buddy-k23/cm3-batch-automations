# Add Record Type Playbook

Operator procedure for adding a new record type to an existing multi-record
SHAW source (e.g. a new `TRANSACTION-CODE` value to the ATOCTRAN umbrella).

This is a pure config + scripts exercise. No `src/` changes are required.
Estimated time: 5–10 minutes per record type plus BA review.

## When to use this playbook

Use this procedure when:

- The source (SHAW, future sources with the same shape) is already onboarded
  via the `prompts/e2e_batch_testing_README.md` 5-step add-a-source checklist.
- The multi-record umbrella YAML (`config/mappings/<SOURCE>_<FILE>.yaml`)
  already exists and is wired into `config/e2e/sources/<SOURCE>.yml`.
- You are adding a new record-type discriminator value (e.g. reactivating
  ATOCTRAN sheet `500`) — not a whole new file type or a whole new source.

If any of the above is not true, follow the source-onboarding checklist or
open an ADR first.

## Worked example

The steps below use **ATOCTRAN sheet `500`** as the worked example because
it is currently skipped per operator (see
`docs/handover/SHAW_source_onboarding_session.md`). Substitute your own
record type and field names.

## Steps

### 1. Add the sheet to the CSV generator's target list

Edit `scripts/generate_shaw_atoctran_csvs.py`:

```python
TARGET_SHEETS = ("100", "200", "300", "500", "605", "700", "900", "060")
```

Add the new sheet name in the correct position (alphabetical / numeric
sort order is preferred but not enforced).

### 2. Regenerate the per-record-type CSVs

From the repo root:

```powershell
$env:PYTHONPATH='.'
python scripts/generate_shaw_atoctran_csvs.py
```

This produces two CSVs under `mappings/csv/shaw_atoctran/`:

- `SHAW_ATOCTRAN_<rectype>_mapping.csv`
- `SHAW_ATOCTRAN_<rectype>_rules.csv`

Confirm the summary table at the end of the run lists the new record
type with non-zero field and rule counts.

### 3. BA review

Open both CSVs in the spreadsheet tool of choice. Verify against the
spec workbook:

- **Mapping CSV**: every field has correct `Position`, `Length`,
  `Data Type`, `Required`, and `Target Name`. `Valid Values` mined from
  the `Transformation` column only (per `prompts/generate-rules-csv.md`'s
  CRITICAL rule: Transformation wins, never the workbook's Valid Values
  column).
- **Rules CSV**: rule emission matches the spec semantics. The generator
  emits:
  - `not_empty` for every `Required: Yes` field.
  - `numeric` for `Data Type: Numeric` fields.
  - `date_format` for `Data Type: Date` fields, using the `Format` cell
    (defaults to `CCYYMMDD` when blank).
  - `length` (min..max) for `Data Type: String` fields. The max comes
    from the spec `Length`; the min comes from
    `STRING_FIELD_MIN_LENGTH_OVERRIDES` in
    `scripts/generate_shaw_atoctran_csvs.py` or `MIN_LENGTH_DEFAULT = 1`
    when the field is not in the override table.
  - `exact_length` for `Numeric`/`Date` fields (their content is
    fixed-width: zero-padded numerics, fixed date formats).
  - `valid_values` when the Transformation column yields a concrete
    enumerated set.

If a string field has a known minimum trimmed length (e.g. ACCT-NUM has
a 14-character minimum in the SHAW data), add it to the override table:

```python
# scripts/generate_shaw_atoctran_csvs.py
STRING_FIELD_MIN_LENGTH_OVERRIDES: Dict[str, int] = {
    "ACCT-NUM": 14,
    "NEW-FIELD-NAME": 8,  # add here
}
```

Then re-run step 2.

Operator sign-off should be captured in
`docs/handover/SHAW_source_onboarding_session.md` as a dated entry.

### 4. Convert CSVs to JSON

```powershell
python scripts/bulk_convert_mappings.py `
    --input-dir mappings/csv/shaw_atoctran `
    --output-dir config/mappings `
    --format fixed_width
python scripts/bulk_convert_rules.py `
    --input-dir mappings/csv/shaw_atoctran `
    --output-dir config/rules
```

The bulk scripts log a "Validation failed" line for every cross-kind
file in a mixed-input directory — the mapping converter sees rules CSVs
as malformed mappings and vice versa. This is **expected symmetric
noise**; the correctly-kinded files in each run produce valid JSON.

Verify the two new JSONs exist at:

- `config/mappings/SHAW_ATOCTRAN_<rectype>_mapping.json`
- `config/rules/SHAW_ATOCTRAN_<rectype>_rules.json`

### 5. Wire the new record type into the umbrella

Edit `config/mappings/SHAW_ATOCTRAN.yaml` (or the appropriate umbrella
file). Add a new entry under `record_types`:

```yaml
record_types:
  # ...existing entries...
  rt_500:
    match: "500"
    mapping: "config/mappings/SHAW_ATOCTRAN_500_mapping.json"
    rules:   "config/rules/SHAW_ATOCTRAN_500_rules.json"
    expect: any
```

Notes on the YAML keys:

- `match` is the discriminator value as it appears in the data file
  (a string, even when numeric).
- `mapping` and `rules` are repo-relative paths to the JSONs from step 4.
- `expect` should be `any` for most ATOCTRAN-style record types. Use
  `exactly_one` or `at_least_one` only when the spec requires it (e.g.
  a header record that must appear exactly once).

If the new record type participates in cross-type rules (header-trailer
count, sum, etc.), also add an entry under `cross_type_rules` per the
schema in `src/config/multi_record_config.py::CrossTypeRule`.

### 6. Smoke-test locally

Run the validator against a sample data file:

```powershell
python scripts/_smoke_atoctran.py
```

Expected output:

- `valid=True` if the sample's rows pass every rule for the new record
  type (and every other type).
- `valid=False` with concrete per-row errors if the new rules are too
  strict or if the data has issues. Iterate on step 3 / step 5.
- The new record type appears in the per-record-type result table. If
  the sample data does not contain rows with the new discriminator
  value, the type will report as `skipped (no_rows)` — expected.

Generate the HTML report set for BA / operator review:

```powershell
python scripts/render_multi_record_html.py `
    --umbrella config/mappings/SHAW_ATOCTRAN.yaml `
    --file     data/samples/atoctran_shaw_20260514.txt `
    --output   reports/smoke/SHAW_ATOCTRAN
```

Open `reports/smoke/SHAW_ATOCTRAN/index.html` in a browser. The umbrella
table now shows the new record type with a link to its detail subpage
(when the sample exercises it) or with `skipped (no_rows)` (when it
doesn't).

## Failure modes

### `valid=False` with every row of the new type erroring

Usually a length-rule mismatch. Check whether the spec `Length` is the
field's fixed-width capacity (the slot width including padding) or the
expected payload length. The `TemplateConverter` adds a `trim`
transformation to every field, so rules run against the **trimmed
payload**. For a string field where the payload can be shorter than the
slot:

- Wrong: `exact_length: 18` rejects every shorter payload.
- Right: `length: 14..18` (set via `STRING_FIELD_MIN_LENGTH_OVERRIDES`).

This is the bug pattern resolved at item 4.10 in the SHAW handover doc.

### `Unknown operator: <name>` lines in stderr

Means the rules JSON uses an operator the engine doesn't recognize.
Pre-2026-05-16 this happened for every BA-friendly JSON; resolved under
[ADR 0006](adr/0006-fix-ba-friendly-rules-execution.md). If it
reappears with a brand-new operator name, either the generator emitted
a typo or a new operator was added to the BA prompt without an engine
counterpart. Cross-reference
`src/validators/rule_engine.py::_validate_field` for the current
operator vocabulary.

### `valid_values: ['100030.0']` in the mapping JSON

Numeric literals corrupted by pandas float coercion. Resolved under
[ADR 0007](adr/0007-template-converter-preserve-string-literals.md);
if it reappears, the CSV → JSON converter is reading the source CSV
without `dtype=str`.

### Umbrella YAML fails to load

`MultiRecordConfig(**yaml.safe_load(...))` raises `ValidationError`.
Common causes:

- New `record_types.rt_<name>.mapping` path does not exist (typo, or
  step 4 was skipped).
- `match` is omitted *and* `position` is omitted (the schema requires
  one or the other).
- `expect` value is not one of `exactly_one`, `at_least_one`, `any`.

## Related documents

- `prompts/e2e_batch_testing_README.md` — 5-step add-a-source checklist
  (used when onboarding a new source from scratch, not when adding a
  record type to an existing source).
- `prompts/generate-mapping-csv.md` — mapping CSV generation spec.
- `prompts/generate-rules-csv.md` — rules CSV generation spec.
- `docs/handover/SHAW_source_onboarding_session.md` — full SHAW
  onboarding history including the ATOCTRAN multi-record umbrella.
- `docs/adr/0005-multi-record-pipeline-dispatch.md` — pipeline-runner
  dispatch for multi-record validators.
- `docs/adr/0006-fix-ba-friendly-rules-execution.md` — BA-friendly
  rule-operator vocabulary fix.
- `docs/adr/0007-template-converter-preserve-string-literals.md` —
  `dtype=str` fix in the template converters.
