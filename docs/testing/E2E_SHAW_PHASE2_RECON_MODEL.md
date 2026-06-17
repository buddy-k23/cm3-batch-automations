# E2E SHAW — Phase 2 Reconciliation Model (READ-ONLY, NO TEST CASES)

**Scope:** Collections Interfaces (ETL) process, source = `shaw`. This document defines the **reconciliation model** only — the rules by which output-file counts, per-record-type cardinalities, cross-file references, and staging loads are checked against a defined truth base. **No test cases are authored here** (that is Phase 3) and **no production code is written.**

Repo root: `/Users/pavankanduri/claude-ws/valdo/valdo-feature-valdo-engine-v3`

**Evidence discipline (carried from Phase 1):** every rule cites a config/harness file path. Where a tolerance, band, join key, or rule is **not** documented in config, it is recorded as an **Open Question** and **NOT invented**. The worked-example magnitudes (116,802 base; per-file deltas) come from the **spec/prompt only** — a repo-wide grep for `116802`, `116,802`, `114875`, `114,875` returned **zero hits**, and `AUDIT_SQL_LOADER` is **not present anywhere in the repo** (grep `audit_sql_loader` → no hits). They are therefore used strictly as **illustrative magnitude**, never as hardcoded expectations.

---

## 1. THE ANCHOR — Consolidated Master Count (the reconciliation base)

**User decision (the reconciliation anchor):** the consolidated master count = the row count of the **`SHAW_LOAN_MASTER`** table **after parent/child consolidation completes**; equivalently it can be read from the **`AUDIT_SQL_LOADER`** table filtered to `source_system='shaw'` AND `table_name='shaw_loan_master'`.

This base count (call it **`BASE`**) is the reconciliation base for **Section 2 (per-output-file count recon)** and **Section 3 (atoctran per-code recon)**. Worked-example magnitude from the spec: **116,802** for `batch_date 20260608` — illustrative only, never a hardcoded expectation.

### 1.1 Two equivalent runtime sources for BASE

| # | Source | SQL shape | Notes |
|---|---|---|---|
| A | `SHAW_LOAN_MASTER` table (post-consolidation) | `SELECT COUNT(*) FROM SHAW_LOAN_MASTER` | Schema-qualified at runtime as `APP_INT.SHAW_LOAN_MASTER` — `staging_schema: "APP_INT"` (`config/e2e/sources/SHAW.yml` line 23). Table is one of the 6 declared `staging_tables` (line 45) and the `LOAN_MASTER` input load target (line 70). Must be read **after** the consolidation step has run, else parent/child rows are not yet merged. **Open Question** whether "post-consolidation" is a distinct table state or a separate table — see §7. |
| B | `AUDIT_SQL_LOADER` audit table | `SELECT <count_col> FROM AUDIT_SQL_LOADER WHERE source_system='shaw' AND table_name='shaw_loan_master'` | `<count_col>` = **OPEN QUESTION** — the exact column name holding the row count is **not present in repo config or code** (grep `audit_sql_loader` → no hits). Filter literals `source_system='shaw'` and `table_name='shaw_loan_master'` are lowercase per the user decision; case-sensitivity of these predicates is itself unconfirmed → §7. |

Both sources must agree; if they diverge, that divergence is itself a finding (consolidation vs. audit-row drift).

### 1.2 Which Valdo primitive runs the anchor query

Confirmed in `src/database/extractor.py`:
- **`DataExtractor.extract_by_query(query, params=None)`** (line 209) — runs an arbitrary SELECT, returns a DataFrame. Source A and Source B both fit (`SELECT COUNT(*) …` / `SELECT <count_col> …`).
- **`DataExtractor.extract_table(table_name, columns=None, limit=None)`** (line 150) — table-mode extract; usable to dump `SHAW_LOAN_MASTER` for an independent count, but `extract_by_query` with `COUNT(*)` is the direct path for BASE.

CLI surface (`src/commands/extract_command.py`): `valdo extract --query "SELECT COUNT(*) …"` (mode 2, line 82) or `--table SHAW_LOAN_MASTER` (mode 1, line 90) or `--sql-file` (mode 3).

MCP surface (`src/mcp/extract_tools.py`, `extract_table_payload`, line 50): accepts **exactly one** of `table` or `query` (mutually exclusive — line 115); `mode` is reported as `"table"` or `"query"` and `row_count` is returned. So the MCP `extract_table` tool runs **both** Source A (`query="SELECT COUNT(*) …"`) and Source B (`query="SELECT <count_col> …"`) via its `query` mode, or Source A via `table` mode.

> **Note on the existing L2b engine:** `db_truth_comparator.reconcile` (`scripts/e2e_lib/db_truth_comparator.py`) does **not** read a whole-file base count — it reconciles **per-record-type** (file rows vs `expected_*.sql` rows). The BASE anchor is a **new harness concern** layered on top of the existing engine; it is not produced by `db_truth_comparator` today (Phase 1 Open Question #3 confirmed).

### Anchor Open Questions
- **OQ-A1:** Exact column name in `AUDIT_SQL_LOADER` holding the count (`<count_col>`) — unknown; not in repo.
- **OQ-A2:** Whether "`SHAW_LOAN_MASTER` after parent/child consolidation" is the same physical table read at a later step, or a distinct consolidated table/view. Phase 1 §2.4 notes `SHAW_LOAN_MASTER` mapping is a TODO stub; the table exists as a staging target but no consolidation DDL is in config.
- **OQ-A3:** Case-sensitivity / exact literal casing of the `source_system` and `table_name` predicates in `AUDIT_SQL_LOADER`.

---

## 2. PER-OUTPUT-FILE COUNT RECONCILIATION RULE + VARIANCE BAND

One row per output file declared under `config/e2e/sources/SHAW.yml` `output_files:`. **Reconciliation class** and the **relation to BASE** are taken from the Section-4 worked example cited in the prompt; the worked-example delta is recorded **as the only evidence** where no band is documented. **No variance band exists anywhere in config or code** — Phase 1 Open Question #5 (`db_truth_comparator.py` line 62: *"Numeric-tolerance comparison. Strict trimmed-string equality only."*; `reconciliation_spec.py` has no tolerance field). Therefore every band below is **`OPEN QUESTION`** unless the worked example states an exact equality (`== BASE`), which is the only band the evidence supports.

Reconciliation classes: `AT_BASE` (count == BASE), `ABOVE_BASE` (count > BASE), `BELOW_BASE` (count < BASE), `SPARSE_CONDITIONAL` (count ≪ BASE, populated only when a condition holds), `MULTI_RECORD_SUM` (file is a sum of heterogeneous record types; does **not** reconcile to BASE as a single count).

| File (glob) | Recon class | Rule (relation to anchor) | Variance band | Source of the band |
|---|---|---|---|---|
| `cdstrans_efw_fee_waivers_*.txt` | AT_BASE | `count == BASE` | exact (== BASE) | Worked example §4: at-base file. |
| `cdstrans_efx_*.txt` | AT_BASE | `count == BASE` | exact (== BASE) | Worked example §4: at-base file. |
| `cdstrans_esa_*.txt` | AT_BASE | `count == BASE` | exact (== BASE) | Worked example §4: at-base file. |
| `cdstrans_est_shaw_*.txt` | AT_BASE | `count == BASE` | exact (== BASE) | Worked example §4: at-base file. |
| `cdstrans_sec_shaw_*.txt` | AT_BASE | `count == BASE` | exact (== BASE) | Worked example §4: at-base file. |
| `p327_shaw_*.txt` | AT_BASE | `count == BASE` | exact (== BASE) | Worked example §4: at-base file. **Layout blocked** — wired `SHAW_P327.json` is a TODO stub; canonical artifact unresolved (Phase 1 OQ #6). Count recon possible; field recon is not. |
| `cdstrans_eac_collateral_*.txt` | ABOVE_BASE | `count > BASE` | **OPEN QUESTION** | Worked example §4: above-base (one row per collateral item; an account may hold multiple). No documented upper band. |
| `cdstrans_xpr_*.txt` | ABOVE_BASE | `count > BASE` | **OPEN QUESTION** | Worked example §4: above-base. No documented band. |
| `cdstrans_efb_*.txt` | BELOW_BASE | `count < BASE` | **OPEN QUESTION** | Worked example §4: below-base. Observed worked-example delta (spec, illustrative only): **114,875 vs 116,802 = −1.65%**. Recorded as the *only* evidence; not a sanctioned tolerance. |
| `cdstrans_efi_*.txt` | BELOW_BASE | `count < BASE` | **OPEN QUESTION** | Worked example §4: below-base. No documented delta or band. |
| `cdstrans_hss_*.txt` | SPARSE_CONDITIONAL | `0 ≤ count ≪ BASE` (populated only when the HSS condition holds) | **OPEN QUESTION** | Worked example §4: sparse. Condition/predicate undocumented in config (stub mapping). |
| `cdstrans_rlt_*.txt` | SPARSE_CONDITIONAL | `0 ≤ count ≪ BASE` | **OPEN QUESTION** | Worked example §4: sparse. Condition undocumented. |
| `tranert_shaw_*.txt` | SPARSE_CONDITIONAL | per-record-type counts, not a single whole-file == BASE relation | **OPEN QUESTION** (per-type covered by `tranert.yml` cardinality, not a BASE band) | Worked example §4: sparse. TRANERT recon is the **per-type** model already in `config/e2e/sources/SHAW/reconciliation/tranert.yml` (count + cardinality + key per type) — it does **not** map to a single BASE count. |
| `atoctran_shaw_*.txt` | MULTI_RECORD_SUM | `count == Σ(per-code counts)`; does **not** reconcile to BASE as one number — see §3 | n/a (sum identity, not a band) | Worked example §4: multi-record sum. Discriminator `TRANSACTION-CODE` pos 25 len 3 (`SHAW_ATOCTRAN.yaml`). |
| `contact_shaw_*.txt` | **OPEN QUESTION** (unclassified) | unknown relation to BASE | **OPEN QUESTION** | Not classified in worked example §4. Stub mapping (Phase 1 §2.4); also the §4 cross-file invariant target (see §4). |
| `contact-account_shaw_*.txt` | **OPEN QUESTION** (unclassified) | unknown relation to BASE | **OPEN QUESTION** | Not classified in §4. Stub mapping; §4 invariant target. |

> **Engine reality:** the existing `db_truth_comparator` produces `PerTypeCount.file_rows` and `expected_rows` per record type but has **no whole-file == BASE comparator and no variance band**. Every `AT_BASE`/`ABOVE_BASE`/`BELOW_BASE`/`SPARSE_CONDITIONAL` count rule above is a **harness-implemented assertion** against the §1 anchor, not a product primitive. Only the `== BASE` (exact) rules are evidence-backed; all `> BASE`, `< BASE`, and `≪ BASE` bands are Open Questions pending BA/PO-supplied tolerances.

---

## 3. ATOCTRAN PER-CODE RECONCILIATION

`atoctran_shaw_*.txt` is multi-record (umbrella `config/mappings/SHAW_ATOCTRAN.yaml`), discriminator `TRANSACTION-CODE` at pos 25 len 3. The whole-file count is `MULTI_RECORD_SUM` (§2) — it does **not** reconcile to a single BASE number. Reconciliation is **per-code**.

**Cardinality vocabulary (from the harness):** `reconciliation_spec.py` `Cardinality` enum (lines 202–204): `one_per_driver_row`, `zero_or_one_per_driver_row`, `many_per_driver_row`. These are the only sanctioned cardinality terms; below they are mapped to the prompt's `one` / `zero_or_one` / `many_per_driver_row`.

**Critical gap:** there is **no `config/e2e/sources/SHAW/reconciliation/atoctran.yml`** (only `tranert.yml` exists — Phase 1 §4 / OQ #2). The per-code **driving staging table + join key + cardinality are NOT in config.** The driving-population descriptions below come from the **spec/prompt only** and are flagged as such; the per-record-type rules JSONs (`config/rules/SHAW_ATOCTRAN_<code>_rules.json`) are **pure field validation** (confirmed for 060 and 200 — `not_empty`/`length`/`valid_values`/`date_format` only; **no cardinality or driving-population rule**).

| Code | In config? | Driving population (spec, NOT config) | Cardinality (harness vocab) | Driving staging table + join key |
|---|---|---|---|---|
| 060 | YES (`rt_060.match: "060"`) | One per consolidated master account | `one_per_driver_row` (`one`) | **OQ** — spec implies driver = consolidated `SHAW_LOAN_MASTER`, join key = account number (`ACCT-NUM` pos 7 len 18). Not declared in any recon YAML. |
| 100 | YES (`rt_100.match: "100"`) | **OQ** — driving population not stated in spec or config | **OQ** | **OQ** — `rt_100` mapping adds `CYCLE-ARRAY-TRANS-AREA`; no driver/key documented. |
| 200 | YES (`rt_200.match: "200"`) | One per **NEW** account in the batch | `one_per_driver_row` (`one`) (one per new account) | **OQ** — driver = the set of accounts new in `batch_date`; join key = `ACCT-NUM` (pos 7 len 18, `SHAW_ATOCTRAN_200_mapping.json`). "New-account" predicate source table undocumented. |
| 300 | YES (`rt_300.match: "300"`) | Credit + debit transactions per master where activity exists in loan-history / posted-trans staging | `many_per_driver_row` (`many_per_driver_row`) | **OQ** — driver tables plausibly `SHAW_TRANS_MASTER` (history) and `SHAW_TRANSACTIONS` (posted-trans) per Phase 1 input_files; join key = account number. Not declared in config. |
| 605 | YES (`rt_605.match: "605"`) | **OQ** — spec does not state; `rt_605` mapping carries `NEW-PORTFOLIO-*` fields (portfolio reassignment) | **OQ** | **OQ**. See also OQ-650 below (possible rename of 650). |
| 700 | YES (`rt_700.match: "700"`) | **OQ** — spec does not state; `rt_700` mapping carries `NG-CHECK-AMOUNT` + third-party fields | **OQ** | **OQ** |
| 900 | YES (`rt_900.match: "900"`) | One per account **charging off on `batch_date`** | `zero_or_one_per_driver_row` (`zero_or_one`) per master (an account charges off at most once on a given batch_date) | **OQ** — driver = consolidated `SHAW_LOAN_MASTER` filtered to charge-off on `batch_date`; join key = `ACCT-NUM`. Predicate column undocumented (cf. TRANERT `CHG_OFF_CD = '1'` in `tranert.yml` rt_32010, but that is TRANERT, not ATOCTRAN). |
| **650** | **NO** | Spec lists 650; **no `SHAW_ATOCTRAN_650_*` artifact exists** and 650 is not in `SHAW_ATOCTRAN.yaml` (Phase 1 OQ #4) | n/a | **enumerate-and-report-if-present** (see rule below). **OQ:** was 650 **renamed to 605**, or is it **genuinely absent**? |

### 3.1 Per-code enumeration is a HARNESS responsibility (not a product primitive)

The model **requires enumerating EVERY code present in the file** and reporting a per-code count — including undocumented codes — not just the 7 configured codes.

- The product `MultiRecordValidator` (`src/validators/multi_record_validator.py`), under `SHAW_ATOCTRAN.yaml` `default_action: error`, emits a **per-line** `CT_UNKNOWN` / unknown-record-type error for any code not in `record_types`. It does **not group or count** undocumented codes into a per-code tally.
- The harness `db_truth_comparator` produces `rows_unknown_type` (a single aggregate counter) and `per_type_counts: Mapping[str, PerTypeCount]` keyed by **configured** record types only (`db_truth_comparator.py` lines 199–208, 137–150). It does not break out per-undocumented-code counts either.
- **Therefore:** per-code enumeration (group-by `TRANSACTION-CODE`, count each distinct value, including codes absent from config such as a stray `650`) is a **harness responsibility** — a `db_truth_comparator` `PerTypeCount`-style tally extended to *all observed discriminator values*, not just configured ones. State clearly in Phase 3 that this is harness-side, since neither the product validator nor the current comparator groups undocumented codes.

### 3.2 Code-650 handling rule (explicit)

650 does **not** exist in config. The model rule: **enumerate-and-report-if-present** — if any `atoctran_shaw_*.txt` line has `TRANSACTION-CODE == "650"`, the harness must report its count (it will also surface as `unknown_record_type` / `CT_UNKNOWN` under `default_action: error`). **Open Question (OQ-650):** confirm whether 650 was renamed to 605 or is genuinely absent from the SHAW scope. Do **not** author 650 as a configured code until BA confirms.

---

## 4. CROSS-FILE INVARIANT (200 → contact AND contact-account)

**Rule:** every `atoctran` type-200 record's **account number** must exist in **BOTH** the `contact_shaw_*.txt` file **AND** the `contact-account_shaw_*.txt` file. This is a **3-way referential integrity check** (200 → contact ∧ 200 → contact-account).

- **Key field in the 200 layout:** `ACCT-NUM`, **position 7, length 18**, string (`config/mappings/SHAW_ATOCTRAN_200_mapping.json`, lines 44–62). (Note: the 200 mapping also carries `CUSTOMER-PORTFOLIO ID` pos 43 len 18 and `CUSTOMER-PORTFOLIO-CONTACT-ID` pos 61 len 24, used only for portfolio accounts — the referential key is `ACCT-NUM`, not these.)
- **Join key in contact / contact-account:** **OPEN QUESTION** — both `SHAW_CONTACT.json` and `SHAW_CONTACT_ACCOUNT.json` are **TODO stubs** (`total_record_length: 1`, single `TODO_FIELD_1_*`; Phase 1 §2.4). The account-number position/length in those files is **not available** and must **not** be invented.
- **No product primitive exists** for a 3-way file-to-file referential check (Phase-0 capability finding; `db_truth_comparator` reconciles file-vs-SQL per record type, not file-vs-file-vs-file). → **harness-implemented** set-membership assertion: `set(ACCT-NUM where code=200) ⊆ set(contact keys) ∩ set(contact-account keys)`.

### Cross-file Open Questions
- **OQ-X1:** account-number position/length in `contact_shaw_*.txt` (contact stub).
- **OQ-X2:** account-number position/length in `contact-account_shaw_*.txt` (stub).
- **OQ-X3:** whether the contact files are multi-record (CONTACT discriminator `RECORD_TYPE` "TBC", Phase 1 OQ #8) — if so, which record type carries the account key.

---

## 5. CONCAT-MERGE INTEGRITY (5.9)

**Rule:** the final merged file's count is an exact-duplicate-aware sum of its per-source inputs:

```
merged_count == Σ(per-source file row counts)  −  exact_duplicate_line_count
```

- `contact` and `contact-account` are merged **separately** (two independent merge streams).
- **No field-level dedup** — only **exact whole-line** duplicates are removed.
- **No re-sort** — merge preserves input order; the assertion must not assume sorted output.
- **No Valdo product primitive** performs concat-merge or merge-count reconciliation (`db_truth_comparator` is file-vs-SQL, not multi-file concat). → **harness / shell assertion** (e.g. `wc -l` per source minus an exact-line dedup count, compared to the merged file's `wc -l`).

### Concat-merge Open Questions
- **OQ-M1:** the exact set of per-source inputs that feed each merged `contact` / `contact-account` output (not enumerated in `SHAW.yml`).
- **OQ-M2:** definition of "exact duplicate line" — full byte-for-byte line including trailing pad, or trimmed? Undocumented.

---

## 6. STAGING-LOAD RECONCILE (5.2) MODEL

**Rule (per pipe-delimited source file → staging table):**

```
staging_count == file_rows − 1   (the single header line)
```

- **Truncate-load semantics:** the load truncates before insert, so there is **no residue** from prior runs — `staging_count` reflects only the current file. (Truncate-load is the design intent per the load wrapper; the wrapper `load_SHAW.sh` is referenced at `config/e2e/sources/SHAW.yml` line 31 but its truncate behavior is not transcribed here → see OQ-S2.)
- **`file_rows − 1`** assumes exactly one header row per pipe-delimited input. Phase 1 flagged header presence for the staging inputs as **OQ** (the input_file stubs carry no header concept). The `−1` is the spec's stated rule; if a file has no header, the rule is `staging_count == file_rows`.
- Driven by the `file_to_staging` gate (`SHAW.yml` line 223, `blocking: true`). The gate's `thresholds.max_errors` defaults to **0** (zero tolerance for load drift; `SHAW.yml` input_files header comment, lines 52–55).

**Known staging tables (from `SHAW.yml` `staging_tables` lines 42–48 and `input_files` mappings):**

| Input file (glob) | Target staging table | Source |
|---|---|---|
| `collateral-master_*.txt` | `SHAW_COLLATERAL` | `SHAW.yml` line 57–60 |
| `fee-master_*.txt` | `SHAW_FEE_MASTER` | line 62–65 |
| `loans-master_*.txt` | `SHAW_LOAN_MASTER` | line 67–70 |
| `loans-name_*.txt` | `SHAW_LOANS_NAME` | line 72–75 |
| `posted-trans_*.txt` | `SHAW_TRANSACTIONS` | line 77–80 |
| `history_*.txt` | `SHAW_TRANS_MASTER` | line 82–85 |

**Open Question:** the `SHAW.yml` comment (lines 36–41) states "Remaining sqlload scripts in `load_SHAW.sh` are load-only (no input file delivered yet)" — so there may be **additional staging tables loaded by the wrapper that are NOT in the 6-row `input_files`/`staging_tables` set**. Those are **OQ** for staging-load recon (no input file to count against). All 6 above are pipe-delimited per the prompt; the actual delimiter is not transcribed from the (stub) mappings → OQ-S1.

### Staging-load Open Questions
- **OQ-S1:** confirm pipe-delimiter and header-row presence for each of the 6 input files (stub mappings give no delimiter/header).
- **OQ-S2:** confirm `load_SHAW.sh` truncate-load behavior (truncate-before-insert) — referenced but not transcribed.
- **OQ-S3:** enumerate staging tables loaded by the wrapper beyond the 6 declared (load-only, no input file).

---

## 7. OPEN QUESTIONS — rolled forward + new

### Rolled forward from Phase 1 (still open)
| # | Gap | Status in Phase 2 |
|---|---|---|
| P1-#1 | Logical type (a-doc-tran / financial-extract / CDS / TriNet) is not an explicit config attribute | Unchanged — does not block the count model. |
| P1-#2 | ATOCTRAN driving staging table(s) + join key(s) + per-code cardinality undefined (no `atoctran.yml`) | **Central to §3** — all per-code driver/key/cardinality rows are OQ. |
| P1-#3 | Consolidated-master-count anchor + audit table + count column do not exist in config/harness | **Addressed by §1's user decision**, but `AUDIT_SQL_LOADER` and `<count_col>` still absent from repo → OQ-A1. |
| P1-#4 | Code 650 absent from config | **OQ-650** in §3.2. |
| P1-#5 | No variance bands / tolerances (strict string equality only) | **Central to §2** — every non-`==BASE` band is OQ. |
| P1-#6 | P327 two conflicting artifacts (wired stub vs unwired 252-field layout) | §2 — P327 count recon possible, field recon blocked. |
| P1-#7 | 11 CDSTRANS + CONTACT + CONTACT_ACCOUNT + wired P327 have no layout | §2/§4 — count recon only; field/key recon blocked. |
| P1-#8 | CONTACT discriminator (`RECORD_TYPE` TBC) unconfirmed | OQ-X3 in §4. |
| P1-#9 | ATOCTRAN/CDS output driving tables OQ (load side resolved) | Ties to §3 OQ. |
| P1-#10 | DB query mechanism (Oracle via `OracleTruthSource`) | Resolved — and §1.2 confirms `DataExtractor`/CLI/MCP extract paths for the anchor. |

### New Open Questions from the Phase 2 model
- **OQ-A1:** `AUDIT_SQL_LOADER.<count_col>` exact column name — not in repo.
- **OQ-A2:** "`SHAW_LOAN_MASTER` after parent/child consolidation" — same table at a later step vs distinct consolidated table/view; no consolidation DDL in config.
- **OQ-A3:** case-sensitivity / exact casing of `source_system='shaw'` and `table_name='shaw_loan_master'` predicates.
- **OQ-650:** was ATOCTRAN code 650 renamed to 605, or genuinely absent?
- **OQ-B1 (variance bands):** every `ABOVE_BASE` / `BELOW_BASE` / `SPARSE_CONDITIONAL` file in §2 needs a BA/PO-supplied tolerance; the engine supports none today (strict equality).
- **OQ-B2:** classification of `contact` and `contact-account` relative to BASE (not given in worked example §4).
- **OQ-X1/X2/X3:** account-number key position in contact / contact-account stubs; multi-record status of contact.
- **OQ-M1/M2:** per-source merge inputs; exact-duplicate-line definition.
- **OQ-S1/S2/S3:** staging-input delimiter+header; wrapper truncate behavior; load-only staging tables beyond the 6.
- **OQ-300/605/700/100:** driving population for codes 300 (driver tables = history/posted-trans? join key?), and undocumented populations for 605/700/100.

---

### Bottom line
The reconciliation base is **`COUNT(*)` of the post-consolidation `SHAW_LOAN_MASTER`** (equivalently the `AUDIT_SQL_LOADER` row for `shaw`/`shaw_loan_master`), run via `DataExtractor.extract_by_query` / `valdo extract --query` / MCP `extract_table(query=…)`. Per-file count classes are evidence-backed only for the **`== BASE`** (at-base) files; every above/below/sparse band is an **Open Question** (the engine does strict string equality, no tolerances). ATOCTRAN reconciles **per-code**, and per-code enumeration of **all** observed discriminator values (including undocumented codes like a stray 650) is a **harness responsibility** because neither `MultiRecordValidator` nor the current `db_truth_comparator` groups undocumented codes. The 200→contact∧contact-account invariant, the concat-merge identity, and the staging `file_rows−1` rule all have **no product primitive** and are harness/shell-implemented. No tolerances, join keys, or rules were invented — gaps are recorded above.
