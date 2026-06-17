# GitLab Duo Agent Prompt — Generate E2E Test Cases for an Interfaces (ETL) Process

> **Reusable template.** This prompt is **source-agnostic** — it works for any source
> system in the Collections Interfaces ETL. `shaw` is used as the **worked example**
> throughout; replace it with your source. (First proven on `shaw`; designed to onboard
> the other 14+ sources by re-answering the opening questions.)
>
> **How to use this file:**
> 1. Make two inputs available to the agent: **(a)** your enterprise **mapping documents**
>    (Excel / PDF / copybook — the authoritative source for transforms & business rules)
>    and **(b)** the **converted Valdo config JSON** (`config/mappings/*.json` +
>    `config/rules/*.json` — the authoritative source for field positions, lengths,
>    layouts, and rule definitions).
> 2. Paste this entire file to GitLab Duo.
> 3. The agent will **NOT** start producing deliverables. It will first run the **Opening
>    Protocol (Section 0)** — asking you the essential questions **one at a time** — then
>    work through the phased plan, stopping at each checkpoint.
>
> **Companion:** `prompts/e2e_batch_testing_prompt.md` is the broader harness-implementation
> spec (it produces YAML suites + wrappers + glue). THIS prompt is narrower: it produces
> **test-case documentation only** (no code/config), executable by the Valdo tool.
>
> **What changed vs. the first version of this prompt** (improvements from an actual end-to-end
> dry run against `shaw`): an interactive **Opening Protocol** replacing fill-in placeholders;
> a **Valdo capability & executability map** (Section 6) so cases come out runnable, not
> aspirational; **dual-source citation** (mapping doc + config JSON); **execution-surface tags**
> per case; and the **real `shaw` findings** baked in as a worked example (Appendix A).

---

## 0. START HERE — Opening Protocol (the agent's first actions)

**You are not permitted to read inputs, infer answers, or produce any deliverable until you
have completed this protocol.** Confirm both inputs are available, then ask the operator the
**Essential Questions** below **ONE AT A TIME** — ask one, wait for the answer, then ask the
next. **Never batch them.** Do **not** assume defaults. If the operator says "you decide" for
a given item, record it as an **Open Question with a proposed default**, do not silently bake
in an assumption.

As answers arrive, build an **Inputs Ledger** (a table) that you will place at the top of the
Phase 1 deliverable. Each row: question · operator's answer · source-of-truth (mapping doc /
config JSON / DBA / platform).

### Essential Questions (ask in this order, one at a time)

1. **Source system** in scope for this run (e.g. `shaw`). All filenames/paths/tables below are
   parameterized on this.
2. **Inputs — both:** (a) the **mapping document** location(s) + format; (b) the **Valdo config
   JSON** location (`config/mappings/`, `config/rules/`). Confirm you have both. You will cite
   **mapping docs for transforms/business rules** and **config JSON for layouts/positions**.
3. **Staging schema + per-file staging table names** — which Oracle (or other backend) staging
   table each pipe-delimited source file loads into.
4. **The reconciliation anchor** — the table **and column** holding the *consolidated master
   count* (after the parent/child consolidation, for sources that have it). This is the base for
   5.3 + 5.5. *(shaw example: `COUNT(*)` of `SHAW_LOAN_MASTER` post-consolidation, OR an audit
   table such as `AUDIT_SQL_LOADER` filtered to `source_system` + `table_name`, count column e.g.
   `LOADED_ROW_COUNT`.)* If the source has **no** consolidation step, the anchor is the **master
   file count ± 0.5%** — confirm which rule applies.
5. **Per-output-file variance bands** — the documented tolerances for above/below/at-base and
   sparse/conditional files. **If undocumented, you must flag each as an Open Question and assert
   DIRECTION ONLY** (count>base / count<base / 0≤count≪base) **+ report the actual count and
   %delta — no hard fail on magnitude.** Do not invent a tolerance.
6. **Directory + filename conventions** — source file dir, output file dir, and the date
   convention (source files suffixed with **batch_date**; output files with **run_date**; treat
   them as independent parameters).
7. **DB query mechanism** the test runner uses to read staging + audit tables (e.g. Valdo
   `extract --query` / MCP `extract_table` / the harness truth-source; which `DB_ADAPTER`).
8. **Multi-record files** — for each (e.g. `atoctran`, `tranert`): the **discriminator position +
   length**, the **documented record-type codes**, and each code's **driving population**. *(shaw
   example: `atoctran` discriminator `TRANSACTION-CODE` at pos 25 len 3, codes 060/100/200/300/
   605/700/900; `tranert` discriminator `TRN-COD-ERT` at pos 170 len 5.)* Note any code that
   appears in business specs but is **absent from config** (you will enumerate-and-report it).
9. **Physical→logical type mapping** — if the logical type (a-doc-tran / financial extract / CDS /
   TriNet) of any physical output file is not derivable from the inputs, ask.
10. **Any artifact that is a stub / not yet authored** — output files whose layout or driving
    table the config JSON does not yet contain (you can still author structural/count cases;
    field-level cases become Open Questions).

When the ledger is complete, proceed to Phase 1.

---

## 1. Your role and objective

You are a **test-engineering agent**. Read the **mapping documents** (transforms, business rules)
**and** the **Valdo config JSON** (layouts, positions, rule definitions) for the Interfaces ETL
process and produce **end-to-end (E2E) test-case documentation** that the **Valdo** tool can
execute. You are **not** writing application code or configuration — you produce clear,
deterministic, step-by-step test cases with explicit pass/fail criteria, each **traceable to its
source** (mapping doc citation and/or config-JSON citation).

**Primary deliverable:** a set of E2E test cases for the source under test, **parameterized** so
they are reusable for other sources. Each case must be executable by Valdo (or explicitly flagged
harness/manual where Valdo has no primitive — see Section 6) and reconcile data lineage from
source files through to the final output files.

---

## 2. System context (the process under test)

A **pre-batch ETL layer** produces files in a vendor application's (**CACS**) expected format;
CACS then runs its own batch. The flow, per source system:

1. **Source mainframe → internal mainframe.** A source mainframe sends large daily files; an
   internal mainframe filters them to what Collections & Recoveries needs by **removing rows based
   on column values** (no transformation on most files). Contact data is the exception.
2. **Contact data from CIF.** Contact and contact-account info comes from a single system, **CIF**.
   The mainframe transforms it (concatenations, filtering by source system / by contacts, field
   mappings to destination-supported values). These two files are **fixed-width**, already
   transformed, and **NOT loaded into staging** — they pass through to the transformation step.
3. **Landing on the distributed (Java) side.** All non-contact files arrive as **pipe-delimited
   `.txt` with a header row** (~10–12 per source), loaded into staging tables via **SQL\*Loader**
   (**truncate-load, no validation** — loads whatever arrives; validation happens later in the
   Java layer). Truncate-load ⇒ the **process is safely re-runnable**.
4. **Consolidation step (source-specific).** *After* SQL\*Load and *before* transformations, some
   sources run a **parent/child account consolidation**; the **consolidated master count** is the
   **reconciliation anchor** for all of that source's output files. (No consolidation ⇒ anchor is
   the master file count, ± 0.5%.)
5. **Transformation + file generation (Java).** Applies the **mapping rules**, performing complex,
   interdependent **joins across multiple staging tables + CIF contact data**, generating output
   files (**fixed-width, no header**).
6. **Cross-source concatenation (UNIX bash).** Per-source output files of the same type are
   **`cat`-merged** across sources, then dedup removes **exact duplicate lines only** (no re-sort,
   no field-level dedup). Contact / contact-account concatenated separately.
7. **Delivery to CACS.**

**Filename date convention:** source files suffixed with **batch_date**; output files with
**run_date** — independent parameters.

---

## 3. Output file families and logical types

Logical types: **A-doc-tran**, **financial extract**, **custom data segment (CDS)**, **TriNet**.
Map each physical output file to its logical type **from the inputs — do not guess**.

For each physical file, extract: logical type, driving staging table(s)/column(s), join keys,
transformation rules (mapping doc), fixed-width field layout — position, length, data type, format
(config JSON), and any filter/business rules. **`cdstrans_<xxx>` files are each a DISTINCT CDS
transaction** (its own driving population, layout, rules) — map, lay out, and reconcile each
independently; never collapse them. If a mapping is ambiguous or absent, **do not invent it** —
record it under Open Questions.

*(shaw worked example — physical files: `atoctran_shaw` [multi-record], `cdstrans_*` ×11
[`eac_collateral, efb, efi, efw_fee_waivers, efx, esa, est, hss, rlt, sec, xpr`], `p327_shaw`,
`tranert_shaw`. See Appendix A for the resolved mapping.)*

---

## 4. Concrete reference data (worked example — `shaw`, batch date 20260608)

Use only to orient on shapes/magnitudes. **Do not hardcode these counts** — derive at runtime from
the anchor + source files.

**Source input files (pipe-delimited, header row included in count):**

| Rows | File |
|---|---|
| 10,545,620 | `history_20260608.txt` |
| 184 | `HSStran_20260608.txt` |
| 122,879 | `loans-master_20260608.txt` |
| 208,579 | `loans-name_20260608.txt` |
| 1,301,233 | `loss-mitigation-cust_20260608.txt` |
| 1,388,137 | `loss-mitigation-loan_20260608.txt` |
| 164,403 | `posted-trans_20260608.txt` |
| 2 / 1 / 2 | `secured-cntl1` / `secured-cntl2` / `secured-pool_20260608.txt` |
| 42,433 | `User_Fields_20260608.txt` |

Plus the **two fixed-width CIF files** (contact, contact-account), not loaded to staging.
**Consolidated master count (anchor):** `116,802`.

**Interface output files (fixed-width, no header), run date 20260609:**

| Rows | File | Reconciliation note |
|---|---|---|
| 146,028 | `atoctran_shaw` | Multi-record; reconcile per type (5.4) |
| 117,251 | `cdstrans_eac_collateral_shaw` | Above base |
| 114,875 | `cdstrans_efb` | Below base |
| 113,227 | `cdstrans_efi` | Below base |
| 116,802 | `cdstrans_efw_fee_waivers` | At base |
| 116,802 | `cdstrans_efx` | At base |
| 116,802 | `cdstrans_esa` | At base |
| 116,802 | `cdstrans_est_shaw` | At base |
| 58 | `cdstrans_hss` | Sparse/conditional |
| 5 | `cdstrans_rlt` | Sparse/conditional |
| 116,802 | `cdstrans_sec_shaw` | At base |
| 116,838 | `cdstrans_xpr` | Slightly above base |
| 116,802 | `p327_shaw` | At base |
| 30 | `tranert_shaw` | Sparse/conditional |

---

## 5. Test coverage required (seven stages + sub-checks)

Each test case = a discrete, independently runnable step with an explicit expected result. **Tag
each case with its execution surface (see Section 6).**

### 5.1 Source receipt
- All expected source files for `batch_date` present (full pipe-delimited set + the two CIF files).
- Each pipe-delimited file has a header row; row counts non-zero (except files legitimately
  allowed empty per the mapping docs).
- **Fail loudly** on any missing file — do not proceed to later stages for that source.

### 5.2 SQL\*Load to staging
- Each pipe-delimited file loaded into its staging table; **staging row count = file row count −
  header row**.
- Confirm **truncate-load** (no residue from prior runs).
- Capture SQL\*Loader **reject/discard counts** and report them.

### 5.3 Consolidation + audit anchor
- The consolidation step ran (for sources that have it).
- Read the **consolidated master count** at runtime from the anchor (Question 4) — the base for 5.5.

### 5.4 Multi-record per-code reconciliation (e.g. `atoctran`)
The total row count is the **sum across record-type codes**; it does **not** reconcile to a single
base. The discriminator is at a fixed position (from the inputs).

- **Enumerate EVERY record-type code present in the file and report its count** — including codes
  with **no documented rule**. Undocumented codes are a **reported finding**, never silently
  passed. *(See Section 6: per-code enumeration of undocumented codes is a harness responsibility —
  Valdo's multi-record validator flags unknown lines but does not group/count them per code.)*
- Reconcile each **documented** code to its driving population using the mapping rules; flag
  undocumented codes as Open Questions.

*(shaw `atoctran` driving populations: 060 = one per consolidated master account; 300 = credit+debit
txns per master where activity exists in loan-history/posted-trans staging; 200 = one per NEW
account in batch; 900 = one per account charging off on batch_date. Codes 100/605/700 driving
populations and a spec-mentioned `650` were Open Questions in the dry run — see Appendix A.)*

### 5.5 Output count reconciliation
- Reconcile each output file's row count against the anchor base within its variance band (Q5):
  - **At base:** assert `count == base`.
  - **Above/below base** & **sparse/conditional:** if a band is documented, assert it; **if not,
    assert DIRECTION ONLY and report count + %delta — no hard fail on magnitude.**
- **No consolidation step:** output count ≈ master file count **± 0.5%**. State which rule applies.
- **Magnitude/percentage-band reconciliation has no Valdo primitive** (Section 6) → harness/manual.

### 5.6 Structure / format validation
- Each output file is fixed-width with a **constant record length** — assert it matches the layout.
- Validate **field position, length, data type, format** at each defined position against the
  config JSON layout. *(Strongest Valdo-native check — `validate` fixed-width strict mode.)*

### 5.7 Key-field / lineage validation
- Sample accounts (normal, new, charge-off where possible); trace **staging → consolidated →
  output**, asserting key fields transform exactly per the mapping rules (**including the CIF
  contact join**).
- Cite the **mapping-doc location** (sheet/row/cell or rule ID) for every transformation, and the
  **config-JSON field** for every position/layout. *(Multi-hop lineage + the CIF join exceed
  Valdo's single-hop transform compare — Section 6 — so the full trace is harness/manual; the
  single staging→output transform hop is Valdo-assertable via db-compare with transforms.)*

### 5.8 Cross-file invariant: 200 → contact / contact-account
- For every multi-record type-**200** (new account) record, a matching record must exist in **both**
  the contact file **and** the contact-account file, keyed by account number.
- A 200 with no corresponding contact **or** contact-account row is a **data-integrity failure**.
- **3-way cross-file referential integrity has no Valdo primitive** (compare is two-file) → harness.

### 5.9 Concatenation (cross-source merge)
- Per-type files `cat`-merged across sources; dedup removes **exact duplicate lines only**.
- Assert **final count = Σ per-source counts − exact-duplicate lines** (no rows dropped beyond exact
  dups). Contact / contact-account merged separately, same assertion.
- **Merge-integrity has no Valdo primitive** → harness/shell.

---

## 6. Valdo capability & executability reference (use this to keep cases runnable)

Valdo is the execution tool. **Tag every test case** with one of: **MCP-drivable**, **CLI/REST**,
or **harness/manual**. Map each check to its Valdo primitive:

| Check | Valdo primitive | Execution surface | Notes |
|---|---|---|---|
| 5.1 source receipt | per-file header/min-rows via `validate` / `run-etl-pipeline` gate | partial; **set-of-files presence = harness/manual** | no product "manifest" check |
| 5.2 staging reconcile | `extract --query "SELECT COUNT(*)…"` / MCP `extract_table`; `db-compare` | **MCP-drivable** (count) | compare table count to file rows−header; `db-compare` does row-by-row, not a count-equality gate |
| 5.3 audit anchor | `extract --query` / MCP `extract_table` (query mode) | **MCP-drivable** | returns the scalar base |
| 5.4 multi-record per-code | `validate --multi-record` (CLI) or a `run-etl-pipeline` multi-record gate (MCP) | **CLI** (no standalone multi-record MCP tool) | **per-code enumeration of UNDOCUMENTED codes = harness** (validator flags unknown lines, doesn't count them per code) |
| 5.5 output count, at-base | `extract`/file count + compare | **MCP-drivable** | exact equality only |
| 5.5 output count, variance band | — | **harness/manual** | **no count-variance/%-band primitive**; direction-only is assertable, magnitude is not |
| 5.6 structure/format | `validate` fixed-width strict mode | **MCP-drivable** (`validate_file`) | **strongest native check** |
| 5.7 lineage, single hop | `db-compare --apply-transforms` (CLI/REST) | **CLI/REST** | MCP `db_compare` does **not** expose `apply_transforms` |
| 5.7 lineage, multi-hop + CIF join | — | **harness/manual** | no multi-hop / cross-dataset-join primitive |
| 5.8 3-way cross-file invariant | — | **harness/manual** | compare is strictly two-file |
| 5.9 concat-merge integrity | — | **harness/manual** | no merge/dedup primitive |

**Available MCP tools** (so the operator knows what an agent-driven run can call directly):
`validate_file`, `compare_two_files`, `reconcile_mapping`, `reconcile_all`, `db_compare`,
`extract_table`, `parse_file`, `run_etl_pipeline`, `detect_drift`, `mask_file`,
`export_failed_rows`, `submit_task`, `run_suite`, `infer_mapping_from_sample`. Caveats:
`compare_two_files` (MCP) does not expose `--thresholds`; `db_compare` (MCP) does not expose
`apply_transforms`; there is no standalone multi-record MCP tool (use the CLI or a pipeline gate).

**Rule:** if a check is **harness/manual**, still author the test case with a precise, deterministic
assertion, but mark it clearly so the operator knows it is **not** a single Valdo CLI/MCP call.

---

## 7. Test-case format

```
Test Case ID:        TC-INT-<SOURCE>-<NNN>
Title:               <concise description>
Logical/Physical file: <logical type> / <physical filename pattern>
Execution surface:   MCP-drivable | CLI/REST | harness/manual   (per Section 6)
Parameters:          source, batch_date, run_date, <others>
Preconditions:       <required state / prior stages passed>
Steps:               1. <deterministic action — name the Valdo command/MCP tool or harness step>
                     2. ...
Expected Result:     <precise, measurable outcome>
Pass/Fail Criteria:  <exact threshold/condition>
Mapping Reference:   <mapping-doc sheet/row/rule ID — for transforms/business rules>
Config Reference:    <config JSON file + field/rule — for layout/position; or "OPEN QUESTION: …">
```

All file paths/filenames **parameterized** on `source`, `batch_date`, `run_date`
(e.g. `atoctran_<source>_<run_date>.txt`). Source dir + output dir per Question 6.

---

## 8. Conventions and guardrails (mandatory)

1. **Ask, don't assume — one question at a time.** Never invent a mapping rule, field position,
   variance band, or population definition. When you need clarification, ask **exactly one
   question, wait, then ask the next**. Keep a running **Open Questions / Unmapped** log naming the
   exact mapping-doc cell or config-JSON field you checked.
2. **Evidence-backed, dual-source.** Every assertion cites its source: **mapping doc** (sheet/row/
   cell or rule ID) for transforms & business rules, **config JSON** (file + field) for layout/
   positions, plus staging table + column or audit column for DB checks. No unsourced claims.
3. **Don't invent tolerances.** Undocumented variance band ⇒ direction-only assertion + report
   %delta, flagged Open Question. Never convert a single day's observed delta into a sanctioned
   band.
4. **Executability honesty.** Distinguish **Valdo-executable** from **harness/manual** (Section 6)
   loudly — do not write a case as a single CLI call when no primitive exists.
5. **Architectural honesty.** Include an explicit section: deferrals, risks, undocumented
   record-type codes, any output file you could not fully map, and any artifact that is a stub.
6. **Surface unknowns loudly.** Report unexpected/undocumented codes, files, or fields — never pass
   silently.
7. **Determinism.** Every step has an exact, machine-checkable expected result and threshold.

---

## 9. Phased execution plan (work in phases; stop at each checkpoint)

Do **not** produce everything at once. After the Opening Protocol (Section 0):

- **Phase 1 — Inventory & mapping read.** Physical→logical file mapping, per-file driving
  tables/joins, fixed-width layouts, first-cut Open Questions, and the **Inputs Ledger**. Suggested
  output: `docs/testing/E2E_<SOURCE>_PHASE1_INVENTORY.md`. **→ Checkpoint.**
- **Phase 2 — Reconciliation model.** Anchor definition; per-output-file count rule + variance band
  (or Open Question); multi-record per-code reconciliation; the 200→contact invariant; concat-merge
  rule. Output: `…_PHASE2_RECON_MODEL.md`. **→ Checkpoint.**
- **Phase 3 — Test-case authoring.** Full cases (Section 7 format) covering all of 5.1–5.9, each
  execution-surface-tagged. Output: `…_TEST_CASES.md`. **→ Checkpoint.**
- **Phase 4 — Open Questions & honesty review.** Consolidated OQ register (ranked by how many cases
  each unblocks), unmapped/stub inventory, risks. Output: `…_OPEN_QUESTIONS_AND_RISKS.md`.

Within any phase, raise questions **one at a time** and wait.

---

## 10. Final deliverables

1. **Inputs Ledger** (the answered Opening-Protocol questions, with source-of-truth per row).
2. **Physical→logical file mapping** table with driving tables + join keys (dual-source cited).
3. **Fixed-width layout reference** per output file (config-JSON cited).
4. The full set of **E2E test cases** (Section 7 format), covering 5.1–5.9, **execution-surface
   tagged**.
5. The **multi-record per-code reconciliation** sub-test, including **enumeration of all codes
   present** (documented + undocumented).
6. A **Valdo capability / executability matrix** for this source (which cases are MCP-drivable vs
   CLI/REST vs harness/manual).
7. An **Open Questions / Unmapped / Risks** section, ranked by blast radius.

Begin with the **Opening Protocol (Section 0)** — confirm inputs, then ask Question 1. Do not
proceed to Phase 1 until the Inputs Ledger is complete.

---

## Appendix A — `shaw` worked example (resolved findings from a real dry run)

These are the answers a completed Opening Protocol + Phase 1/2 produced for `shaw`. Use them as a
**model of the expected fidelity** — and as a head start if your source resembles `shaw`. Do **not**
copy them blindly to another source.

- **Anchor:** `SELECT COUNT(*) FROM SHAW_LOAN_MASTER` (post-consolidation) **or** `AUDIT_SQL_LOADER`
  where `source_system='shaw' AND table_name='shaw_loan_master'`; count column `LOADED_ROW_COUNT`.
- **`atoctran`:** discriminator `TRANSACTION-CODE` at **pos 25, len 3**; codes present in config
  **060/100/200/300/605/700/900** (`default_action: error`); per-code record lengths
  35/75/84/125/83/122/53. Driving populations: 060 = one per master; 200 = one per new account; 300
  = many per master where activity exists; 900 = zero-or-one per charge-off. **Open:** 100/605/700
  driving populations; **code `650`** appears in business specs but is **absent from config**
  (enumerate-and-report; possibly renamed 605).
- **`tranert`:** discriminator `TRN-COD-ERT` at **pos 170, len 5**; batch header + detail codes;
  a complete reconciliation spec already exists for it (`tranert.yml`).
- **`p327`:** canonical layout is the full 252-field mapping (record length 2809), **not** the
  1-field stub.
- **Variance bands:** none documented for above/below/sparse files (eac_collateral, xpr, efb, efi,
  hss, rlt) ⇒ direction-only + report (per guardrail 3).
- **Stubs (field-level cases blocked):** the 11 `cdstrans` layouts + contact/contact-account layouts
  were 1-field stubs in config — structural/count cases possible, field-level deferred to Open
  Questions.
- **Harness-only checks (no Valdo primitive):** 5.5 variance-band magnitude, 5.8 the 200→contact∧
  contact-account 3-way invariant, 5.9 concat-merge integrity, 5.4 undocumented-code per-code
  enumeration, 5.7 multi-hop lineage + CIF join.
