# Changelog

All notable user-visible changes to Valdo are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- EB-S1: `multi_record` / `discriminator_field` fields removed from
  `OutputFileConfig`; multi-record dispatch is now inferred from the
  mapping file extension (`.yaml` -> umbrella, `.json` -> flat) per ADR 0005.
  Legacy keys in source YAML are rejected with a helpful error citing
  the ADR. SHAW.yml migrated inline; EB-S2 will guardrail this in CI.
- R-16 (#45): Flipped ADR 0008, 0009, 0010, 0014 status to Accepted.
  Fixed `run_source.py` lint (removed unused `shlex`/`shutil` imports and
  dead `f2s_blocking`/`f2s_policy` variables). Vestigial validator methods
  deferred to follow-up issue #47.
- R-15: Updated `prompts/architecture_review_meta_prompt.md` Status line from v2 to v3.
- R-15: Fixed stale docstring example in `scripts/e2e_lib/reconciliation_spec.py` to use
  umbrella record-type names (`batch_header`, `rt_32000`, `rt_32010`) instead of bare
  discriminator codes (`"32000"`, `"32010"`).

### Security
- R-14 (#43): Audited all `src/` Python files for residual `shell=True` in
  subprocess calls -- confirmed zero occurrences. Guard test
  (`tests/unit/test_no_shell_true.py`) with regex + AST checks prevents
  reintroduction. All subprocess calls use explicit arg-arrays.

### Added
- EF-S3: MCP resources `taxonomy://violations` and `taxonomy://rules`.
  Live taxonomies introspected from the engine (no hardcoded duplicates).
  Agents can pull both via MCP `resources/read` to ground violation
  diagnosis and rule recommendations.
- EF-S1: MCP Streamable HTTP server scaffold mounted at `/mcp` on the FastAPI
  process. `initialize` handshake advertises tools/resources/prompts capabilities
  (registries empty in this scaffold). Dev-mode auth via `VALDO_MCP_AUTH=dev`;
  unset env returns 401. Tool/resource/prompt implementations land in EF-S2 /
  EF-S3 / EF-S6. Real LDAPS + X-API-Key bridge lands in EF-S7. Python 3.10+
  required (MCP SDK constraint).
- EA-S1: `SourceConfig`, `OutputFileConfig`, `InputFileConfig`,
  `ToleranceConfig`, `ThresholdsConfig` Pydantic models in
  `src/pipeline/etl_config.py` with implicit defaults for
  `strict_fixed_width`, `strict_level`, `tolerance.*`,
  `thresholds.max_errors`. Not yet wired into the source-YAML loader -- 
  follow-up story.
- R-13 (#42): `build_rollup_index.py` now discovers and links per-source
  `multi_record/<file_type>/index.html` reports into the global rollup HTML
  and `summary.json`. Sources without multi-record reports show `&mdash;`
  (graceful). 17 new unit tests added.

User-visible changes from issues
[#9](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/issues/9) (authentication hardening) and
[#10](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/issues/10) (network/SSRF hardening).

### Added
- **Baseline-promotion policy and drift guard** (`scripts/e2e_lib/promote_baseline.py`,
  `config/e2e/promotion_policy.yml`) — ADR 0015 (R-12): `--approved-by` is now
  validated against a config-driven allowlist; a byte-level drift check blocks
  promotion when the candidate differs from the existing baseline (Option A);
  `--force` requires `--reason` which is recorded separately in the manifest
  entry as `force_reason`; valid environments are config-driven (read from
  `paths.yml`). 25 new unit tests in `tests/unit/test_promote_baseline.py`.

- **AGENTS.md carve-out audit ledger** (`docs/AGENTS_CARVE_OUT_AUDIT.md`) —
  a recurring checklist that logs every harness-motivated `src/` change
  (ADRs 0005/0006/0007/0008) with its authorising ADR, scope, and behaviour-
  change status, plus a trend/erosion watch and an audit log. Core `src/`
  changes (ADRs 0009/0010/0014) are listed separately as rule-#1-N/A.
  Cross-linked from AGENTS.md hard rule #1. Architecture-review
  recommendation R-08.
- **Mode-aware E2E wrapper** (`scripts/run_e2e_mode.sh`) — selects an execution
  mode (`integration` | `uat` | `ci`) and delegates to the same orchestrator
  (`scripts/e2e_lib/run_source.py`); no mode forks a parallel path. `--env`
  accepts only `sit`/`ait` (no invented `uat`/`prod` environment); exit codes
  are identical across modes. CI defaults Java shell-outs off. Documented in
  `docs/CICD_GUIDE.md` and guarded by `tests/unit/test_mode_parity.py`.
  Architecture-review recommendation R-04b.
- **Config-schema registry** (`docs/CONFIG_SCHEMA_REGISTRY.md`) — a single
  authority listing every config artifact (mapping/rules JSON+CSV, umbrella
  YAML, reconcile YAML, source YAML, `paths.yml`, pipeline YAML) with its
  schema authority and load-time validation status. Cross-linked from
  `docs/architecture.md` and AGENTS.md. Artifacts lacking a fail-fast loader
  are flagged as follow-ups for R-10b. Architecture-review recommendation
  R-10a.
- **Mapping↔baseline version-pinning enforcement**
  (`scripts/e2e_lib/baseline_resolver.py`) — new
  `BaselineResolver.resolve_for_mapping(...)` reads the live
  `mapping_version` from the mapping JSON (via the new `read_mapping_version`
  helper) and resolves the baseline pinned to *that* version, then
  cross-checks the resolved entry and fails fast with an actionable message
  if the pin disagrees — no more silent comparison against a stale
  baseline when a mapping is bumped without refreshing its baseline.
  Covered by `tests/unit/test_e2e_baseline_resolver.py` (matching and
  mismatching cases). Architecture-review recommendation R-10b.
- **DB-compare engines division-of-labour doc**
  (`docs/DB_COMPARE_ENGINES.md`) — a single doc distinguishing Valdo's two
  DB-to-file comparison paths (`compare_db_to_file`, the `db_compare`
  pipeline/CLI/UI engine, vs `db_truth_comparator.reconcile`, the L2b
  SQL-truth gate): purpose, inputs, truth source, when-to-use, and a stated
  position that no convergence is planned. Cross-linked from
  `docs/architecture.md`. Architecture-review recommendation R-09.
- **Per-source trigger / filename naming** (`config/e2e/paths.yml`,
  `scripts/e2e_lib/path_resolver.py`, `scripts/e2e_lib/watch.py`): a source
  overlay (`config/e2e/sources/<SOURCE>.yml`) may now declare its own
  `filename_patterns` block (replaces the global list for that source) and a
  `trigger_file` block overriding `suffix` / `data_file_line`. This generalises
  the harness away from the CA-ESP/SHAW-specific producer conventions so a
  non-Java producer can be onboarded with config only. The global `paths.yml`
  values remain the defaults; existing sources (e.g. SHAW) are unchanged. The
  watcher routes a run to the trigger directory's owning source even when a
  custom pattern captures a producer-specific `source` token. Architecture-review
  recommendation R-07.
- **Delimited multi-record reader strategy** (`src/validators/multi_record_reader.py`): a `DelimitedFieldStrategy`
  and a `field_strategy_for(config)` factory build on the R-06a seam so a
  multi-record umbrella can dispatch by a delimited column (CSV/pipe) instead
  of a fixed-width slice. Selection is config-only: set
  `discriminator.delimiter` plus `column` (a 1-indexed integer or a name
  resolved against `columns`) in the umbrella YAML. The fixed-width path is
  unchanged (byte-identical) and the same `MultiRecordValidator` /
  reconcile path handles both formats with no code change. See ADR 0014.
  Architecture-review recommendation R-06b.
- **Record-reader field-strategy seam**
  (`src/validators/multi_record_reader.py`) — the per-line discriminator
  extraction in the multi-record reader is now isolated behind a
  `RecordFieldStrategy` protocol, with `FixedWidthFieldStrategy` as the
  default. A new optional `field_strategy` parameter on
  `read_multi_record_file` lets a future delimited reader drive the same
  dispatch logic without changing the reader. Pure refactor: with the
  default strategy the row stream is byte-identical to the prior behaviour
  (verified by a SHA-256 equivalence test over synthetic and SHAW fixtures);
  the validator suite passes unchanged. See ADR 0014. Architecture-review
  recommendation R-06a (seam only; the delimited strategy itself is R-06b).

### Changed

- **Pipeline step dispatch is registry-driven** (`src/pipeline/etl_pipeline_runner.py`) — the former `_execute_step` type-switch is replaced by a small step-type → handler registry (`ETLPipelineRunner._step_handlers`). Adding a step type now means registering an entry plus a handler instead of editing a branch chain; the registry keys are the authoritative recognised-type list and an unknown type still raises `ValueError` enumerating them. Pure refactor: all existing step types (`validate`, `validate_multi_record`, `compare`, `db_compare`, `reconcile`) behave identically. Architecture-review recommendation R-11.

- **L2 regeneration gate retired** (ADR 0012, Accepted — Option B) — the
  permanently-skipped `L2_regeneration` `TODO(valdo-gap)` was removed from the
  E2E harness rather than carried indefinitely. The output-truth axis is covered
  by the orchestrator-driven `L2b_sql_truth` gate and regression by
  `L3_baseline_diff`. Dropped the `VALDO_E2E_ENABLE_L2` env var, the
  `L2_regeneration` gate from `config/e2e/sources/*.yml` and the generated
  `config/e2e/pipelines/**`, and the `_steps_l2_regeneration` generator path;
  `FailureSink` valid layers now list the live `L2b` instead of `L2`. A future
  mapping-driven `valdo regenerate` capability (ADR 0012, Option A) is retained
  as backlog item #46. Architecture-review recommendations R-03a (decision) and
  R-03b (implementation).

- **Empty/header-only batch gate semantics** (ADR 0013, Accepted — Option A) —
  the multi-record `header_trailer_count` cross-type rule gained an opt-in
  `allow_empty_batch` flag (default `false`). When set, a legitimately empty
  batch (declared count `0` and `0` counted rows) is valid instead of a count
  mismatch; a **truncated** file (declared count `>0` with `0` rows) and any
  non-zero mismatch still fail. SHAW TRANERT's umbrella opts in. No change to
  non-empty-batch behaviour or to any rule that does not opt in.
  Architecture-review recommendation R-05.

- **Mode-parity guard test** (`tests/unit/test_mode_parity.py`) — pins the "one
  validation core across surfaces" property: the API router and pipeline runner
  both route through the shared `run_validate_service` /
  `run_multi_record_validate_service`, which delegate to the shared
  `MultiRecordValidator` core (also used directly by the CLI and the harness
  report). Fails fast if a surface drifts onto a parallel path; the single-record
  CLI's direct-validator path is documented and pinned. The parity guarantee is
  described in `docs/architecture.md`. Architecture-review recommendation R-04a.

- **E2E harness now has its own coverage gate** (`scripts/run_harness_coverage.sh`,
  ADR 0011) — `scripts/e2e_lib/` is measured independently of the `src/` gate
  with a ratcheting floor (80%; ~84% at introduction), so a harness coverage
  regression is attributable on its own. Architecture-review recommendation
  R-02-followup.

- **Coverage gate now spans the core engine** (`pytest.ini`, ADR 0011) — the
  `--cov` scope adds `src/validators`, `src/services`, `src/pipeline`, and
  `src/database` to the existing `api`/`commands`/`comparators`/`reports`, so
  the validation/reconciliation engine and pipeline runner are under the
  enforced 80% bar. The threshold is unchanged. Architecture-review
  recommendation R-02.

### Added

- **`TruthSource` backend abstraction** (`src/database/truth_source.py`,
  ADR 0010) — an interface so the L2b SQL-truth reconciliation can target a
  database behind a stable seam, with `OracleTruthSource` as the first adapter.
  The L2b orchestrator (`scripts/e2e_lib/run_source.py::_open_l2b_connection`)
  now opens its Oracle connection through `OracleTruthSource` instead of a
  hard-wired `oracledb.connect` call — no behaviour change (same credentials,
  same degrade-to-infra-error). The L2b comparator
  (`scripts/e2e_lib/db_truth_comparator.py::reconcile`) gained an optional
  `dialect: SqlDialect` parameter (defaults to Oracle; behaviour-neutral) as the
  documented seam for a future non-Oracle truth source; ADR 0010 now carries the
  Oracle-dialect coupling inventory. Architecture-review recommendation R-01
  (slices R-01a + R-01b + R-01c).

- **LDAPS-based interactive login** (config-driven `auth.ldap` in
  `config/ui.yml`, signed Starlette session cookie). Authenticates against
  `ldaps://ldap-wil.bank.internal:636` using `search_then_bind` strategy.
  Group→role mapping via `memberOf`. Opt-in via `auth.enabled: true`.
  `X-API-Key` continues to work for programmatic callers; session takes
  precedence when both are present. Audit events: `ldap_login_success`,
  `ldap_login_failure`, `ldap_logout`.

- **`X-API-Key` header is now mandatory** for every protected endpoint. The
  authentication middleware fails closed: requests without a valid key
  receive HTTP `401 Unauthorized`. Only `GET /api/v1/system/health` remains
  public.
- **`security.trusted_proxies`** config key in `config/ui.yml`. List the
  IPs/CIDR ranges of every reverse proxy in front of Valdo. Required when
  `security.trust_proxy: true`. Used by the IP-whitelist middleware to
  resolve the real client IP from `X-Forwarded-For` without trusting
  attacker-controlled values.
- **`security.callback_allowlist`** config key in `config/ui.yml`. Optional
  hostname allowlist for webhook `callback_url` values. When non-empty,
  only callback URLs whose hostname matches an entry exactly are accepted.
- **`src/api/security/url_validator.py`** module providing reusable SSRF
  validation (scheme allowlist + IP-range blocks) for any future endpoint
  that accepts user-supplied URLs.

### Changed

- **`X-Forwarded-For` parsing is now right-to-left.** With
  `trust_proxy: true`, the IP-whitelist middleware walks the header from
  right to left, skipping addresses present in `trusted_proxies`, and uses
  the first untrusted address as the client IP. If `trusted_proxies` is
  empty (or every XFF address is trusted), the middleware falls back to the
  direct peer address. The previous "first IP wins" behaviour was
  spoofable by any client able to set its own `X-Forwarded-For` header.
- **Global exception handler returns an opaque `error_id`** instead of
  `str(exc)`. Unhandled exceptions now produce
  `{"detail": "Internal server error", "error_id": "<uuid>"}`. The full
  stack trace is logged server-side keyed by the same `error_id`, so
  operators can correlate user-reported failures with log entries without
  leaking internal paths, SQL fragments, or secrets to clients.

### Removed

- **Referer-based authentication bypass for the bundled UI.** The
  middleware no longer treats requests with a same-origin `Referer:`
  header as authenticated. The bundled UI now attaches `X-API-Key`
  directly (the API Tester tab does this automatically). Custom
  front-ends, bookmarklets, or proxies that previously relied on the
  Referer bypass must be updated to send `X-API-Key`.
- **Empty-`API_KEYS` admin fallback.** Previously, when `API_KEYS` was
  unset or empty, the API operated in an implicit "open mode" that
  effectively granted admin access to anyone who could reach the port.
  This has been removed. With `API_KEYS` unset/empty, every protected
  endpoint returns HTTP `503 Service Unavailable` with the body
  `{"detail": "Server is not configured with API keys."}`. `API_KEYS`
  must be set in `.env` or injected from a secrets manager before
  starting the API server.

### Fixed

- **Path-traversal on 12 file-upload sites + `download_template`
  containment.** Every endpoint that accepts an uploaded filename now
  resolves the path with `Path.resolve()` and verifies the result is
  contained within the configured upload root before any read/write/move.
  Crafted filenames containing `..`, absolute paths, or symlinks that
  escape the upload root are rejected with HTTP `400 Bad Request`. The
  `download_template` endpoint applies the same containment check before
  streaming the file.
- **TLS private key permissions.** On POSIX systems, generated and
  fetched TLS private keys are now written with mode `0o600` (owner
  read/write only). Previously the keys inherited the umask, which on
  some deployments left them world-readable. No-op on Windows where the
  permission model differs.
