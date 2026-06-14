# Arch-Review Story Handoff — R-15 (#44)

**Generated:** 2026-06-11
**Story:** [arch-review][R-15] Fix doc/prompt drift (review meta prompt version + reconciliation_spec docstring)
**Predecessor:** docs/handover/ARCH_REVIEW_R-14_HANDOFF.md
**Trunk:** feature/valdo-engine-v3 (commit b96fb66, pushed: yes)
**Safety snapshot:** arch-review-snapshot/R-15 @ 6ab0f01 — deleted after green: yes
**Depends-on satisfied:** none

## 1. What shipped (scope delivered)

- `prompts/architecture_review_meta_prompt.md` — `> **Status:**` line updated from
  "v2 — iterate as needed." to "v3 — iterate as needed." (CRLF-safe patch; file is CRLF).
- `scripts/e2e_lib/reconciliation_spec.py` — stale docstring example under "Spec shape
  (reference)" updated: bare discriminator-code keys (`"32000":`, `"32010":`) replaced with
  umbrella record-type names (`batch_header:`, `rt_32000:`, `rt_32010:`) matching the live
  spec at `config/e2e/sources/SHAW/reconciliation/tranert.yml`. Key fields also corrected
  to match the live spec (`BK-NUM-BRT` for batch_header, `LN-NUM-ERT` for rt_32000/rt_32010).
  (CRLF-safe patch; file is CRLF, no BOM.)
- `CHANGELOG.md` — R-15 entry added under `[Unreleased]`.
- `docs/handover/ARCH_REVIEW_STATUS.md` — rolling pointer updated to next story (#45 R-16).

## 2. Out of scope / deferred (with follow-up note)

- The two pre-existing F401 flake8 violations in `reconciliation_spec.py` (lines 95/98 after
  the docstring expansion shifted line numbers; were 87/90 before) are pre-existing from the
  initial checkin and are not regressions introduced by this story. Confirmed by stash/unstash
  comparison.
- No functional changes to any source file.

## 3. AGENTS.md compliance

- src/ touched? No. Pure doc/docstring fix in `scripts/` and `prompts/`.
- Secrets: none added. Audit table: not mutated. Quick actions: none used.

## 4. ADR(s)

- None required (doc-only story, no architectural decision).

## 5. Verification (actual results)

- black: pre-existing whole-codebase reformatting needed (323 files) — not a regression;
  no new files introduced by this story. Changed files are `.md` and `.py` (docstring only).
- flake8: 2 pre-existing F401 violations in `reconciliation_spec.py` (confirmed pre-existing
  by stash comparison); zero new violations.
- mypy: pre-existing errors (all pre-existing, zero new).
- pytest (unit): 30 failed / 2737 passed / 12 skipped — matches R-14 baseline exactly; no regression.
- Coverage: 80.28% (gate: 80% required — PASS).
- harness offline subset: 267 passed / 1 skipped — matches baseline exactly.
- Live-Oracle tests: SKIPPED (no SIT) — expected.

## 6. Docs updated

- `prompts/architecture_review_meta_prompt.md` (Status line v2 → v3)
- `scripts/e2e_lib/reconciliation_spec.py` (docstring example keys corrected)
- `CHANGELOG.md [Unreleased]` (entry added)
- `docs/handover/ARCH_REVIEW_R-15_HANDOFF.md` (this file)
- `docs/handover/ARCH_REVIEW_STATUS.md` (rolling pointer updated)

## 7. Acceptance criteria

- [x] Prompt status reads v3.
- [x] Docstring example uses `rt_<code>` / `batch_header` keys.
- [x] No functional change; flake8 clean (no new violations vs pre-story baseline).

## 8. Next story

- #45 — R-16 — Flip ADR 0008/0009 to Accepted; burn down TODOs; no blockers/decisions pending.
