# Valdo Architecture Review — 2026-06-03

- **Reviewer:** Principal-architect review (GitLab Duo), driven by
  `prompts/architecture_review_meta_prompt.md` (v3).
- **Commit reviewed:** `7b68cfccc6833dd511ebd0346df6edb364c6d734`
  (branch `feature/valdo-engine-v3`).
- **Depth:** `standard` (Phases 0–6, all probes, target-state + gap analysis).
- **Lens:** `engineering`. **Audience:** `engineering`.
- **Mode:** read-only. Every recommendation is a proposal; no code was changed.
- **One-line verdict:** **B (mostly sound, named gaps).** It is genuinely *one
  engine, configured several ways* for validation and SQL-truth reconciliation;
  the gaps are mode *parity* (UAT mode and a real CI surface are aspirational),
  *backend portability* (Oracle is assumed, not abstracted), and *schema
  governance breadth* (config loaders are excellent in the harness, looser in
  `src/`).

> **Legend.** Findings are tagged **[F]** observed fact (cited), **[I]**
> inference, **[R]** recommendation. Recommendation IDs `R-NN` are stable for
> tracking across future iterations.

---

## 1. Executive summary

**The four-mode question — one engine or four code paths?** Largely **one
engine**. The validation/reconciliation core lives once in `src/` and the
harness drives it without re-implementing it:

- **[F]** The multi-record HTML report rendered by the E2E orchestrator reuses
  the *exact* single-record path: `scripts/render_multi_record_html.py` drives
  `MultiRecordValidator` → per-type results that are "exactly the single-record
  result shape that `ValidationReporter` already knows how to render" (module
  docstring, `scripts/render_multi_record_html.py:1`). No parallel report code.
- **[F]** The pipeline runner dispatches by a single type-switch to shared
  services — `validate` → `run_validate_service`, `validate_multi_record` →
  `run_multi_record_validate_service`, `compare` → `run_compare_service`,
  `db_compare` → `compare_db_to_file`, `reconcile` → `_execute_reconcile_step`
  (`src/pipeline/etl_pipeline_runner.py:291`+). The API uses the same services
  (`src/api/routers/files.py:30`-`35`), and the 2026-02-20 review's P1 work
  closed the prior CLI/API compare drift.
- **[F]** The L2b SQL-truth engine (`scripts/e2e_lib/db_truth_comparator.py`)
  is explicitly generic — "new sources or file types are added by checking in a
  YAML spec plus per-record-type `expected_*.sql` files. No Python edits
  required" (module docstring) — and it composes the shared
  `src/validators/multi_record_reader.py` primitive (ADR 0008) rather than
  re-implementing record dispatch.

**Headline configurability metric (P1/P1b/P1c/P2):** adding a new file type, a
new transaction code, a new single-record file, or a new environment to the
*existing reference source* is achievable with **zero `src/` touches** today,
because the SHAW/TRANERT reference path is fully wired. The honest qualifier:
that "config only" property holds for the *capabilities the reference path
already exercises*. The moment a new source needs a capability the reference
doesn't have (a non-Oracle truth source, a delimited output, an order-sensitive
sequence rule), it becomes a `src/` change governed by an ADR — and the ADR
ledger (0005, 0006×2, 0007, 0008, 0009) shows this has happened five-plus times.

**Top 5 strengths**
1. **[F]** Genuine core reuse across surfaces (CLI/API/pipeline/E2E) via
   `src/services/*`; the multi-record report adds *no* new report code.
2. **[F]** The harness is declarative and side-effect-disciplined: reconcile is
   YAML + SQL (`config/e2e/sources/SHAW/reconciliation/tranert.yml`), the engine
   is generic, and `scripts/e2e_lib/` modules avoid logging side-effects.
3. **[F]** Strong, fail-fast schema governance *in the harness*:
   `reconciliation_spec.py::load_spec` "rejects unknown keys, bad enum values,
   malformed cardinality … with a single `ReconciliationSpecError`".
4. **[F]** Clean secret seam: `scripts/e2e_lib/secret_resolver.py` is the single
   sanctioned `os.environ` chokepoint with a `SecretProvider` protocol and a
   `vault` stub already wired into `default()`.
5. **[F]** Disciplined `src/`-change governance: every harness-motivated core
   change is an ADR with a documented carve-out (ADR 0008 formalised the
   pattern), and the two-gate complementarity (L1/rules vs L2b SQL-truth) is a
   sophisticated, well-reasoned design.

**Top 5 risks**
1. **[F]** **Oracle coupling.** L2b opens Oracle directly (`oracledb.connect`,
   `scripts/e2e_lib/run_source.py::_open_l2b_connection`); there is no truth-
   source interface. P3 (non-Oracle) is the weakest probe. **(R-01)**
2. **[F]** **Coverage gate is narrow.** `pytest.ini` enforces
   `--cov-fail-under=80` but `--cov` is scoped to only `src/api`,
   `src/commands`, `src/comparators`, `src/reports` (`pytest.ini:8`-`14`). The
   validators, services, pipeline, and the entire `scripts/e2e_lib/` harness are
   **outside the enforced gate**. The "80%+ over 1,063 tests" claim in AGENTS.md
   is not what CI actually enforces. **(R-02)**
3. **[F]** **L2 regeneration is a permanent `TODO(valdo-gap)`** — skipped, non-
   blocking, with no `valdo regenerate` CLI (`run_source.py` module docstring).
   The four-gate story is really three gates + a stub. **(R-03)**
4. **[F]** **UAT mode and a first-class CI surface are aspirational.** The four
   modes share a core, but there is no UAT-specific adapter and the CI surface is
   `ci/` templates + `valdo watch`, not a tested mode. **(R-04)**
5. **[F]** **Known empty/header-only-batch inconsistency** (L1/rules FAILs while
   L2b PASSes on a legitimately empty batch) is unresolved and needs a product
   decision (L2B_SESSION_11 §6). **(R-05)**

**Single most important recommendation:** **R-01** — introduce a
`TruthSource` interface so Oracle is one adapter. It is the load-bearing move
for the "reusable ETL-testing application" promise and unblocks P3.

---

## 2. Mode → surface → core matrix

| Mode | Entry surface(s) | Core invoked | Parity note |
|---|---|---|---|
| **Ad-hoc** | `valdo validate/parse/compare` CLI (`src/main.py`, `src/commands/*`); `scripts/render_multi_record_html.py`; `shaw_tranert_smoke reconcile` | `run_validate_service`, `run_multi_record_validate_service`, `run_compare_service`, `db_truth_comparator.reconcile` | **[F]** Same services as batch. Ad-hoc reconcile uses the *same* engine as the gate. **Strong parity.** |
| **Integration batch** | `scripts/run_e2e_source.sh` → `run_source.py`; `valdo run-etl-pipeline` | Pipeline runner type-switch → same services; L2b via `db_truth_comparator`; MR report via the ad-hoc renderer | **[F]** This is the most complete mode. **Strong parity.** |
| **UAT batch** | *None distinct* — would reuse the integration surface with UAT env/baselines | (same as integration) | **[I]** No UAT-specific adapter, reporting, or sign-off path exists. Mode is "integration with different config" by assertion, not by tested artifact. **Gap.** |
| **CI/CD** | `ci/` templates, `azure-pipelines-ci.yml`, `valdo watch`, `POST /api/v1/runs/trigger` | (same services via CLI/webhook) | **[I]** Wiring exists but no first-class, tested CI mode adapter; exit-code contract is the integration path's. **Partial.** |

**[F]** Parity backbone confirmed: CLI, API, and pipeline all call
`src/services/*`; the multi-record report reuses `ValidationReporter`; ad-hoc
and gate reconcile share `db_truth_comparator`. The divergence is *mode breadth*
(UAT/CI are under-specified), not *engine duplication*.

---

## 3. Configurability headline metric (P1 / P1b / P1c / P2)

All four are **config-only (0 `src/` touches)** *for the reference source's
existing capabilities*. File lists below.

- **P1 — new fixed-width file type for SHAW.** `config`: new mapping JSON, new
  `config/e2e/sources/SHAW/reconciliation/<ft>.yml`, `expected_*.sql` under
  `…/sql/<ft>/{00_bootstrap,10_load,20_query}`, a new `output_files` entry +
  `gates` opt-in in `config/e2e/sources/SHAW.yml`. `src`: **0**. Blocker: none
  for fixed-width single-record. **Feasible: yes.**
- **P1b — new transaction code on TRANERT/ATOCTRAN umbrella.** Exactly the
  `docs/ADD_RECORD_TYPE_PLAYBOOK.md` flow: per-type mapping + rules CSV→JSON, a
  `record_types.rt_<code>` block in the umbrella YAML, a `record_types` block +
  `expected_<code>.sql` in the reconcile YAML. `src`: **0**. The dispatch is
  **declarative** — discriminator `match`/`position` lives in
  `config/mappings/SHAW_TRANERT.yaml`; no code knows the codes. **Feasible: yes.**
- **P1c — brand-new single-record file.** Single-record is *not* multi-record
  with N=1: the CLI `validate` path (`run_validate_command`) takes a flat
  mapping JSON directly; no umbrella needed. `src`: **0**. **Feasible: yes**, and
  *simpler* than the multi-record case (no umbrella, no discriminator).
- **P2 — new environment overlay (e.g. `prod`).** `config`: add `envs.prod` to
  `config/e2e/paths.yml`, baselines under `baselines/`, secrets via resolver.
  `src`: **0**. Caveat: only `sit`/`ait` are sanctioned this iteration
  (AGENTS.md), so this is a policy gate, not a code gate. **Feasible: yes.**

**Verdict — "any file or transaction, single or multiple, by config alone":**
**Confirmed YES** for fixed-width files against an Oracle truth source, with the
TRANSACTION-CODE dispatch proven declarative. The boundary of "config only" is
the *capability envelope of the reference path*, not the file/transaction count.

---

## 4. Probe scorecard

| Probe | Feasible today? | `src/` touches | Biggest blocker | Target-state delta |
|---|---|---|---|---|
| **P1** new file type (existing source) | **Yes** | 0 | none (fixed-width/Oracle) | none — already at target |
| **P1b** new transaction code | **Yes** | 0 | none (playbook exists) | none — already at target |
| **P1c** new single-record file | **Yes** | 0 | none | none — already at target |
| **P2** new environment | **Yes** (policy-gated) | 0 | `sit`/`ait`-only policy | lift policy when `prod` is in scope |
| **P3** non-Oracle truth source | **No** | many | `oracledb.connect` hard-wired in `run_source.py`; engine takes a PEP-249 conn but the *opener* is Oracle-only; SQL is Oracle dialect | introduce `TruthSource` interface + adapter (**R-01**) |
| **P4** delimited (non-fixed-width) multi-record output | **Partial** | likely some | `multi_record_reader` + parser are fixed-width-position oriented; discriminator is `position`/slice based | generalise reader to a delimited strategy (**R-06**) |
| **P5** non-Java / different SUT | **Partial** | 0–small | `java_scripts.{load,generate}` shell-outs + trigger-file/path layout assume the CA-ESP producer | already mostly config (`java_scripts` is per-source); generalise naming (**R-07**) |
| **P6** brand-new source, ad-hoc + CI | **Partial** | 0 for ad-hoc; CI mode under-specified | no first-class CI adapter / parity test | define a CI mode adapter + parity test (**R-04**) |

**[I]** P1–P2 are A-grade; P3 is the portability cliff; P4–P6 are "structurally
possible but unproven, with named friction."

---

## 5. Dimension-by-dimension findings

**1. Configurability & the "one engine, N modes" thesis — Rating B.**
**[F]** Engine is config-driven for validation + reconcile; the reconcile YAML
(`tranert.yml`) and umbrella YAML carry all source specifics; `expected_*.sql`
holds the business logic. **[F]** Hard-coded items that should be configurable:
the Oracle driver/dialect (R-01), the `sit`/`ait` env enumeration, and the L2
gap. **[R] R-01, R-03.**

**2. Separation of concerns & layering — Rating B+.**
**[F]** Layers are clean (`parsers/validators/comparators/services/reports/
pipeline`). The `reporters/`+`reporting/` shim ambiguity flagged in 2026-02-20
is resolved to `src/reports/{renderers,adapters,contracts}` (architecture.md;
shims marked deprecated). **[F]** The harness-vs-`src/` boundary holds, with
ADR-governed carve-outs (0008 formalised the pattern). **[I]** Minor risk: the
carve-out is now invoked routinely; watch for normalization of `src/` edits
under harness work. **[R] R-08** (periodic carve-out audit).

**3. The four execution modes — surface & parity — Rating B−.**
**[F]** Validation/comparison engines enumerated: `run_validate_service`,
`run_multi_record_validate_service` (both → `MultiRecordValidator`),
`run_compare_service` (file-vs-file, `src/comparators/file_comparator.py` +
`chunked_comparator.py`), `compare_db_to_file` (`db_file_compare_service`),
`db_truth_comparator.reconcile` (L2b), and GE checkpoints
(`src/quality/gx_checkpoint1.py`). **[I]** These are *complementary* (different
truth sources), not divergent duplicates — but there are **two DB-to-file
comparison paths** (`compare_db_to_file` and `db_truth_comparator`) whose
relationship should be documented to prevent future drift. **[F]** Two-gate
complementarity is real and correctly reasoned (L2B_SESSION_11 §6–7). **[R]
R-04, R-09** (document the two DB-compare engines' division of labour).

**4. Configuration model & schema governance — Rating B.**
**[F]** Harness configs are validated on load (`reconciliation_spec.py`,
`path_resolver.py`, `MultiRecordConfig` via pydantic `model_validate`). **[F]**
Typed pipeline/workflow contracts exist (`src/contracts/*`, per 2026-02-20 P1).
**[F]** Gaps: the per-source `SHAW.yml` and `paths.yml` have a `schema_version:
1` field but no single documented schema authority/validator surfaced in this
review; mapping↔baseline version pinning is referenced
(`baseline_resolver.py`) but the enforcement strength was not confirmed here.
**[R] R-10** (publish a config-schema registry + version-pinning test).

**5. Extensibility & plugin seams — Rating B.**
**[F]** Secret provider is a real strategy seam (`SecretProvider` protocol;
`SECRETS_PROVIDER=env|vault`). **[F]** Pipeline step types and rule operators are
a type-switch / vocabulary in code (`_execute_step`; `rule_engine._validate_field`)
— "edit-the-switch", not a registry. ADR 0009 parameterised `cross_row:sequential`
rather than adding a registry. **[I]** Acceptable at current size (~6 step types),
but truth-source and report-format extension want registries. **[R] R-01, R-11.**

**6. Data & state: idempotency, audit, baselines — Rating B+.**
**[F]** Runs are idempotent by design — re-running overwrites `work_root`,
appends to JSONL + `AUDIT.VALDO_RUN_FAILURES` (append-only honoured;
`run_source.py` docstring + `_FailureSinkContext`). **[F]** Failure rows are
capped (500) to avoid flooding the audit table. **[F]** Baseline promotion has a
tool (`promote_baseline.py`) but the *policy* (who may promote, drift guard) is
an open question (AGENTS.md "do not guess on baseline-promotion policy"). **[R]
R-12.**

**7. Testing strategy & confidence — Rating C+ (gate breadth).**
**[F]** 214 test files; AGENTS.md cites 1,063 unit + 46 E2E at 80%+. **[F]** The
*enforced* gate is narrow: `--cov-fail-under=80` over only
`src/api,src/commands,src/comparators,src/reports` (`pytest.ini:8`-`14`).
`src/validators`, `src/services`, `src/pipeline`, and `scripts/e2e_lib/` are not
in the enforced coverage scope. **[F]** Oracle-dependent tests skip without SIT
(L2B_SESSION_11 §9). **[I]** Confidence in the *core engine* is likely high by
test count, but CI does not *enforce* it where the engine lives. **[R] R-02.**

**8. Operability: deployment, packaging, observability, scale — Rating B.**
**[F]** Multiple targets coherent: `Dockerfile`, `build_pex.sh`, `build_rpm.sh`,
`packaging/`, RHEL setup docs. **[F]** Observability: JSONL per-run logs, rollup
HTML, Splunk setup doc. **[F]** Scale: `chunked_validator`/`chunked_comparator`
+ `docs/SCALABILITY_ANALYSIS.md`/`CHUNKED_PROCESSING.md`; the L2b reader is O(1)
memory by design (ADR 0008). **[F]** Gap: `build_rollup_index.py` does not yet
link per-source `multi_record/<ft>/index.html` into the top rollup
(L2B_SESSION_11 §10.2). **[R] R-13.**

**9. Security & compliance — Rating B+.**
**[F]** Strong LDAP/session design (architecture.md "Key security properties":
LDAPS enforced, HMAC-signed cookies, fail-closed on missing key, filter-injection
guard, open-redirect guard). **[F]** 2026-02-20 P0 replaced `shell=True` in the
pipeline path; the E2E `_Subprocess` runs `shell=False` with arg arrays
(`run_source.py`). **[F]** Secrets never logged (resolver). **[I]** Confirm the
P0 `shell=True` removal is fully closed across `src/pipeline/*` (claimed ✅).
**[R] R-14** (one-time grep audit for residual `shell=True`).

**10. Coupling to the current SUT — Rating C.**
**[F]** Oracle is assumed end-to-end (driver, dialect SQL, `staging_schema`).
Java/CA-ESP is per-source-configurable (`java_scripts`), so SUT coupling is
*looser* than DB coupling. Fixed-width is assumed in the reader/parser. **[R]
R-01, R-06, R-07.**

**11. Documentation & knowledge integrity — Rating B.**
**[F]** Rich, mostly coherent doc set with a living Infographic Known-Issues tab
and an ADR ledger. **[F]** Drift spotted: the *prompt header* says "v3" but its
`Status:` line still reads "v2" (`prompts/architecture_review_meta_prompt.md`);
`reconciliation_spec.py`'s docstring example keys record types by bare codes
(`"32000"`) while the live spec keys by umbrella names (`rt_32000`) — the live
file is correct, the docstring example is stale. **[R] R-15** (fix both drifts).

**12. Technical debt & risk register — Rating B.**
**[F]** Known debt is *tracked*, not hidden: `TODO(valdo-gap)` for L2; ADR
0008's `TODO(#21-followup)` vestigial validator methods; pre-existing lint in
`run_source.py` (`F401`/`F841`, L2B_SESSION_11 §10.8); CSV converters don't yet
author `start`/`step` (ADR 0009). ADR 0008/0009 are still "Proposed". **[R]
R-16** (flip ADRs on merge; burn down the tracked TODOs).

---

## 6. Target state & gap analysis

**To-be reference architecture (incremental, no rewrite):**
- **Core engine boundary** — keep `src/services/*` as the source-agnostic
  primitives (parse/validate/compare/reconcile/report). *Already largely here.*
- **Configuration plane** — one documented schema registry covering mapping
  JSON, rules JSON/CSV, umbrella YAML, reconcile YAML, source YAML, `paths.yml`,
  pipeline YAML; each load-validated and version-pinned.
- **Backend abstraction** — a `TruthSource` interface (open/connect, execute
  expected query, dialect hints) with `OracleTruthSource` as the first adapter;
  the comparator already takes a PEP-249 conn, so only the *opener* + SQL
  dialect need lifting.
- **Mode adapters** — thin ad-hoc/integration/UAT/CI adapters over the same
  core, with a parity test asserting identical core invocation.
- **Audit & state plane** — keep append-only failure sink + JSONL + rollup; add
  baseline-promotion governance.
- **Extension seams** — registries for step types, report formats, truth
  sources; keep the secret-provider strategy.

| Target element | Current state (cited) | Target | Gap | Closing move |
|---|---|---|---|---|
| Source-agnostic core | `src/services/*` reused by CLI/API/pipeline/E2E | same | **None** | — (already target) |
| Single-vs-multi dispatch | declarative in umbrella YAML (`SHAW_TRANERT.yaml`) | same | **None** | — |
| Config schema authority | strong in harness (`reconciliation_spec.py`), looser/undocumented for source+paths YAML | one registry, all validated | **M** | R-10 |
| Backend abstraction | Oracle hard-wired (`run_source.py::_open_l2b_connection`); engine is PEP-249-generic | `TruthSource` + adapters | **L** | R-01 |
| L2 regeneration | `TODO(valdo-gap)`, skipped (`run_source.py`) | real `valdo regenerate` or removal of the gate | **M** | R-03 |
| Mode parity (UAT/CI) | shared core, no distinct adapters/tests | thin adapters + parity test | **M** | R-04 |
| Coverage enforcement | gate scoped to 4 packages (`pytest.ini`) | gate spans core + harness | **M** | R-02 |
| Output shape | fixed-width assumed (reader/parser) | delimited strategy | **M** | R-06 |
| Baseline governance | tool exists; policy undefined | documented promotion policy + guard | **S–M** | R-12 |
| Extension registries | type-switch for steps/operators | registries where it scales | **S** | R-11 |

---

## 7. Risk register

| Risk | Likelihood | Impact | Evidence | Owner/action |
|---|---|---|---|---|
| Non-Oracle source forces a fork | Med | High | `_open_l2b_connection`, Oracle SQL in `expected_*.sql` | R-01 |
| "80% coverage" belief vs enforced scope | High | Med | `pytest.ini:8`-`14` | R-02 |
| L2 gap mistaken for a live gate | Med | Med | `run_source.py` docstring; gate non-blocking | R-03 |
| Empty-batch L1/L2b disagreement gates a legit batch | Med | Med | L2B_SESSION_11 §6 | R-05 |
| UAT/CI "modes" assumed but untested | Med | Med | §2 matrix | R-04 |
| Delimited output unsupported when a new source needs it | Low–Med | Med | fixed-width reader | R-06 |
| `src/` carve-out normalization erodes hard rule #1 | Low | Med | ADR 0005/0006/0007/0008/0009 ledger | R-08 |
| Doc/prompt drift misleads operators | Low | Low | prompt "v3" vs `Status: v2`; spec docstring example | R-15 |

---

## 8. Prioritized roadmap

**Must-fix before claiming multi-mode + multi-backend readiness**
- **R-01 (L)** Introduce `TruthSource` interface; refactor `_open_l2b_connection`
  + dialect assumptions behind `OracleTruthSource`. Unblocks P3. *ADR required.*
- **R-02 (S)** Widen the coverage gate to include `src/validators`,
  `src/services`, `src/pipeline`, and add a `scripts/e2e_lib/` coverage job; or
  explicitly document the intended scope and add a separate harness gate.
- **R-04 (M)** Define explicit UAT and CI mode adapters + a parity test that
  asserts all modes invoke the same core service calls.

**Should-fix next**
- **R-03 (M)** Resolve L2: ship `valdo regenerate` or formally retire the gate.
- **R-05 (M)** Decide empty/header-only-batch semantics (product decision).
- **R-10 (M)** Publish a config-schema registry + version-pinning test.
- **R-09 (S)** Document the `compare_db_to_file` vs `db_truth_comparator`
  division of labour.

**Nice-to-have**
- **R-06 (M)** Delimited multi-record reader strategy.
- **R-07 (S)** Generalise trigger-file/path naming away from CA-ESP specifics.
- **R-08 (S)** Periodic AGENTS.md carve-out audit.
- **R-11 (S)** Registries for step types / report formats.
- **R-12 (S–M)** Baseline-promotion policy.
- **R-13 (S)** Link MR reports into the global rollup.
- **R-14 (S)** Residual `shell=True` grep audit.
- **R-15 (S)** Fix prompt "v3/v2" and `reconciliation_spec.py` docstring drift.
- **R-16 (S)** Flip ADR 0008/0009 to Accepted on merge; burn down tracked TODOs.

---

## 9. Proposed ADRs

- **ADR 0010 — TruthSource backend abstraction.** Decision: introduce a
  `TruthSource` interface so Oracle is one adapter; options: (a) PEP-249 + a thin
  dialect-hint object (smallest), (b) a full repository interface returning
  normalized expected rowsets, (c) status quo. Recommend (a).
- **ADR 0011 — Coverage gate scope.** Decision: what packages the 80% bar
  enforces; options: widen `--cov` to the core, add a separate harness gate, or
  document a deliberate exclusion list.
- **ADR 0012 — Mode adapter contract & parity guarantee.** Decision: formalise
  ad-hoc/integration/UAT/CI adapters and a parity test.
- **ADR 0013 — Empty/header-only batch gate semantics.** Decision: when
  `header.ITM-CNT == 0` and 0 details, should the L1 item-count assertion pass?

---

## 10. Open questions for the team

Per AGENTS.md, the review does **not** guess on these:
1. **Baseline-promotion policy** — who may promote, and what guards drift?
   (R-12)
2. **Gate blocking semantics for empty batches** — the L1/L2b disagreement
   (R-05/ADR 0013).
3. **`prod` environment** — is a third env in scope, and does the `sit`/`ait`-only
   policy lift? (P2)
4. **Two DB-compare engines** — is `compare_db_to_file` intended to converge with
   `db_truth_comparator`, or are their roles permanently distinct? (R-09)
5. **L2 regeneration** — ship `valdo regenerate` or retire the gate? (R-03)

---

## 11. Appendix — read-list consulted (reproducibility)

- `prompts/architecture_review_meta_prompt.md`; `AGENTS.md`.
- `docs/architecture.md`; `docs/ARCHITECTURE_REVIEW_2026-02-20.md`;
  `docs/handover/L2B_SESSION_11_HANDOVER.md`; `docs/ADD_RECORD_TYPE_PLAYBOOK.md`.
- ADRs `0005`, `0008`, `0009` (index of `0001`–`0009` reviewed).
- `scripts/e2e_lib/run_source.py`; `…/db_truth_comparator.py`;
  `…/reconciliation_spec.py` (head); `…/secret_resolver.py`.
- `scripts/render_multi_record_html.py` (head).
- `src/main.py` (head); `src/pipeline/etl_pipeline_runner.py::_execute_step`;
  `src/api/routers/files.py` (head); dir listings of
  `src/{services,validators,comparators,commands,api/routers}`.
- `config/e2e/sources/SHAW.yml`; `config/mappings/SHAW_TRANERT.yaml`;
  `config/e2e/sources/SHAW/reconciliation/tranert.yml`.
- `pytest.ini` (coverage gate); test-file count (214) via `git ls-files`.
- Drift cross-checks: `docs/Valdo-Infographic.html` (Known-Issues tab present).

*Confidence notes:* mapping↔baseline pinning strength (dim. 4) and full
`shell=True` closure (dim. 9) were inferred from cited claims, not exhaustively
re-traced; flagged as R-10/R-14 to confirm.
