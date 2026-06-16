# Changelog

All notable user-visible changes to Valdo are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Removed
- chore(repo): remove pilot_database_validations/ prototype per ADR 0020 — kill-and-defer, no DB-to-DB capability lost (S8.5-2, #394)

### Fixed
- fix(cli): valdo compare returns non-zero exit code on differences (S8-1, #392)
- fix(parsers): FormatDetector routes .csv to CSV parser (comma) and .tsv to TSV (tab), not PipeDelimitedParser (S8-2, #393)

### Added
- feat(mcp): jti-based per-token revocation blocklist + POST /api/v2/mcp/revoke + 24h grace (S9-4, #389)
- feat(mcp): per-token (30/min) + per-IP (60/min) rate limiting on /mcp/ with 429 + Retry-After (S9-3, #388)
- feat(mcp): /mcp/health endpoint exercises handshake + resources + tool count for LB probes (S9-2, #390)
- feat(deploy): nginx TLS reverse-proxy config + production deployment runbook; FastAPI honors X-Forwarded-* (S9-1, #387)
- ci(templates): drift-check workflow for templates/etl/*.yml (S8-5, #384)
- docs(adr): 0018 JSON parser design (S8-3, #377)
- docs(adr): 0020 DB-to-DB disposition — kill-and-defer pilot_database_validations/ (S8-4, #379)
- feat(templates): generic DB-to-file reconciliation template + sample (S7-1, #375)
- feat(mcp): templates://etl/* resources (S7-2, #380)
- feat(mcp): formats://supported resource enumerates Valdo formats (S7-3, #381)
- feat(mcp): compare_two_files tool for ad-hoc file diff (S7-4, #382)
- feat(mcp): pick_etl_shape prompt guides BA to right template (S7-5, #383)

### Changed
- MCP run registry persisted to `APP_MCP_RUN_REGISTRY` so
  `validate_file` / `get_run_status` / `get_violations` survive a
  FastAPI restart (S6-1, #386). EF-S4's process-local dict is now an
  in-memory fallback used only when the database backend is
  unreachable at startup — the server never refuses to boot. New
  Alembic migration `0004_app_mcp_run_registry` adds the table with
  composite `(source, file_path, status)` and `status` indexes. See
  `docs/MCP_SERVER.md` for the operational reference.
- Manual TRANERT VALID fixture restructured for BA-spec compliance: now
  spans 3 accounts at 2 different banks (BK 040 x2 + BK 041 x1), with
  composite 18-char LN-NUM-ERT (blank + BK + BR + CUS + LN), per-account
  BK-NUM-ERT, and CIF-REF-NUM-CUS countdown (999 -> 998) for account 1's
  primary + secondary customers. Detail row count: 15 (NEW1=3, CUS=4,
  ORI=2, COD=2, CBRS=2, REC=2). EXPECTED_*_TBL seed updated to match.

### Added
- `docs/MCP_CLIENTS.md` plus per-client setup guides under
  `docs/mcp-clients/vscode/` and `docs/mcp-clients/gitlab-duo/`. BAs can
  now drop a version-control-friendly `.vscode/mcp.json` into any
  workspace, mint a token with `valdo mcp-login`, and invoke the 9
  Valdo MCP tools (`list_sources` etc.) directly from VSCode Copilot
  Chat. The GitLab Duo guide documents both the emerging native MCP
  registration path (Path A) and the reliable Custom-Tool fallback
  (Path B) that proxies to Valdo's `/api/v2/onboarding/*` REST surface;
  an optional response-shaping relay (`mcp_relay_for_duo.py`) renders
  markdown tables for nicer Duo Chat output. Cross-linked from
  `docs/MCP_SERVER.md` and the README. New TEST_PLAN.md Scenario 7
  covers the VSCode end-to-end smoke test (S6-5, #385).
- `templates/etl/fixed_width_single_record.yml` — BA-facing fixed-width
  single-record validation template, plus a worked sample under
  `templates/etl/fixed_width_single_record_sample/` (deterministic
  10-row × 80-char `input.txt`, hand-curated `mapping.json` mirroring
  the SHAW TRANERT batch-header field-spec shape, `expected_report.json`
  pinning the documented violations contract, and `build_sample.py` as
  the deterministic generator) plus a one-page
  `templates/etl/fixed_width_single_record_README.md`. The template
  validates through `src.pipeline.etl_config.SourceConfig.model_validate`
  and the sample drives the strict fixed-width validator to produce
  exactly three primary violations: `FW_FMT_001` on row 8 (non-numeric
  BALANCE), `FW_VAL_001` on row 9 (ACCT_STATUS not in `['AC','CL','SU']`),
  and `FW_LEN_001` on row 10 (75 chars vs. expected 80). The S6-4
  decision tree's fixed-width single-record row now links to the real
  template instead of a "coming soon" placeholder. New regression
  tests extend `tests/unit/test_etl_templates.py`:
  `test_fw_single_template_validates` and
  `test_fw_single_sample_finds_3_violations` (S6-3, #374).
- `templates/etl/csv_file_comparison.yml` — BA-facing CSV-to-CSV
  reconciliation template, plus a worked sample under
  `templates/etl/csv_file_comparison_sample/` (left.csv, right.csv,
  mapping.json, expected_report.json) and a one-page
  `templates/etl/csv_file_comparison_README.md`. The template validates
  through `src.pipeline.etl_config.SourceConfig.model_validate` and the
  sample exercises `src.comparators.file_comparator.FileComparator` to
  produce the documented per-field diff. The S6-4 decision tree's CSV
  row now links to the real template instead of a "coming soon"
  placeholder. New regression test:
  `tests/unit/test_etl_templates.py` (S6-2, #373).
- `docs/etl/CHOOSE_YOUR_SHAPE.md` — BA/PO decision tree mapping data shape
  (fixed-width single, fixed-width multi-record, CSV, pipe, DB-to-file, plus
  planned JSON/XML/DB-to-DB) to the matching Valdo template or worked
  example, linked from the README under a new "For BAs / POs" section
  (S6-4, #376).
- Manual test harness under `tests/manual/` for SHAW end-to-end validation:
  Oracle DDL (`sql/shaw_setup.sql`), three TRANERT fixed-width fixtures
  generated by `scripts/build_shaw_test_files.py`, and a 5-scenario
  `TEST_PLAN.md` covering onboard-source drift, L1 multi-record dispatch,
  Source Editor UI flow, MCP 9-tool smoke test, and optional L2b
  reconciliation.
- Comprehensive seed for the SHAW manual test harness: the fixture
  generator now also emits matching SQL seed files for both Oracle
  (`sql/shaw_setup.sql`) and SQLite (`sql/shaw_setup_sqlite.sql`) with
  one EXPECTED_*_TBL row per detail row in the VALID fixture (5 NEW1 +
  4 CUS + 3 ORI + 2 COD + 2 CBRS + 2 REC + 1 BATCH_HEADER) plus 5
  synthetic rows per SHAW_* staging table. New cross-backend runner
  `tests/manual/seed_db.py` (`--backend sqlite|oracle`, `--drop-first`)
  applies the seed and prints a row-count summary; new TEST_PLAN.md
  Section 0 walks through the setup.
- EF-S8 (Sprint 5 — PROGRAM COMPLETION): End-to-end agentic walkthrough
  test in `tests/integration/test_mcp_agentic_walkthrough.py`. Drives
  the MCP server through a 10-step BA onboarding flow (auth → prompt
  fetch → workbook upload → dry-run → diff inspection → validate →
  status poll → diagnose → violations → clean disconnect) using a
  mock MCP client. Mocks `run_validate_service` + `ldap_authenticate`
  so no real services are touched. Closes the 5-sprint Valdo workbook-
  driven onboarding + agentic MCP surface program (Sprint 1: Pydantic
  defaults; Sprint 2: workbook + CLI; Sprint 3: drift fix + Move 4
  starts; Sprint 4: Move 4 closes + MCP action/onboarding tools + UI
  scaffold; Sprint 5: UI tree+drift+commit + MCP prompts + auth bridge
  + this walkthrough).
- EF-S7 (Sprint 5): MCP auth bridge replaces dev-mode placeholder.
  HTTP transport accepts `X-API-Key` header OR session cookie (same
  backends as the existing Valdo API). Stdio transport reads
  `~/.valdo/mcp-token` (12-hour HMAC-SHA256 signed). New CLI
  `valdo mcp-login [--server URL] [--ttl-hours N]` prompts for LDAPS
  credentials and stores the token at 0600 perms. New endpoint
  `POST /api/v2/mcp/login`. Dev mode (`VALDO_MCP_AUTH=dev`)
  preserved for local testing.
- EF-S6 (Sprint 5): three MCP prompts —
  `onboard_new_source(workbook_path, source_code)`,
  `diagnose_validation_failure(run_id)`,
  `infer_field_map(sample_file_path, file_type)`.
  Each is a templated workflow that surfaces in MCP clients (Claude
  Desktop, mcp-cli) and guides the agent through the correct tool-call
  sequence. Total MCP surface: 9 tools + 4 resources + 3 prompts.
- EE-S3 (Sprint 5): UI Source Editor tab now has commit buttons —
  "Download ZIP" streams all emitted artefacts to the BA's browser,
  and "Open MR" (gated by `VALDO_UI_ENABLE_OPEN_MR=1`) creates a branch,
  commits artefacts, pushes, and opens a PR via the `gh` CLI. New
  endpoints `POST /api/v2/onboarding/download-zip`,
  `POST /api/v2/onboarding/open-mr`, and
  `GET /api/v2/onboarding/artefact-content` (which feeds the EE-S2
  unified-diff renderer). Closes the BA browser onboarding flow.
- EE-S2 (Sprint 5): UI Source Editor tab now renders the workbook-upload
  preview as a collapsible tree of artefacts grouped by kind
  (source_yaml / mapping_json / rules_json / reconciliation_yaml / sql)
  with per-artefact `new | changed | unchanged` drift status badges.
  `POST /api/v2/onboarding/preview` response now includes `drift`
  aggregate counts + per-artefact `status` field (breaking change vs
  EE-S1 shape; nobody on this depends yet). Drift comparison logic
  refactored from `src/commands/onboard_source.py` into shared
  `src/onboarding/drift.py`. Click an artefact to view its content;
  `changed` artefacts also show a unified diff vs committed.
  New `GET /api/v2/onboarding/committed-artefact?path=` endpoint backs
  the diff modal with directory-traversal-safe whitelisted reads.
- EE-S1 (Sprint 4): UI "Source Editor" tab scaffold in
  `src/reports/static/ui.html`. New API endpoints
  `GET /api/v2/onboarding/sources` + `POST /api/v2/onboarding/preview`
  (multipart workbook upload) thin-wrap the EC-S6 dry-run service layer.
  Tab displays committed source list + workbook-upload form; preview
  response rendered as raw JSON (live tree + drift detection ship in
  EE-S2). Auth posture matches the existing UI.
- EF-S5 (Sprint 4): three MCP onboarding tools —
  `upload_workbook_as_spec`, `onboard_source_dry_run`,
  `infer_mapping_from_sample`. Thin adapters over the existing
  `valdo onboard-source` and `valdo infer-mapping` service layers.
  `upload_workbook_as_spec` copies a validated workbook to
  `~/.valdo/mcp_sandbox/<source>/onboarding.xlsx`. `onboard_source_dry_run`
  returns the planned write list as a structured dict (no disk side-effects).
  `infer_mapping_from_sample` produces a draft field mapping from a CSV
  or fixed-width sample. Total MCP tool count: 9 (3 read + 3 action + 3 onboard).
- EF-S4 (Sprint 4): three MCP action tools — `validate_file`,
  `get_run_status`, `get_violations`. All thin adapters over the
  existing Valdo validation + run-history service layer.
  `validate_file` returns immediately with a `run_id`; status/violations
  poll-able via the other two. `get_violations` supports
  `severity` filter and pagination (default 50, max 200).
  Total MCP tool count: 6 (3 read-only from EF-S2 + 3 action from EF-S4).
- ED-S4 (Sprint 4 / Move 4 closes): per-field `reconciliation` boolean
  column on `*_Mapping` sheets controls which mapping fields are
  included in the reconciliation YAML's `record_types.<name>.fields[]`
  and the SQL emitter's SELECT column list. Closes the field-curation
  and SQL byte-equivalence carve-outs from Sprint 3. SHAW reconciliation
  YAML now byte-equivalent to committed; SHAW TRANERT batch_header SQL
  byte-equivalent to committed (modulo comment headers). Supporting
  override columns (`Reconciliation Order`, `Reconciliation Column`,
  `Reconciliation Predicate`, `Reconciliation SQL Expression`) capture
  BA-curated divergences from emitter defaults so the workbook
  round-trips the committed artefacts; an optional `cardinality` cell
  on `Reconciliation_<FILETYPE>` decouples SQL-rowset cardinality from
  the MultiRecord sheet's file-rowset cardinality. SHAW workbook +
  reader + models + emitters extended. `valdo onboard-source --check`
  against committed state now reports 65/66 (R028B carve-out is the
  sole remaining drift).
- ED-S3 (Sprint 4 / Move 4): SQL emitter supports
  `expected_table_strategy: {view, ctas, ctas_with_drop}` per source.
  When `ctas_with_drop`, emitter wraps the SELECT in `DROP TABLE…PURGE`
  + `CREATE TABLE…AS SELECT…` matching the committed
  `app_int.EXPECTED_<TOKEN>_TBL` pattern for restricted Oracle schemas.
  Default `view` preserves ED-S2 behaviour. SHAW workbook + reader +
  SourceInfo extended.
- ED-S2 (Sprint 3 / Move 4): `src/onboarding/emitters/sql_emitter.py`
  emits Oracle-dialect `expected_*.sql` files from mapping artefacts
  + workbook `Reconciliation_<FILETYPE>.staging_table`/`predicate`.
  DASH-quoted aliases (`AS "LN-NUM-ERT"`). Activated when
  reconciliation row's `expected_sql_override` is blank (uses ED-S1's
  `expected_sql: auto` marker). Hand-authored overrides preserved.
  CTAS-vs-view fallback (ED-S3) and full byte-equivalence polish
  (ED-S4) deferred to Sprint 4. Wired into `valdo onboard-source` CLI.
  Closes Sprint 3.
- ED-S1 (Sprint 3 / Move 4): `src/onboarding/emitters/reconciliation_emitter.py`
  emits reconciliation YAML artefacts from `Reconciliation_<FILETYPE>`
  workbook sheets to `config/e2e/sources/<NAME>/reconciliation/<filetype>.yml`.
  `expected_sql: auto` marker convention reserved for ED-S2 SQL auto-derivation.
  Wired into `valdo onboard-source` CLI.
- EC-S11 (Sprint 3): GitHub Actions workflow
  `.github/workflows/workbook-drift-check.yml` runs
  `valdo onboard-source <workbook> --check` for every committed
  `templates/*_onboarding.xlsx` on every PR + push. Drift lines
  matching an allowlist (`.github/workflows/workbook-drift-allowlist.txt`)
  are tolerated; the SHAW R028B engine-native cross-row rule is the
  sole initial entry per EC-S9. Local-equivalent helper at
  `scripts/check_workbook_drift.sh`.
- EC-S10 (Sprint 3 drift-fix): `TemplateConverter` and
  `BARulesTemplateConverter` accept a `frozen_timestamp` option that
  replaces `datetime.utcnow()` calls with a deterministic value.
  Plumbed through `MappingEmitter`, `RulesEmitter`, and the
  `valdo onboard-source --frozen-timestamp` CLI flag.
  `valdo onboard-source --check` now auto-extracts the committed
  timestamps so `--check` against committed state exits 0 (no
  `modulo timestamps` qualifier). EC-S4 / EC-S5 emitter tests no
  longer need the `_drop_metadata` strip-before-compare hack.

### Fixed
- `valdo validate --multi-record` summary glyphs: header verb now reflects
  the reported `Error Count` (zero errors → `✓ passed`, non-zero → `✗ failed`)
  instead of the misleading per-type `result["valid"]` aggregate; per-type
  rows use a neutral `•` bullet by default and only render `✗` when an
  `expect`-cardinality cross-type check fired for that type. The SHAW
  `tranert_shaw_test_clean_no_violations.txt` fixture is also regenerated
  to a 7-row "1 per declared record type" baseline so the umbrella's
  `at_least_one` cardinality is satisfied and the file lives up to its name.

### Changed
- EC-S9 (Sprint 3 drift-fix): `templates/SHAW_onboarding.xlsx`
  regenerated from committed state via `scripts/build_shaw_onboarding_workbook.py`,
  making the workbook a 1:1 reverse-engineering of the currently committed
  `config/mappings/`, `config/rules/`, `config/e2e/sources/SHAW.yml`, and
  reconciliation YAMLs. `valdo onboard-source --check` against committed
  SHAW state now exits 0 (modulo `metadata.created_date` timestamps —
  EC-S10 lands deterministic timestamps to close that gap). The build
  script gains `_reverse_engineer_mapping_rows_from_json` and
  `_reverse_engineer_rules_rows_from_json` helpers that read committed
  artefacts directly, sidestepping a UTF-8 BOM bug in
  `mappings/csv/shaw_tranert/SHAW_TRANERT_CUS_mapping.csv` that had
  silently blanked the `Field Name` column on every row of the
  TRANERT_CUS mapping sheet. Drift count: 29 of 65 → 64 of 65 (the
  sole remaining drift is the hand-authored `cross_row:sequential`
  R028B countdown rule on `SHAW_TRANERT_CUS_rules.json`, which has
  the engine-native `sequence_field`/`start`/`step` shape that BA
  workbook columns cannot express; this is a documented carve-out
  asserted by the `test_check_mode_clean_against_committed_state`
  regression test, renamed from `test_check_mode_clean_modulo_timestamps`
  by EC-S10 once timestamp drift was eliminated). 34 TODO-stub JSONs
  for SHAW input files and
  CDSTRANS_*/CONTACT_*/P327 outputs are now committed alongside the
  workbook so the artefact COUNT matches 65 of 65.
- EC-S7 (Sprint 3 drift-fix): EC-S4 mapping emitter now populates
  umbrella YAML `record_types.<name>.rules` paths using a shared
  helper `derive_rules_artefact_path()` in
  `src/onboarding/emitters/__init__.py`. EC-S5 rules emitter refactored
  to call the same helper -- both emitters now agree on the canonical
  rules artefact path. Resolves the `rules: ""` drift category in
  `valdo onboard-source --check` against committed SHAW state.
- EA-S3: Stripped ~146 lines of redundant strict/tolerance/thresholds
  boilerplate from `config/e2e/sources/SHAW.yml` (-116 lines) and
  `config/e2e/sources/SRC_A.yml` (-30 lines). The deleted values are now
  restored from the Pydantic defaults added in EA-S1; the generated
  pipeline YAML carries `# default:` comments per EA-S2 for SRE
  traceability. The stale tolerance-defaults header comment in SHAW.yml is
  also removed. SRC_A's non-default `tolerance.ignore_fields` overrides
  are preserved verbatim (surgical strip). `SRC_A.sit.golden.yaml`
  regenerated to reflect the new `# default:` comments. EB-S2 will add
  the CI guardrail preventing reintroduction of the stripped keys.
- EA-S2: `scripts/generate_pipeline_yaml.py` now emits
  `# default: <field>=<value>` comments next to every implicitly-defaulted
  field in `output_files[]` and `input_files[]` entries. SREs can see the
  effective configuration without consulting the Pydantic source.
  Generator wires `SourceConfig.model_validate()` to track defaulted vs
  explicit fields via `model_fields_set`; default values are read from the
  Pydantic model's field defaults so the generator and `etl_config.py` can
  never drift apart. Comments are idempotent across regeneration. The
  generated pipeline YAML now carries informational `input_files:` /
  `output_files:` manifest sections (silently ignored by
  `PipelineDefinition.model_validate`'s default `extra='ignore'`).
  `config/e2e/sources/SRC_A.yml` migrated to drop the legacy
  `multi_record:` keys (EB-S1 inline migration follow-up); golden fixture
  regenerated to reflect the new manifest blocks.
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
- EC-S8 (Sprint 3 drift-fix): New `CrossTypeRules_<FILETYPE>` workbook
  sheet type carries operator-authored cross-record-type rules
  (`header_trailer_count`, etc.) for multi-record output files. EC-S2
  reader parses into `CrossTypeRulesSheet`; EC-S4 mapping emitter
  populates umbrella YAML's `cross_type_rules:` array from workbook
  rows. `templates/SHAW_onboarding.xlsx` extended with the SHAW TRANERT
  cross-type rule from committed state. Resolves the `cross_type_rules`
  drift category in `valdo onboard-source --check`.
- EC-S6 (Sprint 2): `valdo onboard-source <workbook.xlsx>` CLI command
  that orchestrates EC-S3 / EC-S4 / EC-S5 emitters and writes the full
  artefact tree to disk. Modes: normal (write), `--dry-run` (preview),
  `--check` (drift detection for CI). SHAW workbook regenerates all
  65 committed artefacts (1 source YAML + 36 mappings + 28 rules)
  structurally equivalent to current state. Sprint 2 complete — BAs
  can now ship a new source from one Excel workbook.
- EC-S5 (Sprint 2): `src/onboarding/emitters/rules_emitter.py` emits
  per-output-file flat rules JSONs and per-record-type rules JSONs from
  an `OnboardingWorkbook`. Delegates to the existing
  `src/config/ba_rules_template_converter.py` (no duplicated rules
  logic). Returns `EmittedRulesArtefact` instances; does not write to
  disk (EC-S6 CLI handles persistence). Layout-tag de-duplication
  (shared with EC-S4) collapses record-types sharing a mapping layout
  to a single rules artefact. SHAW workbook round-trips to artefacts
  structurally equivalent to committed `config/rules/SHAW_*.json`.
- EC-S4 (Sprint 2): `src/onboarding/emitters/mapping_emitter.py`
  emits per-file flat mapping JSONs and per-multi-record umbrella YAMLs
  + per-record-type JSONs from an `OnboardingWorkbook`. Delegates field-
  mapping conversion to the existing `src/config/template_converter.py`
  (no duplicated converter logic). Returns `EmittedMappingArtefact`
  instances; does not write to disk (EC-S6 CLI handles persistence).
  SHAW workbook round-trips to artefacts structurally equivalent to
  the committed `config/mappings/SHAW_*.json|yaml`.
- EC-S3 (Sprint 2): `src/onboarding/emitters/source_yaml_emitter.py`
  converts an `OnboardingWorkbook` (from EC-S2) into a source-YAML text
  semantically equivalent to today's committed
  `config/e2e/sources/SHAW.yml`. Honours every Sprint 1 contract:
  no EA-S1 default boilerplate, EA-S3 surgical-strip preserved for
  non-default `tolerance`/`thresholds` overrides, EB-S1 multi-record
  convention (mapping path extension drives `is_multi_record`), EB-S2
  guardrail-clean. Emitted YAML validates through `SourceConfig`.
- EC-S2 (Sprint 2): `src/onboarding/workbook_reader.py` parses the
  onboarding workbook into typed `OnboardingWorkbook` dataclass
  (`src/onboarding/models.py`). DASH-style field names preserved
  verbatim; pipe-separated cells parsed; blank optional cells return
  `None`; bool cells accept `true/false/yes/no/1/0` case-insensitively.
  Delegates schema validation to EC-S1. EC-S3/S4/S5 emitters consume
  `OnboardingWorkbook` rather than raw openpyxl rows.
- EC-S1 (Sprint 2): Canonical source-onboarding Excel workbook template
  at `templates/source_onboarding_template.xlsx` + worked-example
  `templates/SHAW_onboarding.xlsx`. Schema validator at
  `src/onboarding/workbook_schema.py` enforces required sheets and
  columns with cell-addressable error messages. Documentation at
  `templates/source_onboarding_template_README.md`. EC-S2 reader, EC-S3
  source YAML emitter, EC-S4 mapping emitter, EC-S5 rules emitter, and
  EC-S6 `valdo onboard-source` CLI all consume this template.
- EB-S2: CI guardrail (`tests/unit/test_source_yaml_guardrails.py`)
  rejects reintroduction of legacy multi-record keys (`multi_record`,
  `discriminator_field`) and default-equal boilerplate
  (`strict_fixed_width: true`, `strict_level: all`, all-zero
  `tolerance` / `thresholds` blocks) in any `config/e2e/sources/*.yml`.
  Each violation gets an actionable, citation-linked error message.
- EF-S2: Three read-only MCP tools — `list_sources`, `get_source_spec`,
  `list_recent_runs`. All wrap the existing service layer (no duplicated
  business logic). `get_source_spec` returns the bundle of existing
  artefacts (source YAML + mapping JSONs/YAMLs + rules JSONs +
  reconciliation YAML + expected SQL files). `list_recent_runs`
  degrades gracefully when run history is offline. Auth: same dev-mode
  gate from EF-S1.
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
