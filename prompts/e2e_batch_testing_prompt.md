# Valdo E2E Batch Testing — LLM Implementation Prompt

> **How to use**: Paste this entire file as the system/user prompt to a coding LLM
> (Claude, GitLab Duo, Copilot, ChatGPT). The LLM will produce YAML suites, shell
> wrappers, JSON configs, SQL DDL, and a small amount of glue code — **not** changes
> to Valdo's internal modules. Assume Valdo's existing CLI / REST API / `watch`
> surfaces are complete except for the gaps explicitly listed under
> `<valdo_gaps_to_implement>`.

---

<role>
You are a senior batch-automation engineer building an end-to-end (E2E) regression
harness on top of the **Valdo** engine. Valdo is an existing, non-agentic file
validation / comparison / ETL testing tool with a CLI (`valdo …`), a FastAPI REST
API, a `valdo watch` trigger-file daemon, and a YAML-driven gate-based pipeline
runner (`valdo run-etl-pipeline`). You will automate Valdo via **scripts +
configuration only**. You will not refactor Valdo internals unless a gap is
explicitly listed below.
</role>

<context>

### System under test
- A **Java batch application** running on **RHEL** servers, orchestrated by **CA ESP
  Workload Automation**. Java jobs are kicked off by shell scripts.
- For each **source system** (15+ distinct sources) the batch is a 2-step flow:
  1. **Load**: pipe-delimited input files → Oracle **staging tables** (min. 5 tables
     per source).
  2. **Generate**: staging tables + mapping documents → **6 fixed-width output
     files** per source. 4 of those 6 files contain **multiple transaction types**,
     some with **interdependent transactions and headers**.
- Mappings are **one mapping per output file**. Multi-record-type mappings already
  exist in Valdo format but are **not fully tested** — the E2E harness must
  exercise them.
- Sources are processed **independently** (no cross-source dependencies in the
  common case) → parallelizable.
- Valdo runs on the **same RHEL host** as the Java batch.

### Environments
- Two environments only: **`sit`** and **`ait`**. Prod is out of scope for this
  iteration but the config layout must not block a future `prod` overlay.
- Data is already privatized in non-prod → **no PII redaction** in reports
  (`--suppress-pii` stays OFF).

### Secrets
- Start with **environment variables / `.env`**.
- Design the secret-resolution layer so a future flip to **HashiCorp Vault** is a
  config change only (Valdo already supports `SECRETS_PROVIDER=env|vault|azure_kv`).
  Do not hardcode `os.environ` lookups outside one resolver helper.

### Baselines (golden data) strategy — MANDATORY DESIGN
The harness must implement a **dual-baseline, version-pinned** approach because
mapping documents and expected output evolve release-to-release:

1. **Per-release golden baselines** under `baselines/{env}/{source}/{release_tag}/…`.
2. **Mapping-version-aware comparison**: every mapping JSON carries a `version`
   field; the run manifest pins `mapping_version → baseline_version` so an MR that
   changes a mapping must atomically refresh the baseline.
3. **Three comparison layers per output file** — every E2E run executes all three:
   - **L1 Structural**: `valdo validate` against current mapping + rules. No
     baseline needed. Catches format / width / rule breaks.
   - **L2 Regeneration**: Valdo re-derives the output from current staging using
     the current mapping, then `valdo compare` vs. the Java-generated output.
     Catches Java code drift.
   - **L3 Baseline diff**: `valdo compare` of the new Java-generated output vs.
     the pinned golden baseline, with **tolerance config** (timestamps, sequence
     numbers, batch IDs declared as ignored fields per source/file).
4. **Baseline refresh**: a `scripts/promote_baseline.sh` wrapper copies a
   QA-signed-off run into the next `{release_tag}` folder and updates
   `baselines/manifest.json`. Mapping owners approve via CODEOWNERS.
5. **Staging truth** = the input file. Use `valdo extract` from staging and
   compare against the input file directly; do not snapshot staging.

</context>

<goal>
Produce a complete, runnable E2E test harness — **scripts, YAML, JSON, SQL, and
one short README** — that:

1. Watches for **input-file trigger** and **output-file trigger** drops from CA ESP
   on the RHEL server and runs the corresponding Valdo workflow per source.
2. For each source, runs the **3-layer comparison** (L1/L2/L3) for all 6 output
   files plus the **file → staging** check for each input file.
3. **Shells out** to the existing Java/shell scripts when the harness itself needs
   to trigger a Java step (configurable: shell-out vs. skip).
4. Runs each source in an **isolated working dir** so 15+ sources can execute in
   parallel without colliding on temp files, reports, or staging prefixes.
5. Treats **every gate as configurably blocking / non-blocking** (Valdo
   `Gate.blocking` already supports this — just expose it cleanly per source).
6. Writes **per-file HTML reports** (Valdo's default) plus a **per-source roll-up
   index** and a **per-run global roll-up index** that link to every report.
7. On **any gate failure**, writes a row into an Oracle audit table
   `AUDIT.VALDO_RUN_FAILURES` (one row per failed gate).
8. Supports `--env sit|ait` as the single switch that flips every path, DSN,
   schema, and credential.

</goal>

<valdo_gaps_to_implement>
The implementer must ADD these to Valdo (or, if a gap is non-trivial, deliver a
**thin wrapper script** that calls existing Valdo primitives and explicitly TODO
the deeper integration). Treat each as a named deliverable:

- **(a) Failure-to-DB sink**: a small writer module + CLI flag
  (`--failure-sink-dsn`, `--failure-sink-table`) used by `valdo run-etl-pipeline`
  and the wrapper scripts. Inserts ONE row per failed gate. Schema in
  `<failure_sink_schema>` below.
- **(b) Per-run roll-up HTML index**: a script (`scripts/build_rollup_index.py`)
  that scans `reports/{run_id}/` and emits `index.html` + `summary.json` linking
  every per-file report with pass/fail status, source, gate, layer (L1/L2/L3),
  row count, error count, and report path. No changes to Valdo's per-file
  renderer.
- **(c) Source/environment-aware path resolver**: a Python helper
  (`scripts/e2e_lib/path_resolver.py`) that accepts templated paths with placeholders
  `{env}`, `{source}`, `{run_date}`, `{file_type}`, `{release_tag}` and resolves
  them against `config/e2e/paths.yml` + per-source overrides in
  `config/e2e/sources/{source}.yml`. Also supports **filename glob/regex**
  matching trigger files → `(source, file_type)`.
- **(d) Shell-out gate step**: if Valdo's `GateStep.type` does not already
  support `shell`, deliver a wrapper that runs the YAML pipeline up to a gate,
  shells out, then resumes — keep it script-side so Valdo internals are
  untouched. (Confirm in the codebase before implementing; current known types
  are `validate`, `compare`, `db_compare`, `reconcile`.)
- **(e) Baseline-version manifest + promote script**: `baselines/manifest.json`
  schema + `scripts/promote_baseline.sh` + `scripts/e2e_lib/baseline_resolver.py`
  that looks up the right baseline file given `(env, source, file_type,
  mapping_version)`.
- **(f) Tolerance/ignore config plumbing**: per-source YAML declares fields to
  ignore in L3 compare (timestamps, sequence numbers). The wrapper passes these
  as `--ignore-fields` to `valdo compare` (if the flag does not exist, deliver
  a thin pre-processor that strips/masks those columns from both files into a
  temp dir before invoking `valdo compare`).

If any of (a)–(f) already exists in Valdo, the implementer must detect that
(grep / `valdo --help`) and skip the implementation, noting it in the README.

</valdo_gaps_to_implement>

<failure_sink_schema>

```sql
-- Schema: AUDIT (separate from staging)
-- One row per failed gate.
CREATE TABLE AUDIT.VALDO_RUN_FAILURES (
    failure_id        NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id            VARCHAR2(64)  NOT NULL,
    env               VARCHAR2(8)   NOT NULL,            -- 'sit' | 'ait'
    source            VARCHAR2(64)  NOT NULL,
    pipeline_name     VARCHAR2(128) NOT NULL,
    gate_name         VARCHAR2(128) NOT NULL,
    gate_stage        VARCHAR2(32),                       -- 'input' | 'output' | etc.
    layer             VARCHAR2(8),                        -- 'L1' | 'L2' | 'L3' | NULL
    file_name         VARCHAR2(256),
    file_type         VARCHAR2(64),
    mapping_path      VARCHAR2(512),
    mapping_version   VARCHAR2(32),
    error_type        VARCHAR2(64)  NOT NULL,             -- 'structural' | 'compare_diff' | 'threshold_breach' | 'java_step_failed' | etc.
    error_count       NUMBER        DEFAULT 0,
    row_count         NUMBER,
    report_path       VARCHAR2(512),
    blocking          NUMBER(1)     DEFAULT 1,            -- 1 = halted source, 0 = recorded only
    failed_at         TIMESTAMP     DEFAULT SYSTIMESTAMP,
    failure_detail    CLOB                                -- short JSON: first N error rows / message
);

CREATE INDEX IDX_VRF_RUN ON AUDIT.VALDO_RUN_FAILURES (run_id);
CREATE INDEX IDX_VRF_SRC ON AUDIT.VALDO_RUN_FAILURES (env, source, failed_at);
```

Deliver this DDL at `scripts/sql/audit_valdo_run_failures.sql`.

</failure_sink_schema>

<deliverables>

Produce the following files. Use exactly these paths.

### 1. Top-level config
- `config/e2e/paths.yml` — env-keyed root paths and templated patterns:
  ```yaml
  envs:
    sit:
      input_root:   "/data/sit/{source}/input"
      output_root:  "/data/sit/{source}/output"
      trigger_root: "/data/sit/{source}/triggers"
      report_root:  "/data/sit/_reports/{run_id}/{source}"
      work_root:    "/data/sit/_work/{run_id}/{source}"
      baseline_root: "baselines/sit/{source}/{release_tag}"
      oracle_dsn_env: "ORACLE_DSN_SIT"
      oracle_user_env: "ORACLE_USER_SIT"
      oracle_password_env: "ORACLE_PASSWORD_SIT"
      staging_schema: "STG_SIT"
      audit_schema:   "AUDIT"
    ait:
      input_root:   "/data/ait/{source}/input"
      output_root:  "/data/ait/{source}/output"
      trigger_root: "/data/ait/{source}/triggers"
      report_root:  "/data/ait/_reports/{run_id}/{source}"
      work_root:    "/data/ait/_work/{run_id}/{source}"
      baseline_root: "baselines/ait/{source}/{release_tag}"
      oracle_dsn_env: "ORACLE_DSN_AIT"
      oracle_user_env: "ORACLE_USER_AIT"
      oracle_password_env: "ORACLE_PASSWORD_AIT"
      staging_schema: "STG_AIT"
      audit_schema:   "AUDIT"
  filename_patterns:
    # regex → (source, file_type, direction)
    - pattern: '^(?P<source>[A-Z0-9_]+)_input_(?P<file_type>[A-Z0-9_]+)_\d{8}\.dat$'
      direction: input
    - pattern: '^(?P<source>[A-Z0-9_]+)_(?P<file_type>P327|P328|P329|P330|P331|P332)_\d{8}\.txt$'
      direction: output
  ```
- `config/e2e/sources/{SOURCE}.yml` — one file per source. Template:
  ```yaml
  source: SRC_A
  release_tag: "R2026.05"
  java_scripts:
    load:     "/apps/batch/bin/load_SRC_A.sh"
    generate: "/apps/batch/bin/generate_SRC_A.sh"
  staging_tables:
    - STG_SRC_A_HEADER
    - STG_SRC_A_TXN
    - STG_SRC_A_PARTY
    - STG_SRC_A_ADDR
    - STG_SRC_A_AUDIT
  input_files:
    - file_type: HEADER
      glob: "SRC_A_input_HEADER_*.dat"
      mapping: "config/mappings/SRC_A_input_HEADER.json"
      target_staging_table: "STG_SRC_A_HEADER"
    # … one entry per input file
  output_files:
    - file_type: P327
      glob: "SRC_A_P327_*.txt"
      mapping: "config/mappings/SRC_A_P327.json"
      rules:   "config/rules/SRC_A_P327.json"
      multi_record: false
      strict_fixed_width: true
      strict_level: all
      tolerance:
        ignore_fields: ["FILE_CREATE_TS", "BATCH_SEQ_NO"]
        max_error_pct: 0
    - file_type: P328
      glob: "SRC_A_P328_*.txt"
      mapping: "config/mappings/SRC_A_P328.json"
      rules:   "config/rules/SRC_A_P328.json"
      multi_record: true
      discriminator_field: "RECORD_TYPE"
      strict_fixed_width: true
      strict_level: all
      tolerance:
        ignore_fields: ["FILE_CREATE_TS"]
        max_error_pct: 0
    # … entries for P329, P330, P331, P332
  gates:
    load_step:        { blocking: true,  invoke_java: true  }
    file_to_staging:  { blocking: true,  invoke_java: false }
    generate_step:    { blocking: true,  invoke_java: true  }
    L1_structural:    { blocking: true,  invoke_java: false }
    L3_baseline_diff: { blocking: false, invoke_java: false }
  ```
  > Note: the original `L2_regeneration` gate shown in earlier drafts was
  > retired (ADR 0012); the output-truth axis is covered by the
  > orchestrator-driven `L2b_sql_truth` gate and regression by
  > `L3_baseline_diff`.

### 2. Pipeline YAML (per source, generated from the source config)
- `scripts/generate_pipeline_yaml.py` — reads
  `config/e2e/sources/{SOURCE}.yml` + `config/e2e/paths.yml` and emits
  `config/e2e/pipelines/{env}/{SOURCE}.pipeline.yaml` consumable by
  `valdo run-etl-pipeline`. Each gate uses `for_each: source` where appropriate
  and maps to the existing `GateStep` types (`validate`, `compare`,
  `db_compare`). The shell-out step (Java invocation) is handled outside the
  YAML by the wrapper script in #3.

### 3. Wrapper scripts
- `scripts/run_e2e_source.sh` — orchestrates ONE source end-to-end:
  1. Resolve paths for `(env, source, run_id)`.
  2. Create isolated work dir.
  3. (Optional, configurable) shell-out to `java_scripts.load`.
  4. Run `valdo run-etl-pipeline` for the `file_to_staging` gate.
  5. (Optional, configurable) shell-out to `java_scripts.generate`.
  6. Run `valdo run-etl-pipeline` for `L1_structural`, then `L3_baseline_diff`
     gates (the former `L2_regeneration` gate was retired — ADR 0012).
  7. On any failure, write to `AUDIT.VALDO_RUN_FAILURES` via the failure-sink
     module (gap **a**) — one row per failed gate.
  8. Emit the per-source roll-up at `report_root/index.html`.
  9. Exit non-zero only if a **blocking** gate failed.
- `scripts/run_e2e_all.sh` — fans out `run_e2e_source.sh` across all sources
  in parallel (configurable concurrency, default 4), waits, then calls
  `scripts/build_rollup_index.py` to produce the global roll-up.
- `scripts/valdo_watch_wrapper.sh` — wraps `valdo watch` with the filename
  pattern config so a trigger file drop maps to `(source, file_type, direction)`
  and invokes the right step of `run_e2e_source.sh`. Document the trigger-file
  naming contract in the README.

### 4. SQL
- `scripts/sql/audit_valdo_run_failures.sql` — exactly the DDL in
  `<failure_sink_schema>`.

### 5. Baseline machinery
- `baselines/manifest.json` — example entries pinning
  `(env, source, file_type) → release_tag → mapping_version`.
- `scripts/promote_baseline.sh` — copies a signed-off run's outputs into the
  next `release_tag` folder and updates the manifest with an audit comment.
- `scripts/lib/baseline_resolver.py` — pure-function lookup.

### 6. Roll-up index
- `scripts/build_rollup_index.py` — emits `index.html` + `summary.json`.

### 7. Shared Python helpers
- `scripts/e2e_lib/path_resolver.py`
- `scripts/e2e_lib/secret_resolver.py` (env-var implementation now, Vault
  adapter stub for later — same interface).
- `scripts/e2e_lib/failure_sink.py` (inserts into `AUDIT.VALDO_RUN_FAILURES`).
- `scripts/e2e_lib/ignore_field_filter.py` (L3 tolerance pre-processor).

> **Note on directory naming**: helpers live under `scripts/e2e_lib/` (not
> `scripts/lib/`) because the repo's `.gitignore` excludes any directory
> literally named `lib/`. Do not rename this directory.

### 8. Documentation
- `prompts/e2e_batch_testing_README.md` — short operator README:
  - How CA ESP drops trigger files
  - Trigger filename grammar
  - How to add a new source (5-step checklist)
  - How to promote a baseline
  - How to read the failure table and roll-up index
  - Known gaps vs. Valdo's current capabilities

</deliverables>

<acceptance_criteria>

A reviewer must be able to verify ALL of the following without changes to
Valdo's internal source tree (only the wrapper scripts and configs you ship):

1. `bash scripts/run_e2e_source.sh --env sit --source SRC_A --run-id $(date +%Y%m%d_%H%M%S)`
   completes and produces:
   - 6 per-file HTML reports (one per output file) at the resolved `report_root`
   - 1 per-source `index.html` linking all of them
   - 0 rows in `AUDIT.VALDO_RUN_FAILURES` for that `run_id` when inputs match
     the pinned baseline
2. Deliberately corrupting one output file (e.g., truncating a fixed-width
   record) causes:
   - The L1 gate to fail, source halted (because `L1_structural.blocking=true`)
   - Exactly **one** row in `AUDIT.VALDO_RUN_FAILURES` with
     `error_type='structural'` and `blocking=1`
   - Exit code non-zero
3. Deliberately changing one cell in the Java-generated output (within
   tolerance fields) causes **no** L3 failure; changing a non-tolerance cell
   causes an L3 failure recorded as `blocking=0` (run continues, exit code 0).
4. `bash scripts/run_e2e_all.sh --env sit` fans out >=2 sources concurrently
   without file collisions and produces a global roll-up at
   `/data/sit/_reports/{run_id}/index.html`.
5. Trigger-file mode: dropping a file matching the configured input pattern
   into `trigger_root` causes `valdo_watch_wrapper.sh` to invoke the correct
   step of `run_e2e_source.sh` within ≤10 seconds.
6. Flipping `--env ait` changes every resolved path, DSN, and schema with no
   other code change.
7. `git grep -n "os.environ"` outside `scripts/e2e_lib/secret_resolver.py`
   returns no hits in the **new** code for this harness (secret-resolution is
   centralized; pre-existing legacy callers elsewhere in `scripts/` are out
   of scope).
8. `valdo --help` and existing unit/integration tests still pass — no Valdo
   internal modules were modified.

</acceptance_criteria>

<constraints>

- **No modifications to Valdo internals.** Only add files under `scripts/`,
  `config/e2e/`, `baselines/`, and `prompts/`. If a gap genuinely requires
  internal change, leave a TODO and a wrapper that achieves the behavior
  externally.
- **Python 3.9+**, **bash** for shell wrappers (RHEL target).
- **Idempotent**: re-running the same `(env, source, run_id)` must either
  resume cleanly or refuse with a clear message — never half-write
  `AUDIT.VALDO_RUN_FAILURES`.
- **Parallel-safe**: every per-source artifact lives under `work_root` /
  `report_root` templated with `{run_id}` and `{source}`. No global temp files.
- **Failure rows are append-only**. Never `UPDATE` or `DELETE` from
  `AUDIT.VALDO_RUN_FAILURES`.
- **Configuration over code**: every path, table name, threshold, tolerance
  field, and gate-blocking flag must be reachable from YAML/JSON config. No
  source-specific logic in `.py` or `.sh` files.
- **Logging**: every wrapper script must emit a structured JSON line per
  significant event to `logs/e2e_{run_id}_{source}.jsonl` (consistent with
  Valdo's `audit.jsonl` format).
- **Exit codes**: 0 = all blocking gates passed; 2 = a blocking gate failed;
  3 = infrastructure error (DB unreachable, config invalid, missing baseline).

</constraints>

<implementation_order>

Produce the deliverables in this order and stop after each milestone for
review:

1. **M1 — Config skeleton + path resolver**: `config/e2e/paths.yml`, one
   `config/e2e/sources/SRC_A.yml`, `scripts/e2e_lib/path_resolver.py`,
   `scripts/e2e_lib/secret_resolver.py`, unit tests for both helpers.
2. **M2 — Failure sink + DDL**: `scripts/sql/audit_valdo_run_failures.sql`,
   `scripts/e2e_lib/failure_sink.py`, integration test using SQLite as a stand-in.
3. **M3 — Pipeline YAML generator**: `scripts/generate_pipeline_yaml.py` +
   golden-file test for `SRC_A`.
4. **M4 — Single-source wrapper**: `scripts/run_e2e_source.sh` covering the
   full 6-step flow including shell-outs and failure-sink writes.
5. **M5 — Baseline machinery**: `baselines/manifest.json` example,
   `scripts/e2e_lib/baseline_resolver.py`, `scripts/promote_baseline.sh`,
   `scripts/e2e_lib/ignore_field_filter.py`.
6. **M6 — Roll-up + fan-out**: `scripts/build_rollup_index.py`,
   `scripts/run_e2e_all.sh`.
7. **M7 — Trigger-file mode**: `scripts/valdo_watch_wrapper.sh` + filename
   pattern tests.
8. **M8 — Operator README** at `prompts/e2e_batch_testing_README.md`.

</implementation_order>

<known_valdo_surfaces_to_use>

Use these existing capabilities — do NOT reimplement them:

- `valdo validate -f … -m … -r … --strict-fixed-width --strict-level all -o …`
- `valdo compare -f1 … -f2 … -k <keys> --use-chunked -o …`
- `valdo extract -t <TABLE> -o … -l <limit>` (for the file→staging check, extract
  staging into a temp file and `compare` against the input file).
- `valdo run-etl-pipeline --config <yaml>` with `Gate.for_each: source` and
  `Gate.blocking: true|false` (defined in `src/pipeline/etl_config.py`).
- `valdo watch` polls a trigger directory for `.trigger` files; pair with
  `scripts/valdo_watch_wrapper.sh` to route to the right step.
- `POST /api/v1/runs/trigger` webhook is available for future CA ESP API
  integration (out of scope this iteration).
- Multi-record-type mapping support is already implemented; the L1 gate must
  exercise it for the 4 multi-record output files via the `multi_record: true`
  flag on each output file entry.

</known_valdo_surfaces_to_use>

<out_of_scope>

- Cross-environment comparison (e.g., `sit` vs. `ait` outputs). Listed for
  backlog only.
- Splunk / email / ticketing notifications. Failure-sink to DB only.
- PII redaction.
- Prod-environment paths and credentials.
- Modifying Valdo's HTML report renderer.
- CA ESP API integration (we use file-trigger only in this iteration).

</out_of_scope>

<final_instruction>
Begin with **M1** only. Produce the files for M1 in full, with complete code
(no placeholders), and stop. Wait for review before proceeding to M2.
</final_instruction>
