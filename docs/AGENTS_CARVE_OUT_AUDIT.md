# AGENTS.md Hard-Rule #1 Carve-Out Audit

> **Purpose.** Hard rule #1 in `AGENTS.md` forbids modifying Valdo internals
> (anything under `src/`) when doing **E2E batch-testing harness** work
> (`scripts/e2e_lib/`, `config/e2e/`, `baselines/`). A bounded **carve-out**
> allows a `src/` change *only* when an ADR documents it. This ledger exists so
> that carve-out does not silently become routine and erode the rule (arch
> review R-08, dimension 2).
>
> **Authority.** This doc is the running ledger. `AGENTS.md` hard rule #1 is the
> rule; `docs/adr/` ADRs are the per-change authority. If this ledger and an ADR
> disagree, the ADR (plus the actual diff) wins — update the ledger.

## How to use this checklist

Run this audit **periodically** (recommended: at the start of every
arch-review wave, and whenever an MR/commit touches both `scripts/e2e_lib/` and
`src/`). For each harness-motivated `src/` change:

- [ ] Is the change **harness-motivated** (driven by `scripts/e2e_lib/`,
      `config/e2e/`, or `baselines/` work)? If it is a **core** engine change
      driven through the normal MR flow, hard rule #1 does not apply — record it
      below under "Core changes (rule #1 N/A)" for completeness, not as a
      carve-out.
- [ ] Does an **ADR** authorize it (`docs/adr/NNNN-*.md`)?
- [ ] Is the ADR's `src/` scope **minimal and additive** (no behaviour change
      for existing consumers, or a pure refactor)?
- [ ] Did the `src/` change **land as its own commit/MR**, separate from the
      harness change that motivated it?
- [ ] Is the ADR status current (`Accepted` once merged, not stuck at
      `Proposed`)?

**Red flag (escalate):** any `src/` change reachable from harness work that has
**no ADR**, or an ADR whose scope the diff exceeds, or a `TODO(valdo-gap)` that
should have been a wrapper but instead became a `src/` edit. File an issue and
stop the offending work until an ADR exists.

## Carve-out ledger (harness-motivated `src/` changes)

Newest first. "Shape" distinguishes the *kind* of carve-out: **bugfix**
(harness surfaced a pre-existing Valdo bug) vs **primitive** (harness needed a
reusable primitive that existed only as private internal logic).

| ADR | Date | Shape | `src/` files touched | Behaviour change? | ADR status | Carve-out OK? |
|---|---|---|---|---|---|---|
| [0008](adr/0008-extract-multi-record-reader-primitive.md) | 2026-05-27 | primitive | `src/validators/multi_record_validator.py` (expose `_read_and_group` dispatch as a reusable primitive) | No (pure refactor) | Proposed → flip to Accepted on merge | ✅ Yes — own MR, additive, formalised the carve-out paragraph in hard rule #1 |
| [0007](adr/0007-template-converter-preserve-string-literals.md) | 2026-05-16 | bugfix | `src/config/template_converter.py`, `src/config/ba_rules_template_converter.py` (`dtype=str` on `pd.read_csv`/`pd.read_excel`) | No (closes silent corruption; no caller relied on float inference) | Accepted | ✅ Yes — minimal, additive, regression-tested |
| [0006](adr/0006-fix-ba-friendly-rules-execution.md) | 2026-05-16 | bugfix (×2) | `src/config/multi_record_config.py` (schema coercion, #12); `src/validators/field_validator.py`, `src/validators/rule_engine.py`, `src/validators/_date_formats.py` (8 BA operators, #13) | No (existing operators byte-identical) | Accepted | ✅ Yes — one MR per bug, additive only |
| [0005](adr/0005-multi-record-pipeline-dispatch.md) | 2026-05-15 | primitive (dispatcher seam) | `src/pipeline/etl_pipeline_runner.py` (one `validate_multi_record` dispatcher branch + docstring); `src/pipeline/etl_config.py` (doc note) | No (strict additive dispatch target) | Proposed | ✅ Yes — single additive branch using an existing service |

### Core changes (rule #1 N/A — listed for completeness)

These `src/` changes are **core engine work** via the normal ADR + MR flow,
*not* harness-motivated, so hard rule #1's carve-out does not apply. They are
recorded here so the auditor can quickly confirm they are correctly classified
(i.e. they were not harness work mislabelled to dodge the rule).

| ADR | Date | `src/` files touched | Why rule #1 does not apply |
|---|---|---|---|
| [0014](adr/0014-record-reader-strategy-seam.md) | 2026-06 | record-reader strategy seam (R-06a/b) | Additive core seam via normal MR flow; behaviour-preserving (fixed-width default) |
| [0010](adr/0010-truthsource-backend-abstraction.md) | 2026-06 | `TruthSource` interface + `OracleTruthSource` (R-01a/b/c) | Core backend abstraction via normal MR flow |
| [0009](adr/0009-parameterized-cross-row-sequential.md) | 2026-06-03 | `src/validators/cross_row_validator.py` (`start`/`step` on `cross_row:sequential`) | ADR states explicitly this is a core rules-engine capability gap, not harness work; defaults keep existing rules unaffected |

## Trend / erosion watch

- **Harness-motivated carve-outs to date:** 4 ADRs (0005, 0006, 0007, 0008),
  spanning 5 logical fixes (0006 covers two bugs). All bugfix/primitive shape;
  none rewrote Valdo internals to suit the harness.
- **Pattern formalised:** ADR 0008 commit 4 appended the carve-out paragraph to
  hard rule #1 (see `docs/handover/L2B_SESSION_3_HANDOVER.md` §5.4 and
  `L2B_SESSION_4_HANDOVER.md` §4.3, `f0fa46a`).
- **Signal to escalate the rule itself:** if a *sixth* harness-motivated `src/`
  change appears that is **not** a pure bugfix or primitive extraction (i.e. the
  harness is shaping Valdo internals around itself), open an ADR to revisit the
  wording of hard rule #1 rather than granting another ad-hoc exception.

## Audit log

| Date | Auditor | Carve-outs reviewed | Findings |
|---|---|---|---|
| 2026-06-10 | arch-review R-08 (#39) | 0005, 0006, 0007, 0008 (harness); 0009, 0010, 0014 (core, N/A) | All harness `src/` changes are ADR-authorised, minimal/additive, and landed as their own change. No undocumented `src/` edits found. ADRs 0005/0008 still `Proposed` — flip to `Accepted` on merge (tracked by R-16/#45). |

## Optional CI guard (deferred follow-up)

The issue (#39) offers an **optional** CI lint that warns when a single MR
touches both `scripts/e2e_lib/` and `src/` without referencing an ADR. This is
recorded as a follow-up rather than implemented in this story to keep the change
docs-only and avoid coupling the audit to the single-developer trunk workflow
(no MRs are used today; see `prompts/arch_review_story_runner_prompt.md`). A
sketch for when MR-based review returns:

- A pipeline job greps the MR diff's changed-file list; if it includes both a
  `scripts/e2e_lib/**` path and a `src/**` path, require the MR description or a
  commit message to reference an `docs/adr/NNNN-*.md` file, else warn (non-
  blocking).

When implemented, link the job here and tick the optional acceptance box in #39.
