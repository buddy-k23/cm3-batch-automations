# CI Guardrails

This document catalogues every CI guardrail Valdo enforces on every PR
and push. Each entry covers what the guardrail does, why it exists,
where to look when it fails, and how to legitimately bypass it (where
possible).

---

## Workbook drift check (EC-S11, Sprint 3)

### Purpose

The Sprint 3 onboarding pipeline (EC-S6 through EC-S10) makes a
source-onboarding workbook (`templates/<SOURCE>_onboarding.xlsx`) the
**single source of truth** for the artefacts under
`config/mappings/<SOURCE>_*.json`, `config/rules/<SOURCE>_*.json`,
`config/e2e/sources/<SOURCE>.yml`, and the umbrella reconciliation
YAMLs.

This guardrail ensures the contract holds. On every PR (and every push
to `valdo-version-v4` / `main`):

1. Discover every committed workbook under `templates/*.xlsx`,
   excluding the blank scaffold `templates/source_onboarding_template.xlsx`.
2. Invoke `valdo onboard-source <workbook> --check` for each.
3. Capture the drift output, filter against the allowlist, and fail
   the job if any non-allowlisted drift line remains.

If this guardrail fires, it means someone hand-edited a generated
artefact under `config/{mappings,rules}/<SOURCE>_*.json` without
updating the workbook. The fix is **always** the same: regenerate the
artefact via the workbook, not via the JSON.

### Files

| File | Role |
|------|------|
| `.github/workflows/workbook-drift-check.yml` | GitHub Actions workflow. Pinned to Python 3.11 (de-facto floor since Sprint 1). |
| `.github/workflows/workbook-drift-allowlist.txt` | Substring allowlist for drift lines the guardrail tolerates. |
| `scripts/check_workbook_drift.py` | The drift driver. Owns workbook discovery, `--check` invocation, allowlist filter, and exit-code logic. |
| `scripts/check_workbook_drift.sh` | Local-developer convenience wrapper. Calls the same Python helper the CI workflow invokes. |
| `tests/unit/test_workbook_drift_check.py` | Unit tests for the filter logic and the workflow YAML's validity. |

### Local-equivalent run

The CI guardrail can be reproduced exactly on a developer workstation:

```bash
./scripts/check_workbook_drift.sh                                    # every workbook
./scripts/check_workbook_drift.sh templates/SHAW_onboarding.xlsx     # one workbook
```

The Python helper accepts the same arguments and a few extra options
useful in local debugging:

```bash
python scripts/check_workbook_drift.py \
    templates/SHAW_onboarding.xlsx \
    --allowlist .github/workflows/workbook-drift-allowlist.txt
```

Exit codes:

| Code | Meaning |
|------|---------|
| `0` | All workbooks passed (no drift, or only allowlisted drift). |
| `1` | At least one workbook reported non-allowlisted drift. |

### Allowlist mechanism

Drift detection is sometimes irreducible: certain rule shapes the
engine accepts (e.g. `cross_row:sequential` with `sequence_field` /
`start` / `step`) cannot currently be expressed in the BA-authored
workbook columns. Rather than block all of Sprint 3 on a perfect
emitter, EC-S11 introduces a narrow allowlist mechanism so a known
irreducible drift line does not gate every PR.

The allowlist file (`.github/workflows/workbook-drift-allowlist.txt`)
is a plain-text file: one substring per non-blank, non-comment line.
A drift line reported by `--check` is tolerated if it contains ANY
allowlist substring. Everything else fails the guardrail.

#### Convention: every entry needs a comment

Each allowlist entry MUST be preceded by a comment immediately above
the entry that documents:

1. **Which workbook + artefact** the entry covers
   (e.g. `SHAW / TRANERT_CUS rules`).
2. **Why the drift is irreducible** -- ideally with a link to an ADR
   entry, the relevant `CHANGELOG.md` block, or the originating
   sprint story.

Adding a bare substring with no rationale is a review-block. The intent
is to make every entry traceable so the allowlist does not become a
silent escape hatch.

#### Current entries

| Entry | Reason |
|-------|--------|
| `config/rules/SHAW_TRANERT_CUS_rules.json` | The hand-authored cross_row:sequential R028B countdown rule uses the engine-native `sequence_field`/`start`/`step` shape that BA workbook columns cannot currently express. Carve-out documented by EC-S9 / EC-S10 and asserted by `test_check_mode_clean_against_committed_state`. See `CHANGELOG.md` entry for EC-S9. |

### Adding a new source workbook

CI auto-discovers every `templates/*.xlsx` other than the blank
scaffold. The BA workflow for a new source is:

1. Copy `templates/source_onboarding_template.xlsx` to
   `templates/<NEWSOURCE>_onboarding.xlsx`.
2. Fill in the workbook sheets per `templates/source_onboarding_template_README.md`.
3. Run locally: `valdo onboard-source templates/<NEWSOURCE>_onboarding.xlsx`.
   This writes the full artefact tree under `config/`.
4. Run the CI guardrail locally to confirm clean state:
   `./scripts/check_workbook_drift.sh templates/<NEWSOURCE>_onboarding.xlsx`.
5. Commit the workbook AND the regenerated artefacts together.

CI will pick up the new workbook on the next push or PR; no workflow
edits required.

### Updating the allowlist

The allowlist exists to handle irreducible drift, not to suppress
inconvenient drift. Before adding an entry:

1. **Try to fix it in the workbook + emitter first.** If the drift is
   "the workbook can express this but the emitter is buggy", that is
   an emitter bug, not an allowlist entry.
2. **If genuinely irreducible**, file an issue or ADR entry that
   articulates why the workbook columns cannot capture the engine
   shape. Reference the issue from the allowlist comment.
3. **Add the comment block first**, then the substring. Without the
   comment, the entry will fail code review per the convention above.
4. **Pick the narrowest substring possible.** Prefer the artefact
   path (e.g. `config/rules/SHAW_TRANERT_CUS_rules.json`) over the
   reason text (e.g. `value for key 'rules' differs`) -- the reason
   text changes if the diff summariser is refactored, the path does
   not.

To remove an entry (the desired end state): make the workbook +
emitter capable of expressing the artefact shape, regenerate, confirm
`--check` is clean, and delete the line. The unit test
`test_committed_allowlist_contains_shaw_carve_out` will need updating
when the SHAW entry is finally removed.

### Why not just enforce zero drift?

Two answers, both sourced from Sprint 3 retros:

* **Engineering pragmatism.** EC-S9 ground out 65 - 1 = 64 of 65
  drift sources. Holding Sprint 3 hostage to the last 1 (which would
  require a meaningful workbook schema expansion in EC-S12+) is not
  worth the gate.
* **The allowlist mechanism IS the guardrail's guardrail.** Every
  allowlist entry needs a comment with rationale and an explicit
  reference. Reviewers can object to a vague entry. The line count
  in the allowlist file is a measurable tech-debt metric that should
  trend toward zero.

---
