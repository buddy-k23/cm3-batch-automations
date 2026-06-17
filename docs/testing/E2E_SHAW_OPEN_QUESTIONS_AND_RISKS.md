# E2E SHAW — Open Questions, Stubs & Risk Register (Phase 4, Honesty Review)

**Scope:** Collections Interfaces (ETL) process, source = `shaw`. This is the **final consolidation pass** over the three prior phase documents. It deduplicates every Open Question, inventories every stub/unmapped artifact, states every risk and deferral, and quantifies how many of the 32 authored test cases are runnable today. **No production code, no test execution, no git.**

Repo root: `/Users/pavankanduri/claude-ws/valdo/valdo-feature-valdo-engine-v3`

**Source documents consolidated here:**
- `docs/testing/E2E_SHAW_PHASE1_INVENTORY.md` — file inventory, fixed-width layouts, multi-record model, harness reconciliation engine. (Open Questions #1–#10.)
- `docs/testing/E2E_SHAW_PHASE2_RECON_MODEL.md` — anchor decision, per-file variance classes, per-code model, cross-file invariant, concat-merge, staging-load model. (OQ-A1/A2/A3, OQ-650, OQ-B1/B2, OQ-X1/X2/X3, OQ-M1/M2, OQ-S1/S2/S3, OQ-300/605/700/100.)
- `docs/testing/E2E_SHAW_TEST_CASES.md` — the 32 authored cases (TC-INT-SHAW-001..081) across Sections 5.1–5.9. (Adds OQ-PATH, OQ-6, OQ-7.)

**Discipline:** This document invents nothing. Every defaulted answer below is labelled "Proposed default if unanswered" and is a *testing fallback*, not a confirmed fact. The single deliverable for the user is **Section 1 — the consolidated register**: answering the top rows unblocks the most cases.

---

## 1. CONSOLIDATED OPEN-QUESTIONS REGISTER

Deduped across all three phase docs. Ranked by **blast radius** (number of test cases each answer unblocks/strengthens). Existing OQ IDs are preserved. "Blocks TC" lists the specific cases that cannot run end-to-end (or run only as report-only/directional) until the answer lands. "Blocks Section" maps to the 5.x stage.

| Rank | OQ ID | Question (one line) | Blocks which test cases | Blocks Section 5.x | Who can answer | Proposed default if unanswered |
|---|---|---|---|---|---|---|
| 1 | **OQ-7** | Field-level layouts for the 11 CDS files + contact + contact-account are 1-field TODO stubs — when do real layouts land? | 040, 041, 042, 043, 045, 053, 054, 061, 070, 080, 081 (field-level portions) | 5.5, 5.6, 5.7, 5.8, 5.9 | BA/PO (layout sheets) | Run **structural-only** (constant-width + count) now; defer all field/key/dedup assertions. |
| 2 | **OQ-2** | No `atoctran.yml` recon spec — what are the per-code driving staging table(s), join key(s), and cardinality for atoctran? | 031, 032, 033, 034, 060, 061, 062 | 5.4, 5.7 | BA/PO + DBA | Treat 060/200/900 driver = consolidated `SHAW_LOAN_MASTER` keyed on `ACCT-NUM` (pos 7 len 18); assert direction/bound only, not exact driver join. |
| 3 | **OQ-A1** | Exact column name in `AUDIT_SQL_LOADER` that holds the row count (`<count_col>`); table not present in repo. | 022 (hard-blocked); strengthens 021, 031, 034, 040, 041, 042, 043 (cross-check of BASE) | 5.3, 5.4, 5.5 | DBA / platform | Use **Source A only** (`COUNT(*)` of `SHAW_LOAN_MASTER`) as BASE; skip the Source-B cross-check (022) until column supplied. |
| 4 | **OQ-B1** | Variance bands/tolerances for ABOVE/BELOW/SPARSE files — none documented; engine does strict equality only. | 041, 042, 043 (magnitude assertions) | 5.5 | BA/PO | **Direction-only** assertions (`>`, `<`, `0≤count≪BASE`); report actual count + %delta, never hard-fail on magnitude. |
| 5 | **OQ-A2** | Is "post-consolidation `SHAW_LOAN_MASTER`" the same physical table at a later step, or a distinct consolidated table/view? No consolidation DDL in config. | 020, 021, 060 | 5.3, 5.7 | DBA / platform | Treat as the **same table read after the consolidation step completes**; gate BASE read on consolidation-complete signal. |
| 6 | **OQ-X1 / OQ-X2** | Account-number position/length in `contact` and `contact-account` files (both stubs). | 070, 061 (contact-join portion) | 5.7, 5.8 | BA/PO (layout) | Cannot execute the 200→contact∧contact-account membership check; author it but mark Open-Question-parameterized. |
| 7 | **OQ-650** | Was atoctran code 650 renamed to 605, or is it genuinely absent from SHAW scope? | 036 | 5.4 | BA/PO | **Enumerate-and-report-if-present**; do not author 650 as a configured code. 650 lines hard-fail L1 under `default_action: error`. |
| 8 | **OQ-S1** | Pipe-delimiter + header-row presence for the 6 staging input files (stub mappings give neither). | 002, 010, 011 | 5.1, 5.2 | BA/PO + platform | Assume **one header line** (`staging_count == file_rows − 1`); if no header, rule degrades to `file_rows`. |
| 9 | **OQ-M1 / OQ-M2** | Exact per-source inputs feeding each merged contact/contact-account; definition of "exact duplicate line" (byte-for-byte vs trimmed). | 080, 081 | 5.9 | BA/PO + platform | Cannot compute the merge identity; author the assertion but defer execution until inputs + dedup definition land. |
| 10 | **OQ-6** | P327: wired `SHAW_P327.json` is a 1-field stub; canonical `P327_SHAW_M06_mapping.json` is 252 fields / 2809 chars. Which is canonical, and should `SHAW.yml` re-point? | 052 (orchestrator path); 040 (record-length-aware count) | 5.5, 5.6 | BA/PO + platform | Use `P327_SHAW_M06_mapping.json` for authoring (decision #3); run `validate_file` **directly** with that mapping (CLI/REST), not via the orchestrator gate, until `SHAW.yml` re-points. |
| 11 | **OQ-300 / OQ-605 / OQ-700 / OQ-100** | Driving population for atoctran codes 100, 300, 605, 700 — undocumented in spec and config. | 033, 035 | 5.4 | BA/PO | Report counts only; no magnitude assertion for these codes. |
| 12 | **OQ-B2** | Classification of `contact` / `contact-account` relative to BASE — not given in the worked example. | 045 | 5.5 | BA/PO | Receipt + non-zero + count-report only; assert no BASE relation. |
| 13 | **OQ-A3** | Case-sensitivity / exact literal casing of `source_system='shaw'` and `table_name='shaw_loan_master'` predicates in `AUDIT_SQL_LOADER`. | 022 | 5.3 | DBA | Lowercase literals per the user decision; fold into OQ-A1 (same blocked case). |
| 14 | **OQ-X3 / OQ-8** | Is `contact` multi-record (discriminator `RECORD_TYPE` "TBC")? If so, which record type carries the account key? | 054, 070 | 5.6, 5.8 | BA/PO | Assume single-record constant-width until confirmed. |
| 15 | **OQ-S2** | `load_SHAW.sh` truncate-before-insert behavior (referenced, not transcribed). | 011, 012 | 5.2 | platform | Assume truncate-load (design intent); if wrapper appends, re-spec 011. |
| 16 | **OQ-S3** | Staging tables loaded by `load_SHAW.sh` beyond the 6 declared `input_files` (load-only, no input file). | 001 (scope note) | 5.1 | platform | Out of scope for the source-receipt presence check; document as load-only. |
| 17 | **OQ-PATH** | Output dir is `/app/software/APPS/ftp/input/shaw` (`SHAW.yml`) vs `/app/software/CACS/ftp/input/<source>/` (prompt). | All 5.5–5.9 output cases (path resolution) | 5.5–5.9 | platform | Use the prompt-specified `CACS` path per instruction; flag the discrepancy. Does not block logic, only file resolution. |
| 18 | **OQ-1** | Logical type (a-doc-tran / financial-extract / CDS / TriNet) is not an explicit config attribute — inferred from filename + comments. | none (documentation only) | n/a | BA/PO | Keep the inferred classification; does not block any count/structure assertion. |

### 1.1 The 3–5 answers that unblock the most cases

In priority order (this is what the user should answer first):

1. **OQ-7** (CDS + contact layouts) — unblocks/strengthens **11 cases**: 040, 041, 042, 043, 045, 053, 054, 061, 070, 080, 081 field-level coverage. Single biggest lever.
2. **OQ-2** (atoctran driving tables/keys/cardinality) — unblocks **7 cases**: 031, 032, 033, 034, 060, 061, 062 from report-only/directional to true driver reconciliation.
3. **OQ-A1 (+OQ-A3)** (AUDIT_SQL_LOADER count column) — unblocks **1 hard-blocked case (022)** and strengthens the BASE cross-check behind **~7** count cases.
4. **OQ-B1** (variance bands) — upgrades **3 cases** (041, 042, 043) from direction-only to magnitude assertions.
5. **OQ-X1/X2** (contact-side account-key positions) — unblocks the **3-way referential** case (070) and the contact-join portion of 061.

---

## 2. UNMAPPED / STUB INVENTORY

What is a stub today, what coverage IS possible now (structural/count), and what is blocked (field-level) until layouts land.

| Artifact group | Count | State | Coverage POSSIBLE now | Coverage BLOCKED (needs layout) | OQ |
|---|---|---|---|---|---|
| **CDS mappings** (`SHAW_CDSTRANS_EAC_COLLATERAL`, `_EFB`, `_EFI`, `_EFW_FEE_WAIVERS`, `_EFX`, `_ESA`, `_EST`, `_HSS`, `_RLT`, `_SEC`, `_XPR`) | 11 | All 1-field TODO stubs (`total_record_length: 1`, `TODO_FIELD_1_*`) | (a) Whole-file row **count** vs BASE — AT_BASE hard-assert (`==BASE`); ABOVE/BELOW/SPARSE direction-only (TC-040/041/042/043). (b) **Constant-record-length** (max line len == min line len) per file (TC-053). | Per-field position/length/type/format validation; any field-level lineage. | OQ-7, OQ-B1 |
| **contact** (`SHAW_CONTACT.json`) | 1 | 1-field TODO stub; possible multi-record (`RECORD_TYPE` "TBC") | Receipt + non-zero + count report (TC-045); constant-record-length (TC-054). | Account-key position (the 200→contact membership key); per-field layout; multi-record dispatch. | OQ-7, OQ-X1, OQ-X3/OQ-8 |
| **contact-account** (`SHAW_CONTACT_ACCOUNT.json`) | 1 | 1-field TODO stub | Receipt + non-zero + count report (TC-045); constant-record-length (TC-054). | Account-key position (the 200→contact-account membership key); per-field layout. | OQ-7, OQ-X2 |
| **6 staging masters** (`SHAW_LOAN_MASTER`, `SHAW_TRANS_MASTER`, `SHAW_POSTED_TRANS`, `SHAW_LOANS_NAME`, `SHAW_FEE_MASTER`, `SHAW_COLLATERAL_MASTER`) | 6 | **All stubs** — confirmed 1-field TODO (Phase 1 §2.4). None authored. The tables exist as load targets in `SHAW.yml` (`staging_tables`), but no field mapping or consolidation DDL. | Staging-load **count** recon (`staging_count == file_rows − 1`) via DB COUNT(*) (TC-010/011); reject/discard empty (TC-012). `SHAW_LOAN_MASTER` COUNT(*) = BASE anchor (TC-020/021). | Per-field load validation; parent/child consolidation field transforms; "post-consolidation = same table?" semantics. | OQ-7, OQ-A2, OQ-S1 |
| **P327 file-wiring conflict** | 1 file, 3+ copies | `SHAW.yml` wires the **1-field stub** `SHAW_P327.json`. A fully authored `P327_SHAW_M06_mapping.json` (252 fields, 2809 chars) exists **unwired**, plus a `_v2` and a misspelled `p327-shaw-m06-maaping.json` duplicate, and two rules copies. | With the canonical layout (decision #3): full 252-field structural validation via `validate_file --mapping P327_SHAW_M06_mapping.json` run **directly** (CLI/REST) (TC-052); record-length-aware count == BASE (TC-040, P327 is AT_BASE). | Orchestrator-gated (`run_etl_pipeline`) P327 validation — blocked until `SHAW.yml` re-points P327 to the canonical mapping; canonical-vs-duplicate disambiguation. | OQ-6 |

**Bottom line for §2:** Structural-presence, constant-record-length, and whole-file/per-table **count** coverage is achievable today for every stub artifact (the harness counts lines and queries `COUNT(*)` without needing a layout). **Field-level** coverage — positions, types, join keys, dedup keys, consolidation transforms — is uniformly blocked behind OQ-7 (CDS/contact layouts) and OQ-A2 (consolidation). P327 is the exception: it has a real layout that is merely mis-wired (OQ-6), so its field validation is runnable now via a direct `validate_file` invocation.

---

## 3. RISKS & DEFERRALS

Explicit, with the testing impact of each.

**(a) atoctran code 650 spec/config mismatch.**
The test spec lists code `650`; no `SHAW_ATOCTRAN_650_*` mapping/rules exist and 650 is not in `SHAW_ATOCTRAN.yaml`. Under `default_action: error`, any 650 line hard-fails L1 as `CT_UNKNOWN`.
*Testing impact:* TC-036 is authored as **enumerate-and-report-if-present** only — it cannot assert a configured-code count or layout. If 650 is genuinely in scope, a mapping + rules + umbrella entry must be authored first (escalation). Risk of false-negative if BA intended 650→605 rename and the rename never happened in config. Tracked as **OQ-650**.

**(b) No atoctran reconciliation spec vs the complete `tranert.yml`.**
TRANERT has a complete recon spec (7 record types with `key`, `cardinality`, `fields`, predicates, `ignored_fields`, `batch_header_count`). ATOCTRAN has **none** — no `config/e2e/sources/SHAW/reconciliation/atoctran.yml`. The per-code rules JSONs are pure field validation, with no cardinality or driving-population.
*Testing impact:* TRANERT count recon (TC-044) is **fully grounded and L2b-runnable today**. ATOCTRAN per-code cases (TC-031/032/033/034/060/061/062) degrade to **direction/bound/report-only** assertions against BASE — the true driver-row join (which staging rows produce which code) cannot be reconciled. This is the asymmetry the user should weigh: tranert is production-grade, atoctran is provisional. Tracked as **OQ-2** (+ OQ-300/605/700/100).

**(c) Undocumented variance bands → direction-only assertions (weaker).**
No tolerance/band exists anywhere in config or engine — `db_truth_comparator.py` is explicit: "Strict trimmed-string equality only." `reconciliation_spec.py` has no tolerance field.
*Testing impact:* For ABOVE_BASE (eac_collateral, xpr), BELOW_BASE (efb, efi), SPARSE_CONDITIONAL (hss, rlt) the suite asserts **direction only** and reports %delta — it will **not catch a magnitude regression** that stays on the correct side of BASE (e.g. efb dropping from −1.65% to −40% still passes "count < BASE"). Only the AT_BASE files (`==BASE`) get a hard equality. This is a deliberately weaker assertion class pending **OQ-B1**.

**(d) Three capabilities with no Valdo product primitive (harness-only → not Duo/MCP-drivable).**
1. **Count-variance-band reconcile** (whole-file count vs BASE anchor with a tolerance) — `db_truth_comparator` reconciles per-record-type file-vs-SQL, has no whole-file BASE comparator and no band.
2. **3-way cross-file referential** (200→contact ∧ 200→contact-account set membership) — the comparator is file-vs-SQL, not file-vs-file-vs-file.
3. **Concat-merge integrity** (`merged == Σ inputs − exact_dup_lines`) — no primitive does multi-file concat-merge reconciliation.
*Testing impact:* TC-040..043, 045 (BASE comparison), TC-070 (3-way), TC-080/081 (merge) are **harness-only** — implemented as shell/Python assertions (`wc -l`, `COUNT(*)` via extract, set membership). They are **not invokable by a single MCP tool call and are not GitLab-Duo/MCP-drivable**; they run in the E2E harness. This caps how much of the suite is "product-driven" — roughly a third of the cases require harness glue that does not exist as a shipped Valdo capability.

**(e) Per-code enumeration of undocumented codes is a harness responsibility.**
The product `MultiRecordValidator` only flags unknown lines per-line under `default_action: error`; it does **not** group/count undocumented codes. The harness `db_truth_comparator` keys `per_type_counts` to **configured** types only and emits a single `rows_unknown_type` aggregate.
*Testing impact:* TC-030 (group-by `TRANSACTION-CODE`, count every distinct value including a stray 650) is **harness-side** — a `PerTypeCount`-style tally extended to all observed discriminator values. MCP `validate_file`/`run_etl_pipeline` will surface unknown-code errors but will **not** emit the per-code histogram. Any reviewer expecting per-undocumented-code counts from the product alone will be disappointed; this is an explicit harness extension.

**Cross-cutting deferral:** The illustrative magnitudes (116,802 base; 114,875 efb; `AUDIT_SQL_LOADER`) are **spec-only** — repo-wide greps returned zero hits. They are used as magnitude illustration **only**, never as hardcoded expectations. BASE is always read at runtime.

---

## 4. EXECUTABILITY SUMMARY

Of the **32** authored cases (TC-INT-SHAW-001..081):

| Bucket | Count | Case IDs |
|---|---|---|
| **Runnable TODAY end-to-end** (all inputs grounded; structural/count/recon achievable with current config + harness) | **17** | 001, 002, 010, 011, 012, 020, 021, 030, 044, 050, 051, 052, 053, 054, 045, 060, 062 |
| **Blocked pending an OQ answer** (logic authored, but a value — count column, driver/key, band, dedup def, contact key — must land) | **14** | 022 (OQ-A1/A3), 031 (OQ-2/strengthen), 032 (OQ-2), 033 (OQ-2/300), 034 (OQ-2), 035 (OQ-300/605/700/100), 036 (OQ-650), 041 (OQ-B1), 042 (OQ-B1), 043 (OQ-B1), 061 (OQ-X1/X2), 070 (OQ-X1/X2/X3), 080 (OQ-M1/M2), 081 (OQ-M1/M2) |
| **Blocked pending a stub layout** (field-level coverage impossible until CDS/contact layouts land — only the structural/count slice runs now) | **1 (field-level only)** | 040 runs now for count==BASE; its CDS **field-level** coverage and TC-053/054 field coverage are deferred. (Counted under "runnable" for their count/structural slice; field slice tracked under OQ-7.) |

**Reading the buckets honestly:**
- **17 runnable today** includes the fully-grounded backbone: source receipt (001/002), staging-load recon (010–012), BASE anchor Source A (020/021), the atoctran per-code histogram + structural layout (030/050), **tranert full L2b recon (044/051)** — the strongest case in the suite — p327 direct structural validation (052), and constant-width checks (053/054 structural slice). Charge-off lineage (062) runs because its tranert half (32010, `CHG_OFF_CD='1'`) is grounded.
- **14 blocked pending an OQ** are authored and ready; they flip to runnable the moment the corresponding answer arrives. Most are clustered on **OQ-2** (atoctran drivers, 5 cases) and **OQ-B1** (bands, 3 cases).
- **Field-level CDS/contact coverage** is the one bucket that needs real artifacts (OQ-7), not just an answer — those layouts must be authored by BA before field validation exists at all.

**The 3–5 OQ answers that unblock the most cases** (repeated from §1.1 for the executive view):
1. **OQ-7** (CDS + contact layouts) → +11 cases gain field-level coverage.
2. **OQ-2** (atoctran driver tables/keys/cardinality) → 7 cases upgrade from directional to true reconciliation.
3. **OQ-A1/OQ-A3** (AUDIT_SQL_LOADER count column + casing) → unblocks TC-022 outright and hardens the BASE cross-check behind ~7 count cases.
4. **OQ-B1** (variance bands) → upgrades TC-041/042/043 from direction-only to magnitude.
5. **OQ-X1/OQ-X2** (contact-side account-key positions) → unblocks the 3-way referential TC-070 and the contact-join half of TC-061.

Answering items 1–3 alone moves the suite from **17/32 fully executable** to roughly **27/32**, leaving only the band-magnitude (OQ-B1), contact-key (OQ-X1/X2), and merge-definition (OQ-M1/M2) refinements outstanding.
</content>
</invoke>
