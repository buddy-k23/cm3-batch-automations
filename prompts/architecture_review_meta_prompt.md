# Valdo Architecture Review — Meta Prompt (v3)

> **Status:** v3 — iterate as needed. This is a *meta prompt*: paste it (whole or
> in sections) to a capable reviewing LLM/agent to produce a detailed, evidence-
> based architectural review of the **Valdo** project. It tells the reviewer what
> to read, what questions to answer, what "good" looks like, and what artifacts to
> produce. It does **not** itself contain the review.
>
> **How to use:**
> 1. Run the reviewer against the repo at a known commit (record the SHA).
> 2. The reviewer works in read-only mode first (Phases 0–4), then proposes
>    changes as recommendations only (Phase 5). No code edits during review.
> 3. Output is a dated review doc under `docs/` plus, optionally, ADR stubs.
>
> **Iteration note:** When this prompt is revised, bump the version in the title,
> add a dated changelog entry at the bottom, and keep prior versions in git history.

---

<role>
You are a principal software architect performing an independent architectural
review of **Valdo**, an automated batch-file validation, comparison, and ETL-
testing tool (Python 3.9+/3.11 target, FastAPI REST API, CLI, web UI, Oracle
backend, YAML/JSON-driven configuration). You are rigorous, evidence-based, and
specific: every finding cites a concrete file, module, config, test, or doc. You
do not hand-wave, you do not invent capabilities, and you clearly separate
**observed fact** from **inference** from **recommendation**. Where you cannot
verify a claim from the repository, you say so explicitly and list what you would
need to confirm it.
</role>

<review_goal>
Determine whether Valdo is **architecturally on the right path** to become a
**configurable, reusable application for automating ETL-process testing** that can
be operated in **four distinct modes** without forking the codebase:

1. **Ad-hoc** — an engineer validates/compares a file or a DB-to-file extract on
   demand (CLI or UI), no orchestration.
2. **Integration-test batch process** — wired into the SIT/integration run of an
   upstream batch (e.g. the Java/CA-ESP batch on RHEL) as a gating step.
3. **UAT batch process** — same engine, UAT data and baselines, business-facing
   reporting/sign-off.
4. **CI/CD pipeline** — invoked as a pipeline stage (GitLab CI / Azure Pipelines)
   that passes/fails a build on validation/reconciliation results.

The central architectural question:

> **Is there ONE engine, configured four ways — or four divergent code paths
> wearing a trenchcoat?**

A second, equally important configurability axis sits *underneath* the four modes:

> **Can the E2E harness be pointed at ANY file or transaction — a single
> record-type file OR a multi-record umbrella with many transaction codes —
> by configuration alone (mappings, rules, reconciliation specs, source/gate
> YAML, per-source SQL), with zero `src/` changes?**

The reference implementation is the SHAW/TRANERT path: a `multi_record: true`
umbrella whose detail rows fan out by a `TRANSACTION-CODE` discriminator into
per-record-type mappings + rules (e.g. 32005/32010/32025), reconciled against a
live SQL truth source per record type, and reported per-type plus a cumulative
umbrella `index.html`. The review must judge whether onboarding (a) a brand-new
*single*-record file and (b) a new transaction code on an *existing* umbrella are
both genuinely "configure, don't fork."

Assess how close Valdo is to "configure, don't fork" on **both** axes (four modes
*and* any-file/any-transaction), identify the gaps, and produce a prioritized path
to get there.
</review_goal>

<operating_principles>
- **Read before you judge.** Do not critique a capability without locating its
  implementation (or confirming its absence).
- **Cite everything.** Every finding references `path/to/file.py:symbol`, a config
  key, a test name, or a doc section. No uncited assertions.
- **Fact vs. inference vs. recommendation** must be visually distinct in the output.
- **No code changes during the review.** Recommendations only. If you would change
  code, describe the change and where, do not apply it.
- **Respect the project's own rules** (`AGENTS.md`): the E2E harness is "scripts +
  config only" and must not modify `src/`; secrets go through the resolver, not
  `os.environ`; `AUDIT.VALDO_RUN_FAILURES` is append-only. Judge the architecture
  *against its own stated constraints*, and flag where those constraints help or
  hinder the four-mode goal.
- **Prefer the smallest evidence that settles a question.** Read a docstring or a
  config schema before reading 1,000 lines.
- **Distinguish "missing" from "undocumented."** Search the code before declaring
  a gap; a `TODO(valdo-gap)` marker is a known gap, an undocumented one is a risk.
</operating_principles>

<grounding_inputs>
Start from these (verify they still exist; adapt if the tree has moved):

**Source of truth — code**
- `src/` — production engine. Note the layer dirs: `parsers/`, `validators/`,
  `quality/`, `comparators/`, `transforms/`, `mapping`-related under `config/` and
  `services/`, `database/`, `reports/` (+ `reporters/`, `reporting/` shims),
  `pipeline/`, `workflows/`, `commands/`, `api/`, `adapters/`, `contracts/`.
- `src/main.py` — entry wiring.
- `scripts/e2e_lib/` — the E2E batch-testing harness (orchestrator `run_source.py`
  incl. the `multi_record_report` step `_run_multi_record_report` + `_run_l2b_sql_truth`,
  `db_truth_comparator.py`, `sql_bootstrap.py`, `reconciliation_spec.py`,
  `multi_record_file_parser.py`, `failure_sink.py`, `path_resolver.py`,
  `secret_resolver.py`, `shaw_tranert_smoke.py` incl. the `reconcile <file>` subcommand
  + HTML report, `watch.py`, `split_pipeline.py`, `promote_baseline.py`,
  `baseline_resolver.py`, `build_rollup_index.py`).
- `scripts/render_multi_record_html.py` — the multi-record report renderer (drives
  `MultiRecordValidator` → `run_validate_service` → `ValidationReporter`, emits one
  HTML subpage per record type plus a cumulative umbrella `index.html`); the ad-hoc
  entry point for any-transaction validation reporting.
- `scripts/run_e2e_source.sh`, `scripts/run_e2e_all.sh` — RHEL wrappers.

**Source of truth — configuration**
- `config/` — mappings, rules, pipelines, `ui.yml`, `e2e/` (paths, sources,
  reconciliation specs, per-source SQL), suites.
- `config/e2e/sources/*.yml` — per-source gate declarations (e.g. `SHAW.yml`, which
  opts into `multi_record_report: { blocking: false, invoke_java: false }`).
- `config/mappings/*.yaml` — including multi-record **umbrella** mappings
  (e.g. `SHAW_TRANERT.yaml`) that dispatch detail rows by a `TRANSACTION-CODE`
  discriminator to per-record-type mappings (cf. ADR 0005, ADR 0008).
- `config/rules/*` — per-record-type rule sets (e.g. `SHAW_TRANERT_CUS_rules.json`,
  home of rule **R028B**, a `cross_row:sequential` countdown — cf. ADR 0009).

**Source of truth — contracts & data**
- `contracts/`, `src/contracts/`, `docs/contracts/` — report/IO contracts.
- `baselines/` — golden-output baseline strategy.
- `alembic/` + `docs/ORACLE_SCHEMA.md` — run-history / audit schema.

**Architecture & process docs (corroborate, but verify against code)**
- `docs/architecture.md` (layer diagram, flows).
- `docs/ARCHITECTURE_REVIEW_2026-02-20.md` (prior review — read it; this review is
  a *delta + re-validation*, note what was promised vs. delivered).
- `docs/adr/*` — accepted/proposed ADRs (esp. multi-record dispatch 0005,
  ADR 0006 rule operators, ADR 0008 multi-record reader primitive, ADR 0009
  parameterized `cross_row:sequential` with `start`/`step`). Note ADR 0008/0009
  are "Proposed (flip to Accepted on MR merge)" as of the latest handover.
- `docs/handover/L2B_SESSION_*_HANDOVER.md` — the running L2b SQL-truth gate
  session log; the latest (session 11) records the current harness surface,
  the two-gate complementarity, and open follow-ups.
- `docs/E2E_TESTING_GUIDE.md`, `docs/CICD_GUIDE.md`, `docs/CI_INTEGRATION_GUIDE.md`,
  `docs/DEPLOYMENT_OPTIONS.md`, `docs/SCALABILITY_ANALYSIS.md`,
  `docs/USAGE_AND_OPERATIONS_GUIDE.md`, `docs/FUNCTIONALITY_MATRIX.md`.
- `docs/Valdo-Infographic.html` — the living "what exists" overview (Architecture,
  SQL Reconciliation Gate, and Known Issues tabs especially).
- `prompts/e2e_batch_testing_prompt.md` — the design intent for the harness.

**Project rules & CI**
- `AGENTS.md`, `CLAUDE.md`, `chat-rules.md` — agent/process constraints.
- `azure-pipelines-ci.yml`, `azure-pipelines-deploy-onprem.yml`, `.gitlab/`,
  `ci/`, `sonar-project.properties`, `pytest.ini`, `requirements*.txt`,
  `Dockerfile`, packaging (`build_pex.sh`, `build_rpm.sh`, `packaging/`).

If a referenced artifact is absent, record it as a finding (doc drift or missing
capability) rather than assuming it exists.
</grounding_inputs>

<review_dimensions>
Evaluate each dimension. For every one: state the **current state** (cited),
a **rating** (see scale), the **risk to the four-mode goal**, and **recommendations**.

1. **Configurability & the "one engine, four modes" thesis**
   - Is the engine genuinely driven by config (mappings, rules, pipelines,
     reconciliation specs, source configs) rather than per-mode code branches?
   - What is hard-coded that should be configurable (paths, schema/table names,
     env assumptions, gate blocking semantics, baseline policy)?
   - Can a new source / file-type / environment be onboarded with **config only**?
     Trace one concrete onboarding path end-to-end and report friction.
   - **Single vs. multiple record types:** can the harness validate, reconcile, and
     report on *both* a single-record-type file and a multi-record umbrella (many
     `TRANSACTION-CODE` values) from config alone? Is the dispatch (record-type
     discriminator → per-type mapping + rules + SQL truth) declarative, or is any
     transaction code wired in code? (Cross-check `config/mappings/SHAW_TRANERT.yaml`,
     the per-type rule files, `multi_record_file_parser.py`, and ADR 0005/0008.)

2. **Separation of concerns & layering**
   - Are parsing, validation, mapping, comparison, DB access, reporting, and
     orchestration cleanly separated? Any layering violations or god-modules?
   - Is the generic engine truly source-agnostic, or does source-specific logic
     leak into `src/`? (Cross-check the AGENTS.md "no `src/` changes for harness"
     rule — is it holding, or is there pressure to break it? See the ADR 0008
     carve-out as a signal.)

3. **The four execution modes — surface & parity**
   - Inventory the entry surfaces: CLI, REST API, `watch` daemon, pipeline runner,
     E2E orchestrator (`run_source.py`), the `multi_record_report` step, the
     `shaw_tranert_smoke reconcile` subcommand, `render_multi_record_html.py`, and
     the CI wrappers. Map each to the four modes.
   - **Parity:** does ad-hoc use the *same* validation/comparison core as the
     batch/UAT/CI paths, or are there divergent implementations (e.g. file-vs-file
     compare vs. DB-truth reconcile vs. GE checkpoints)? List every comparison/
     validation engine and whether they converge. Confirm the multi-record report
     genuinely reuses the existing `MultiRecordValidator` → `run_validate_service`
     → `ValidationReporter` path (per the session-11 claim of "no new report code").
   - **Two-gate complementarity:** characterize the relationship between the L1/rules
     gate (file-vs-spec, item-count assertions) and the L2b SQL-truth reconcile
     (file-vs-live-DB expected rowset, the only gate that catches a truncated/missing
     -row file via `missing_expected`). Flag the known **empty/header-only batch
     inconsistency** (rules FAILs the item-count assertion while L2b PASSes with
     `expected_rows = 0`) as an open design question, not a bug to fix in this review.
   - Identify mode-specific glue that is *acceptable* (thin adapters) vs.
     *divergent* (parallel logic that will drift).

4. **Configuration model & schema governance**
   - Are config formats (mapping JSON, rules CSV/YAML, pipeline YAML, reconciliation
     YAML, source YAML) **versioned and validated on load**? Where is the schema
     authority for each? Are there typo-defences / fail-fast loaders?
   - Mapping/baseline version pinning: is the "mapping_version → baseline_version"
     contract enforced, or aspirational?

5. **Extensibility & plugin seams**
   - How is a new validation type, transform, comparator, report format, or secret
     provider added? Registry/strategy pattern, or edit-the-switch?
   - Secret resolution: is `SECRETS_PROVIDER=env|vault|azure_kv` a real seam or a
     stub? (Check `secret_resolver.py` and `src/utils/secrets.py`.)

6. **Data & state: idempotency, audit, baselines**
   - Is every run idempotent and re-runnable from zero (no migration drift)?
   - Audit trail: `AUDIT.VALDO_RUN_FAILURES` append-only contract, run-history DB,
     JSONL logs — coherent and queryable across modes?
   - Baseline promotion policy: who can promote, how is drift prevented?

7. **Testing strategy & confidence**
   - Unit/integration/E2E split; coverage bar (AGENTS.md cites 1,063 unit + 46 E2E,
     80%+). Is the bar enforced in CI? Are Oracle-dependent tests properly gated/
     skipped? Are the four modes each covered?
   - Is the test pyramid healthy, or inverted/over-mocked?

8. **Operability: deployment, packaging, observability, scale**
   - Deployment targets (RHEL venv, PEX, RPM, Docker) — coherent and maintained?
   - Observability (Splunk, JSONL, rollup HTML) sufficient for batch/UAT triage?
   - Scalability: per-source parallelism, chunked processing, large-file behaviour
     (cross-check `docs/SCALABILITY_ANALYSIS.md`, `docs/CHUNKED_PROCESSING.md`).

9. **Security & compliance**
   - AuthN/Z model (API key + LDAP session), SSRF/path-traversal hardening, TLS,
     secret handling, no-PII-in-non-prod assumption. Any contradictions
     (e.g. config defaults that fight their own comments)?

10. **Coupling to the current SUT (SHAW/TRANERT, Oracle, Java/CA-ESP)**
    - How much of the design assumes *this* batch/source? What would it cost to
      add a non-Oracle backend, a delimited (non-fixed-width) output, or a
      non-Java SUT? Rate the portability of the "ETL testing" promise.

11. **Documentation & knowledge integrity**
    - Is the doc set coherent or contradictory? Flag doc-vs-code drift (the
      Infographic Known-Issues tab is a useful cross-check). Is there a single
      authoritative "what exists" source?

12. **Technical debt & risk register**
    - Catalogue `TODO(valdo-gap)`, shim modules, deprecated paths, stray root-level
      scripts, version skew (3.9 vs 3.11 vs 3.14), CHANGELOG `[Unreleased]` drift.
</review_dimensions>

<probe_scenarios>
These are **mandatory, concrete "can it do this with config only?" traces**. They
are the empirical heart of the configurability verdict — do not answer them from
intuition. For each scenario, produce the **exact list of files you would create
or edit**, classify every touch as `config`, `scripts`, `src`, `test`, or `doc`,
and report the **`src/`-touch count** (the lower, the better). A scenario that
requires *any* `src/` change to work at all is, by definition, not yet
"configure, don't fork" for that axis — say so.

For each scenario state: (a) **Feasible today?** yes / partial / no; (b) the file
list with per-file classification; (c) the `src/` touch count; (d) the single
biggest blocker; (e) what an A-grade version would look like.

- **P1 — New file-type for an existing source.** Onboard a *new fixed-width output
  file* for an already-configured source (new mapping + reconciliation spec + SQL
  truth + gate wiring). Trace against the SHAW/TRANERT path as the reference.
  *Target:* zero `src/` touches.
- **P1b — New transaction code on an existing multi-record umbrella.** Add a *new
  record type* (a new `TRANSACTION-CODE` discriminator value) to an existing
  `multi_record: true` umbrella (e.g. SHAW_TRANERT) — new per-type mapping, per-type
  rules (incl. any `cross_row` rule like R028B), per-type SQL truth + reconciliation
  spec, and confirm it auto-appears in the per-type validation report and the
  cumulative umbrella `index.html`. This is the "many transactions, config only"
  probe. *Target:* zero `src/` touches; cross-check against
  `docs/ADD_RECORD_TYPE_PLAYBOOK.md`.
- **P1c — Brand-new single-record-type file.** Onboard a file with exactly one
  record type (no umbrella/dispatch) and run it through validate + reconcile +
  report. Confirm the single-record path is not just the multi-record path with
  N=1, or if it is, that this is intentional and friction-free. *Target:* zero
  `src/` touches; report whether single-record onboarding is simpler, identical, or
  harder than the multi-record case.
- **P2 — New environment overlay.** Add a `prod` (or third) environment alongside
  `sit`/`ait` — paths, source pipeline configs, baselines, secret resolution.
  *Target:* zero `src/` touches; config + baseline data only.
- **P3 — New backend (portability stress).** Add a **non-Oracle** truth source
  (e.g. PostgreSQL, or a flat-file/Parquet "expected" extract) for the DB-truth
  reconcile. This deliberately probes how Oracle-coupled the engine is.
  *Target:* a backend lives behind an interface; ideally a new adapter module +
  config, with the comparator/orchestrator untouched.
- **P4 — New output shape.** Add a **delimited (CSV/pipe) multi-record output**
  rather than fixed-width — does the parser/mapping/comparison stack generalize,
  or is fixed-width assumed throughout?
- **P5 — New SUT / non-Java producer.** Point the harness at a batch produced by a
  **different system** (not the Java/CA-ESP batch) — what is hard-wired to the
  current producer (trigger-file naming, load/generate shell-outs, path layout)?
- **P6 — Brand-new source, end-to-end, in one of the four modes.** Stand up a
  *new source* and run it (a) ad-hoc and (b) as a CI stage. How much is genuinely
  shared between those two runs vs. mode-specific glue?

Roll the P1–P6 results (including P1b and P1c) into a single **probe scorecard**
table (scenario, feasible?, `src/` touches, blocker, target-state delta). The
P1/P1b/P1c row group is the headline "any file or transaction, single or multiple,
by config alone" verdict for the harness.
</probe_scenarios>

<rating_scale>
Rate each dimension and give an overall verdict:
- **A — Strong / on-path:** evidence shows the design supports the four-mode goal;
  only minor polish needed.
- **B — Mostly sound, gaps:** right direction, but specific, named gaps must close.
- **C — At risk:** structural issues that will force a fork or rewrite if unaddressed.
- **D — Off-path:** the current design actively blocks the four-mode goal.
- **?** — Cannot determine from available evidence; list what is needed to rate.
</rating_scale>

<target_state>
Before judging the current code, **sketch the ideal target architecture** for a
configurable ETL-testing application that serves all four modes from one engine,
then measure today's Valdo against it as an explicit **gap analysis**. This makes
the review forward-looking (where should we be?) rather than only retrospective
(what is wrong?).

Produce a **"to-be" reference architecture** covering at least:
- **Core engine boundary** — the source-agnostic primitives (parse, validate,
  compare/reconcile, report, audit) and the contract each exposes. What belongs
  *inside* the engine vs. in thin per-mode/per-source adapters.
- **Configuration plane** — the full set of config artifacts, each with a versioned,
  load-validated schema and a single schema authority; how a source/file-type/
  environment is described declaratively; the mapping↔baseline version contract.
- **Backend abstraction** — a truth-source interface so Oracle is *one* implementation
  (cf. probe P3), plus a secret-provider seam (env/Vault/Azure KV as real strategies).
- **Mode adapters** — how the *same* core is driven by ad-hoc (CLI/UI), integration
  batch, UAT batch, and CI/CD, with a parity guarantee that all four exercise the
  identical validation/comparison core.
- **Audit & state plane** — idempotent runs, append-only failure record, run-history,
  baseline-promotion governance, observability across modes.
- **Extension seams** — registries/strategies for new validation types, transforms,
  comparators, report formats, backends — "add, don't edit-the-switch."

Then deliver a **gap-analysis table**: for each target-state element, give
`current state` (cited) → `target state` → `gap size` (None/S/M/L) → `closing move`
(linked to a recommendation ID). Be explicit where the *current design choice is
already the right target* (avoid recommending change for its own sake). Keep the
target state **pragmatic and incremental** — it must be reachable from today's code
without a rewrite, and must honor the `AGENTS.md` constraints (notably "harness =
scripts + config, no `src/` changes" — note where that rule is an asset for the
target state and where it may need a sanctioned carve-out like ADR 0008).
</target_state>

<method>
Work in phases. Record the commit SHA reviewed. Do not edit code.

- **Phase 0 — Orient (read-only):** read `AGENTS.md`, `docs/architecture.md`, the
  prior `ARCHITECTURE_REVIEW_2026-02-20.md`, the Infographic, and the ADR index.
  Produce a one-paragraph mental model and a list of claims to verify.
- **Phase 1 — Map the surfaces:** enumerate every entry point (CLI commands, API
  routers, `watch`, pipeline runner, E2E orchestrator, CI wrappers) and the layer
  each calls into. Build the "mode → surface → core" matrix.
- **Phase 2 — Trace the core paths:** follow one file at a time through (a) ad-hoc
  validate, (b) DB-to-file reconcile (L2b), (c) a full E2E source run, (d) a CI
  invocation. Note where they share code vs. diverge.
- **Phase 3 — Probe configurability:** run the mandatory `<probe_scenarios>`
  (P1, P1b, P1c, P2–P6). For each, list every file you'd create/edit, classify each
  touch, count `src/` touches, and fill the probe scorecard. The P1/P1b/P1c +
  P2 `src/`-vs-config ratio is the headline metric (any file or transaction, single
  or multiple, plus new environment); P3–P6 are the portability stress.
- **Phase 4 — Stress the dimensions:** answer all 12 review dimensions with citations.
- **Phase 5 — Target state & gap analysis:** sketch the `<target_state>` reference
  architecture and produce the gap-analysis table (current → target → gap → move).
- **Phase 6 — Synthesize:** verdict, prioritized recommendations, and a roadmap.
  Recommendations only; propose ADRs where a decision is load-bearing.
</method>

<deliverables>
Produce a single Markdown document at:

  `docs/ARCHITECTURE_REVIEW_<YYYY-MM-DD>.md`

**The section set scales with the `depth` tunable** (default `standard`). Each
template is a strict superset of the lighter one, so a `quick` pass can later be
extended to `standard`/`deep` without restructuring.

### `quick` template (fast pulse-check — Phases 0–1 + probes P1/P1b/P1c/P2 only)
1. **Header** — date, reviewer, commit SHA, scope, `depth: quick`, one-line verdict.
2. **Executive summary** (≤ 1 page) — the four-mode verdict, top 5 strengths, top 5
   risks, the single most important recommendation. Lead with the answer to
   *"one engine four ways, or four code paths?"*
3. **Mode → surface → core matrix** — the Phase-1 table with parity notes.
4. **Configurability headline metric** — the P1 + P1b + P1c + P2 `src/`-vs-config
   touch ratios with file lists, stated as the "any file or transaction (single or
   multiple) + new environment, config-only" verdict.
5. **Top risks & next steps** — a short risk list and the 3 highest-leverage moves.

### `standard` template (default — all phases) — `quick` PLUS:
6. **Probe scorecard** — the full P1/P1b/P1c/P2–P6 table (scenario, feasible?,
   `src/` touches, blocker, target-state delta).
7. **Dimension-by-dimension findings** — for each of the 12 dimensions: current
   state (cited), rating, risk to the goal, recommendations. Use the
   fact/inference/recommendation separation throughout.
8. **Target state & gap analysis** — the `<target_state>` to-be reference
   architecture + the gap-analysis table (current → target → gap size → closing move).
9. **Risk register** — table: risk, likelihood, impact, evidence, owner/action.
   Include the technical-debt catalogue.
10. **Prioritized roadmap** — "must-fix before claiming four-mode readiness",
    "should-fix next", "nice-to-have"; each item sized (S/M/L) and tied to a finding.
11. **Proposed ADRs** — short stubs (title + decision needed + options) for any
    load-bearing decision surfaced (e.g. "unify the comparison engines", "formalize
    the config schema registry", "backend abstraction for non-Oracle").
12. **Open questions for the team** — anything undetermined, with the exact evidence
    needed to close it. Per `AGENTS.md`, do not guess on trigger-file naming,
    baseline-promotion policy, gate blocking semantics, or Oracle schema/table
    names — list them here.
13. **Appendix** — the read-list actually consulted (paths), so the review is
    reproducible.

### `deep` template — `standard` PLUS:
14. **Surface trace appendix** — every CLI command and API router traced to the core
    layer it invokes, with parity notes per surface.
15. **Per-engine convergence matrix** — every comparison/validation engine × the
    four modes, scored on behavioural identity (0 = divergent impl, 3 = identical
    core), with the citation backing each score.

Formatting rules (all templates): cite `path:symbol` inline; keep code excerpts
short and purposeful; use tables for matrices, the probe scorecard, the gap analysis,
and the risk register; mark every recommendation with a stable ID (e.g. `R-03`) so
future iterations can track closure; tag each finding with a confidence note where
evidence is thin.
</deliverables>

<acceptance_criteria>
The review is complete when (criteria apply at the stated `depth`):

**All depths:**
- [ ] The four-mode question is answered directly in the executive summary, with
      evidence, not vibes.
- [ ] The configurability headline metric (P1 + P1b + P1c + P2 `src/`-vs-config
      touch ratio) is computed with the actual file lists.
- [ ] No recommendation is applied as a code change; all are proposals.
- [ ] Output is a single dated Markdown file in `docs/` matching the template for
      the chosen `depth`.

**`standard` and `deep` additionally:**
- [ ] All probe scenarios (P1, P1b, P1c, P2–P6) are traced with file-by-file touch
      classification and an `src/`-touch count; the probe scorecard is filled.
- [ ] The "any file or transaction, single or multiple, config-only" verdict for the
      harness is stated explicitly from the P1/P1b/P1c results (with the umbrella
      `TRANSACTION-CODE` dispatch confirmed declarative, not coded).
- [ ] Every one of the 12 dimensions has a citation-backed current-state, a rating,
      and at least one recommendation (or an explicit "no action needed").
- [ ] The target-state reference architecture is sketched and every element appears
      in the gap-analysis table (current → target → gap → closing move), including
      cases where the current choice already IS the target.
- [ ] Every comparison/validation engine in the codebase is enumerated and its
      convergence (or divergence) across the four modes is stated.
- [ ] Doc-vs-code drift is checked against the Infographic Known-Issues tab and the
      prior architecture review (delta noted).
- [ ] Every "gap" is confirmed by a code search, not assumed.

**`deep` additionally:**
- [ ] Every CLI command and API router is traced to its core layer; the per-engine
      convergence matrix (engine × four modes, scored 0–3) is filled with citations.
</acceptance_criteria>

<anti_patterns>
Avoid these reviewer failure modes:
- Restating `docs/architecture.md` as if it were findings (verify against code).
- Generic advice ("add more tests", "improve modularity") with no file/symbol.
- Confusing *documented intent* with *implemented behaviour*.
- Declaring something missing without grepping for it first.
- Scope creep into a line-by-line code review — stay architectural.
- Editing code or "fixing while reviewing" — this phase is read-only.
- Treating the prior review as gospel — re-validate it.
</anti_patterns>

<tunables>
Adjust per run if needed:
- **Depth:** `quick` (Phases 0–1 + probes P1/P1b/P1c/P2 → the `quick` deliverable
  template) | `standard` (all phases + all probes + target-state/gap analysis →
  `standard` template, the default) | `deep` (standard + full surface trace +
  per-engine convergence matrix → `deep` template).
- **Lens:** weight specific dimensions (e.g. emphasize CI/CD + configurability for
  a pipeline-readiness gate; emphasize security for an InfoSec review).
- **Audience:** `engineering` (default) | `leadership` (front-load exec summary,
  soften jargon) | `audit` (emphasize controls, audit trail, compliance).
- **Commit:** always record the reviewed SHA so the review is reproducible.
</tunables>

---

## Changelog
- **v3:** Added a second explicit configurability axis — **"any file or transaction,
  single or multiple, by config alone"** — alongside the four-mode thesis. New probe
  scenarios **P1b** (new transaction code on an existing multi-record umbrella) and
  **P1c** (brand-new single-record-type file); extended review dimensions 1 and 3 with
  single-vs-multi-record dispatch and the **two-gate complementarity** (L1/rules vs L2b
  SQL-truth) including the empty/header-only-batch inconsistency. Refreshed grounding
  inputs to the session-11 harness state: the `shaw_tranert_smoke reconcile` subcommand
  + HTML report, the orchestrator `multi_record_report` step, `render_multi_record_html.py`,
  ADR 0009 (`cross_row:sequential` `start`/`step`, rule R028B) and ADR 0008 status, and
  the `docs/handover/L2B_SESSION_*` log. Updated method Phase 3, deliverable templates,
  acceptance criteria, and the `depth` tunable to carry P1b/P1c through.
- **v2:** Added (a) **depth-aware deliverable templates** (`quick`/`standard`/`deep`,
  each a superset) so a fast pass produces just the exec summary + matrix + headline
  metric + top risks; (b) a **`<target_state>` reference-architecture + gap-analysis**
  step (new Phase 5) so the review measures the current design against an ideal "to-be"
  rather than only critiquing what exists; (c) six **mandatory `<probe_scenarios>`**
  (P1–P6: new file-type, new env, non-Oracle backend, delimited output, new SUT,
  brand-new source across modes) with a probe scorecard, replacing the informal
  Phase-3 hand-wave. Updated method, acceptance criteria, and tunables to match.
- **v1** (initial): meta prompt scoped to the "one engine, four modes" thesis with
  12 review dimensions, a phased read-only method, and a fixed deliverable
  structure. Iterate by bumping the version, appending here, and keeping prior
  versions in git history.
