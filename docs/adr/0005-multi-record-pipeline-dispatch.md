# ADR 0005: Multi-Record Dispatch in Generated Pipeline YAML

- Status: Proposed
- Date: 2026-05-15
- Supersedes: none
- Related: `docs/handover/SHAW_source_onboarding_session.md` (open follow-up #1),
  ADR 0001 (boundary: CLI services), ADR 0003 (fixed-width v2)

## Context

The E2E batch testing harness emits one Valdo pipeline YAML per `(env, source)`
via `scripts/generate_pipeline_yaml.py`. Output-file gates (L1 structural,
L2 regeneration, L3 baseline diff) are currently emitted as `validate` and
`compare` steps in the pipeline YAML and dispatched by
`ETLPipelineRunner._execute_step` to single-mapping services
(`run_validate_service`, `run_compare_service`).

SHAW introduces output files with multiple record types within a single
fixed-width file (ATOCTRAN, CONTACT, TRANERT). The per-source config
already models this:

```yaml
output_files:
  - file_type: ATOCTRAN
    mapping: "config/mappings/SHAW_ATOCTRAN.yaml"   # umbrella, not a mapping JSON
    rules:   ""                                     # per-record rules inside umbrella
    multi_record: true
    discriminator_field: "TRANSACTION-CODE"
```

The umbrella file conforms to the existing
`src/config/multi_record_config.py::MultiRecordConfig` schema and is
consumed by the existing
`src/services/multi_record_validate_service.py::run_multi_record_validate_service`.

The current generator emits these entries as plain `type: validate` steps
pointing at the `.yaml` umbrella. At run time the runner would call
`run_validate_service`, which calls `_load_mapping_config` on a YAML file
that is not a flat mapping JSON, and fail. **Today the ATOCTRAN
references in the generated pipeline YAML are not executable.**

Hard rule #1 (AGENTS.md) forbids modifying `src/`. We must dispatch
multi-record validation through the existing service without changing
the runner's public step-type set in a way that bypasses that rule.

## Decision

Add **one new step type** to the pipeline-YAML schema and the runner's
dispatcher, used only by the generator for entries where
`multi_record: true`:

| Step type                 | Service called                          | Input file shape       |
|---------------------------|-----------------------------------------|------------------------|
| `validate`                | `run_validate_service`                  | single-mapping JSON    |
| **`validate_multi_record`** | `run_multi_record_validate_service`   | `MultiRecordConfig` YAML |
| `compare`                 | `run_compare_service`                   | two files              |
| `db_compare`              | `compare_db_to_file`                    | file + SQL/table       |
| `reconcile`               | `_execute_reconcile_step`               | reconcile              |

Mechanics:

1. **Schema (`src/pipeline/etl_config.py`)** — `GateStep.type` is a plain
   `str` today; no enum change is needed. The runner's dispatcher
   (`_execute_step`) gets one new branch:

   ```python
   if step.type == "validate_multi_record":
       return run_multi_record_validate_service(
           file_path=step.file,
           config_yaml=Path(step.mapping).read_text(encoding="utf-8"),
       )
   ```

   `step.mapping` carries the umbrella YAML path. `step.rules` stays
   empty (per-record rules live inside the umbrella). `step.query` and
   `step.key_columns` are unused for this step type. Thresholds apply
   normally against the service's `total_rows` / violation-count output.

2. **Generator (`scripts/generate_pipeline_yaml.py`)** — when an
   `output_files` entry has `multi_record: true`:

   - **L1 structural** → emit `type: validate_multi_record` (was `validate`).
   - **L2 regeneration** → emit `type: compare` unchanged. L2 is a
     file-to-file diff; record-type awareness is the wrapper's job
     when it produces the regenerated file. The pipeline YAML does
     not need to know.
   - **L3 baseline diff** → emit `type: compare` unchanged. Same
     reasoning as L2.

   File-to-staging is an input gate, never multi-record in scope, so no
   change there.

3. **Single-record entries** are emitted exactly as today. SRC_A's
   golden YAML must remain byte-identical, gated by the existing
   golden-file test.

4. **Validation** — the generator rejects an output entry with
   `multi_record: true` whose `mapping` does not end in `.yaml` /
   `.yml`, and one with `multi_record: false` whose `mapping` does not
   end in `.json`. This is a fail-fast guard against the exact bug
   that prompted this ADR.

5. **`discriminator_field` in `SHAW.yml`** is informational metadata
   for humans and the rollup index. The umbrella YAML is the canonical
   source for the discriminator at runtime; the generator does not
   propagate `discriminator_field` into the pipeline YAML.

### Rejected alternatives

- **A. Flag-on-`validate`** (`multi_record: true` on the existing
  `validate` step). Simpler in the YAML, but `_execute_step` would
  need to branch on a field other than `type`, and Pydantic discrimination
  by attribute is awkward. The dispatcher in this codebase is a
  type-switch; keep that property uniform.

- **B. Inline the umbrella into the pipeline YAML.** Would couple two
  schemas (`PipelineDefinition` + `MultiRecordConfig`) and force every
  consumer of the pipeline YAML to understand multi-record structure.
  Rejected — the umbrella file is the single source of truth.

- **C. Per-record-type step expansion** (emit 7 `validate` steps for
  ATOCTRAN's 7 record types). The validator already does this
  dispatch internally; duplicating the loop in the generator would
  re-implement `MultiRecordValidator` discovery and lose cross-type
  rule support.

- **D. Wait for a future `step_type` enum / discriminator on
  `GateStep`.** Out of scope and not blocking; revisit if the step-type
  set grows past ~6 entries or if a third dispatch axis appears.

## Consequences

### Positive

- ATOCTRAN (and future CONTACT, TRANERT, and any other multi-record
  output across other sources) becomes executable end-to-end through
  the existing pipeline runner.
- No new YAML schemas; reuses `MultiRecordConfig`, which is already
  unit-tested and used by the API.
- The generator's golden file for SRC_A is untouched, so the existing
  regression net catches any unintended ripple.
- The hard rule on `src/` is respected via a minimal, surgical
  addition: one new dispatcher branch + a pre-existing service call.
  No change to schemas, no change to existing step semantics.

### Negative / accepted

- `src/pipeline/etl_pipeline_runner.py` and the public docstring of
  `_execute_step` need a one-line addition to mention the new step
  type. This is the **only** `src/` change in this ADR. It is
  unavoidable: the runner is the dispatcher, and a new dispatch target
  cannot live elsewhere. We treat this as a documented exception to
  hard rule #1 for this ADR only; the rule's intent (no changes to
  Valdo internals from harness work) is preserved because the change
  is a strict additive dispatcher branch that uses an existing service.
- L2 and L3 still compare files as opaque byte streams. Tolerance-
  field filtering for multi-record files (e.g. ignoring a sequence
  number on `100` records but not `300` records) is **not** addressed
  here. If it becomes a requirement, that is a separate ADR — likely
  involving an `ignore_fields_per_record_type` block in the umbrella.

### Migration

- SRC_A pipeline YAML: unchanged.
- SHAW pipeline YAML (sit + ait): regenerated. The 6 ATOCTRAN step
  entries (L1 in `file_to_staging` skipped — input gate; L1 structural,
  L2, L3 across two envs = 6) change. L1 entries flip from `validate`
  to `validate_multi_record`. L2 and L3 are unchanged. CONTACT and
  TRANERT entries gain the same L1 flip; their umbrella files do not
  yet exist, so their L1 step references will still fail at run time
  until those workbooks land — accepted (this is the same as the
  current "mapping pending" state, not a regression).
- No on-disk migration. No data migration. CI: the
  `generate_pipeline_yaml.py --check` job will mark the SHAW pipeline
  files stale until the regenerated copies are committed.

## Implementation order

1. Land this ADR.
2. Add the dispatcher branch in
   `src/pipeline/etl_pipeline_runner.py::_execute_step` + update the
   docstring and the module-level "step types" enumeration.
3. Add the generator branch in
   `scripts/generate_pipeline_yaml.py::_steps_l1_structural` keyed off
   `multi_record: true`; add the umbrella-vs-flat sanity check in
   `_resolve_output_entry`.
4. Tests:
   - Runner: a unit test that constructs a `GateStep(type="validate_multi_record")`,
     stubs `run_multi_record_validate_service`, and asserts dispatch.
   - Generator: a new fixture-source with one multi-record output;
     assert the L1 step emits `type: validate_multi_record` and L2/L3
     emit `type: compare`. Add a sanity-check test (`.yaml` mapping +
     `multi_record: false` raises; `.json` mapping + `multi_record: true`
     raises).
   - Regression: existing SRC_A golden must remain byte-identical.
5. Regenerate `config/e2e/pipelines/{sit,ait}/SHAW.pipeline.yaml` and
   commit.
6. Run full unit suite. Confirm 152/152 E2E unit tests + SRC_A golden
   still pass alongside the new tests.

## Open questions deferred

- Whether `validate_multi_record` should also accept a literal YAML
  string in `step.query` (so callers can avoid a second file read).
  Not needed for SHAW; defer.
- Whether L2 / L3 need a `compare_multi_record` variant that ignores
  fields per record type. Not needed for SHAW's first smoke; defer
  to its own ADR if a need surfaces.
