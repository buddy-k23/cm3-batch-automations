# Choose Your ETL Shape

A one-page decision guide for BAs and Product Owners: **which Valdo template do I start from?**

Read this before hand-authoring a mapping. Pick the shape that matches your data, follow the link to a template (or worked example), and adapt — don't rebuild from scratch.

---

## 1. Summary — every ETL shape Valdo supports

| # | Shape | Status | Template / Worked example | Best for | BA complexity |
|---|-------|--------|---------------------------|----------|---------------|
| 1 | Fixed-width single-record | Engine ready | `templates/etl/fixed_width_single_record.yml` (coming in Sprint 6 S6-3) | One record type per file (e.g., DDA accounts, customer master) | S |
| 2 | Fixed-width multi-record | End-to-end | [`config/mappings/SHAW_TRANERT.yaml`](../../config/mappings/SHAW_TRANERT.yaml) (worked example) | Header + multiple detail types + trailer (e.g., TRANERT NEW1/CUS/ORI/COD/CBRS/REC) | L |
| 3 | CSV / TSV | End-to-end | [`templates/etl/csv_file_comparison.yml`](../../templates/etl/csv_file_comparison.yml) + [worked sample](../../templates/etl/csv_file_comparison_sample/) ([README](../../templates/etl/csv_file_comparison_README.md)) | Comma- or tab-delimited files with a header row | S |
| 4 | Pipe-delimited | Engine ready | No dedicated template — adapt the CSV template, set `delimiter: "\|"` | Pipe-separated extracts (common in mainframe → distributed handoffs) | S |
| 5 | File → Database (staging load) | L1 + file_to_staging | [`config/e2e/sources/SHAW.yml`](../../config/e2e/sources/SHAW.yml) (`input_files` block) | Load file rows into a staging table, then validate inside the DB | M |
| 6 | Database → File (reconciliation) | L2b SQL truth | `templates/etl/db_to_file_reconciliation.yml` (planned — Sprint 7) | Reconcile a generated output file against an expected SQL result set | M |
| 7 | Database → Database | ADR queued | None — see [#379](https://github.com/buddy-k23/valdo/issues/379) | Comparing rows between two databases (source vs target) | — |
| 8 | JSON payloads | ADR queued | None — see [#377](https://github.com/buddy-k23/valdo/issues/377) | API response validation, structured event payloads | — |
| 9 | XML payloads | ADR queued | None — see [#378](https://github.com/buddy-k23/valdo/issues/378) | Legacy XML batch payloads, SWIFT/ISO message envelopes | — |

**Legend.** *Engine ready* = the parser/validator works today; the BA-facing template lands this sprint. *End-to-end* = template, worked example, and tests are all in tree. *ADR queued* = the shape is intentionally not built yet — the design is being captured in a linked issue first.

---

## 2. Decision tree — answer four questions, get a template

```
START. What does your data look like?

Q1. Is your source data in a FILE or a DATABASE?
    +-- FILE      --> go to Q2
    +-- DATABASE  --> go to Q4

Q2. Is the file DELIMITED (CSV, TSV, pipe) or FIXED-WIDTH?
    +-- DELIMITED       --> Shape 3 (CSV / TSV) or Shape 4 (pipe variant)
    +-- FIXED-WIDTH     --> go to Q3
    +-- JSON or XML     --> no template yet; see #377 (JSON) / #378 (XML)

Q3. Does every line in the file carry the SAME record shape?
    +-- YES, one record type per file   --> Shape 1 (fixed-width single-record)
    +-- NO, multiple record types per file
        (e.g., header + detail rows + trailer, or NEW1/CUS/ORI/COD)
                                         --> Shape 2 (fixed-width multi-record, SHAW TRANERT)

Q4. Are you comparing the database against a FILE or against ANOTHER DATABASE?
    +-- AGAINST A FILE        --> Shape 6 (DB-to-file reconciliation, L2b SQL truth)
    +-- AGAINST ANOTHER DB    --> no template yet; see #379
    +-- LOADING A FILE INTO IT --> Shape 5 (file -> staging table)
```

**How to read this:** start at Q1, follow the branches until you land on a Shape number, then jump to that row in the table above for the template path.

---

## 3. When in doubt — start from SHAW TRANERT

If your file has any of: a header row, a trailer row, multiple record types per line, cross-record-type checks (e.g., trailer count must equal sum of detail rows), or composite keys spanning multiple record types — **start from the SHAW TRANERT umbrella shape**: [`config/mappings/SHAW_TRANERT.yaml`](../../config/mappings/SHAW_TRANERT.yaml) plus the per-type rules under [`config/rules/SHAW_TRANERT_*.json`](../../config/rules/).

TRANERT is the most complete reference in the codebase — it exercises 7 cross-record-type checks, composite keys, and header/trailer reconciliation. **Caveat:** it is also the most complex. If your shape is genuinely simpler (one record type, no cross-type checks), do not adopt TRANERT wholesale — strip it down or start from the single-record template instead.

---

## 4. What if my shape isn't listed?

1. **Open a GitHub issue** describing your shape: sample of the data (with any PII redacted), the source system, expected validations, and the regulatory context (SOX, PCI-DSS, etc. — this drives prioritization).
2. **Upvote a planned shape** if your need maps to one already on the roadmap:
   - JSON payloads: [#377](https://github.com/buddy-k23/valdo/issues/377)
   - XML payloads: [#378](https://github.com/buddy-k23/valdo/issues/378)
   - Database-to-database: [#379](https://github.com/buddy-k23/valdo/issues/379)
3. **Hand-author as a fallback** — the [Usage & Operations Guide](../USAGE_AND_OPERATIONS_GUIDE.md) walks through writing a mapping from scratch. This is supported but slower; the templates exist precisely so most BAs never have to do it.

---

## 5. Related references

- [Usage & Operations Guide](../USAGE_AND_OPERATIONS_GUIDE.md) — full mapping/rules authoring reference
- [Mapping Quickstart](../MAPPING_QUICKSTART.md) — 5-minute mapping primer
- [Functionality Matrix](../FUNCTIONALITY_MATRIX.md) — feature-by-feature support
- [Add Record Type Playbook](../ADD_RECORD_TYPE_PLAYBOOK.md) — when you need to extend a multi-record config
- [Fixed-Width Mapping Checklist](../FIXED_WIDTH_MAPPING_CHECKLIST.md) — pre-flight checks before authoring fixed-width
- [CSV Standards](../CSV_STANDARDS.md) — delimited-file conventions

---

*Owned by the Valdo BA UX track. If a link here ever breaks, that's a bug — file an issue.*
