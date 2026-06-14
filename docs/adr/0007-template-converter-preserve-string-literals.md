# ADR 0007: Preserve String Literals in TemplateConverter CSV/Excel Reads

- Status: Accepted — Implemented 2026-05-16
- Date: 2026-05-16
- Supersedes: none
- Related: [ADR 0005](0005-multi-record-pipeline-dispatch.md),
  [ADR 0006](0006-fix-ba-friendly-rules-execution.md),
  `docs/handover/SHAW_atoctran_smoke_findings.md`

## Context

After [ADR 0006](0006-fix-ba-friendly-rules-execution.md) MR 2 landed,
the SHAW ATOCTRAN smoke run finally produced data-driven verdicts
instead of crashing in the rule engine. The clean run immediately
surfaced a different bug — a silent data-corruption seam in
`src/config/template_converter.py`.

Symptom from the smoke output, e.g. for `LOCATION-CODE` in
`config/mappings/SHAW_ATOCTRAN_900_mapping.json`:

```
"expected": "one of ['100030.0']",
"actual":   "100030"
```

213,398 fixed-width validator findings under `FW_VAL_001` on rt_060
alone. Every row of every ATOCTRAN record type that has a numeric
`Valid Values` cell is a false positive.

### Root cause

`TemplateConverter.from_csv` (line 59) calls
``pd.read_csv(csv_path)`` and ``TemplateConverter.from_excel``
(lines 40 and 42) call ``pd.read_excel(...)`` — **neither passes
``dtype=str``**. Pandas auto-infers numeric columns: a CSV cell
containing the integer literal ``100030`` is read as the float
``100030.0``. ``_convert_row_to_field`` later does:

```python
values_str = str(row['Valid Values']).strip()
```

That ``str()`` of a float ``100030.0`` produces ``'100030.0'``. The
resulting JSON ships ``valid_values: ['100030.0']``, and at runtime
the fixed-width validator compares the unpadded source string
``'100030'`` against ``'100030.0'`` and flags every row.

### Scope of impact

- **Every Valdo source** with numeric-string ``Valid Values`` entries
  (codes, IDs, type tokens) silently produces corrupted mapping JSONs.
  SHAW ATOCTRAN is the first source to exercise the seam end-to-end
  with strict fixed-width validation enabled at L1; the bug has been
  shipping unnoticed for any source whose strict gate was permissive
  or whose ``Valid Values`` columns contained only alphabetic codes.
- The bulk wrapper ``scripts/bulk_convert_mappings.py`` reads its own
  copy of the CSV with ``dtype=str`` for *validation* but then hands
  conversion to ``TemplateConverter.from_csv`` which re-reads the
  file without that hint. The validation pass sees the truth; the
  conversion pass loses it.
- Callers affected (all of them): the API mapping-upload route, the
  CLI, ``scripts/bulk_convert_mappings.py``, and any future tooling.

### AGENTS.md hard rule #1

Hard rule #1 forbids ``src/`` modifications from harness work. ADRs
0005 and 0006 have already documented two exceptions. This ADR
authorizes a third — for a one-line ``dtype=str`` argument on each of
the two ``pd.read_*`` calls and a regression test.

The intent of rule #1 is preserved: harness work doesn't rewrite
Valdo internals to suit itself. This is a faithful bugfix that closes
silent data corruption that has been shipping for every Valdo source,
not just SHAW. The wrapper alternative (post-process JSON files to
strip trailing ``.0``) was rejected — it patches symptoms in one
caller while the root cause keeps producing corrupted JSONs for every
other caller (API upload, CLI, future tooling).

## Decision

**Fix in both ``src/config/template_converter.py`` and
``src/config/ba_rules_template_converter.py`` with strict additive
discipline.** Pass ``dtype=str`` to every ``pd.read_csv`` and
``pd.read_excel`` call in the four reader methods (``from_csv`` /
``from_excel`` on each converter). No other behavioral changes.

```python
# src/config/template_converter.py — from_excel
if sheet_name:
    df = pd.read_excel(excel_path, sheet_name=sheet_name, dtype=str)
else:
    df = pd.read_excel(excel_path, sheet_name=0, dtype=str)

# src/config/template_converter.py — from_csv
df = pd.read_csv(csv_path, dtype=str)

# src/config/ba_rules_template_converter.py — from_csv
df = pd.read_csv(csv_path, dtype=str)

# src/config/ba_rules_template_converter.py — from_excel
df = pd.read_excel(excel_path, sheet_name=sheet_name or 0, dtype=str)
```

### Why BARulesTemplateConverter is in scope

The SHAW ATOCTRAN rules JSONs happened to escape the bug **by
accident**: the ``Expected / Values`` column contains a mix of
numeric (``6``, ``18``, ``100030``) and non-numeric (``CCYYMMDD``)
cells, which forces pandas to use ``object`` (string) dtype for that
column. The bug would surface for any rules CSV whose
``Expected / Values`` column is *purely* numeric — e.g. an
all-``exact_length`` rules file with only integer thresholds, which
is exactly what a tight BA spec might produce. Closing both seams in
one MR prevents this from biting a future source while leaving the
mapping seam fixed.

### Why ``dtype=str`` is correct

- The template's contract is: every cell is a string label that the
  converter interprets per column. ``Position`` and ``Length`` are
  already explicitly coerced via ``int(...)`` in
  ``_convert_row_to_field``, so forcing ``dtype=str`` does not break
  them.
- ``Required`` is parsed via ``.strip().upper() in {'Y', 'YES', ...}``
  — string-safe.
- ``Valid Values``, ``Description``, ``Format``, ``Default Value``,
  ``Transformation``, ``Field Name``, ``Data Type``, ``Target Name``
  — all already piped through ``str(...).strip()``.
- The current behavior of letting pandas auto-infer types is an
  accident, not a contract. No existing test asserts that float
  inference is desired.

### Test plan

- New regression test in ``tests/unit/test_template_converter_valid_values.py``:
  a CSV with a numeric-only ``Valid Values`` cell (``100030``) and a
  pipe-separated numeric cell (``100030|100040|200000``) must produce
  JSON ``valid_values`` whose elements are the original unpadded
  strings (``"100030"``, ``["100030", "100040", "200000"]``).
- New regression test in ``tests/unit/test_ba_rules_converter.py``:
  a BA rules CSV whose ``Expected / Values`` column contains *only*
  numeric cells (so pandas would otherwise auto-infer ``float64``)
  must produce a rules JSON whose ``values`` / ``value`` entries are
  the original unpadded strings/ints, not floats with ``.0``.
- The pre-existing alphabetic-codes tests in both files must
  continue to pass unchanged.

### Acceptance criteria

1. Regenerate the seven SHAW ATOCTRAN mapping JSONs:

   ```bash
   PYTHONPATH=. python scripts/bulk_convert_mappings.py \
       --input-dir mappings/csv/shaw_atoctran \
       --output-dir config/mappings \
       --format fixed_width
   ```

   Spot check: ``config/mappings/SHAW_ATOCTRAN_900_mapping.json``
   ``LOCATION-CODE.valid_values`` must read ``["100030"]``, not
   ``["100030.0"]``.

2. Re-run the SHAW ATOCTRAN smoke recipe documented in
   ``docs/handover/SHAW_atoctran_smoke_findings.md``. The
   ``record_type_results.rt_*.issue_code_summary`` ``FW_VAL_001``
   counts must drop sharply (close to zero, modulo unrelated
   real findings the data may contain).

3. Existing ``test_template_converter_valid_values.py`` and
   ``test_mapping_upload.py`` tests pass unchanged.

### Rejected alternatives

- **Wrapper-only fix in ``scripts/bulk_convert_mappings.py``.**
  Discussed in Context. Patches symptoms in one caller while
  ``TemplateConverter`` continues to silently corrupt numeric
  literals for every other caller (API upload route, CLI, future
  tooling). Would require a ``TODO(valdo-gap)`` marker that lives
  forever.
- **Coerce on read in ``_convert_row_to_field``.** Possible but
  invasive: every cell access would need defensive
  ``float-to-int-string`` handling, and the conversion logic would
  carry a forever-burden that ``dtype=str`` at the read boundary
  removes in one line.
- **Add a separate ``read_template_strict`` helper.** Over-engineered
  for a one-line fix. The strictness should be the default at the
  file-read boundary; templates have no native numeric semantics
  before column-specific interpretation.

## Consequences

### Positive

- Every Valdo source's mapping JSONs become byte-correct on
  next regeneration. The 213,398 false positives in the SHAW
  ATOCTRAN rt_060 group disappear.
- Closes a silent data-corruption seam that has been shipping for
  every API mapping upload, every CLI conversion, and every bulk
  script run.
- No behavioral change for any caller that was already passing
  alphabetic-only ``Valid Values`` cells.

### Negative / accepted

- ``src/`` discipline relaxed a third time (after ADR 0005 and
  ADR 0006 MR 1 + MR 2). All three are documented exceptions for
  pre-existing bugs surfaced by harness work. The pattern is
  established but worth watching: if the next harness milestone
  finds a fourth Valdo-internal bug, AGENTS.md hard rule #1 should
  be revised to formalize the "harness work surfaces internal bugs
  → ADR-authorized minimal fix" pattern rather than treating each
  case as an exception.
- Any caller that was *relying on* pandas-inferred numeric types
  from a template CSV would break. None exist in the codebase
  (verified by grepping for ``from_csv``/``from_excel`` callers);
  any external consumer doing so is depending on undocumented
  behavior.
- Existing mapping JSONs on disk that already contain the ``.0``
  artifact must be regenerated. This is a one-command operation;
  the affected files are the SHAW ATOCTRAN 7 mapping JSONs (other
  sources may have the same artifact and should be regenerated as
  a hygiene pass).

### Migration

- Existing on-disk mapping JSONs with the ``.0`` artifact: regenerate
  from CSV after this MR lands. The seven SHAW ATOCTRAN files are
  regenerated as part of this MR.
- No data migration. No on-disk format change. No API change.

## Implementation order

1. Land this ADR.
2. **MR — ``dtype=str`` on read boundary**:
   - ``src/config/template_converter.py``: pass ``dtype=str`` to the
     two ``pd.read_excel`` calls in ``from_excel`` and the one
     ``pd.read_csv`` call in ``from_csv``.
   - ``tests/unit/test_template_converter_valid_values.py``: add
     regression test covering numeric-only and pipe-separated
     numeric ``Valid Values`` cells.
3. Regenerate the seven SHAW ATOCTRAN mapping JSONs via
   ``scripts/bulk_convert_mappings.py``.
4. Re-run the SHAW ATOCTRAN smoke and verify ``FW_VAL_001`` counts
   drop sharply.
5. Update
   ``docs/handover/SHAW_source_onboarding_session.md`` item 4.9 to
   ``Done``.
6. Update ``docs/handover/SHAW_atoctran_smoke_findings.md``
   "Resolution" section with the after-state ``issue_code_summary``.

## Open questions deferred

- Whether every other Valdo source's existing mapping JSONs should be
  audited and regenerated as a hygiene pass. Recommendation: yes,
  scripted, as a follow-up MR (one ``--format`` per source family).
  Out of scope for this ADR.
- Whether ``BARulesTemplateConverter`` has the same numeric-coercion
  seam in its CSV/Excel read paths. Likely yes; should be audited as
  part of the hygiene pass.
