# E2E SHAW — Phase 1 Inventory & Mapping (READ-ONLY)

**Scope:** Collections Interfaces (ETL) process, source = `shaw`. This document is an evidence-backed inventory of what the ingested Valdo config (and the existing E2E harness) actually contains. **No test cases are authored here.** Every claim cites a file path and the specific field/key it came from. Gaps are recorded under Open Questions — layouts, join keys, variance bands, and rules are **not** invented.

Repo root: `/Users/pavankanduri/claude-ws/valdo/valdo-feature-valdo-engine-v3`

> **Convention note:** The source onboarding config (`config/e2e/sources/SHAW.yml`) labels every CDS, P327, CONTACT, and staging-master mapping/rules file with an inline `# TODO(mapping-pending)` / `# TODO(rules-pending)` comment. All of those JSON files were confirmed to be **single-field TODO stubs** (`total_record_length: 1`, one field named `TODO_FIELD_1_*`). Only the **ATOCTRAN** and **TRANERT** per-record-type mappings, plus **P327_SHAW_M06** (a separate file — see §1/§2), are fully authored with real positions/lengths.

---

## 1. Physical → Logical File Mapping Table

One row per physical output file declared for `shaw` under `config/e2e/sources/SHAW.yml` → `output_files:`. The `glob` and `mapping`/`rules` values are quoted directly from that file. "Logical type" is inferred from the file naming + umbrella comments; where it is not stated in config it is flagged.

| Physical file pattern (`glob`) | Logical type | Mapping config | Rules config | Driving staging table(s) | Join key(s) | Record format | Has-header |
|---|---|---|---|---|---|---|---|
| `atoctran_shaw_*.txt` | a-doc-tran (multi-record) | `config/mappings/SHAW_ATOCTRAN.yaml` (umbrella) → 7 per-type JSONs | per-type JSONs under `config/rules/SHAW_ATOCTRAN_*_rules.json` (referenced inside umbrella; `rules:` blank in SHAW.yml) | **OQ** — no ATOCTRAN reconciliation YAML exists (see §5) | **OQ** — no ATOCTRAN recon spec / `key:` defined | fixed-width (`source.format: "fixed_width"` in each `SHAW_ATOCTRAN_*_mapping.json`) | No header record; discriminator at pos 25 len 3 (`SHAW_ATOCTRAN.yaml` `discriminator`) |
| `cdstrans_eac_collateral_shaw_*.txt` | CDS | `config/mappings/SHAW_CDSTRANS_EAC_COLLATERAL.json` (TODO stub) | `config/rules/SHAW_CDSTRANS_EAC_COLLATERAL.json` (TODO stub) | **OQ** — not in config | **OQ** | fixed_width (stub `source.format`) | **OQ** — stub has no header concept |
| `cdstrans_efb_*.txt` | CDS | `config/mappings/SHAW_CDSTRANS_EFB.json` (TODO stub) | `config/rules/SHAW_CDSTRANS_EFB.json` (TODO stub) | **OQ** | **OQ** | fixed_width (stub) | **OQ** |
| `cdstrans_efi_*.txt` | CDS | `SHAW_CDSTRANS_EFI.json` (TODO stub) | `SHAW_CDSTRANS_EFI.json` (TODO stub) | **OQ** | **OQ** | fixed_width (stub) | **OQ** |
| `cdstrans_efw_fee_waivers_*.txt` | CDS | `SHAW_CDSTRANS_EFW_FEE_WAIVERS.json` (TODO stub) | `SHAW_CDSTRANS_EFW_FEE_WAIVERS.json` (TODO stub) | **OQ** | **OQ** | fixed_width (stub) | **OQ** |
| `cdstrans_efx_*.txt` | CDS | `SHAW_CDSTRANS_EFX.json` (TODO stub) | `SHAW_CDSTRANS_EFX.json` (TODO stub) | **OQ** | **OQ** | fixed_width (stub) | **OQ** |
| `cdstrans_esa_*.txt` | CDS | `SHAW_CDSTRANS_ESA.json` (TODO stub) | `SHAW_CDSTRANS_ESA.json` (TODO stub) | **OQ** | **OQ** | fixed_width (stub) | **OQ** |
| `cdstrans_est_shaw_*.txt` | CDS | `SHAW_CDSTRANS_EST.json` (TODO stub) | `SHAW_CDSTRANS_EST.json` (TODO stub) | **OQ** | **OQ** | fixed_width (stub) | **OQ** |
| `cdstrans_hss_*.txt` | CDS | `SHAW_CDSTRANS_HSS.json` (TODO stub) | `SHAW_CDSTRANS_HSS.json` (TODO stub) | **OQ** | **OQ** | fixed_width (stub) | **OQ** |
| `cdstrans_rlt_*.txt` | CDS | `SHAW_CDSTRANS_RLT.json` (TODO stub) | `SHAW_CDSTRANS_RLT.json` (TODO stub) | **OQ** | **OQ** | fixed_width (stub) | **OQ** |
| `cdstrans_sec_shaw_*.txt` | CDS | `SHAW_CDSTRANS_SEC.json` (TODO stub) | `SHAW_CDSTRANS_SEC.json` (TODO stub) | **OQ** | **OQ** | fixed_width (stub) | **OQ** |
| `cdstrans_xpr_*.txt` | CDS | `SHAW_CDSTRANS_XPR.json` (TODO stub) | `SHAW_CDSTRANS_XPR.json` (TODO stub) | **OQ** | **OQ** | fixed_width (stub) | **OQ** |
| `contact_shaw_*.txt` | TriNet contact | `config/mappings/SHAW_CONTACT.json` (TODO stub; umbrella deferred) | `config/rules/SHAW_CONTACT.json` (TODO stub) | **OQ** | **OQ** | fixed_width (stub); `SHAW.yml` notes future discriminator `RECORD_TYPE` (TBC) | **OQ** |
| `contact-account_shaw_*.txt` | TriNet contact-account | `config/mappings/SHAW_CONTACT_ACCOUNT.json` (TODO stub) | `config/rules/SHAW_CONTACT_ACCOUNT.json` (TODO stub) | **OQ** | **OQ** | fixed_width (stub) | **OQ** |
| `p327_shaw_*.txt` | TriNet / P327 (logical type **not stated in config** → **OQ**) | `config/mappings/SHAW_P327.json` (TODO stub) — note a separate authored file `P327_SHAW_M06_mapping.json` exists, **not wired** into SHAW.yml | `config/rules/SHAW_P327.json` (TODO stub) | **OQ** | **OQ** | fixed_width (stub) | **OQ** |
| `tranert_shaw_*.txt` | financial extract (multi-record) | `config/mappings/SHAW_TRANERT.yaml` (umbrella) → 7 per-type JSONs | per-type JSONs under `config/rules/SHAW_TRANERT_*_rules.json` (inside umbrella; `rules:` blank in SHAW.yml) | `SHAW_LOAN_MASTER`, `SHAW_LOANS_NAME`, `SHAW_TRANSACTIONS`, etc. — see §4 / `expected_*.sql` (driver = `tranert_driver.sql`) | `LN-NUM-ERT` (most types); `BK-NUM-BRT` (batch_header); composite `[LN-NUM-ERT, CONTACT-ID]` (rt_32005) — from `config/e2e/sources/SHAW/reconciliation/tranert.yml` `key:` | fixed-width; discriminator `TRN-COD-ERT` at pos 170 len 5 (`SHAW_TRANERT.yaml`) | **Yes** — Batch Header record, matched by `position: "first"` (`SHAW_TRANERT.yaml` `batch_header.position`) |

**Logical-type sourcing note:** None of the config files contain an explicit "logical type" attribute (a-doc-tran / financial extract / CDS / TriNet). The logical types above are inferred from file-name prefixes and the umbrella comment blocks. **The a-doc-tran / financial-extract / CDS / TriNet classification is not an explicit config field — treat the column as an inference, flagged as Open Question #1.**

**`input_files` (staging-load side, for reference)** — from `config/e2e/sources/SHAW.yml` `input_files:`. These feed the `file_to_staging` gate, distinct from the output files above:

| file_type | glob | mapping | target_staging_table |
|---|---|---|---|
| COLLATERAL_MASTER | `collateral-master_*.txt` | `SHAW_COLLATERAL_MASTER.json` (stub) | `SHAW_COLLATERAL` |
| FEE_MASTER | `fee-master_*.txt` | `SHAW_FEE_MASTER.json` (stub) | `SHAW_FEE_MASTER` |
| LOAN_MASTER | `loans-master_*.txt` | `SHAW_LOAN_MASTER.json` (stub) | `SHAW_LOAN_MASTER` |
| LOANS_NAME | `loans-name_*.txt` | `SHAW_LOANS_NAME.json` (stub) | `SHAW_LOANS_NAME` |
| POSTED_TRANS | `posted-trans_*.txt` | `SHAW_POSTED_TRANS.json` (stub) | `SHAW_TRANSACTIONS` |
| TRANS_MASTER | `history_*.txt` | `SHAW_TRANS_MASTER.json` (stub) | `SHAW_TRANS_MASTER` |

---

## 2. Fixed-Width Layout Reference per Output File

### 2.1 ATOCTRAN per-record-type layouts (FULLY AUTHORED)

Each `config/mappings/SHAW_ATOCTRAN_<code>_mapping.json` declares `source.format: "fixed_width"`, per-field `position`/`length`/`data_type`/(optional)`format`, and a `total_record_length`. Positions are 1-indexed.

**Common header fields (identical across all 7 codes):**
| name | start | len | data_type | format |
|---|---|---|---|---|
| LOCATION-CODE | 1 | 6 | string | — |
| ACCT-NUM | 7 | 18 | string | — |
| TRANSACTION-CODE | 25 | 3 | string | (discriminator) |
| TRANSACTION-DATE | 28 | 8 | date | CCYYMMDD |

**rt_060** (`SHAW_ATOCTRAN_060_mapping.json`, `total_record_length: 35`, 4 fields): common header only.

**rt_100** (`..._100_mapping.json`, `total_record_length: 75`, 5 fields): + `CYCLE-ARRAY-TRANS-AREA` pos 36 len 40, decimal, format `9(2), 20 occur.`

**rt_200** (`..._200_mapping.json`, `total_record_length: 84`, 8 fields): + `PREVIOUS-CCI` (36/1), `PORTFOLIO-LOCATION-CODE` (37/6), `CUSTOMER-PORTFOLIO ID` (43/18), `CUSTOMER-PORTFOLIO-CONTACT-ID` (61/24). All string.

**rt_300** (`..._300_mapping.json`, `total_record_length: 125`, 15 fields): + `TRANSACTION-AMOUNT` (36/18, decimal `9(12)V9(6)`), `NUMBER-OF-DEBITS` (54/2, decimal), `CATEGORY-CODE` (56/1), `THIRD-PARTY-ID` (57/8), `THIRD-PARTY-COMM-AMT` (65/18, decimal), `BATCH-INFO` (83/25), `ORIGINAL-TRAN-INFO` (108/10), `THIRD-PARTY-AMT-AFFECTED` (118/1), `THIRD-PARTY-WITHHELD-FLAG` (119/1), `INPUT-SOURCE-CODE` (120/3, decimal `9(3)`), `REFERENCE-NUM` (123/3).

**rt_605** (`..._605_mapping.json`, `total_record_length: 83`, 7 fields): + `NEW-PORTFOLIO-LOCATION-CODE` (36/6), `NEW-PORTFOLIO-ID` (42/18), `NEW-PORTFOLIO-CONTACT-ID` (60/24). All string.

**rt_700** (`..._700_mapping.json`, `total_record_length: 122`, 13 fields): + `NG-CHECK-AMOUNT` (36/18, decimal), `THIRD-PARTY-ID` (54/8), `THIRD-PARTY-COMM-AMT` (62/18, decimal), `BATCH-INFO` (80/25), `ORIGINAL-TRAN-INFO` (105/10), `THIRD-PARTY-WITHHELD-FLAG` (115/1), `INPUT-SOURCE-CODE` (116/3, decimal `9(3)`), `REFERENCE-NUM` (119/3), `THIRD-PARTY-AMT-AFFECTED` (122/1).

**rt_900** (`..._900_mapping.json`, `total_record_length: 53`, 5 fields): + `TRANSACTION-AMT` (36/18, decimal `9(12)V9(6)`).

### 2.2 TRANERT per-record-type layouts (FULLY AUTHORED)

Each `config/mappings/SHAW_TRANERT_<type>_mapping.json` is `fixed_width` with positions/lengths and a `total_record_length`. Field counts and record lengths confirmed:

| Record type | mapping JSON | total_record_length | field count |
|---|---|---|---|
| batch_header | `SHAW_TRANERT_BATCH_HEADER_mapping.json` | 158 | 22 |
| NEW1 (32000/32001) | `SHAW_TRANERT_NEW1_mapping.json` | 205 | 14 |
| CUS (32005) | `SHAW_TRANERT_CUS_mapping.json` | 336 | 35 |
| ORI (32010) | `SHAW_TRANERT_ORI_mapping.json` | 636 | 48 |
| COD (32025) | `SHAW_TRANERT_COD_mapping.json` | 551 | 57 |
| CBRS (32040) | `SHAW_TRANERT_CBRS_mapping.json` | 524 | 69 |
| REC (32075) | `SHAW_TRANERT_REC_mapping.json` | 528 | 35 |

(Per-field tables for TRANERT are large — 22 to 69 fields each — and are not transcribed here; they are present and authored in the listed JSONs. Discriminator `TRN-COD-ERT` lives at pos 170 len 5 per `SHAW_TRANERT.yaml`.)

### 2.3 P327 — TWO conflicting artifacts

- **Wired:** `config/mappings/SHAW_P327.json` referenced by `SHAW.yml` is a **TODO stub** (`total_record_length: 1`, single `TODO_FIELD_1_P327`). **Layout NOT available.**
- **Authored but NOT wired:** `config/mappings/P327_SHAW_M06_mapping.json` is fully authored — `fixed_width`, `total_record_length: 2809`, **252 fields**, first field `LOCATION-CODE`. There are 3 near-duplicate copies (`P327_SHAW_M06_mapping.json`, `P327_SHAW_M06_mapping_v2.json`, `p327-shaw-m06-maaping.json` [sic, misspelled]) plus `config/rules/P327_SHAW_M06_rules.json` and `p327-shaw-m06-rules.json`. **Open Question #6:** which P327 artifact is canonical, and should `SHAW.yml`'s `P327` entry point at `P327_SHAW_M06_mapping.json` instead of the stub?

### 2.4 CDS / CONTACT / CONTACT_ACCOUNT / staging masters — NO LAYOUT

All of the following are **TODO stubs** with `total_record_length: 1` and a single field `TODO_FIELD_1_*`. **Fixed-width layout is NOT available in config for any of these — do not author layout-dependent test cases against them:**

- CDS: `SHAW_CDSTRANS_EAC_COLLATERAL`, `_EFB`, `_EFI`, `_EFW_FEE_WAIVERS`, `_EFX`, `_ESA`, `_EST`, `_HSS`, `_RLT`, `_SEC`, `_XPR` (all 11 confirmed stubs in `config/mappings/`).
- TriNet: `SHAW_CONTACT`, `SHAW_CONTACT_ACCOUNT`.
- Staging masters: `SHAW_LOAN_MASTER`, `SHAW_TRANS_MASTER`, `SHAW_POSTED_TRANS`, `SHAW_LOANS_NAME`, `SHAW_FEE_MASTER`, `SHAW_COLLATERAL_MASTER`.

The stub rules JSONs carry explicit markers, e.g. `config/rules/SHAW_CDSTRANS_EFB.json` rule message: `"TODO(rules-pending): CDSTRANS_EFB rules not yet authored."` and `config/rules/SHAW_CONTACT.json`: `"TODO(rules-pending): CONTACT rules not yet authored."`

---

## 3. ATOCTRAN Multi-Record Model

**Discriminator** (`config/mappings/SHAW_ATOCTRAN.yaml` `discriminator:`):
- `field: TRANSACTION-CODE`
- `position: 25`
- `length: 3`

This matches the `TRANSACTION-CODE` field (pos 25, len 3) in every per-type mapping JSON, and the umbrella comment "discriminator at position 25-27".

**Record-type codes that HAVE both a mapping and a rules config (confirmed present on disk):**
`060, 100, 200, 300, 605, 700, 900` — all 7 wired in `SHAW_ATOCTRAN.yaml` `record_types:` (keys `rt_060`, `rt_100`, `rt_200`, `rt_300`, `rt_605`, `rt_700`, `rt_900`), each with `match`, `mapping`, `rules`, and `expect: any`. Both `config/mappings/SHAW_ATOCTRAN_<code>_mapping.json` and `config/rules/SHAW_ATOCTRAN_<code>_rules.json` exist for each.

**`SHAW.yml` output_files comment** lists the same value set: "values 100, 200, 300, 605, 700, 900, 060".

**Spec codes vs config (per the prompt's test-spec list `060, 300, 200, 900, 650, ...`):**
| Spec code | In ATOCTRAN config? | Evidence |
|---|---|---|
| 060 | YES | `SHAW_ATOCTRAN.yaml` `rt_060.match: "060"` |
| 200 | YES | `rt_200.match: "200"` |
| 300 | YES | `rt_300.match: "300"` |
| 900 | YES | `rt_900.match: "900"` |
| **650** | **NO** | **No `SHAW_ATOCTRAN_650_*` file exists** (confirmed: no `650` mapping or rules in `config/mappings/` or `config/rules/`; not in `SHAW_ATOCTRAN.yaml`). **Open Question #4.** |
| 100, 605, 700 | YES (in config; not all in the spec excerpt) | umbrella `rt_100/rt_605/rt_700` |

**Codes in config with no documented driving-population rule:** All 7 umbrella entries use `expect: any` and the umbrella comment states "No cross-type rules for ATOCTRAN" and "Per-account record-creation gates are enforced at the per-record-type rules level". The **driving-population / record-creation gate** (which staging rows produce which ATOCTRAN record type, and the join key) is **not** captured in a reconciliation spec — there is **no `config/e2e/sources/SHAW/reconciliation/atoctran.yml`** (only `tranert.yml` exists). So for ATOCTRAN, the driving table + join key + per-code cardinality are **undefined in config → Open Question #2/#5**.

**`default_action: error`** (`SHAW_ATOCTRAN.yaml`): unknown `TRANSACTION-CODE` values are treated as a hard failure ("a Java-side defect — fail loudly").

**TRANERT cross-check (for completeness):** `SHAW_TRANERT.yaml` wires `batch_header` (position "first") + detail codes `32000, 32001` (→ NEW1), `32005` (CUS), `32010` (ORI), `32025` (COD), `32040` (CBRS), `32075` (REC). Codes `32030 (VR)` and `32070 (CON)` are **commented-out TODOs** ("layout sheet not yet authored"). `default_action: error`. One cross-type rule: `header_trailer_count` on `ITM-CNT-BRT` (batch_header, pos 131 len 9) == count of detail rows, with `allow_empty_batch: true` (ADR 0013).

---

## 4. Existing-Harness Reconciliation Model (reuse vs author)

The harness **already implements a generic, declarative SQL-truth reconciliation engine**. Most of what an E2E "DB-truth" test needs for TRANERT is reusable; for ATOCTRAN/CDS/etc. nothing is wired yet.

### What `reconciliation_spec.py` defines (the schema)
A frozen-dataclass schema + strict loader (`load_spec`) for per-source/per-file-type YAML manifests at `config/e2e/sources/<SOURCE>/reconciliation/<file_type>.yml`. Public types:
- `ReconciliationSpec` (top level): `schema_version`, `source`, `file_type`, `umbrella_mapping`, `bootstrap_dir`, `load_dir`, `query_dir`, `record_types`, `assertions`.
- `RecordTypeSpec`: `record_type`, `expected_sql` (the per-type SELECT file), `cardinality`, `key` (tuple of join columns), `fields` (file_field ↔ expected_column), optional `predicate`, `ignored_fields`.
- `Cardinality` enum: `one_per_driver_row`, `zero_or_one_per_driver_row`, `many_per_driver_row` (`reconciliation_spec.py` lines 202-204).
- `FieldSpec`: `file_field`, `expected_column`, optional `predicate`, `regression_only`.
- `AssertionSpec`: `name` + `expr` (narrow grammar, e.g. `header.ITM-CNT-BRT == sum(detail_row_counts)`).

### What `db_truth_comparator.py` already implements (the engine)
`reconcile(...)` returns a `ReconciliationReport` with (lines 188-208):
- `rows_unknown_type: int`
- `per_type_counts: Mapping[str, PerTypeCount]` where `PerTypeCount` (lines 137-150) holds `file_rows`, `expected_rows`, and the field-mismatch count — i.e. **per-record-type cardinality counts are already produced** (file count vs expected SQL count per type).
- `violations` of these kinds (docstring lines 36-49): `field_mismatch`, `missing_expected`, `unexpected_file_row`, `cardinality_violation`, `assertion_failed`, `unknown_record_type`.

Cardinality enforcement is in `_check_cardinality` (lines 647+): `one_per_driver_row` → both counts must be 1; `zero_or_one_per_driver_row` → both ≤ 1; `many_per_driver_row` → only a count mismatch flags. **Unknown-code reporting** is implemented: `rows_unknown_type` counter + `_collect_unknown_record_type_violations` (lines 306-347), honoring the umbrella `default_action`.

### What is NOT implemented (must NOT be assumed)
- **No numeric tolerance / variance bands.** `db_truth_comparator.py` docstring (line 62) states explicitly: *"Numeric-tolerance comparison. Strict trimmed-string equality only."* Values are normalized via `str(v).strip()`, `None → ""`. **There is no per-CDS / per-record-type variance band anywhere in config or code → Open Question #5.**
- **No consolidated-master-count anchor.** A repo-wide search for `consolidated`, `master count`, `daily audit`, `control count` across `scripts/e2e_lib/` and `config/e2e/` returned **nothing**. The reconciliation model is per-record-type (file-count vs expected-SQL-count) plus the single `batch_header_count` assertion — there is **no whole-file "expected total against a daily audit table" anchor → Open Question #3.**
- **No expected SQL except TRANERT.** The `expected_*.sql` files (`expected_batch_header.sql`, `expected_32000/32005/32010/32025/32040/32075.sql`) exist only under `config/e2e/sources/SHAW/sql/tranert/20_query/`. Bootstrap (`010_lookup_tables.sql`, `030_expected_tables.sql`, `040_source_registry.sql`), load (`010_load_lookups_from_csv.sql`, `020_refresh_expected.sql`, `030_refresh_source_registry.sql`), and driver/helper queries (`tranert_driver.sql`, `loan_master_batch_dates.sql`, `contacts_merged.sql`, `cost_merged.sql`, etc.) are all TRANERT-only.

### TRANERT reconciliation already authored
`config/e2e/sources/SHAW/reconciliation/tranert.yml` is a complete spec: 7 record types with `key`, `cardinality`, `fields`, predicates (e.g. `rt_32010` predicate `"CHG_OFF_CD = '1'"`), `ignored_fields` (deferred Java-stateful fields like `CIF-REF-NUM-CUS`, `CIF-ACT-COD-CUS`, `LN-OFC-CUR-COD`), and the `batch_header_count` assertion. **For TRANERT, count-recon + per-type expected SQL + cardinality + key are all reusable.**

### Orchestration (`run_source.py`)
Stages/gates (lines 84-103, 276-402): `load_step` → `file_to_staging` (`_INPUT_PHASE_GATES`) → `generate_step` (disabled for SHAW) → output phase `L1_structural`, `L3_baseline_diff` (`_OUTPUT_PHASE_GATES`) → `L2b_sql_truth` (orchestrator-driven, only when a reconciliation YAML exists for the file type) → `multi_record_report` (non-blocking). Gate blocking policy comes from `SHAW.yml` `gates:` (`L1_structural`, `L3_baseline_diff`, `L2b_sql_truth` all `blocking: true`). Exit code: 0 all-pass, 2 if a blocking gate fails.

**DB query mechanism:** `_open_l2b_connection` (lines 1039-1071) delegates to `src.database.truth_source.OracleTruthSource` (ADR 0010), credentials `ORACLE_DSN` / `ORACLE_USER` / `ORACLE_PASSWORD` resolved via `SecretResolver`. Failure-sink writes go to Oracle table **`AUDIT.VALDO_RUN_FAILURES`** (`failure_sink.py` `DEFAULT_TABLE`, DDL at `scripts/sql/audit_valdo_run_failures.sql`). `VALDO_E2E_DISABLE_L2B=1` skips L2b when Oracle is unreachable.

---

## 5. First-Cut Open Questions / Unmapped

| # | Gap | File(s) checked | Status |
|---|---|---|---|
| 1 | **Logical type (a-doc-tran / financial extract / CDS / TriNet) is not an explicit config attribute.** Classification in §1 is inferred from filename + comments. | `config/e2e/sources/SHAW.yml` (`output_files[].file_type` only gives the file type, not the logical category) | Needs BA/PO confirmation of the intended logical categorization per file. |
| 2 | **ATOCTRAN driving staging table(s) + join key(s) + per-code cardinality are undefined.** Umbrella says gates are "per-record-type rules level" but no reconciliation spec exists. | `config/mappings/SHAW_ATOCTRAN.yaml` (no key/cardinality); `config/e2e/sources/SHAW/reconciliation/` (only `tranert.yml`) | Needs an `atoctran.yml` reconciliation spec or BA driving-population rules before DB-truth tests can be authored. |
| 3 | **Consolidated-master-count anchor + daily audit table name + the column holding the consolidated count — DO NOT EXIST in config or harness.** | grep across `scripts/e2e_lib/`, `config/e2e/` for `consolidated`/`master count`/`daily audit`/`control count` → no hits; only `AUDIT.VALDO_RUN_FAILURES` (failure sink, not a count anchor) found in `failure_sink.py` | If the test suite needs a "total expected vs daily audit count" reconciliation, the table + column must be supplied by BA/PO; nothing in config provides it. |
| 4 | **ATOCTRAN code `650` (in test spec) is absent from config.** | `config/mappings/`, `config/rules/`, `SHAW_ATOCTRAN.yaml` — no `650` artifact | Confirm whether 650 is in scope; if so, mapping/rules + umbrella entry must be authored first. |
| 5 | **Per-CDS / per-record-type variance bands / tolerances are documented NOWHERE.** Engine is strict trimmed-string equality. | `db_truth_comparator.py` line 62 ("Strict trimmed-string equality only"); `reconciliation_spec.py` (no tolerance field in schema); `tranert.yml` (no tolerance) | If tolerance bands are required (e.g. for decimal amount fields), the schema and engine do not support them today — escalate. |
| 6 | **P327 has two conflicting artifacts.** Wired `SHAW_P327.json` is a 1-field stub; unwired `P327_SHAW_M06_mapping.json` is a real 252-field / 2809-char layout (plus a v2 and a misspelled duplicate). | `config/mappings/SHAW_P327.json`, `P327_SHAW_M06_mapping*.json`, `p327-shaw-m06-maaping.json`, `config/e2e/sources/SHAW.yml` P327 entry | Confirm canonical P327 mapping and whether `SHAW.yml` should re-point. Layout-dependent P327 tests blocked until resolved. |
| 7 | **Output files with NO layout and NO driving table found:** all 11 CDSTRANS, CONTACT, CONTACT_ACCOUNT, P327 (wired stub). | per-file `config/mappings/SHAW_*` (all `total_record_length: 1`, `TODO_FIELD_1_*`); rules stubs with `TODO(rules-pending)` messages | Cannot author layout/field/DB-truth tests for these until BA layouts land. Structural-presence / negative tests only. |
| 8 | **CONTACT discriminator unconfirmed.** `SHAW.yml` notes future multi-record `RECORD_TYPE` "(TBC)" and umbrella deferred. | `config/e2e/sources/SHAW.yml` CONTACT entry comment | Confirm whether CONTACT is multi-record and its discriminator position/length. |
| 9 | **Staging schema + table names: in config (not env) for the load side; partially OQ for output driving side.** `staging_schema: "APP_INT"` and 6 `staging_tables` are in `SHAW.yml`; `output_root: /app/software/APPS/ftp/input/shaw`. TRANERT expected tables are materialized in `APP_INT` (CTAS) per `tranert.yml` header. | `config/e2e/sources/SHAW.yml` `staging_schema`/`staging_tables`/`output_root`; `tranert.yml` header note | Resolved for load side + TRANERT; **still OQ for ATOCTRAN/CDS output driving tables** (ties to #2). |
| 10 | **DB query mechanism (resolved):** Oracle via `OracleTruthSource` (ADR 0010), creds `ORACLE_DSN/USER/PASSWORD` via `SecretResolver`; CLI gates via `valdo run-etl-pipeline`; L2b via orchestrator's `db_truth_comparator`. | `run_source.py` `_open_l2b_connection` (1039-1071); `SHAW.yml` `gates` | Resolved — documented for reuse, not an open gap. |

---

### One-line bottom line
**TRANERT and ATOCTRAN have real fixed-width layouts; the generic SQL-truth engine + TRANERT reconciliation YAML are reusable as-is (count + per-type cardinality + key already encoded). Everything else (11 CDS files, CONTACT/CONTACT_ACCOUNT, wired P327, all 6 staging masters) is a TODO stub with no layout. There is no consolidated-master-count anchor, no daily-audit count table, no variance bands, no ATOCTRAN reconciliation spec, and no code 650 — these are the gating Open Questions before authoring.**
