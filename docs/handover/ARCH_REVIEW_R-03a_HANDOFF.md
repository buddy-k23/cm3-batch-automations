# Arch-Review Story Handoff — R-03a (#28)

**Generated:** 2026-06-04
**Story:** [arch-review][R-03a] Decide L2 regeneration: ship valdo regenerate or retire the gate (ADR)
**Predecessor:** docs/handover/ARCH_REVIEW_R-04b_HANDOFF.md
**Trunk:** feature/valdo-engine-v3 (commit 47610a3, pushed: yes)
**Safety snapshot:** arch-review-snapshot/R-03a @ a49a1ba — deleted after green: no (kept; story is a STOP boundary)
**Depends-on satisfied:** none (R-03a has no dependency; it gates #29)

## 1. What shipped (scope delivered)
- `docs/adr/0012-l2-regeneration-disposition.md` — ADR (Proposed) weighing the
  two options for `L2_regeneration` and giving a clear recommendation:
  - **Option A** — ship a `valdo regenerate` CLI over
    `oracle_expected_generator.generate_expected_from_oracle`, wire it into
    `run_source.py`, flip `VALDO_E2E_ENABLE_L2`, decide blocking policy.
  - **Option B (recommended)** — retire `L2_regeneration` as a gate; L2b
    SQL-truth + L3 baseline already cover the truth and regression axes, and the
    existing generator is SQL-manifest driven (same truth axis as L2b), so a
    regeneration gate would add cost without new signal.
- `CHANGELOG.md` `[Unreleased]` — a docs/decision note (no behaviour change).

This is a **DECISION story**: deliverable is the ADR only. **No code or config
changed.** #29 (R-03b) is deliberately NOT implemented.

## 2. Out of scope / deferred (with follow-up note)
- All implementation is R-03b (#29), blocked on the user's Option A/B choice.
- If Option A is chosen, R-03b must be re-sized (new CLI + TruthSource routing +
  manifest schema sub-decision + tests + docs + blocking policy) — not "Small".

## 3. AGENTS.md compliance
- src/ touched? **No**. Docs-only (one ADR + CHANGELOG note).
- Secrets: none added. Audit table: not mutated. Quick actions: none used.
- Do-not-guess honored: gate blocking semantics and any manifest schema are
  left for the user; the ADR proposes, it does not decide for them.

## 4. ADR(s)
- `docs/adr/0012-l2-regeneration-disposition.md` — **Proposed** (awaiting user
  decision; flips to Accepted with R-03b's merge per the #45/R-16 sweep).

## 5. Verification (actual results)
- Docs-only change; no source/test/config touched.
- black/flake8/mypy: not applicable to a Markdown-only delta (no Python changed).
- Full gate not re-run for a docs-only change beyond a pre-commit sanity check;
  numbers carried from R-04b baseline (43 env failures, ~2669 passed,
  cov ~80.23% core). See completion summary for the sanity check actually run.

## 6. Docs updated
- `docs/adr/0012-l2-regeneration-disposition.md` (new)
- `CHANGELOG.md` (`[Unreleased]` decision note)

## 7. Acceptance criteria
- [x] ADR added (Proposed) with a clear recommendation and the trade-offs.
- [x] The downstream implementation story (R-03b) is unblocked *in design* by
      the chosen option — the ADR maps each option to a concrete R-03b scope.
      (Final unblock requires the user to pick A or B; see §8.)

## 8. Next story
- #29 — R-03b — Implement the L2-regeneration decision. **BLOCKED** on the user
  selecting Option A (ship `valdo regenerate`) or Option B (retire the gate).
  Recommendation on record: **Option B (retire)**. STOP-AND-REVIEW here.
