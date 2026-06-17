# E2E SHAW — Phase 3 Test-Case Documentation (Collections Interfaces ETL)

**Scope:** Collections Interfaces (ETL) process, source = `shaw`. This document authors the **end-to-end test cases** for the SHAW batch, organized by pipeline stage (Sections 5.1–5.9). It is **test-case documentation only — no production code is written here.**

Repo root: `/Users/pavankanduri/claude-ws/valdo/valdo-feature-valdo-engine-v3`

**Grounding & discipline.** Every case cites the config/harness file it derives from. Layouts, join keys, and tolerances that are **not** documented in config are marked `OPEN QUESTION` and are **never invented**. The two prior phase documents are the source of grounded facts and Open Questions:
- `docs/testing/E2E_SHAW_PHASE1_INVENTORY.md` (file inventory, fixed-width layouts, multi-record model, harness reconciliation engine).
- `docs/testing/E2E_SHAW_PHASE2_RECON_MODEL.md` (anchor decision, per-file variance classes, per-code model, cross-file invariant, concat-merge, staging-load model).

## Locked decisions applied (do not re-litigate)

1. **Anchor (BASE).** BASE = `COUNT(*)` of `SHAW_LOAN_MASTER` post-consolidation (schema-qualified `APP_INT.SHAW_LOAN_MASTER`), OR the `AUDIT_SQL_LOADER` row `WHERE source_system='shaw' AND table_name='shaw_loan_master'`. Run via `valdo extract --query` / MCP `extract_table` query mode. Exact `AUDIT_SQL_LOADER` count column = **OPEN QUESTION (OQ-A1)**.
2. **Variance bands undocumented.** For `ABOVE_BASE` (`eac_collateral`, `xpr`), `BELOW_BASE` (`efb`, `efi`), `SPARSE_CONDITIONAL` (`hss`, `rlt`): assert **direction only** (`count>BASE` / `count<BASE` / `0<=count<<BASE`) and **report actual count + %delta-from-BASE**; **no hard fail on magnitude**. For `AT_BASE` (`efw_fee_waivers`, `efx`, `esa`, `est`, `sec`, `p327`): **hard-assert `count == BASE`**. `atoctran` = `MULTI_RECORD_SUM` (per-code). `tranert` = per-type via `tranert.yml`.
3. **P327 layout** = `config/mappings/P327_SHAW_M06_mapping.json` (252 fields, `total_record_length: 2809`, first `LOCATION-CODE` 1/6, last `HMDA-ULI-CODE` 2765/45). The 1-field wired `SHAW_P327.json` stub is ignored for layout purposes.
4. **atoctran code 650** (in spec, absent from config): enumerate-and-report case; OPEN QUESTION whether renamed to 605 (OQ-650).
5. **Per-code enumeration** of ALL codes present (incl. undocumented) = **harness responsibility** (`db_truth_comparator` `PerTypeCount` extended to all observed discriminator values). Product `MultiRecordValidator` only flags unknown lines per `default_action: error`; it does not count per-code.
6. **Cross-file 200→contact ∧ contact-account invariant** and **concat-merge integrity** = **harness-implemented** (no product primitive). Authored with precise assertions plus a note they run in the harness, not via one CLI/MCP call.

## Path & filename conventions (parameterized)

- Source input dir: `/app/software/ftp/input/<source>/` — source files suffixed `batch_date`.
- Output dir: `/app/software/CACS/ftp/input/<source>/` — output files suffixed `run_date`.
  - (Note: `config/e2e/sources/SHAW.yml` line 24 declares `output_root: /app/software/APPS/ftp/input/shaw`. The prompt-specified output dir `/app/software/CACS/ftp/input/<source>/` is used per instruction; the `APPS` vs `CACS` discrepancy is **OPEN QUESTION (OQ-PATH)**.)
- `<source>` = `shaw` throughout. All globs come from `config/e2e/sources/SHAW.yml` `input_files:` / `output_files:`.

## Execution surfaces (legend)

- **MCP** — runnable via an MCP tool: `validate_file`, `db_compare`, `extract_table`, `run_etl_pipeline`.
- **CLI/REST** — runnable via `valdo` CLI / FastAPI only (no dedicated MCP tool, or orchestrator-driven).
- **harness-only** — no single product primitive exists; implemented as an E2E harness/shell assertion (`db_truth_comparator` extension, `wc -l`, set-membership, etc.).

---

## 5.1 Source receipt

Confirms the expected source-file set arrived for `batch_date`, each carries a header row, and each is non-empty. These are the precondition for every downstream stage; failure must be loud (missing = hard fail), not a silent zero-row pass. The expected source-file set is the 6 `input_files[]` globs in `config/e2e/sources/SHAW.yml` (lines 56–85). Output files (the 15 `output_files[]` entries) are receipt-checked at 5.5, not here.

```
Test Case ID:        TC-INT-SHAW-001
Title:               All expected SHAW source input files present for batch_date
Logical/Physical file: staging inputs (6) / collateral-master_<batch_date>.txt, fee-master_<batch_date>.txt, loans-master_<batch_date>.txt, loans-name_<batch_date>.txt, posted-trans_<batch_date>.txt, history_<batch_date>.txt
Parameters:          source=shaw, batch_date
Preconditions:       SHAW SFTP delivery for batch_date completed; input dir /app/software/ftp/input/shaw/ readable.
Steps:               1. harness: for each input_files[] glob in config/e2e/sources/SHAW.yml, resolve the batch_date-suffixed filename under /app/software/ftp/input/shaw/.
                     2. harness: assert exactly one file matches each of the 6 globs.
                     3. harness: list any glob with zero matches and any unexpected extra file.
Expected Result:     All 6 expected source files are present for batch_date; no required glob unmatched.
Pass/Fail Criteria:  PASS iff each of the 6 input_files[] globs matches >=1 file for batch_date. FAIL (loud) if any required glob has zero matches; report the missing file_type(s) by name.
Mapping Reference:   config/e2e/sources/SHAW.yml input_files[] (lines 56-85): COLLATERAL_MASTER, FEE_MASTER, LOAN_MASTER, LOANS_NAME, POSTED_TRANS, TRANS_MASTER. OPEN QUESTION (OQ-S3): load_SHAW.sh may load additional load-only staging tables with no delivered input file (SHAW.yml lines 36-41) — those are out of scope for this presence check.
Execution surface:   harness-only (filesystem presence check; no product primitive enumerates a source-file set).
```

```
Test Case ID:        TC-INT-SHAW-002
Title:               Each SHAW source file has a header row and at least one data row
Logical/Physical file: staging inputs (6) / <input glob>_<batch_date>.txt
Parameters:          source=shaw, batch_date
Preconditions:       TC-INT-SHAW-001 passed (all 6 files present).
Steps:               1. harness: for each of the 6 input files, read line count via wc -l.
                     2. harness: assert line count >= 2 (1 header + >=1 data row) under the file_rows-1 staging model (Phase 2 Section 6).
                     3. harness: capture the first line as the candidate header for the 5.2 staging-count rule.
Expected Result:     Every source file has >= 2 lines (header present, non-zero data rows).
Pass/Fail Criteria:  PASS iff every source file line count >= 2. FAIL (loud) if any file is empty or header-only (line count < 2); report the file_type and observed line count.
Mapping Reference:   Phase 2 Section 6 staging rule "staging_count == file_rows - 1 (the single header line)". OPEN QUESTION (OQ-S1): header-row presence + delimiter are not confirmed in config (input mappings are TODO stubs). If a file genuinely has no header, the rule degrades to file_rows and >=1 line; this case assumes the documented one-header model.
Execution surface:   harness-only (line-count assertion; no product primitive for source receipt).
```


## 5.2 SQL*Load reconcile (file -> staging)

Each pipe-delimited source file is SQL*Loaded into its target staging table. The reconciliation rule (Phase 2 Section 6): `staging_count == file_rows - 1` (one header line). The load is truncate-before-insert, so a re-run must leave no residue from a prior batch. Loader reject/discard files must be captured and asserted empty. This is the `file_to_staging` gate (`SHAW.yml` line 223, `blocking: true`, `thresholds.max_errors` default 0). Staging table names are grounded (`SHAW.yml` `staging_tables` lines 42–48 + `input_files[].target_staging_table`).

```
Test Case ID:        TC-INT-SHAW-010
Title:               Per-file staging count equals file data rows (file_rows - 1)
Logical/Physical file: staging inputs (6) / <input glob>_<batch_date>.txt -> APP_INT.<target_staging_table>
Parameters:          source=shaw, batch_date, staging_schema=APP_INT
Preconditions:       TC-INT-SHAW-001/002 passed; file_to_staging gate executed (load_SHAW.sh ran).
Steps:               1. harness/CLI: count source data rows = wc -l of the input file minus 1 (header).
                     2. MCP extract_table (query mode): SELECT COUNT(*) FROM APP_INT.<target_staging_table>.
                     3. harness: assert staging COUNT(*) == (file_rows - 1) for each of the 6 file->table pairs.
Expected Result:     For each pair, staging row count == file_rows - 1.
Pass/Fail Criteria:  PASS iff staging_count == file_rows - 1 for all 6 pairs (zero tolerance; SHAW.yml file_to_staging max_errors default 0). FAIL if any pair differs; report table, expected, actual, delta.
Mapping Reference:   SHAW.yml input_files[]->target_staging_table (lines 56-85): collateral-master->SHAW_COLLATERAL, fee-master->SHAW_FEE_MASTER, loans-master->SHAW_LOAN_MASTER, loans-name->SHAW_LOANS_NAME, posted-trans->SHAW_TRANSACTIONS, history->SHAW_TRANS_MASTER. staging_schema APP_INT (SHAW.yml line 23). OPEN QUESTION (OQ-S1): pipe-delimiter + header presence unconfirmed (stub mappings); if no header, rule is staging_count == file_rows.
Execution surface:   MCP (extract_table query mode for the COUNT) + harness (file line count + comparison). The file_to_staging gate itself is orchestrator/CLI-driven (invoke_java via load_SHAW.sh).
```

```
Test Case ID:        TC-INT-SHAW-011
Title:               Truncate-load leaves no residue across re-runs
Logical/Physical file: staging inputs (6) / -> APP_INT.<target_staging_table>
Parameters:          source=shaw, batch_date, prior_batch_date
Preconditions:       A prior batch (prior_batch_date) was loaded into the same staging tables.
Steps:               1. harness: record staging COUNT(*) after prior_batch_date load.
                     2. harness: re-run load_SHAW.sh for batch_date (truncate-before-insert).
                     3. MCP extract_table (query mode): SELECT COUNT(*) FROM APP_INT.<target_staging_table> after the new load.
                     4. harness: assert new count == (batch_date file_rows - 1), independent of the prior batch count (no carryover rows).
Expected Result:     Staging count reflects ONLY batch_date's file; no rows from prior_batch_date remain.
Pass/Fail Criteria:  PASS iff post-reload staging_count == (batch_date file_rows - 1) for every table, with no additive residue. FAIL if count > current-file rows (residue) for any table.
Mapping Reference:   Phase 2 Section 6 truncate-load semantics. OPEN QUESTION (OQ-S2): load_SHAW.sh truncate-before-insert behavior referenced (SHAW.yml line 31) but not transcribed; if the wrapper appends rather than truncates, this case must be re-specified.
Execution surface:   MCP (extract_table query COUNT) + harness (re-run orchestration + comparison).
```

```
Test Case ID:        TC-INT-SHAW-012
Title:               SQL*Loader reject/discard files are empty (zero load errors)
Logical/Physical file: staging inputs (6) / SQL*Loader .bad / .dsc artifacts
Parameters:          source=shaw, batch_date
Preconditions:       file_to_staging gate executed.
Steps:               1. harness: locate the SQL*Loader .bad (reject) and .dsc (discard) files produced by load_SHAW.sh for each input file.
                     2. harness: assert each .bad and .dsc file is absent or zero-length.
                     3. harness: cross-check against the file_to_staging gate error count (SHAW.yml thresholds.max_errors default 0).
Expected Result:     No rejected or discarded rows for any of the 6 loads.
Pass/Fail Criteria:  PASS iff all reject/discard artifacts are empty AND the file_to_staging gate reports 0 errors. FAIL (blocking) if any reject/discard row exists; report the file_type and reject count.
Mapping Reference:   SHAW.yml gates.file_to_staging blocking:true (line 223); input_files header comment max_errors default 0 (lines 52-55). OPEN QUESTION (OQ-S2/OQ-LOG): exact .bad/.dsc path naming convention from load_SHAW.sh not transcribed.
Execution surface:   harness-only (loader artifact inspection; no product primitive parses SQL*Loader logs).
```


## 5.3 Consolidation + audit anchor (BASE)

Establishes BASE — the reconciliation anchor for 5.4 and 5.5 (decision #1). BASE = `COUNT(*)` of `SHAW_LOAN_MASTER` after parent/child consolidation, equivalently the `AUDIT_SQL_LOADER` row for `shaw`/`shaw_loan_master`. Both sources should agree; divergence is itself a finding (consolidation vs audit drift). The illustrative magnitude (116,802 for batch_date 20260608) is from the spec only and is never a hardcoded expectation (Phase 2 Section 1 — grep for the literal returned zero hits).

```
Test Case ID:        TC-INT-SHAW-020
Title:               Consolidation step ran before BASE is read
Logical/Physical file: n/a (DB state) / APP_INT.SHAW_LOAN_MASTER
Parameters:          source=shaw, batch_date
Preconditions:       file_to_staging passed for loans-master (TC-INT-SHAW-010).
Steps:               1. harness: confirm the parent/child consolidation step has completed for batch_date before reading BASE (so child rows are merged into SHAW_LOAN_MASTER).
                     2. MCP extract_table (query mode): SELECT COUNT(*) FROM APP_INT.SHAW_LOAN_MASTER.
Expected Result:     SHAW_LOAN_MASTER is in its post-consolidation state; the count is stable (re-reading returns the same value).
Pass/Fail Criteria:  PASS iff consolidation completed and the count is reproducible. FAIL if BASE is read before consolidation (parent/child not merged).
Mapping Reference:   Phase 2 Section 1.1 Source A. OPEN QUESTION (OQ-A2): whether "SHAW_LOAN_MASTER after parent/child consolidation" is the same physical table at a later step or a distinct consolidated table/view — no consolidation DDL in config; SHAW_LOAN_MASTER mapping is a TODO stub (Phase 1 Section 2.4).
Execution surface:   MCP (extract_table query COUNT) + harness (ordering/precondition check).
```

```
Test Case ID:        TC-INT-SHAW-021
Title:               BASE = COUNT(*) of post-consolidation SHAW_LOAN_MASTER (Source A)
Logical/Physical file: n/a (DB anchor) / APP_INT.SHAW_LOAN_MASTER
Parameters:          source=shaw, batch_date, staging_schema=APP_INT
Preconditions:       TC-INT-SHAW-020 passed.
Steps:               1. MCP extract_table (query mode) OR valdo extract --query "SELECT COUNT(*) FROM APP_INT.SHAW_LOAN_MASTER".
                     2. harness: capture the returned row_count as BASE for use by 5.4 and 5.5.
Expected Result:     A single integer BASE is obtained and recorded for batch_date.
Pass/Fail Criteria:  PASS iff a non-null, non-zero BASE is returned and persisted for the run. FAIL if the query errors or returns 0.
Mapping Reference:   Phase 2 Section 1.1 Source A + Section 1.2 (DataExtractor.extract_by_query, src/database/extractor.py line 209; MCP extract_table query mode, src/mcp/extract_tools.py). Magnitude 116,802 is illustrative only (spec), never hardcoded.
Execution surface:   MCP (extract_table query mode) / CLI (valdo extract --query).
```

```
Test Case ID:        TC-INT-SHAW-022
Title:               BASE cross-check against AUDIT_SQL_LOADER (Source B) agrees with Source A
Logical/Physical file: n/a (DB anchor) / AUDIT_SQL_LOADER
Parameters:          source=shaw, batch_date
Preconditions:       TC-INT-SHAW-021 produced BASE (Source A).
Steps:               1. MCP extract_table (query mode): SELECT <count_col> FROM AUDIT_SQL_LOADER WHERE source_system='shaw' AND table_name='shaw_loan_master'.
                     2. harness: assert Source B count == Source A BASE.
Expected Result:     The audit-table count equals the post-consolidation SHAW_LOAN_MASTER count.
Pass/Fail Criteria:  PASS iff Source B == Source A. FAIL (finding) if they diverge; report both values and delta as a consolidation-vs-audit drift.
Mapping Reference:   Phase 2 Section 1.1 Source B. OPEN QUESTION (OQ-A1): AUDIT_SQL_LOADER count column name <count_col> NOT in repo (grep audit_sql_loader -> no hits). OPEN QUESTION (OQ-A3): case-sensitivity / exact casing of source_system='shaw' and table_name='shaw_loan_master' predicates unconfirmed. This case is Open-Question-parameterized: it cannot run until <count_col> is supplied.
Execution surface:   MCP (extract_table query mode) / CLI (valdo extract --query) — blocked pending OQ-A1.
```


## 5.4 atoctran per-code reconciliation

`atoctran_shaw_<run_date>.txt` is a multi-record file (umbrella `config/mappings/SHAW_ATOCTRAN.yaml`), discriminator `TRANSACTION-CODE` at **pos 25 len 3**. Whole-file count is `MULTI_RECORD_SUM` (does not reconcile to a single BASE number); reconciliation is **per-code**. Configured codes: `060, 100, 200, 300, 605, 700, 900` (`SHAW_ATOCTRAN.yaml` `record_types`). `default_action: error` → unknown codes fail loudly. **No `atoctran.yml` reconciliation spec exists** (only `tranert.yml`), so per-code driving table + join key + cardinality are **OPEN QUESTION (OQ-2)** for most codes; only directional/structural assertions are evidence-backed where the spec states a population.

```
Test Case ID:        TC-INT-SHAW-030
Title:               Enumerate ALL TRANSACTION-CODE values present and report per-code counts
Logical/Physical file: a-doc-tran (multi-record) / atoctran_shaw_<run_date>.txt
Parameters:          source=shaw, run_date
Preconditions:       atoctran output file generated for run_date.
Steps:               1. harness: group-by the discriminator at pos 25 len 3 over every line; count each distinct value (including codes NOT in config such as a stray 650).
                     2. harness: report the per-code tally and flag any code not in {060,100,200,300,605,700,900}.
                     3. harness: assert whole-file line count == sum of all per-code counts (MULTI_RECORD_SUM identity).
Expected Result:     A complete per-code histogram; sum of per-code counts == total lines; unknown codes surfaced explicitly.
Pass/Fail Criteria:  PASS iff the per-code sum equals the file line count AND every observed code is enumerated. Report (do not silently drop) any undocumented code. (Unknown codes additionally hard-fail L1 under SHAW_ATOCTRAN.yaml default_action: error.)
Mapping Reference:   SHAW_ATOCTRAN.yaml discriminator (field TRANSACTION-CODE, position 25, length 3) + default_action: error. Phase 2 Section 3.1: per-code enumeration of ALL observed values is a HARNESS responsibility (db_truth_comparator PerTypeCount keyed only to configured types; MultiRecordValidator only flags unknown lines, does not count per-code).
Execution surface:   harness-only (group-by enumeration over all observed discriminator values). MCP validate_file/run_etl_pipeline will flag unknown codes but will not emit a per-code histogram including undocumented codes.
```

```
Test Case ID:        TC-INT-SHAW-031
Title:               atoctran code 060 count == BASE (one per consolidated master account)
Logical/Physical file: a-doc-tran rt_060 / atoctran_shaw_<run_date>.txt (TRANSACTION-CODE=060)
Parameters:          source=shaw, run_date, batch_date
Preconditions:       BASE established (TC-INT-SHAW-021); per-code histogram available (TC-INT-SHAW-030).
Steps:               1. harness: take per-code count for code 060 from TC-INT-SHAW-030.
                     2. harness: assert count(060) == BASE.
                     3. harness: report count(060), BASE, and delta.
Expected Result:     Exactly one type-060 record per consolidated master account.
Pass/Fail Criteria:  PASS iff count(060) == BASE. FAIL with reported delta otherwise.
Mapping Reference:   SHAW_ATOCTRAN.yaml rt_060.match "060"; SHAW_ATOCTRAN_060_mapping.json (total_record_length 35). Phase 2 Section 3: 060 driving population = "one per consolidated master account" (cardinality one_per_driver_row) is from SPEC, NOT config. OPEN QUESTION (OQ-2): driver table SHAW_LOAN_MASTER + join key ACCT-NUM (pos 7 len 18) not declared in any reconciliation YAML.
Execution surface:   harness-only (no atoctran.yml; per-code count vs BASE is a harness assertion).
```

```
Test Case ID:        TC-INT-SHAW-032
Title:               atoctran code 200 count == count of NEW accounts in batch (one per new account)
Logical/Physical file: a-doc-tran rt_200 / atoctran_shaw_<run_date>.txt (TRANSACTION-CODE=200)
Parameters:          source=shaw, run_date, batch_date
Preconditions:       per-code histogram available (TC-INT-SHAW-030); definition of "new account in batch_date" supplied.
Steps:               1. harness: take per-code count for 200 from TC-INT-SHAW-030.
                     2. harness: assert count(200) == count of accounts new in batch_date (one per new account).
                     3. harness: report count(200) and the new-account driver count.
Expected Result:     Exactly one type-200 record per new account.
Pass/Fail Criteria:  PASS iff count(200) == new-account count. FAIL with reported delta otherwise.
Mapping Reference:   SHAW_ATOCTRAN.yaml rt_200.match "200"; SHAW_ATOCTRAN_200_mapping.json (total_record_length 84, ACCT-NUM pos 7 len 18). Phase 2 Section 3: "one per NEW account in the batch" (one_per_driver_row) is from SPEC, NOT config. OPEN QUESTION (OQ-2): the "new-account" predicate + source table are undocumented; driver/join key not in a recon YAML.
Execution surface:   harness-only (driver-count source undefined in config -> Open-Question-parameterized).
```

```
Test Case ID:        TC-INT-SHAW-033
Title:               atoctran code 300 present as many-per-driver (credit/debit transactions)
Logical/Physical file: a-doc-tran rt_300 / atoctran_shaw_<run_date>.txt (TRANSACTION-CODE=300)
Parameters:          source=shaw, run_date, batch_date
Preconditions:       per-code histogram available (TC-INT-SHAW-030).
Steps:               1. harness: take per-code count for 300 from TC-INT-SHAW-030.
                     2. harness: report count(300); assert >= 0 (many-per-driver, no single BASE relation).
                     3. harness: (deferred) reconcile against transaction activity in SHAW_TRANS_MASTER / SHAW_TRANSACTIONS per account once a driver/key is defined.
Expected Result:     Type-300 count is a many-per-driver-row total of credit/debit transactions; reported, not equated to BASE.
Pass/Fail Criteria:  PASS iff count(300) is reported and >= 0 (no magnitude hard-fail). Driver reconciliation deferred pending OQ-2.
Mapping Reference:   SHAW_ATOCTRAN.yaml rt_300.match "300"; SHAW_ATOCTRAN_300_mapping.json (total_record_length 125; TRANSACTION-AMOUNT pos 36 len 18). Phase 2 Section 3: cardinality many_per_driver_row, driver tables plausibly SHAW_TRANS_MASTER + SHAW_TRANSACTIONS (SPEC, NOT config). OPEN QUESTION (OQ-2/OQ-300): driver tables + join key not declared.
Execution surface:   harness-only (driver undefined -> report-only assertion).
```

```
Test Case ID:        TC-INT-SHAW-034
Title:               atoctran code 900 count <= BASE (zero-or-one per account charging off on batch_date)
Logical/Physical file: a-doc-tran rt_900 / atoctran_shaw_<run_date>.txt (TRANSACTION-CODE=900)
Parameters:          source=shaw, run_date, batch_date
Preconditions:       BASE established; per-code histogram available (TC-INT-SHAW-030).
Steps:               1. harness: take per-code count for 900 from TC-INT-SHAW-030.
                     2. harness: assert 0 <= count(900) <= BASE (an account charges off at most once on a batch_date).
                     3. harness: report count(900), BASE, and ratio.
Expected Result:     Type-900 count is at most one per master (charge-off accounts only) and never exceeds BASE.
Pass/Fail Criteria:  PASS iff 0 <= count(900) <= BASE. FAIL if count(900) > BASE (impossible under zero-or-one cardinality).
Mapping Reference:   SHAW_ATOCTRAN.yaml rt_900.match "900"; SHAW_ATOCTRAN_900_mapping.json (total_record_length 53; TRANSACTION-AMT pos 36 len 18). Phase 2 Section 3: zero_or_one_per_driver_row, driver = SHAW_LOAN_MASTER filtered to charge-off on batch_date (SPEC, NOT config). OPEN QUESTION (OQ-2): charge-off predicate column undocumented for ATOCTRAN (cf. TRANERT rt_32010 CHG_OFF_CD='1', a different file).
Execution surface:   harness-only (charge-off driver undefined -> directional/bound assertion only).
```

```
Test Case ID:        TC-INT-SHAW-035
Title:               atoctran codes 100 / 605 / 700 present and counted (driving population undocumented)
Logical/Physical file: a-doc-tran rt_100/rt_605/rt_700 / atoctran_shaw_<run_date>.txt
Parameters:          source=shaw, run_date
Preconditions:       per-code histogram available (TC-INT-SHAW-030).
Steps:               1. harness: take per-code counts for 100, 605, 700 from TC-INT-SHAW-030.
                     2. harness: report each count; assert each line of these codes parses cleanly against its per-type layout (structure checked in 5.6).
                     3. harness: do NOT assert any count magnitude (driving population undocumented).
Expected Result:     Codes 100/605/700 are recognized configured codes; counts reported; no magnitude assertion.
Pass/Fail Criteria:  PASS iff each code (when present) is a configured code parsing without unknown-code errors. No count threshold applied.
Mapping Reference:   SHAW_ATOCTRAN.yaml rt_100/rt_605/rt_700; mappings 100 (total_record_length 75), 605 (83), 700 (122). Phase 2 Section 3: driving population for 100/605/700 is OPEN QUESTION (OQ-300/605/700/100) — not stated in spec or config. 605 carries NEW-PORTFOLIO-* (reassignment); 700 carries NG-CHECK-AMOUNT + third-party fields.
Execution surface:   harness-only (report counts) + MCP (validate_file --multi-record confirms they are configured codes).
```

```
Test Case ID:        TC-INT-SHAW-036
Title:               atoctran code 650 enumerate-and-report (absent from config)
Logical/Physical file: a-doc-tran (unconfigured) / atoctran_shaw_<run_date>.txt (TRANSACTION-CODE=650)
Parameters:          source=shaw, run_date
Preconditions:       per-code histogram available (TC-INT-SHAW-030).
Steps:               1. harness: from TC-INT-SHAW-030, check whether code 650 appears.
                     2. harness: if present, report count(650) and flag it as an unconfigured code.
                     3. MCP/CLI: confirm that under SHAW_ATOCTRAN.yaml default_action: error, any 650 line raises an unknown-record-type / CT_UNKNOWN error at L1.
Expected Result:     If 650 occurs it is reported with its count and surfaced as an unknown code (not silently passed). If absent, report count 0.
Pass/Fail Criteria:  REPORT-ONLY for enumeration; L1 hard-fails any 650 line under default_action: error. Either outcome is captured, not masked.
Mapping Reference:   Phase 1 OQ #4 / Phase 2 Section 3.2: no SHAW_ATOCTRAN_650_* artifact exists; 650 not in SHAW_ATOCTRAN.yaml. OPEN QUESTION (OQ-650): was 650 renamed to 605, or genuinely absent from SHAW scope? Do NOT author 650 as a configured code until BA confirms.
Execution surface:   harness-only (enumeration) + MCP (validate_file/run_etl_pipeline surfaces the unknown-code error).
```


## 5.5 Output count reconcile (per output file by recon class + variance decision #2)

One case per output file, grouped by Phase 2 reconciliation class. **AT_BASE** files hard-assert `count == BASE`. **ABOVE_BASE / BELOW_BASE / SPARSE_CONDITIONAL** assert **direction only** and report actual count + %delta-from-BASE (no magnitude hard-fail — bands are OPEN QUESTION; engine does strict equality, no tolerances, Phase 2 Section 2). `atoctran` (MULTI_RECORD_SUM) is covered in 5.4; `tranert` per-type is covered by `tranert.yml` (L2b). `contact` / `contact-account` are unclassified relative to BASE (OQ-B2). All output files are receipt-checked here too (present + header-where-applicable + non-zero). Output filenames are `run_date`-suffixed under `/app/software/CACS/ftp/input/shaw/`.

```
Test Case ID:        TC-INT-SHAW-040
Title:               AT_BASE CDS/P327 files: count == BASE
Logical/Physical file: CDS + P327 (AT_BASE set) / cdstrans_efw_fee_waivers_shaw_<run_date>.txt, cdstrans_efx_shaw_<run_date>.txt, cdstrans_esa_shaw_<run_date>.txt, cdstrans_est_shaw_<run_date>.txt, cdstrans_sec_shaw_<run_date>.txt, p327_shaw_<run_date>.txt
Parameters:          source=shaw, run_date, batch_date
Preconditions:       BASE established (TC-INT-SHAW-021); each output file present for run_date.
Steps:               1. harness/CLI: count rows of each AT_BASE output file (record-length-aware count; see 5.6 for fixed-width record length).
                     2. harness: assert count == BASE for each file.
                     3. harness: report each file's count, BASE, and delta.
Expected Result:     Each AT_BASE file has exactly BASE records (one per consolidated master account).
Pass/Fail Criteria:  PASS iff count == BASE for ALL six AT_BASE files. FAIL (hard) for any file where count != BASE; report file, count, BASE, delta.
Mapping Reference:   Phase 2 Section 2 AT_BASE rows (efw_fee_waivers, efx, esa, est, sec, p327). globs from SHAW.yml output_files[] (efw lines 138-141, efx 143-146, esa 148-151, est 153-156, sec 168-171, p327 197-200). NOTE: CDS mappings are TODO stubs (count recon only, field recon blocked). P327 layout = P327_SHAW_M06_mapping.json (record length 2809) for the record-length-aware count; wired SHAW_P327.json stub is ignored (OQ-6).
Execution surface:   MCP (extract_table query mode for BASE) + harness (per-file row count + == BASE assertion). No product primitive does whole-file == BASE.
```

```
Test Case ID:        TC-INT-SHAW-041
Title:               ABOVE_BASE CDS files: count > BASE (direction only)
Logical/Physical file: CDS (ABOVE_BASE) / cdstrans_eac_collateral_shaw_<run_date>.txt, cdstrans_xpr_shaw_<run_date>.txt
Parameters:          source=shaw, run_date, batch_date
Preconditions:       BASE established; both files present.
Steps:               1. harness: count rows of each ABOVE_BASE file.
                     2. harness: assert count > BASE (direction only).
                     3. harness: REPORT count, BASE, and %delta-from-BASE; do NOT hard-fail on magnitude.
Expected Result:     Each file has more rows than BASE (an account may have multiple collateral / xpr items).
Pass/Fail Criteria:  PASS iff count > BASE for both files. FAIL only on wrong direction (count <= BASE). Magnitude/%delta is reported, never a fail criterion.
Mapping Reference:   Phase 2 Section 2 ABOVE_BASE (eac_collateral, xpr); globs SHAW.yml lines 123-126, 173-176. OPEN QUESTION (OQ-B1): upper variance band undocumented; engine has no tolerance (db_truth_comparator strict equality). CDS mappings are TODO stubs (count recon only).
Execution surface:   MCP (extract_table for BASE) + harness (row count + directional assertion + %delta report).
```

```
Test Case ID:        TC-INT-SHAW-042
Title:               BELOW_BASE CDS files: count < BASE (direction only)
Logical/Physical file: CDS (BELOW_BASE) / cdstrans_efb_shaw_<run_date>.txt, cdstrans_efi_shaw_<run_date>.txt
Parameters:          source=shaw, run_date, batch_date
Preconditions:       BASE established; both files present.
Steps:               1. harness: count rows of each BELOW_BASE file.
                     2. harness: assert count < BASE (direction only).
                     3. harness: REPORT count, BASE, and %delta-from-BASE; no magnitude hard-fail.
Expected Result:     Each file has fewer rows than BASE (subset of accounts qualifies).
Pass/Fail Criteria:  PASS iff count < BASE for both files. FAIL only on wrong direction (count >= BASE). %delta reported, never a fail criterion.
Mapping Reference:   Phase 2 Section 2 BELOW_BASE (efb, efi); globs SHAW.yml lines 128-131, 133-136. Worked-example delta for efb (114,875 vs 116,802 = -1.65%) is SPEC illustrative only, NOT a sanctioned tolerance. OPEN QUESTION (OQ-B1): lower band undocumented.
Execution surface:   MCP (extract_table for BASE) + harness (row count + directional assertion + %delta report).
```

```
Test Case ID:        TC-INT-SHAW-043
Title:               SPARSE_CONDITIONAL CDS files: 0 <= count << BASE (direction only)
Logical/Physical file: CDS (SPARSE_CONDITIONAL) / cdstrans_hss_shaw_<run_date>.txt, cdstrans_rlt_shaw_<run_date>.txt
Parameters:          source=shaw, run_date, batch_date
Preconditions:       BASE established; both files present (may be empty by design).
Steps:               1. harness: count rows of each sparse file.
                     2. harness: assert 0 <= count and count << BASE (much smaller than BASE; populated only when the condition holds).
                     3. harness: REPORT count, BASE, %delta; tolerate zero rows.
Expected Result:     Each file is sparse (zero or far fewer rows than BASE).
Pass/Fail Criteria:  PASS iff 0 <= count and count is small relative to BASE (direction). FAIL only if count >= BASE (not sparse). No magnitude hard-fail; zero rows is acceptable.
Mapping Reference:   Phase 2 Section 2 SPARSE_CONDITIONAL (hss, rlt); globs SHAW.yml lines 158-161, 163-166. OPEN QUESTION (OQ-B1): the HSS/RLT populating condition/predicate is undocumented (stub mappings).
Execution surface:   MCP (extract_table for BASE) + harness (row count + sparse-direction assertion + %delta report).
```

```
Test Case ID:        TC-INT-SHAW-044
Title:               tranert per-type counts reconcile via tranert.yml (L2b SQL-truth)
Logical/Physical file: financial extract (multi-record) / tranert_shaw_<run_date>.txt
Parameters:          source=shaw, run_date, batch_date
Preconditions:       tranert output present; Oracle reachable (ORACLE_DSN/USER/PASSWORD); expected_*.sql materialized in APP_INT.
Steps:               1. orchestrator: run the L2b_sql_truth gate (db_truth_comparator) against config/e2e/sources/SHAW/reconciliation/tranert.yml.
                     2. harness: for each record type, assert PerTypeCount.file_rows == expected_rows and the configured cardinality holds.
                     3. harness: assert the batch_header_count assertion (header.ITM-CNT-BRT == sum(detail_row_counts)).
Expected Result:     Every tranert record type's file count matches its expected_*.sql count under its cardinality; header item-count equals total detail rows.
Pass/Fail Criteria:  PASS iff all per-type counts match, all cardinalities hold, and batch_header_count passes (L2b is blocking). FAIL (blocking) on any cardinality_violation / count mismatch / assertion_failed.
Mapping Reference:   tranert.yml (record types batch_header, rt_32000, rt_32005, rt_32010 [predicate CHG_OFF_CD='1'], rt_32025, rt_32040, rt_32075; keys/cardinalities/fields) + assertion batch_header_count. SHAW.yml gates.L2b_sql_truth blocking:true (line 232). Discriminator TRN-COD-ERT pos 170 len 5 (SHAW_TRANERT.yaml).
Execution surface:   MCP (run_etl_pipeline) / orchestrator-driven L2b (db_truth_comparator); count recon is fully grounded for tranert.
```

```
Test Case ID:        TC-INT-SHAW-045
Title:               contact / contact-account output receipt + non-zero (classification OPEN QUESTION)
Logical/Physical file: TriNet contact + contact-account / contact_shaw_<run_date>.txt, contact-account_shaw_<run_date>.txt
Parameters:          source=shaw, run_date, batch_date
Preconditions:       Both output files generated for run_date.
Steps:               1. harness: assert each file is present for run_date and non-empty.
                     2. harness: REPORT each row count and %delta-from-BASE (no class assertion).
Expected Result:     Both files present and non-zero; counts reported for analyst review.
Pass/Fail Criteria:  PASS iff both files present and non-empty. No BASE relation asserted.
Mapping Reference:   SHAW.yml output_files CONTACT (lines 178-191), CONTACT_ACCOUNT (lines 192-195). OPEN QUESTION (OQ-B2): classification relative to BASE not given in worked example. Mappings are TODO stubs (count/receipt recon only; field recon blocked).
Execution surface:   harness-only (presence + count report; class unclassified).
```


## 5.6 Structure / format (constant record length + per-field pos/len/type/format)

Fixed-width structural validation: every record is a constant length, and each field matches its declared position/length/type/format. **Grounded** for atoctran (per code, from `SHAW_ATOCTRAN_<code>_mapping.json`), tranert (per type, from `SHAW_TRANERT_<type>_mapping.json`), and p327 (`P327_SHAW_M06_mapping.json`, 252 fields). For the 11 CDS files + contact + contact-account, mappings are 1-field TODO stubs → layout is `OPEN QUESTION`; only a generic constant-record-length assertion is authored.

```
Test Case ID:        TC-INT-SHAW-050
Title:               atoctran per-code record length + field layout (grounded, all 7 codes)
Logical/Physical file: a-doc-tran (multi-record) / atoctran_shaw_<run_date>.txt
Parameters:          source=shaw, run_date
Preconditions:       atoctran output present; SHAW_ATOCTRAN.yaml + 7 per-code mapping JSONs loaded.
Steps:               1. MCP validate_file (--multi-record SHAW_ATOCTRAN.yaml): dispatch each line by TRANSACTION-CODE (pos 25 len 3).
                     2. harness/validator: assert each dispatched record's length equals its code's total_record_length: 060=35, 100=75, 200=84, 300=125, 605=83, 700=122, 900=53.
                     3. validator: assert per-field position/length/type/format for every field of each code (e.g. common header LOCATION-CODE 1/6, ACCT-NUM 7/18, TRANSACTION-CODE 25/3, TRANSACTION-DATE 28/8 date CCYYMMDD; 300 TRANSACTION-AMOUNT 36/18 decimal 9(12)V9(6); 900 TRANSACTION-AMT 36/18 decimal 9(12)V9(6)).
Expected Result:     Every atoctran line matches its code's fixed-width layout exactly (length + each field).
Pass/Fail Criteria:  PASS iff each record's length == its code total_record_length AND all field-level rules pass. FAIL (blocking L1_structural) on any length or field mismatch; unknown code -> CT_UNKNOWN under default_action: error.
Mapping Reference:   SHAW_ATOCTRAN_{060,100,200,300,605,700,900}_mapping.json (positions/lengths/types per Phase 1 Section 2.1); SHAW_ATOCTRAN.yaml discriminator. Layout fully GROUNDED.
Execution surface:   MCP (validate_file --multi-record) — L1_structural gate (SHAW.yml line 225, blocking).
```

```
Test Case ID:        TC-INT-SHAW-051
Title:               tranert per-type record length + field layout + header (grounded, all types)
Logical/Physical file: financial extract (multi-record) / tranert_shaw_<run_date>.txt
Parameters:          source=shaw, run_date
Preconditions:       tranert output present; SHAW_TRANERT.yaml + per-type mapping JSONs loaded.
Steps:               1. MCP validate_file (--multi-record SHAW_TRANERT.yaml): batch_header matched by position "first"; details dispatched by TRN-COD-ERT (pos 170 len 5).
                     2. validator: assert each record length equals its type total_record_length: batch_header=158, NEW1=205, CUS=336, ORI=636, COD=551, CBRS=524, REC=528.
                     3. validator: assert per-field pos/len/type for each type; assert ITM-CNT-BRT (batch_header pos 131 len 9) cross-type count rule.
Expected Result:     Every tranert record matches its type layout; header present as first record; item-count rule holds.
Pass/Fail Criteria:  PASS iff each record length == its type total_record_length AND field rules pass AND header_trailer_count holds. FAIL (blocking L1) on mismatch; unknown TRN-COD-ERT -> error (default_action: error). Codes 32030/32070 are commented-out TODOs (out of scope).
Mapping Reference:   SHAW_TRANERT_{BATCH_HEADER,NEW1,CUS,ORI,COD,CBRS,REC}_mapping.json (record lengths per Phase 1 Section 2.2); SHAW_TRANERT.yaml discriminator TRN-COD-ERT pos 170 len 5, batch_header position "first". Layout GROUNDED.
Execution surface:   MCP (validate_file --multi-record) — L1_structural gate (blocking).
```

```
Test Case ID:        TC-INT-SHAW-052
Title:               p327 constant record length 2809 + per-field layout (252 fields, grounded)
Logical/Physical file: TriNet/P327 / p327_shaw_<run_date>.txt
Parameters:          source=shaw, run_date
Preconditions:       p327 output present; canonical layout P327_SHAW_M06_mapping.json used.
Steps:               1. MCP validate_file (--mapping P327_SHAW_M06_mapping.json): parse fixed-width.
                     2. validator: assert every record is exactly 2809 chars.
                     3. validator: assert per-field pos/len/type for all 252 fields, anchored by first field LOCATION-CODE (1/6) and last field HMDA-ULI-CODE (2765/45, ending at 2809).
Expected Result:     Every p327 record is 2809 chars and all 252 fields match the canonical layout.
Pass/Fail Criteria:  PASS iff record length == 2809 AND all 252 field rules pass. FAIL (blocking L1) on any mismatch.
Mapping Reference:   P327_SHAW_M06_mapping.json (252 fields, total_record_length 2809, LOCATION-CODE 1/6 .. HMDA-ULI-CODE 2765/45; verified). OPEN QUESTION (OQ-6): SHAW.yml P327 entry points at the 1-field stub SHAW_P327.json, NOT this canonical layout — wiring must be re-pointed before run_etl_pipeline uses it; per decision #3 the canonical 252-field layout is used for authoring.
Execution surface:   MCP (validate_file --mapping) once SHAW.yml re-points P327 (OQ-6); until then validate_file must be invoked directly with P327_SHAW_M06_mapping.json (CLI/REST), not via the orchestrator gate.
```

```
Test Case ID:        TC-INT-SHAW-053
Title:               CDS files (11) constant record length only (layout OPEN QUESTION)
Logical/Physical file: CDS (11) / cdstrans_eac_collateral|efb|efi|efw_fee_waivers|efx|esa|est|hss|rlt|sec|xpr_shaw_<run_date>.txt
Parameters:          source=shaw, run_date
Preconditions:       Each CDS output file present.
Steps:               1. harness: for each CDS file, assert all records share one constant byte length (max line length == min line length).
                     2. harness: report the observed constant record length per file.
                     3. harness: do NOT assert per-field positions (mapping is a 1-field stub).
Expected Result:     Each CDS file is internally constant-width; observed record length reported per file.
Pass/Fail Criteria:  PASS iff every record within a file has identical length. FAIL if record lengths vary within a file. Per-field validation NOT performed.
Mapping Reference:   SHAW.yml output_files CDSTRANS_* (lines 123-176). OPEN QUESTION (OQ-7): layout not yet authored — all 11 SHAW_CDSTRANS_*.json mappings are TODO stubs (total_record_length 1, TODO_FIELD_1_*; Phase 1 Section 2.4). Field-level structure cannot be authored until BA layouts land.
Execution surface:   harness-only (generic constant-width check; product validate_file cannot field-validate against a stub).
```

```
Test Case ID:        TC-INT-SHAW-054
Title:               contact / contact-account constant record length only (layout OPEN QUESTION)
Logical/Physical file: TriNet contact + contact-account / contact_shaw_<run_date>.txt, contact-account_shaw_<run_date>.txt
Parameters:          source=shaw, run_date
Preconditions:       Both files present.
Steps:               1. harness: for each file, assert all records share one constant byte length.
                     2. harness: report observed record length per file.
                     3. harness: do NOT assert per-field positions (stub mappings); note possible multi-record contact (RECORD_TYPE TBC).
Expected Result:     Each file is internally constant-width; observed record length reported.
Pass/Fail Criteria:  PASS iff every record within a file has identical length. FAIL if lengths vary. Per-field validation NOT performed.
Mapping Reference:   SHAW.yml CONTACT (lines 178-191), CONTACT_ACCOUNT (192-195). OPEN QUESTION (OQ-7): SHAW_CONTACT.json / SHAW_CONTACT_ACCOUNT.json are TODO stubs. OPEN QUESTION (OQ-X3/OQ-8): contact may be multi-record (discriminator RECORD_TYPE "TBC") — constant-length check assumes single-record until confirmed.
Execution surface:   harness-only (generic constant-width check).
```


## 5.7 Lineage / transformation (sample accounts staging->consolidated->output)

Traces representative accounts end-to-end: staging table -> consolidated `SHAW_LOAN_MASTER` -> output file, asserting field transforms at each hop. Each assertion cites its transform rule or marks it Open Question. Three sample profiles: a normal account, a new account (drives atoctran 200), and a charge-off account (drives atoctran 900 / tranert 32010). Where the only authored layouts are atoctran/tranert/p327, lineage assertions target those output fields; contact-side joins are Open Question due to stub layouts.

```
Test Case ID:        TC-INT-SHAW-060
Title:               Normal account lineage staging -> consolidated -> atoctran/tranert output
Logical/Physical file: multi (staging + a-doc-tran + financial extract) / SHAW_LOAN_MASTER -> atoctran_shaw_<run_date>.txt, tranert_shaw_<run_date>.txt
Parameters:          source=shaw, run_date, batch_date, sample_acct (normal)
Preconditions:       Staging loaded (5.2), consolidation done (5.3), outputs generated.
Steps:               1. MCP extract_table (query mode): SELECT key columns for sample_acct from APP_INT.SHAW_LOAN_MASTER (post-consolidation).
                     2. harness: locate the atoctran rt_060 record for sample_acct (ACCT-NUM pos 7 len 18) and assert LOCATION-CODE (1/6), ACCT-NUM (7/18), TRANSACTION-CODE=060 (25/3), TRANSACTION-DATE (28/8 CCYYMMDD) carry the consolidated values.
                     3. harness: locate the tranert detail rows for the loan and assert LN-NUM-ERT and EFF-DAT-ERT match staging-derived values per tranert.yml field map.
Expected Result:     Sample account's identity + key fields flow unchanged (trim only) from consolidated master into atoctran 060 and tranert.
Pass/Fail Criteria:  PASS iff every traced field equals the consolidated source value under the declared transform. FAIL with the first mismatching field reported.
Mapping Reference:   SHAW_ATOCTRAN_060_mapping.json (common header fields, transform = trim); tranert.yml rt_* field map (LN-NUM-ERT, EFF-DAT-ERT etc.). OPEN QUESTION (OQ-2): ATOCTRAN join key ACCT-NUM is inferred (no atoctran.yml). Consolidation field-level transforms beyond trim are undocumented (OQ-A2; SHAW_LOAN_MASTER mapping is a stub).
Execution surface:   MCP (extract_table query for consolidated values) + harness (output-field extraction + comparison).
```

```
Test Case ID:        TC-INT-SHAW-061
Title:               New account lineage -> atoctran type-200 with CIF contact join fields
Logical/Physical file: a-doc-tran rt_200 / atoctran_shaw_<run_date>.txt (TRANSACTION-CODE=200)
Parameters:          source=shaw, run_date, batch_date, sample_acct (new in batch_date)
Preconditions:       sample_acct confirmed new in batch_date; atoctran output present.
Steps:               1. harness: locate the rt_200 record for sample_acct (ACCT-NUM pos 7 len 18).
                     2. validator: assert PREVIOUS-CCI (36/1) in {0,2}, PORTFOLIO-LOCATION-CODE (37/6) in {200000} when present, CUSTOMER-PORTFOLIO ID (43/18), CUSTOMER-PORTFOLIO-CONTACT-ID (61/24).
                     3. harness: assert the account's CIF/contact linkage (the 200->contact join) — see 5.8; cite the contact-side key only when stub layout resolved.
Expected Result:     New account produces exactly one type-200 record with valid CCI/portfolio fields; contact linkage holds (per 5.8).
Pass/Fail Criteria:  PASS iff the 200 record exists with valid field values per the 200 mapping rules. Contact-join portion deferred to 5.8.
Mapping Reference:   SHAW_ATOCTRAN_200_mapping.json (PREVIOUS-CCI 36/1 valid_values [0,2]; PORTFOLIO-LOCATION-CODE 37/6 [200000]; CUSTOMER-PORTFOLIO ID 43/18; CUSTOMER-PORTFOLIO-CONTACT-ID 61/24). OPEN QUESTION (OQ-X1/X2): contact / contact-account join-key position is a stub -> CIF contact-join field-level lineage cannot be asserted yet.
Execution surface:   MCP (validate_file --multi-record for field rules) + harness (record location + 5.8 cross-file).
```

```
Test Case ID:        TC-INT-SHAW-062
Title:               Charge-off account lineage -> atoctran 900 and tranert 32010 (ORI)
Logical/Physical file: a-doc-tran rt_900 + financial extract rt_32010 / atoctran_shaw_<run_date>.txt, tranert_shaw_<run_date>.txt
Parameters:          source=shaw, run_date, batch_date, sample_acct (charge-off on batch_date)
Preconditions:       sample_acct charges off on batch_date; both outputs present.
Steps:               1. harness: assert at most one atoctran rt_900 record for sample_acct (zero_or_one cardinality) with TRANSACTION-AMT (36/18 decimal 9(12)V9(6)).
                     2. orchestrator/L2b: assert the tranert rt_32010 (ORI) row exists for the loan under predicate CHG_OFF_CD='1' (tranert.yml rt_32010) and its fields match expected_32010.sql.
                     3. harness: cross-confirm the charge-off account appears in both outputs consistently.
Expected Result:     Charge-off account yields <=1 atoctran 900 record and a tranert 32010 row gated by CHG_OFF_CD='1'.
Pass/Fail Criteria:  PASS iff 900 cardinality holds AND tranert 32010 L2b reconciliation passes for the loan. FAIL if 900 appears >1 time or 32010 row missing/ mismatched.
Mapping Reference:   SHAW_ATOCTRAN_900_mapping.json (TRANSACTION-AMT 36/18); tranert.yml rt_32010 predicate "CHG_OFF_CD = '1'", cardinality zero_or_one_per_driver_row, key LN-NUM-ERT. OPEN QUESTION (OQ-2): ATOCTRAN 900 charge-off predicate column undocumented (only TRANERT's CHG_OFF_CD is grounded; cross-file consistency is inferred, not config-declared).
Execution surface:   harness (atoctran 900 cardinality) + MCP/orchestrator (run_etl_pipeline L2b for tranert 32010).
```


## 5.8 Cross-file 200 -> contact AND contact-account invariant (decision #6)

Every atoctran type-200 record's account number must exist in **BOTH** the contact file **AND** the contact-account file (3-way referential integrity). No product primitive does file-vs-file-vs-file membership — this is harness-implemented (set membership). The atoctran 200 key is grounded (`ACCT-NUM` pos 7 len 18); the contact-side keys are stub layouts (Open Question).

```
Test Case ID:        TC-INT-SHAW-070
Title:               Every atoctran 200 account exists in BOTH contact and contact-account
Logical/Physical file: a-doc-tran 200 -> TriNet contact + contact-account / atoctran_shaw_<run_date>.txt, contact_shaw_<run_date>.txt, contact-account_shaw_<run_date>.txt
Parameters:          source=shaw, run_date
Preconditions:       atoctran, contact, contact-account outputs all present for run_date.
Steps:               1. harness: build set A200 = { ACCT-NUM (pos 7 len 18) of every atoctran line where TRANSACTION-CODE(25/3)=200 }.
                     2. harness: build set C = { account key in contact_shaw_<run_date>.txt } and CA = { account key in contact-account_shaw_<run_date>.txt }.
                     3. harness: assert A200 subset of (C intersect CA); report any account in A200 missing from C and/or CA.
Expected Result:     A200 is fully contained in both contact and contact-account key sets.
Pass/Fail Criteria:  PASS iff A200 subset of (C intersect CA). FAIL listing each orphan 200 account and which file(s) it is missing from.
Mapping Reference:   SHAW_ATOCTRAN_200_mapping.json ACCT-NUM pos 7 len 18 (the referential key; NOT CUSTOMER-PORTFOLIO ID 43/18). Phase 2 Section 4. OPEN QUESTION (OQ-X1): contact account-key position/length (SHAW_CONTACT.json stub). OPEN QUESTION (OQ-X2): contact-account account-key position/length (stub). OPEN QUESTION (OQ-X3): whether contact is multi-record (RECORD_TYPE TBC) and which record type carries the key. This case is authored but Open-Question-parameterized on the contact-side key positions; it cannot execute until those land.
Execution surface:   harness-only (3-way set-membership; no product primitive — runs in the harness, not one CLI/MCP call).
```

## 5.9 Concat-merge integrity (decision #6)

The final merged contact / contact-account files are an exact-duplicate-aware sum of their per-source inputs: `merged_count == Sum(per-source row counts) - exact_duplicate_line_count`. contact and contact-account merge as two independent streams. No field-level dedup (exact whole-line only), no re-sort. No product primitive — harness/shell assertion.

```
Test Case ID:        TC-INT-SHAW-080
Title:               contact concat-merge count identity (dedup-aware)
Logical/Physical file: TriNet contact (merged) / contact_shaw_<run_date>.txt
Parameters:          source=shaw, run_date
Preconditions:       Per-source contact input files enumerated; merged contact output present.
Steps:               1. harness: wc -l each per-source contact input; sum to S.
                     2. harness: compute D = count of exact-duplicate whole lines removed across the inputs.
                     3. harness: wc -l the merged contact_shaw_<run_date>.txt = M; assert M == S - D.
Expected Result:     Merged contact count equals sum of inputs minus exact-duplicate lines.
Pass/Fail Criteria:  PASS iff M == S - D. FAIL with S, D, M reported.
Mapping Reference:   Phase 2 Section 5 concat-merge rule. OPEN QUESTION (OQ-M1): exact set of per-source inputs feeding the merged contact not enumerated in SHAW.yml. OPEN QUESTION (OQ-M2): definition of "exact duplicate line" (byte-for-byte incl. trailing pad vs trimmed) undocumented.
Execution surface:   harness-only (wc -l + dedup count + arithmetic; no product primitive does concat-merge).
```

```
Test Case ID:        TC-INT-SHAW-081
Title:               contact-account concat-merge count identity (dedup-aware, separate stream)
Logical/Physical file: TriNet contact-account (merged) / contact-account_shaw_<run_date>.txt
Parameters:          source=shaw, run_date
Preconditions:       Per-source contact-account input files enumerated; merged output present.
Steps:               1. harness: wc -l each per-source contact-account input; sum to S.
                     2. harness: compute D = exact-duplicate whole-line count across inputs.
                     3. harness: wc -l merged contact-account_shaw_<run_date>.txt = M; assert M == S - D.
Expected Result:     Merged contact-account count equals sum of inputs minus exact-duplicate lines; merged independently of contact.
Pass/Fail Criteria:  PASS iff M == S - D. FAIL with S, D, M reported.
Mapping Reference:   Phase 2 Section 5 (contact and contact-account merge separately; no re-sort; exact-line dedup only). OPEN QUESTION (OQ-M1/OQ-M2): per-source inputs + exact-duplicate definition undocumented.
Execution surface:   harness-only (wc -l + dedup + arithmetic).
```


---

## Coverage matrix

| Section | Title | # cases | Case IDs | Grounded vs Open-Question-parameterized | Execution surface |
|---|---|---|---|---|---|
| 5.1 | Source receipt | 2 | 001-002 | Grounded (6 input globs from SHAW.yml); OQ-S1 on header/delimiter | harness-only |
| 5.2 | SQL*Load reconcile | 3 | 010-012 | Grounded (6 file->table pairs, gate config); OQ-S1/S2 on delimiter/truncate/.bad paths | MCP (extract_table) + harness; loader artifacts harness-only |
| 5.3 | Consolidation + audit anchor | 3 | 020-022 | Source A grounded (SHAW_LOAN_MASTER COUNT); Source B Open-Question-parameterized (OQ-A1 count column) | MCP (extract_table query) / CLI; 022 blocked on OQ-A1 |
| 5.4 | atoctran per-code | 7 | 030-036 | Discriminator + codes + record-lengths grounded; per-code driver/key/cardinality OQ-2/OQ-650 | harness-only (enumeration) + MCP (validate_file flags unknown codes) |
| 5.5 | Output count reconcile | 6 | 040-045 | AT_BASE == BASE grounded (direction); above/below/sparse bands OQ-B1; contact class OQ-B2; tranert grounded | MCP (extract_table + run_etl_pipeline) + harness |
| 5.6 | Structure / format | 5 | 050-054 | atoctran/tranert/p327 layouts fully grounded; 11 CDS + contact/contact-account layout OQ-7 (constant-length only) | MCP (validate_file) for grounded; harness-only generic width for stubs |
| 5.7 | Lineage / transformation | 3 | 060-062 | Output-side fields grounded (atoctran/tranert); consolidation transforms + contact join OQ-2/OQ-A2/OQ-X1/X2 | MCP (extract_table + run_etl_pipeline) + harness |
| 5.8 | Cross-file 200 invariant | 1 | 070 | atoctran 200 key grounded; contact-side keys OQ-X1/X2/X3 (parameterized) | harness-only (3-way set membership) |
| 5.9 | Concat-merge integrity | 2 | 080-081 | Merge identity rule grounded; per-source inputs + dup definition OQ-M1/M2 | harness-only (wc -l + dedup) |
| **Total** | | **32** | 001-081 | 17 fully grounded / 15 Open-Question-parameterized | MCP: 11, harness-only: 17, mixed/CLI: 4 |

### Open Questions referenced (consolidated, carried from Phases 1-2)
- **OQ-PATH:** output dir APPS (SHAW.yml) vs CACS (prompt) discrepancy.
- **OQ-A1:** AUDIT_SQL_LOADER count column name (Source B blocked).
- **OQ-A2:** SHAW_LOAN_MASTER post-consolidation = same table vs distinct; consolidation transforms undocumented.
- **OQ-A3:** case-sensitivity of source_system / table_name predicates.
- **OQ-2:** no atoctran.yml — per-code driver/join-key/cardinality undefined.
- **OQ-650:** code 650 renamed to 605 vs genuinely absent.
- **OQ-300/605/700/100:** driving population for these codes undocumented.
- **OQ-B1:** variance bands for above/below/sparse undocumented (engine = strict equality).
- **OQ-B2:** contact / contact-account classification vs BASE not given.
- **OQ-6:** P327 wired stub vs canonical 252-field layout (re-point SHAW.yml).
- **OQ-7:** 11 CDS + contact + contact-account layouts are 1-field TODO stubs.
- **OQ-8/OQ-X3:** contact multi-record (RECORD_TYPE TBC).
- **OQ-X1/X2:** contact / contact-account account-key positions (stubs).
- **OQ-M1/M2:** per-source merge inputs + exact-duplicate-line definition.
- **OQ-S1/S2/S3:** staging delimiter/header; load_SHAW.sh truncate behavior; load-only staging tables beyond the 6.

