# Config-Schema Registry

**Status:** Living document. **Recommendation:** R-10a (arch-review 2026-06-03,
dimension 4). **Companion:** R-10b (#34) adds the missing fail-fast loaders and a
mapping↔baseline version-pinning test flagged here.

## Purpose

Valdo and its E2E harness are driven by several distinct configuration
artifacts. Some are validated on load with a single fail-fast exception type;
others are parsed leniently. This document is the **single authority** that, for
every config artifact, records:

1. **What** it is and where it lives.
2. **Schema authority** — the module/class that defines its shape.
3. **Load-validated?** — whether loading rejects malformed input fail-fast, and
   *where* that validation happens.

It is descriptive of the current state. Gaps (artifacts lacking a fail-fast
loader, or carrying a `schema_version` no loader checks) are flagged in
[§3 Follow-ups](#3-follow-ups) and feed R-10b (#34).

## 1. Registry

| Config artifact | Example path | Schema authority (module / class) | Load-validated? (where) |
|---|---|---|---|
| **Mapping JSON** | `config/mappings/SHAW_*.json` | `src/config/models.py` → `MappingConfig` (pydantic) | **Yes, when loaded via `MappingConfig.from_file` / `from_dict` / `from_json`** — pydantic validation + non-empty `fields`. **Partial/No** via `src/config/loader.py::ConfigLoader.load_mapping`, which does a raw `json.load` with **no schema validation** (returns a bare dict). |
| **Rules JSON** | `config/rules/SHAW_*.json` | `src/config/models.py` → `RulesConfig` / `RuleConfig` (pydantic) | **Yes, when loaded via `RulesConfig.from_file` / `from_dict` / `from_json`** — pydantic validation; `RuleConfig` allows extra operator-specific keys by design (`extra="allow"`). |
| **Rules CSV / XLSX** | BA-authored rules template | `src/config/ba_rules_template_converter.py` → `BARulesTemplateConverter`; `src/config/rules_template_converter.py` → `RulesTemplateConverter` | **Convert-time only, not a load-validated runtime schema.** The CSV/XLSX is *lowered* into rules JSON by the converter (`from_csv`); the produced JSON is then validated by `RulesConfig`. There is no fail-fast row-shape schema for the CSV itself beyond the converter's own checks. |
| **Umbrella YAML** (multi-record) | `config/mappings/SHAW_TRANERT.yaml`, `SHAW_ATOCTRAN.yaml` | `src/config/multi_record_config.py` → `MultiRecordConfig` / `RecordTypeConfig` / `DiscriminatorConfig` / `CrossTypeRule` (pydantic) | **Yes (structural).** Loaded as `MultiRecordConfig(**yaml.safe_load(...))` in `src/commands/multi_record_command.py`, `src/services/multi_record_validate_service.py`, and the API router. Pydantic validates field types/positions. **Caveats:** no `schema_version` field, and pydantic's default `extra="ignore"` means **unknown keys are silently dropped** (no typo defence). |
| **Reconcile YAML** (L2b SQL-truth) | `config/e2e/sources/SHAW/reconciliation/tranert.yml` | `scripts/e2e_lib/reconciliation_spec.py` → `ReconciliationSpec` (frozen dataclasses) | **Yes — strongest fail-fast in the repo.** `load_spec` validates `schema_version == 1`, **rejects unknown keys** at every level, checks enum values (`Cardinality`), cardinality, key lists, and `.sql` suffixes. Single error type `ReconciliationSpecError`. |
| **Promotion policy YAML** | `config/e2e/promotion_policy.yml` | `scripts/e2e_lib/promote_baseline.py` → `load_promotion_policy` | **Yes (structural).** Validates: top-level is a mapping, `approvers` is a non-empty list. Loaded at promotion time; unknown keys are silently ignored. Schema authority: ADR 0015. |
| **Source YAML** (per-source overlay) | `config/e2e/sources/SHAW.yml` | `scripts/e2e_lib/path_resolver.py` → `PathResolver.source_config` / `_compile_source_patterns` / `trigger_config` | **Partial.** Validates: top-level is a mapping, `source` matches the filename, and that the overlay does **not** redefine env-forbidden keys (`_SOURCE_OVERRIDE_FORBIDDEN_KEYS`). **R-07 optional overrides** (validated when present): `filename_patterns` (a non-empty list compiled with the same contract as the global block — unique names, valid regex, `source`/`file_type` named groups, valid `direction`; **replaces** the global list for that source) and `trigger_file.{suffix,data_file_line}` (non-empty string suffix; integer `data_file_line` ≥ 1; each key falls back to the global default when absent). **Does NOT validate `schema_version`** (the file declares `schema_version: 1` but no loader checks it) and does not reject other unknown keys. |
| **`paths.yml`** (env-keyed roots + patterns) | `config/e2e/paths.yml` | `scripts/e2e_lib/path_resolver.py` → `PathResolver.from_files` / `_validate_and_compile` | **Partial (strong on its core).** Validates: non-empty `envs`, all required env keys present, only allowed placeholders in templates, `filename_patterns` shape (unique names, valid regex, required named groups `source`/`file_type`, valid `direction`). The global `filename_patterns` and `trigger_file.{suffix,data_file_line}` are the **defaults**; a source overlay may override either (R-07, see Source YAML row). **Does NOT validate `schema_version`** (declared `schema_version: 1`, unchecked). |
| **Pipeline YAML** (ETL gate runner) | `config/e2e/pipelines/<env>/<SOURCE>.pipeline.yaml` | `src/pipeline/etl_config.py` → `PipelineDefinition` / `Gate` / `GateStep` / `SourceDefinition` (pydantic) | **Yes (structural).** Loaded via `PipelineDefinition.model_validate(raw)` in `src/pipeline/etl_pipeline_runner.py`. Generated (not hand-authored) by `scripts/generate_pipeline_yaml.py`. **Caveats:** no `schema_version`; pydantic default `extra="ignore"`. |

### Adjacent / supporting config (not the primary E2E set, listed for completeness)

| Config artifact | Example path | Schema authority | Load-validated? |
|---|---|---|---|
| Suite definition YAML | `config/suites/*.yml` | `src/services/scheduler_service.py` → `SuiteDefinition.model_validate` | Yes (pydantic). |
| Pipeline profile YAML | (runner profiles) | `src/pipeline/runner.py` → `PipelineProfile.model_validate` | Yes (pydantic). |
| GE checkpoint targets/expectations CSV | `src/quality/gx_checkpoint1.py` | `load_targets_csv` / `load_expectations_csv` (`csv.DictReader`) | Convert-time row parsing; no separate runtime schema model. |
| UI / auth config | `config/ui.yml` | `src/api/main.py`, `src/main.py` (`yaml.safe_load`) | Lenient `yaml.safe_load`; documented in `docs/architecture.md`. |

## 2. Validation-strength legend

- **Yes (fail-fast, typo-defended):** rejects unknown keys + checks
  `schema_version`. Today only **Reconcile YAML** meets this bar.
- **Yes (structural):** pydantic/dataclass type validation, but **silently
  ignores unknown keys** and has no `schema_version` check (Umbrella YAML,
  Pipeline YAML, Mapping/Rules JSON via the `*Config` models).
- **Partial:** validates the keys it consumes but ignores `schema_version` and
  unknown keys (Source YAML, `paths.yml`).
- **No / convert-time only:** raw `json.load` / converter lowering with no
  runtime schema model (`ConfigLoader.load_mapping`, Rules CSV/XLSX).

## 3. Follow-ups (feed R-10b / #34)

Artifacts lacking a fully fail-fast loader, flagged for R-10b:

- [ ] **Source YAML** declares `schema_version: 1` but `path_resolver.py` never
      validates it. Add a `schema_version` check + unknown-key rejection.
- [ ] **`paths.yml`** declares `schema_version: 1` but
      `_validate_and_compile` never validates it. Add a `schema_version` check.
- [ ] **Umbrella YAML** (`MultiRecordConfig`): no `schema_version`; pydantic
      `extra="ignore"` silently drops typos. Consider `extra="forbid"` and/or a
      `schema_version` field for parity with the reconcile spec.
- [ ] **Pipeline YAML** (`PipelineDefinition`): no `schema_version`; `extra`
      defaults to ignore. Consider tightening, noting it is machine-generated.
- [ ] **Mapping JSON via `ConfigLoader.load_mapping`**: raw `json.load` path
      bypasses `MappingConfig` validation. Route load-time consumers through
      `MappingConfig.from_file`.
- [x] **Mapping↔baseline version pinning** (R-10 / `baseline_resolver.py`):
      **Done in R-10b (#34).** `BaselineResolver.resolve_for_mapping(...)` now
      reads the live `mapping_version` from the mapping JSON (via the new
      `read_mapping_version` helper) and resolves the baseline pinned to that
      version, then cross-checks the resolved entry and fails fast with an
      actionable `BaselineResolverError` on mismatch — no more silent
      comparison against a stale baseline. Covered (matching + mismatching) by
      `tests/unit/test_e2e_baseline_resolver.py`.

> Out of scope for R-10a: *implementing* the remaining unchecked validators
> above (the `schema_version` / unknown-key tightenings). R-10b (#34) delivers
> only the mapping↔baseline pinning item; the rest remain open follow-ups.
