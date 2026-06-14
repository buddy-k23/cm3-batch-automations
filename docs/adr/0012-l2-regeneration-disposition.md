# ADR 0012: Disposition of the L2 Regeneration Gate

- Status: Accepted — **Option B (retire the gate)** chosen by the user
  (2026-06-04) and implemented in R-03b (#29). Option A (ship `valdo
  regenerate`) is retained as a future backlog item, not abandoned.
- Date: 2026-06-04
- Supersedes: none
- Related: `docs/ARCHITECTURE_REVIEW_2026-06-03.md` (recommendation R-03),
  `scripts/e2e_lib/run_source.py` (module docstring + `_run_valdo_phase` skip
  path, env `VALDO_E2E_ENABLE_L2`), `src/pipeline/oracle_expected_generator.py`,
  ADR 0010 (TruthSource backend abstraction), ADR 0008 (multi-record reader
  primitive). Implementation is deferred to R-03b (#29).

## Context

The E2E harness advertises a four-gate output story
(`file_to_staging` → `L1_structural` → `L2_regeneration` → `L3_baseline_diff`),
but `L2_regeneration` is a permanent `TODO(valdo-gap)`. It is filtered out at
run time, recorded as `skipped`, and declared **non-blocking** in every source
config (`config/e2e/sources/SHAW.yml:351`, `config/e2e/sources/SRC_A.yml:138`).
The four-gate story is, in practice, **three gates plus a stub** (R-03).

The intent behind L2 was: *re-derive an output from the current staging data
using the current mapping, then diff that regenerated output against the
batch-produced output.* This catches mapping/transform drift that a
structural-only check (L1) and a golden-baseline diff (L3) can miss.

Two facts have changed the picture since L2 was first reserved:

1. **The L2b SQL-Truth gate now exists.** `db_truth_comparator.reconcile`
   (driven by `run_source.py::_run_l2b_sql_truth`) reconciles each output file
   against the *same Oracle staging data the Java reads from*, using a
   per-source reconciliation YAML and the `expected_*.sql` business logic. This
   directly covers the "is the output faithful to source-of-truth data?" axis
   that L2 was meant to probe — but via SQL truth rather than a Valdo
   re-derivation.
2. **L3 baseline diff** covers regression against a signed-off golden output.

So the **truth axis** (does the output match authoritative data?) is covered by
L2b, and the **regression axis** (did the output change vs. a known-good
snapshot?) is covered by L3. L2's distinct remaining value would be catching
*mapping drift that L2b's SQL does not encode* — i.e. when the Valdo mapping and
the `expected_*.sql` diverge. That is a real but narrow gap, and it depends on
the mapping and the SQL being maintained independently.

The only existing regeneration machinery is
`src.pipeline.oracle_expected_generator.generate_expected_from_oracle`, an
internal helper with no CLI front-door. It reads a manifest of
`{query_file, output_file, delimiter}` jobs, runs each query via
`DataExtractor`, and writes delimited output. It is **SQL-manifest driven, not
mapping driven** — it re-runs `expected_*.sql`, which is exactly the same
business-logic source L2b already consumes.

## Decision drivers

- **AGENTS.md hard rule #1:** harness work is scripts + config only; closing the
  L2 gap by shipping a CLI is a *core* `src/` change and must go through the
  normal ADR + (in this single-dev trunk workflow) commit-to-trunk flow, not a
  harness carve-out.
- **AGENTS.md "do not guess":** gate blocking semantics are a listed
  do-not-guess item. This ADR proposes; the user disposes.
- **Honesty of the published story:** a permanently-skipped non-blocking gate
  advertised as a first-class gate is misleading (R-03's core complaint).
- **Non-duplication:** if `valdo regenerate` would re-run the same
  `expected_*.sql` that L2b already reconciles against, L2 adds cost without a
  new truth axis.

## Options considered

### Option A — Ship `valdo regenerate` and make L2 a real gate

Expose a `valdo regenerate` CLI front-door over
`oracle_expected_generator.generate_expected_from_oracle`, replace the
`_run_l2_regeneration_skipped` path in `run_source.py` with a real invocation,
flip the `VALDO_E2E_ENABLE_L2` default to enabled, and decide a blocking policy.

- **Pros**
  - Delivers the advertised four-gate story literally.
  - Gives an ad-hoc operator a first-class `valdo regenerate` command, useful
    outside the harness (re-derive an expected file on demand).
  - A Valdo-side re-derivation *could* one day be mapping-driven (not just
    SQL-manifest driven), catching mapping↔SQL drift L2b cannot.
- **Cons**
  - As currently designed, `generate_expected_from_oracle` is **SQL-manifest
    driven** — it re-runs `expected_*.sql`. So today it would diff "output of
    `expected_*.sql`" against "the batch output," which is **the same truth axis
    L2b already covers**, just expressed as a file-vs-file diff instead of a
    SQL-reconcile. Net new signal ≈ 0 until a mapping-driven regenerator exists.
  - Adds a second Oracle-touching path through the harness; needs the
    TruthSource seam (ADR 0010) to stay backend-agnostic, otherwise it
    reintroduces the very Oracle coupling R-01 just removed.
  - Larger surface: new CLI command + unit tests + docs + a manifest/spec
    schema decision (do-not-guess territory), plus a blocking-policy decision.
  - Requires a `src/` change (new command) and a `VALDO_E2E_ENABLE_L2` default
    flip — load-bearing, beyond a "Small" story if done properly.

### Option B — Retire `L2_regeneration` as a gate (recommended)

Formally remove `L2_regeneration` from `_OUTPUT_PHASE_GATES`, the source
`gates:` blocks, and the docs; delete the `TODO(valdo-gap)` and the skip path.
L2b (SQL truth) + L3 (baseline diff) become the documented output-truth and
output-regression gates.

- **Pros**
  - The published gate story becomes **honest**: every advertised gate actually
    runs. No permanently-skipped stub.
  - No duplicate truth axis: L2b already reconciles against the authoritative
    SQL truth; L3 already guards regression.
  - Smallest correct change; **config + docs + orchestrator-internal**, no new
    CLI, no new schema decision, no Oracle path added. Fits the "Small" sizing.
  - Removes a latent foot-gun (`VALDO_E2E_ENABLE_L2=1` enabling a half-built
    path).
- **Cons**
  - Drops the *potential future* mapping↔SQL drift check that a mapping-driven
    regenerator would give. Mitigation: this is partially covered by R-10b
    (#34), the mapping↔baseline version-pinning test, and can be reintroduced as
    its own first-class story if a mapping-driven regenerator is ever built.
  - `valdo regenerate` as an ad-hoc operator convenience is not delivered.
    Mitigation: `oracle_expected_generator` remains available internally; a
    standalone "ship `valdo regenerate`" story can be filed on its own merits,
    decoupled from the E2E gate question.

## Recommendation

**Option B — retire the `L2_regeneration` gate.** Rationale: with L2b SQL-truth
live, a regeneration gate built on the existing SQL-manifest generator would
re-test the same truth axis L2b already covers, so it adds cost without new
signal. Retiring it makes the harness's advertised gate set honest immediately
and is the smallest correct change. The genuinely-new capability (a
*mapping-driven* re-derivation, and/or an ad-hoc `valdo regenerate` CLI) is a
distinct, larger piece of work that should be justified and scoped on its own,
not smuggled in to satisfy a stubbed gate.

If the user instead values delivering the literal four-gate story now, or wants
the ad-hoc `valdo regenerate` command, choose Option A — but note it should then
be re-sized (it is not "Small" if done with the TruthSource seam, a manifest
schema, tests, and a blocking-policy decision) and it must not reintroduce
direct Oracle coupling (route it through ADR 0010's `TruthSource`).

## Consequences

### If Option B is accepted (recommended)
- R-03b (#29) removes `L2_regeneration` from `_OUTPUT_PHASE_GATES`, the
  `ENV_ENABLE_L2` skip branch, the `L2_regeneration` entries in
  `config/e2e/sources/*.yml`, and the `_layer_for_gate`/`_error_type_for_gate`
  `L2_regeneration` mappings, and deletes the `TODO(valdo-gap)` + L2 sections of
  the `run_source.py` docstring.
- Docs (`run_source.py` docstring, the E2E guide, the infographic's L2 card)
  state two output-truth gates: L2b (SQL truth) + L3 (baseline diff).
- Orchestrator unit tests asserting the L2-skip behaviour are updated to assert
  the gate is absent.

### If Option A is accepted
- R-03b is re-scoped: new `valdo regenerate` command over
  `generate_expected_from_oracle` routed through `TruthSource` (ADR 0010), a
  real invocation replacing `_run_l2_regeneration_skipped`, a
  `VALDO_E2E_ENABLE_L2` default flip, a blocking-policy decision, plus CLI unit
  tests and docs. A manifest/spec-schema sub-decision will need user input
  (do-not-guess).

### Common
- This ADR records the decision; **no code or config changes land under R-03a.**
- The `expected_*.sql` artifacts and `oracle_expected_generator` are untouched
  by this ADR either way.

## Rejected alternatives

- **C. Repurpose `L2_regeneration` to mean the SQL-truth check.** Rejected:
  that role is already filled by the distinct `L2b_sql_truth` gate (see
  `docs/handover/L2B_SESSION_2_HANDOVER.md`); conflating them re-muddies a
  boundary that was deliberately drawn.
- **D. Leave the stub as-is.** Rejected: that is precisely R-03's finding — a
  permanently-skipped gate advertised as first-class is misleading.

## Decision outcome

The user selected **Option B (retire the `L2_regeneration` gate)** on
2026-06-04 and asked that **Option A (ship `valdo regenerate`)** be filed as a
future backlog item rather than dropped. R-03b (#29) implemented Option B:

- `scripts/e2e_lib/run_source.py`: dropped `L2_regeneration` from
  `_OUTPUT_PHASE_GATES`, removed the `VALDO_E2E_ENABLE_L2` env var + skip
  branch, and removed the `L2` mappings from `_layer_for_gate` /
  `_error_type_for_gate`; docstring rewritten.
- `scripts/generate_pipeline_yaml.py`: removed `L2_regeneration` from
  `_VALDO_RUNNABLE_GATES`, deleted `_steps_l2_regeneration`, and dropped the
  now-orphaned `regenerated_path` / `work_root_template` plumbing.
- `scripts/build_rollup_index.py`, `scripts/e2e_lib/split_pipeline.py`,
  `scripts/e2e_lib/failure_sink.py` (`_VALID_LAYERS`: dropped `L2`, added the
  live `L2b`), `scripts/run_e2e_source.sh`, `scripts/run_e2e_all.sh`.
- `config/e2e/sources/{SHAW,SRC_A}.yml` gate blocks; regenerated
  `config/e2e/pipelines/**` and the golden fixture.
- Tests and docs updated; no dangling `L2_regeneration` references remain
  outside this ADR and historical handoffs.

The future Option-A work (a mapping-driven `valdo regenerate` CLI and/or making
L2 a real gate) is tracked as backlog item #46, filed alongside this decision.
