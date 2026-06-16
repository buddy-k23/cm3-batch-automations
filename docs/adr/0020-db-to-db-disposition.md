# ADR 0020 — DB-to-DB disposition: `pilot_database_validations/`

- **Status:** Accepted
- **Date:** 2026-06-15
- **Sprint:** 8 (S8-4, [#379](https://github.com/buddy-k23/valdo/issues/379))
- **Decision:** **Kill-and-defer**
- **Related:**
  [ADR 0010](0010-truthsource-backend-abstraction.md),
  [ADR 0018](0018-json-parser-design.md),
  `pilot_database_validations/` (sibling sub-project at repo root),
  `src/comparators/file_comparator.py`,
  `src/database/` (Oracle/Postgres/SQLite adapters, extractor, query_executor,
  truth_source, reconciliation, run_history),
  `src/pipeline/etl_config.py` (`SourceDefinition`, `GateStep` with `type="db_compare"`),
  `src/config/template_converter.py` (ADR 0007),
  `prompts/` (AI prompt library for LLM-assisted mapping/rules CSV generation),
  `docs/sprints/SPRINT_8_KICKOFF.md` (EC-S9 flag of this sub-project).

## Context

EC-S9 flagged `pilot_database_validations/` as "absorb or kill — decide before
the next BA-onboarded source." The sub-project sits at the repository root,
out of `src/`, with no entry in the engine's CLI, no FastAPI routes, no
inclusion in `tests/unit/`, and no mention in the Sphinx tree. It was
migrated in from an external `database-validations` worktree (per its
`README.md`) as a candidate "integration surface into Valdo mainline."

The name is misleading. After reading the directory end-to-end, the
prototype is **not** a DB-to-DB comparator and contains **no** runtime
DB-to-DB comparison logic. It is a **template ingestion + rules extraction
+ promotion-gate harness** that converts BA/QA CSV/Excel workbooks into
mapping/rules JSON contracts, runs deterministic e2e self-checks, and emits
promotion evidence. Two of the 13 tools (`e2e_app_int_oracle.py`,
`generate_all_file_contracts.py`) connect to Oracle, but only to **seed
demo tables and read `user_tab_columns` metadata** for contract generation
— neither implements a row-by-row DB-to-DB diff.

The verdict this ADR delivers determines whether ~3.8k LOC of prototype
tooling gets folded into `src/`, rewritten cleanly, or removed. EC-S9's
"before next BA-onboarded source" framing makes this load-bearing: any
absorb work must be justified by a BA request that the existing engine
cannot already satisfy.

## Investigation findings

### What the prototype actually does

13 tools, ~3,815 LOC under `pilot_database_validations/tools/`:

| Tool | LOC | Purpose |
|---|---:|---|
| `template_parser.py` | 787 | CSV/XLSX template → canonical ingest JSON; header alias normalisation; lineage warnings; derivation modes |
| `e2e_runner.py` | 578 | Pipeline: template → canonical → mapping.json + rules.json + conversion-report.json + summary + telemetry; deterministic mode |
| `generate_contracts.py` | 417 | Canonical ingest JSON → mapping.json + rules.json + conversion-report.json |
| `e2e_app_int_oracle.py` | 281 | **One-shot Oracle demo**: creates `APP_DEMO_*` tables, inserts seed rows, dumps a fixed-width file, runs validation, writes summary. Reads `/Users/buddy/.openclaw/workspace/valdo/.env` (hardcoded developer path, lines 11–12) |
| `e2e_oracle_style_demo.py` | 239 | SQLite-based imitation of the Oracle demo — same shape, different backend |
| `rule_promotion.py` | 238 | Rule lifecycle state machine (proposed → active → archived) |
| `wave5_multiworkbook_harness.py` | 231 | Runs `e2e_runner.py` across N workbook fixtures via `subprocess` and aggregates a consolidated report |
| `pilot_orchestrator.py` | 226 | Deterministic 5-round positive/negative/edge scenario runner against an `onboarded-tables.json` index |
| `promotion_gate.py` | 219 | Policy evaluator: pre-gate `SUCCESS/WARN/FAILED` → terminal `PASS/FAIL` decision |
| `rules_extraction.py` | 200 | Transform-logic text → rule JSON (default, nullable, valid-values patterns) |
| `schema_validation.py` | 154 | JSON Schema validator with stdlib fallback (no `jsonschema` dep required) |
| `generate_all_file_contracts.py` | 129 | Loops Oracle `user_tab_columns` to autogenerate per-table contract JSON. Same `.env` hardcode |
| `e2e_app_int_to_wave_artifacts.py` | 116 | Adapter glue |
| `generate-contracts.py` | 17 | Legacy alias / thin wrapper |

Plus 4 JSON schemas (`schemas/mapping.schema.json`, `rules.schema.json`,
`report.schema.json`, `telemetry.schema.json`) and ~30 markdown trial /
governance docs under `docs/`.

**What it does not do:** there is no class, function, or CLI command that
opens two database connections and diffs row sets. No SQL `MINUS` /
`EXCEPT` orchestration. No hashing pipeline against two truth sources. No
`SourceConfig` shape for "DB on one side, DB on the other." The "database"
in the directory name refers to the **target domain** (validating data
that originates in databases) — not to the comparison topology.

### Quality assessment

**Code quality (where it exists):** the code is competent. Modules are
typed with PEP 604 unions, use `dataclasses(frozen=True)` for policy
objects, write JSON deterministically (`sort_keys=True, separators=(",",
":")`), and the cross-tool imports use a defensive
`try: from tools.X / except ModuleNotFoundError: from X` shim
(`e2e_runner.py:24-31`) to run both as a package and as ad-hoc scripts.
Determinism is taken seriously: `_stable_run_id` (`e2e_runner.py:47-49`)
hashes canonical input + version + owner; `DETERMINISTIC_TIMESTAMP =
"1970-01-01T00:00:00+00:00"` is wired through every output for
reproducible test artifacts.

**Red flags found:**

- `pilot_database_validations/tools/generate_all_file_contracts.py:12`
  hardcodes `ENV_FILE = Path("/Users/buddy/.openclaw/workspace/valdo/.env")`
  — a developer's home directory. Violates Valdo architecture principle #5
  ("no hardcoded paths"). Same pattern at
  `e2e_app_int_oracle.py:22-30` (custom `.env` parser instead of
  `pydantic-settings` or `python-dotenv`).
- `pilot_database_validations/tools/wave5_multiworkbook_harness.py:55-60`
  shells out via `subprocess` to invoke `python3 tools/e2e_runner.py`
  as a child process. The harness is a thin process-spawner where the
  same effect could be a direct `run_pipeline()` call (already exposed
  by `e2e_runner.py`). Violates Valdo architecture principle #2
  (`shell=False` is correct here, but spawning a process for a function
  call is engineering anti-pattern).
- `pilot_database_validations/tools/template_parser.py:29-42` reinvents
  header alias normalisation that **already exists** in
  `src/config/template_converter.py` (the BA-facing canonical template
  ingestion path, per ADR 0007). Two normalisation maps in two places
  will drift.
- `pilot_database_validations/tools/rules_extraction.py:28-35` regex-extracts
  `default` values from transform logic — duplicating the `default_value`
  column extraction already shipped in Valdo's mapping converter (per
  CLAUDE.md: "converter extracts defaults from transformation text").
- `pilot_database_validations/tools/e2e_app_int_oracle.py:63-79` uses
  raw f-string SQL (`f"DROP TABLE {t} PURGE"`, `f"CREATE TABLE
  {TABLE_ACCOUNTS} (...)"`). Acceptable for a demo seeder against
  developer-owned table names; would be a SQL-injection rewrite blocker
  if absorbed into a multi-tenant engine path.

**Test coverage:** 7 test files, 860 LOC total. All **non-trivial**:
`unittest.TestCase` subclasses with real fixture-driven assertions, not
stubs.

| Test file | LOC | What it covers |
|---|---:|---|
| `test_e2e_runner.py` | 260 | Pipeline end-to-end determinism, artifact generation |
| `test_template_parser.py` | 212 | CSV + XLSX parse, alias resolution, validation errors |
| `test_rule_promotion.py` | 100 | State-machine transitions |
| `test_lineage_hardening.py` | 90 | Source-lineage warnings, confidence thresholds |
| `test_rules_extraction.py` | 73 | Transform-logic → rule conversion |
| `test_promotion_gates.py` | 67 | Policy evaluation per pre-gate input |
| `test_schema_validation.py` | 58 | Built-in subset validator parity with `jsonschema` |

Tests are **not** integrated into Valdo's `pytest tests/unit/` run; they
live in `pilot_database_validations/tests/` and would not be picked up by
the coverage gate.

**Engine alignment:** poor. The prototype implements a **parallel
template ingestion + rules extraction + e2e harness** stack that overlaps
substantially with Valdo's existing capabilities:

| Pilot concern | Valdo equivalent |
|---|---|
| `template_parser.py` (CSV/XLSX → canonical JSON) | `src/config/template_converter.py` (ADR 0007), `valdo infer-mapping` CLI |
| `generate_contracts.py` (canonical → mapping.json + rules.json) | `TemplateConverter.convert()` + the rules converter sibling |
| `rules_extraction.py` (transform-logic → rule JSON) | `prompts/` AI prompt library + the BA-friendly rules CSV converter (ADR 0006) |
| `e2e_runner.py` + `wave5_multiworkbook_harness.py` (multi-fixture orchestrated run) | `valdo run-etl-pipeline` (ETL pipeline runner, YAML-defined gates) |
| `promotion_gate.py` + `rule_promotion.py` (policy / state machine) | ADR 0015 baseline promotion policy + suite runner thresholds |
| `pilot_orchestrator.py` (5-round determinism check) | `tests/unit/` + suite scheduler |
| `schema_validation.py` (JSON Schema validator) | `pydantic` v2 (already a Valdo dep) |
| `e2e_app_int_oracle.py` (Oracle demo) | `src/database/extractor.py` + `src/database/adapters/oracle_adapter.py` (production-grade) |

**Engine `db_compare` already exists** at `src/pipeline/etl_config.py:77`
as a first-class `GateStep.type = "db_compare"` (DB-extract vs file).
The full `src/database/` package (16 modules including the pluggable
adapter factory, truth_source abstraction per ADR 0010, run_history
persistence) is the production substrate. The pilot does not extend
this — it sits beside it.

**Declared dependencies:** none. No `pyproject.toml`, no `setup.py`, no
`requirements.txt` in `pilot_database_validations/`. Imports observed:
`oracledb` (2 files, matches Valdo's existing dep), stdlib only
elsewhere (`csv`, `json`, `hashlib`, `pathlib`, `xml.etree`, `zipfile`,
`subprocess`, `argparse`, `dataclasses`, `re`, `sqlite3`, `datetime`).
**Zero new external deps** would be introduced by absorbing. Conversely:
the pilot reinvents `pydantic` validation (`schema_validation.py`,
154 LOC) that Valdo already uses production-wide.

### Absorb path scope

To make this useful to the engine, "absorb" would mean **discarding the
misleading name** and folding the genuinely novel pieces (rule extraction
from transform-logic text, promotion-gate policy evaluator,
multi-workbook harness) into Valdo's existing surface:

| Move / merge | Target | LOC |
|---|---|---:|
| `template_parser.py` header aliases + lineage warnings | merge into `src/config/template_converter.py` | ~150 LOC merged + ~60 LOC discarded duplicate |
| `rules_extraction.py` transform-logic → rule conversion | merge into the existing rules converter; align with ADR 0006 operator vocabulary | ~120 LOC merged |
| `promotion_gate.py` policy evaluator | new `src/services/promotion_gate_service.py`; reconcile with ADR 0015 | ~220 LOC moved |
| `rule_promotion.py` state machine | new `src/services/rule_promotion_service.py` | ~240 LOC moved |
| `e2e_runner.py` + `wave5_multiworkbook_harness.py` | fold into `valdo run-etl-pipeline` as a new `harness` step type | ~250 LOC merged, ~360 LOC discarded |
| `schema_validation.py` | **discard** (use pydantic) | 0 LOC moved |
| `pilot_orchestrator.py` + `e2e_app_int_oracle.py` + `e2e_oracle_style_demo.py` + `generate_all_file_contracts.py` | **discard** (replaced by `src/database/` + suite runner) | 0 LOC moved |
| `tests/*` | port to `tests/unit/`, rewrite against new module paths | ~860 LOC rewritten ≈ ~600 LOC after dedup |
| New `SourceConfig` block: **none required for DB-to-DB** — pilot does not provide DB-to-DB logic | — | 0 |

**Total absorb LOC:** ~1,540 LOC moved/rewritten + ~600 LOC test port.
**Estimated effort:** 12–16 dev-days (L issue, possibly two issues split
along "rules pipeline" vs "promotion gate"). Plus ADR-0006 / ADR-0007 /
ADR-0015 reconciliation work to converge the rule operator vocabulary,
the template-converter contract, and the promotion policy.

**Critically:** none of this delivers DB-to-DB validation. EC-S9's "before
next BA-onboarded source" framing assumes a DB-to-DB capability gap that
this prototype does not fill.

### Kill-and-rewrite path scope

If a real BA-driven DB-to-DB requirement landed tomorrow (it has not),
the rewrite anchor is `src/comparators/file_comparator.py` and
`src/database/extractor.py`:

| Component | Anchor LOC | Est. greenfield LOC |
|---|---:|---:|
| `DbToDbComparator` (mirror `FileComparator`, hash-join two `Truth Source` adapters) | `file_comparator.py` ≈ 350 | ~250 |
| Extend `SourceConfig` / `GateStep` for `db_to_db` step type | `etl_config.py` SourceDefinition + GateStep | ~80 |
| CLI command `valdo db-to-db-compare` | mirror `valdo db-compare` | ~80 |
| Service layer + chunked comparator integration | `services/` + `chunked_comparator.py` | ~150 |
| Tests | mirror `tests/unit/test_file_comparator.py` | ~250 |
| Docs / sample template | mirror `templates/etl/db_to_file_reconciliation.yml` | ~100 |
| **Total** | | **~910 LOC** |

**Estimated effort:** 5 dev-days (one M issue). This is **cheaper than
absorb** because (a) it builds on the existing `TruthSource` abstraction
(ADR 0010), (b) it reuses the `FileComparator` algebra rather than
inheriting the pilot's parallel ingestion/rule-extraction stack, and
(c) it does not require reconciling three ADRs' worth of duplicated
contracts.

## Decision

**Picked: Kill-and-defer.**

1. **The prototype does not deliver DB-to-DB validation** — the only
   capability EC-S9's flag predicated the absorb/kill question on. The
   name misled the audit; the contents are a parallel
   template-ingestion / rules-extraction / promotion-gate stack that
   duplicates `src/config/template_converter.py`,
   `valdo run-etl-pipeline`, ADR 0015 promotion policy, and the
   `prompts/` library. Absorbing it ships zero new BA capability and
   introduces three-way contract drift.
2. **No BA-onboarded source in the current backlog requires DB-to-DB.**
   The active onboarding artefacts (SHAW TRANERT / ESA / EST / P327)
   are file-based or DB-extract-to-file (already covered by `db_compare`
   at `etl_config.py:77`). The kickoff doc explicitly defaults the
   verdict to defer if no concrete BA ask exists; that condition holds.
3. **Kill-and-rewrite is cheaper than absorb if and when a DB-to-DB
   ask materialises** (~910 LOC vs ~1,540 LOC moved + ~600 LOC ported
   tests + three-ADR reconciliation). The 350-LOC `FileComparator` is
   the right algebra to mirror; the pilot is not a head start.
4. **Determinism / promotion-gate ideas are worth salvaging, but
   separately.** The `_stable_run_id` hashing, the JSON
   deterministic-write contract, and the `WARN` / `FAILED` /
   `review-required` policy vocabulary are good ideas that should be
   distilled into an ADR-0015 follow-up — not absorbed as 3.8k LOC of
   parallel code.

Status `Accepted`. The deletion lands in a follow-up PR (issue scoped
below); this ADR is the justification.

## Consequences

### Positive

- Engine surface stays focused on the proven layered architecture
  (CLI / commands / services / parsers / validators / DB adapters).
  No new parallel ingestion stack to maintain.
- No three-way contract reconciliation across ADR 0006 (rules operator
  vocabulary), ADR 0007 (template converter string-literal preservation),
  ADR 0015 (baseline promotion policy).
- Sprint focus stays on the breadth ADRs that move BA capability —
  JSON (ADR 0018) and XML (ADR 0019 / #378, deferred). DB-to-DB lands
  if and when a real source demands it.
- `pilot_database_validations/` deletion removes ~4.7k LOC of code,
  schemas, and trial markdown from the repo root, simplifying CI scope
  decisions and reducing onboarding confusion ("which template
  converter do I use?").
- The good ideas (deterministic run IDs, promotion gate policy
  vocabulary, transform-logic → rule extraction) are captured in this
  ADR as salvage candidates for the existing engine modules.

### Negative / risks

- **Risk: a future BA request needs DB-to-DB and we have to build from
  scratch.** Mitigation: the ~910-LOC kill-and-rewrite estimate above
  is realistic and based on existing engine LOC anchors. The
  `TruthSource` abstraction (ADR 0010) is already in place to make
  this a 5-day build.
- **Risk: salvage ideas get lost when the directory is deleted.**
  Mitigation: this ADR records the four salvage candidates
  (deterministic run IDs, promotion gate vocabulary, transform-logic
  extraction patterns, multi-workbook harness shape) explicitly in
  the follow-ups list. The deletion PR's commit message will also
  reference this ADR by number.
- **Risk: someone in another team / fork has built tooling against
  the pilot's `tools/*` entry points.** Mitigation: low — the
  directory has zero external imports from `src/`, no tests in
  `tests/unit/` reference it, and `git log` of the directory would
  confirm activity is limited to the original migration. The deletion
  PR will note this in the description.
- **Risk: EC-S9's audit re-opens the question next sprint.**
  Mitigation: this ADR is the answer. EC-S9 audit close-out should
  cite ADR 0020 by name. Re-opening requires a new EC ticket with a
  concrete BA-onboarded source name demanding DB-to-DB — at which
  point the kill-and-rewrite scope above is the starting point.

### Follow-ups

- **Deletion PR (S):** title
  `chore(repo): remove pilot_database_validations/ per ADR 0020`.
  Removes the entire `pilot_database_validations/` directory.
  Justification in PR description = this ADR. **Do not file as part
  of Sprint 8** — lands as a Sprint 8.5 / Sprint 9 hygiene PR with
  ADR 0020 as the link. The kickoff doc's "deletion lands in a paired
  PR" guidance applies.
- **Salvage idea capture (S, optional):** an issue titled
  `docs(adr): salvage promotion-gate vocabulary + deterministic run
  IDs from pilot_database_validations into ADR 0015 follow-up`.
  Scope: distill the four salvage candidates listed above into the
  existing engine modules. Estimate: 1 dev-day for the ADR delta,
  no code change. **Defer to a hygiene mini-sprint.**
- **Conditional DB-to-DB issue (M, not filed):** if a BA-onboarded
  source lands that requires comparing two live databases (not
  DB-extract-to-file, which `db_compare` already handles), file
  `feat(comparators): db-to-db comparator + db_to_db gate step`
  using the ~910-LOC scope above as the starting estimate. **Do not
  file speculatively** — wait for the concrete BA request.
- **Revisit trigger:** if the BA backlog adds a DB-to-DB request,
  this ADR is the starting point for the rewrite scope and the
  EC-S9 close-out justification.
