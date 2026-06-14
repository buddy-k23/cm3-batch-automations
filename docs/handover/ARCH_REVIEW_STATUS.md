# Arch-Review Loop — Rolling Status Pointer

> **This is the one file a fresh session reads to orient itself.** It is a
> *hint*; the runner prompt's `<bootstrap_state>` always re-derives the truth
> from the repo (per-story handoffs + GitLab issue checkboxes + a clean tree).
> Keep it short. Update it as part of every story's commit.

- **Trunk branch:** `feature/valdo-engine-v3`
- **Last pushed SHA:** `1518e2b`
- **Last completed story:** `#45 — R-16 — Flip ADR 0008/0009/0010/0014 to Accepted; burn down TODOs` (issue closed)
- **Next story:** None — arch-review backlog (#23–#45) is complete.
- **Pending decision blocking next?** N/A — backlog done.
- **Follow-up (not arch-review):** #47 — remove vestigial MultiRecordValidator methods.
- **Issue close policy:** Option 1 (API close) authorized — close each story's
  issue via the API once its gate is green and the push succeeded.

## How to start a new session
1. Read `AGENTS.md` -> "Arch-review loop state" (durable facts + gotchas).
2. Read `prompts/arch_review_story_runner_prompt.md` -> run `<bootstrap_state>`.
3. Backlog is complete; no next story. Work on follow-up #47 if desired.

## Expected `git status` at rest (NOT yours to commit unless a story owns it)
- ` M prompts/architecture_review_meta_prompt.md` -- pre-existing, owned by #44 (R-15).
- `?? "config/templates/DAOOperations (2).java"`, `?? "config/templates/TranertMapper (1).java"`,
  `?? prompts/L2B_Session_7_Continue_prompt.txt` -- gitignored leavings; leave alone.
- `?? _patch_*.py`, `?? _gate_*.py`, `?? _find_anchors.py`, `?? _fix_*.py`, `?? _check_lines.py` -- local scratch files; safe to delete.
Anything else dirty -> STOP and report.

## Gate baseline (no NEW failures vs this)
- Policy + numbers: `docs/handover/ARCH_REVIEW_TEST_BASELINE_2026-06-03.md`.
- Documented baseline: 43 pre-existing environmental failures (NOT regressions);
  harness offline subset: 267 passed / 1 skipped.
- Full suite at R-12: 43 failed / 2768 passed / 12 skipped.
- Unit suite at R-13/R-16: 30 failed / 2737 passed / 12 skipped (all failures pre-existing).

## Per-story audit trail (newest first)
- `#45` R-16  -> `docs/handover/ARCH_REVIEW_R-16_HANDOFF.md`
- `#44` R-15  -> `docs/handover/ARCH_REVIEW_R-15_HANDOFF.md`
- `#43` R-14  -> `docs/handover/ARCH_REVIEW_R-14_HANDOFF.md`
- `#42` R-13  -> `docs/handover/ARCH_REVIEW_R-13_HANDOFF.md`
- `#41` R-12  -> `docs/handover/ARCH_REVIEW_R-12_HANDOFF.md`
- `#40` R-11  -> `docs/handover/ARCH_REVIEW_R-11_HANDOFF.md`
- `#39` R-08  -> `docs/handover/ARCH_REVIEW_R-08_HANDOFF.md`
- `#38` R-07  -> `docs/handover/ARCH_REVIEW_R-07_HANDOFF.md`
- `#37` R-06b -> `docs/handover/ARCH_REVIEW_R-06b_HANDOFF.md`
- `#36` R-06a -> `docs/handover/ARCH_REVIEW_R-06a_HANDOFF.md`
- `#35` R-09  -> `docs/handover/ARCH_REVIEW_R-09_HANDOFF.md`
- `#34` R-10b -> `docs/handover/ARCH_REVIEW_R-10b_HANDOFF.md`
- `#33` R-10a -> `docs/handover/ARCH_REVIEW_R-10a_HANDOFF.md`
- `#32` R-05  -> `docs/handover/ARCH_REVIEW_R-05_HANDOFF.md`
- `#29` R-03b -> `docs/handover/ARCH_REVIEW_R-03b_HANDOFF.md`
- `#28` R-03a -> `docs/handover/ARCH_REVIEW_R-03a_HANDOFF.md`
- Session rollups: `ARCH_REVIEW_SESSION_1_HANDOFF.md`, `ARCH_REVIEW_SESSION_2_HANDOFF.md`
