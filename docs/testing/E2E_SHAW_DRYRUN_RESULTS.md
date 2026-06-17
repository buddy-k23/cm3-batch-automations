# E2E SHAW — Local DRY-RUN Execution Results (Honest, Real Output)

**Date:** 2026-06-17
**Repo root:** `/Users/pavankanduri/claude-ws/valdo/valdo-feature-valdo-engine-v3`
**Scope:** Execute the **17 grounded** E2E SHAW test cases (per `E2E_SHAW_OPEN_QUESTIONS_AND_RISKS.md` §4) on **this local machine** and record **only real command output**. No production code was modified. No git was run. Synthetic test fixtures were created (clearly labelled `_SYNTH`) and a local SQLite DB was stood up for a labelled mechanism smoke; nothing here uses real SHAW production data.

> **Honesty rule applied throughout:** Cases that cannot run end-to-end locally (no Oracle with loaded SHAW staging, no `/app/software/...` source/output files) are marked **BLOCKED** with the exact reason. Cases run against synthetic data built from grounded layouts are marked **MECHANISM-ONLY** (the validation *engine path* is proven; the *real SHAW data* is not present).

---

## 1. Environment — what is available / missing here

| Probe | Result |
|---|---|
| CLI entrypoint | `python3 -m src.main` (console script `valdo` not installed). Run via the provisioned venv. |
| Working interpreter | `./.venv/bin/python` (Python 3.11). **Note:** `.venv311` exists but is missing `jsonpath_ng`; `.venv` is the only fully-provisioned env. System `python3` (3.13) lacks `click`. |
| `DB_ADAPTER=sqlite` | Works; SQLite file selected by `DB_PATH` env var. |
| Oracle DB | **Not usable.** Port 1521 has *a* listener, but `APP_INT@localhost:1521/FREEPDB1` → `ORA-01017: invalid credential / logon denied`. No SHAW staging loaded. |
| `/app/software/ftp/input/shaw/` (source dir) | **Absent** (`No such file or directory`). |
| `/app/software/CACS|APPS/ftp/input/shaw/` (output dir) | **Absent.** No SHAW output files (atoctran/tranert/p327/CDS/contact) exist locally. |
| `AUDIT_SQL_LOADER` table / count column | **Not present** anywhere in repo (appears only in the testing docs). Confirms **OQ-A1** is still blocking. |
| tranert recon spec | **Present:** `config/e2e/sources/SHAW/reconciliation/tranert.yml` (the L2b recon spec for TC-044). |
| SHAW input globs | **Present** in `config/e2e/sources/SHAW.yml`: 6 grounded globs (collateral-master, fee-master, loans-master, loans-name, posted-trans, history). |
| **Real fixtures present** | `tests/manual/fixtures/tranert_shaw_test_{clean_no_violations,valid,structural_failures}.txt` (636-char lines; 7 / 16 / 5 lines). |
| atoctran / p327 sample data | **None exist** and **no script generates them** — `scripts/generate_shaw_atoctran_csvs.py` / `generate_p327_shaw_csvs.py` only regenerate *mapping/rules CSVs from Excel*, not data files. `scripts/build_shaw_test_files.py` generates **tranert fixtures + SQL only**. Synthetic atoctran/p327 fixtures were therefore hand-built from the grounded JSON layouts (labelled `_SYNTH`). |

**Product bug found (real, reproducible):** `valdo validate --multi-record ... -o <file>.json` crashes with `TypeError: Object of type int64 is not JSON serializable` in `src/commands/multi_record_command.py:118` (numpy int64 from the per-type row counts is not JSON-coerced before `json.dump`). The **validation itself completes and prints correct console results**; only the JSON-file write path fails. Console output (no `-o`) is unaffected. Recommend a follow-up fix (coerce numpy scalars before `json.dump`).

---

## 2. Per-case results

Legend: **PASS** (ran on real fixtures, expected outcome) · **MECHANISM-ONLY** (engine path proven on synthetic data built from grounded layouts; real SHAW data absent) · **BLOCKED** (cannot run E2E locally — reason given).

| Case ID | Title (short) | Status | Command (abridged; run from repo root with `./.venv/bin/python`) | Evidence (real output) |
|---|---|---|---|---|
| **TC-INT-SHAW-001** | Source files present | **BLOCKED** | n/a (filesystem presence check) | `/app/software/ftp/input/shaw/` → *No such file or directory*. The 6 input globs ARE grounded in `SHAW.yml`, but no batch_date source files exist locally. |
| **TC-INT-SHAW-002** | Header + ≥1 data row | **BLOCKED** | n/a (`wc -l` on source files) | Same as 001 — no source files to line-count. |
| **TC-INT-SHAW-010** | Staging count == file_rows−1 | **BLOCKED** | needs Oracle `COUNT(*)` + source files | No Oracle SHAW staging + no source files. |
| **TC-INT-SHAW-011** | Truncate-load no residue | **BLOCKED** | needs Oracle + load_SHAW.sh | No Oracle; no loader. |
| **TC-INT-SHAW-012** | Loader reject/discard empty | **BLOCKED** | needs SQL*Loader `.bad`/`.dsc` artifacts | No loader run locally. |
| **TC-INT-SHAW-020** | Consolidation ran before BASE | **BLOCKED** | needs Oracle SHAW_LOAN_MASTER | No real consolidated table. |
| **TC-INT-SHAW-021** | BASE = COUNT(*) SHAW_LOAN_MASTER | **MECHANISM-ONLY** | `DB_ADAPTER=sqlite DB_PATH=/tmp/shaw_mechanism.db valdo extract --query "SELECT COUNT(*) AS BASE FROM SHAW_LOAN_MASTER" -o /tmp/base_anchor.txt` | `Extracted 1 rows`; output file → `BASE` / `5`. Query MECHANISM proven on a synthetic 5-row SHAW_LOAN_MASTER (from `build_shaw_test_files.py` sqlite seed). **NOT real SHAW data** — real BASE requires Oracle. |
| **TC-INT-SHAW-030** | Per-code histogram + sum identity | **MECHANISM-ONLY** | `valdo validate -f atoctran_shaw_test_valid_SYNTH.txt --multi-record SHAW_ATOCTRAN.yaml` | Per-type counts: `rt_060: 2, rt_100/200/300/605/700/900: 1 each` → sum = 8 == Total Rows 8. Histogram mechanism proven on synthetic data. (Per-undocumented-code tally is a harness extension — see 036.) |
| **TC-INT-SHAW-044** | tranert per-type recon (L2b SQL-truth) | **BLOCKED** | needs Oracle + materialized `expected_*.sql` in APP_INT | `tranert.yml` recon spec IS present, but L2b requires Oracle with SHAW data. The **structural** half of tranert (record types, counts, header rule) DID run — see TC-051. |
| **TC-INT-SHAW-045** | contact/contact-account receipt | **BLOCKED** | needs output files | No `contact_shaw_*` / `contact-account_shaw_*` output files locally. |
| **TC-INT-SHAW-050** | atoctran per-code structure (7 codes) | **MECHANISM-ONLY** | `valdo validate -f atoctran_shaw_test_valid_SYNTH.txt --multi-record SHAW_ATOCTRAN.yaml` | `✓ Multi-record validation passed`, 0 errors, all 7 codes dispatched at pos 25/3 and parsed at their `total_record_length` (060=35,100=75,200=84,300=125,605=83,700=122,900=53). Engine proven; **synthetic** data. |
| **TC-INT-SHAW-051** | tranert structure + header (all types) | **PASS** | `valdo validate -f tranert_shaw_test_<clean\|valid> --multi-record SHAW_TRANERT.yaml` | **REAL fixtures.** clean(7): `✓ passed`, 0 err. valid(16): `✓ passed`, 0 err. structural_failures(5): `✗ failed`, 6 err (unknown code `99999` + 5 missing required types). Exit 0 / 0 / 1. |
| **TC-INT-SHAW-052** | p327 2809-char / 252-field structure | **MECHANISM-ONLY** | `valdo validate -f p327_shaw_test_clean_SYNTH.txt --mapping P327_SHAW_M06_mapping.json` | `✓ File is valid`, error_count 0, all records parsed at 2809 chars against the 252-field canonical layout (LOCATION-CODE 1/6 … HMDA-ULI-CODE 2765/45). A deliberately 10-char-short record AND a record missing required `ACCT-NUM` both `✗ failed` correctly. Engine proven; **synthetic** data; run directly with the canonical mapping per OQ-6 (SHAW.yml still wires the 1-field stub). |
| **TC-INT-SHAW-053** | CDS files constant record length | **BLOCKED** | needs 11 CDS output files | No CDS output files locally (harness-only `wc`-style check; nothing to measure). |
| **TC-INT-SHAW-054** | contact/contact-account constant length | **BLOCKED** | needs contact output files | No contact output files locally. |
| **TC-INT-SHAW-060** | Normal account lineage | **BLOCKED** | needs Oracle SHAW_LOAN_MASTER + outputs | No consolidated DB rows + no output files to trace. |
| **TC-INT-SHAW-062** | Charge-off lineage (atoctran 900 + tranert 32010) | **BLOCKED** | needs Oracle L2b + outputs | tranert 32010 half needs L2b/Oracle; atoctran 900 needs a real output file. (atoctran 900 *structure* is covered by the 050 mechanism run.) |

### Bonus mechanism evidence (supports 5.4 / 5.8 robustness)

| Item | Status | Command | Evidence |
|---|---|---|---|
| atoctran undocumented code 650 surfaced (TC-036 behavior) | **MECHANISM-ONLY** | `valdo validate -f atoctran_shaw_test_unknown650_SYNTH.txt --multi-record SHAW_ATOCTRAN.yaml` | `✗ failed`, exit 1: `Unrecognized record type: discriminator value '650' does not match any configured type.` Confirms `default_action: error` hard-fails an undocumented code at L1. |
| MCP `extract_table` tool reachable | **MECHANISM-ONLY** | `import src.mcp.extract_tools` | Imports cleanly; exposes `extract_table_payload`, `EXTRACT_TABLE_DESCRIPTION`. |

---

## 3. Headline tranert result (real numbers, real fixtures)

`valdo validate --multi-record config/mappings/SHAW_TRANERT.yaml` against the three committed fixtures:

```
clean_no_violations  (7 lines):  ✓ PASSED  | Total 7  | Errors 0 | exit 0
  batch_header 1, rt_32000 1, rt_32005 1, rt_32010 1, rt_32025 1, rt_32040 1, rt_32075 1

valid               (16 lines):  ✓ PASSED  | Total 16 | Errors 0 | exit 0
  batch_header 1, rt_32000 3, rt_32005 4, rt_32010 2, rt_32025 2, rt_32040 2, rt_32075 2

structural_failures  (5 lines):  ✗ FAILED  | Total 5  | Errors 6 | exit 1
  • Unrecognized record type: discriminator value '99999' (default_action: error)
  • Expected at least 1 row of type rt_32005 / rt_32010 / rt_32025 / rt_32040 / rt_32075 (5 missing-required)
```

This is the strongest grounded result in the suite: real fixtures, the discriminator (`TRN-COD-ERT` pos 170/5) and batch_header-by-position model both work, `default_action: error` correctly rejects the unknown `99999`, and `expect: at_least_one` correctly flags missing required types.

---

## 4. To run the DB-dependent (BLOCKED) cases — exactly what is needed

The following must be provisioned for the BLOCKED cases (001, 002, 010, 011, 012, 020, 044, 045, 053, 054, 060, 062) plus the real-data version of 021/030/050/052 to run end-to-end:

1. **Oracle (or Postgres via `DB_ADAPTER=postgresql`) with loaded SHAW staging.** Set `ORACLE_USER/PASSWORD/DSN/SCHEMA` (default schema `APP_INT`). Required tables, post-load + post-consolidation:
   - `APP_INT.SHAW_LOAN_MASTER` (BASE anchor — TC-020/021/060) and the other 5 staging masters (`SHAW_COLLATERAL`, `SHAW_FEE_MASTER`, `SHAW_LOANS_NAME`, `SHAW_TRANSACTIONS`, `SHAW_TRANS_MASTER`) for TC-010/011.
   - `AUDIT_SQL_LOADER` with the **count column name resolved (OQ-A1)** and predicate casing resolved (OQ-A3) for the BASE cross-check (TC-022, itself already in the blocked-on-OQ bucket, not the grounded 17).
   - The `expected_*.sql` materializations referenced by `config/e2e/sources/SHAW/reconciliation/tranert.yml`, in `APP_INT`, for the L2b `db_truth_comparator` gate (TC-044, TC-062 tranert half).
2. **Source files** under `/app/software/ftp/input/shaw/`, batch_date-suffixed, matching the 6 `SHAW.yml` globs — for source-receipt (001/002) and staging-load reconcile (010–012).
3. **SQL*Loader artifacts** (`.bad`/`.dsc`) from a real `load_SHAW.sh` run — for TC-012 (path convention is OQ-S2/OQ-LOG).
4. **Output files** under the configured output dir (`/app/software/CACS/ftp/input/shaw/` per prompt, vs `APPS` in SHAW.yml — OQ-PATH), run_date-suffixed:
   - `atoctran_shaw_*`, `tranert_shaw_*`, `p327_shaw_*` (real layouts) for the real-data versions of 030/044/050/051/052/062.
   - 11 `cdstrans_*`, `contact_shaw_*`, `contact-account_shaw_*` for 045/053/054 (field-level still blocked on OQ-7 stub layouts).
5. **P327 re-wiring (OQ-6):** repoint `SHAW.yml` P327 from the 1-field `SHAW_P327.json` stub to the canonical `P327_SHAW_M06_mapping.json` to run TC-052 via the orchestrator gate (the direct `validate --mapping` path was used here instead).

Once (1)+(2)+(4) land with real SHAW data, the SQLite mechanism smoke for TC-021 and the synthetic-data mechanism runs for 030/050/052 convert directly into real PASS/FAIL.

---

## 5. Artifacts produced by this dry run (test-data only)

- `tests/manual/fixtures/atoctran_shaw_test_valid_SYNTH.txt` (8 lines, all 7 codes)
- `tests/manual/fixtures/atoctran_shaw_test_unknown650_SYNTH.txt` (3 lines, incl. undocumented 650)
- `tests/manual/fixtures/p327_shaw_test_clean_SYNTH.txt` / `_valid_SYNTH.txt` / `_badlen_SYNTH.txt`
- `tests/manual/sql/shaw_setup_sqlite.sql` (regenerated by `build_shaw_test_files.py`)
- `/tmp/shaw_mechanism.db` (SQLite, synthetic SHAW_LOAN_MASTER = 5 rows), `/tmp/base_anchor.txt`

No production code or config was modified.

---

## 6. Tally

| Bucket | Count | Case IDs |
|---|---|---|
| **PASS** (real fixtures) | **1** | 051 |
| **MECHANISM-ONLY** (engine proven, synthetic data) | **4** | 021, 030, 050, 052 |
| **BLOCKED** (no Oracle SHAW staging / no source / no output files locally) | **12** | 001, 002, 010, 011, 012, 020, 044, 045, 053, 054, 060, 062 |
| **Total grounded** | **17** | |

**FAIL: 0** of the grounded cases failed unexpectedly. The only `✗ failed` outputs (tranert structural_failures, atoctran 650) are **expected failures** — they are negative-path assertions that the engine correctly rejected, and are reported under their parent PASS/MECHANISM-ONLY cases.
