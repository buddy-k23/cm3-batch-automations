# E2E SHAW Test-Case Package — Collections Interfaces (ETL)

End-to-end test-case authoring package for the Valdo **Collections Interfaces** ETL process, **source = `shaw`**. This is test-case *documentation* — no production code, no test execution. Every claim is evidence-backed against the ingested Valdo config (`config/e2e/sources/SHAW.yml`, mappings/rules, `tranert.yml`) and the existing E2E harness (`scripts/e2e_lib/`); gaps are recorded as Open Questions and never invented.

## Package contents (read in order)

| Phase | Document | Purpose |
|---|---|---|
| 1 | [`E2E_SHAW_PHASE1_INVENTORY.md`](./E2E_SHAW_PHASE1_INVENTORY.md) | **Inventory.** Physical→logical file map, fixed-width layouts (atoctran, tranert, p327 authored; CDS/contact/staging = stubs), ATOCTRAN multi-record model, and the reusable harness reconciliation engine. First-cut Open Questions #1–#10. |
| 2 | [`E2E_SHAW_PHASE2_RECON_MODEL.md`](./E2E_SHAW_PHASE2_RECON_MODEL.md) | **Reconciliation model.** The BASE anchor (`COUNT(*)` of post-consolidation `SHAW_LOAN_MASTER`), per-output-file count classes (AT/ABOVE/BELOW/SPARSE), atoctran per-code model, the 200→contact∧contact-account invariant, concat-merge identity, and staging-load rule. |
| 3 | [`E2E_SHAW_TEST_CASES.md`](./E2E_SHAW_TEST_CASES.md) | **The 32 test cases** (TC-INT-SHAW-001..081) across Sections 5.1–5.9, each with steps, pass/fail criteria, mapping reference, and execution surface (MCP / CLI / harness-only). Includes the coverage matrix. |
| 4 | [`E2E_SHAW_OPEN_QUESTIONS_AND_RISKS.md`](./E2E_SHAW_OPEN_QUESTIONS_AND_RISKS.md) | **Honesty review.** The single consolidated Open-Questions register (ranked by blast radius), the stub/unmapped inventory, the risks & deferrals, and the executability summary (17 runnable today / 14 OQ-blocked / field-level stub-blocked). |

## Source & parameterization

- **Source:** `shaw` (Collections Interfaces ETL). Source/output globs come from `config/e2e/sources/SHAW.yml` (`input_files:` / `output_files:`).
- **Parameterization:** every case is parameterized on **`source`** (=`shaw`), **`batch_date`** (suffixes the source input files under `/app/software/ftp/input/shaw/`), and **`run_date`** (suffixes the output files under `/app/software/CACS/ftp/input/shaw/` per the prompt; note the `APPS` vs `CACS` discrepancy — OQ-PATH). Staging schema is `APP_INT`. No magnitudes are hardcoded — BASE is read at runtime.

## How to run the dry run

Start with the **fully-grounded cases** — those whose inputs are entirely backed by authored config, so they execute end-to-end with no Open-Question answer required:

1. **atoctran structure + per-code histogram** — `TC-INT-SHAW-050` (per-code record length + field layout, all 7 codes via `validate_file --multi-record SHAW_ATOCTRAN.yaml`) and `TC-INT-SHAW-030` (group-by `TRANSACTION-CODE` per-code counts).
2. **tranert full reconciliation** — `TC-INT-SHAW-051` (per-type layout + header) and `TC-INT-SHAW-044` (per-type count + cardinality + `batch_header_count` via the L2b `db_truth_comparator` against `config/e2e/sources/SHAW/reconciliation/tranert.yml`). This is the strongest, production-grade slice.
3. **p327 structure** — `TC-INT-SHAW-052` using the canonical `P327_SHAW_M06_mapping.json` (252 fields, 2809 chars) invoked **directly** via `validate_file --mapping` (not the orchestrator gate, until `SHAW.yml` re-points — OQ-6).

These exercise the authored layouts and the reusable recon engine without touching any stub. Then layer in source receipt (`001/002`), staging-load recon (`010–012`), and the BASE anchor Source A (`020/021`). Defer the atoctran-driver cases (OQ-2), variance-band cases (OQ-B1), contact/CDS field cases (OQ-7), and the merge cases (OQ-M1/M2) until the corresponding Open Questions in Phase 4 §1 are answered — see the ranked register and the "3–5 answers that unblock the most cases" there.
</content>
