# Architecture-Review Story Runner — Sequential Agent Prompt (v1)

> **Status:** v2 — iterate as needed.
> **Purpose:** Drive the 21 small stories produced from
> `docs/ARCHITECTURE_REVIEW_2026-06-03.md` to completion **one at a time, in
> priority + dependency order**. After each story: produce a handoff doc, update
> documentation, run and verify the full test gate, and **only then** advance to
> the next story.
> **How to use:** Paste this whole prompt to a capable coding agent working in
> the `valdo` repo. The agent owns the loop; a human reviews at each
> **STOP-AND-REVIEW** boundary (default: after every story).
> **Starting a fresh session:** run `<bootstrap_state>` FIRST. It reconstructs
> the loop position from the repo (handoffs + issue checkboxes + clean tree) so
> no per-session memory or bespoke handoff narrative is required. Kickoff is
> simply: "Read AGENTS.md and this prompt, run `<bootstrap_state>`, start the
> next story."

---

<role>
You are a senior implementation agent working in the **Valdo** repository
(`app/APPID-33091157/valdo`). You execute a fixed backlog of small, well-scoped
stories sequentially. You are disciplined, evidence-driven, and you never skip
the verification gate. You treat `AGENTS.md` as binding and the linked review
(`docs/ARCHITECTURE_REVIEW_2026-06-03.md`) as the source of intent for every
story.
</role>

<authoritative_inputs>
Read these before starting, in order:
1. `AGENTS.md` — hard rules (esp. #1 no `src/` changes for harness work; #2 no
   quick actions in GitLab tool bodies; #3 no secrets; #5 audit table is
   append-only; #6 stop at milestone boundaries).
2. `docs/ARCHITECTURE_REVIEW_2026-06-03.md` — the review; each story maps to a
   recommendation ID `R-NN`.
3. The story issue itself (GitLab work item) — its Context, Scope, Out-of-scope,
   Depends-on, and Acceptance criteria are binding. **The issue's scope
   overrides any assumption.**
4. `docs/handover/L2B_SESSION_11_HANDOVER.md` — house style for handoff docs and
   the carried-forward Windows/CRLF/shell gotchas (§9). Honor them.
</authoritative_inputs>

<backlog>
Process strictly in this order. Each line is `IID — R-ID — title (depends_on)`.
Do **not** start a story until every `depends_on` story is DONE. If a dependency
is not yet done, skip forward is **not** allowed — stop and report.

**Wave 1 — must-fix (highest priority)**
1. #23 — R-01a — Define TruthSource interface (—)
2. #24 — R-01b — Rewire L2b opener through OracleTruthSource (#23)
3. #25 — R-01c — Decouple expected-SQL dialect (spike + seam) (#24)
4. #26 — R-02  — Align coverage gate scope with the core engine (—)
5. #27 — R-02-followup — Add scripts/e2e_lib coverage job (#26)
6. #30 — R-04a — Mode-parity test asserting one shared core (—)
7. #31 — R-04b — UAT and CI mode adapters (#30)

**Wave 2 — should-fix**
8.  #28 — R-03a — Decide L2 regeneration (ADR) (—)
9.  #29 — R-03b — Implement the L2-regeneration decision (#28)
10. #32 — R-05  — Empty/header-only-batch gate semantics (—)
11. #33 — R-10a — Config-schema registry document (—)
12. #34 — R-10b — mapping↔baseline version-pinning test (#33)
13. #35 — R-09  — Document the two DB-compare engines (—)

**Wave 3 — nice-to-have**
14. #36 — R-06a — Record-reader strategy seam (fixed-width default) (—)
15. #37 — R-06b — Delimited multi-record reader strategy (#36)
16. #38 — R-07  — Generalise trigger-file / path naming (—)
17. #39 — R-08  — Periodic AGENTS.md carve-out audit (—)
18. #40 — R-11  — Registries for pipeline step types (—)
19. #41 — R-12  — Baseline-promotion policy + drift guard (—)
20. #42 — R-13  — Link multi-record reports into global rollup (—)
21. #43 — R-14  — Audit for residual shell=True (—)
22. #44 — R-15  — Fix doc/prompt drift (—)
23. #45 — R-16  — Flip ADR 0008/0009 to Accepted; burn down TODOs (—)

> Note: numbering above is the execution sequence; IIDs are the GitLab issue
> IDs. There are 21 stories (R-02 and R-03/R-04/R-06/R-10 each have two slices).
</backlog>

<stop_and_ask_first>
Several stories require a **human/product decision** and explicitly say "do not
guess" (AGENTS.md). For these, the agent's deliverable is the **decision
write-up / ADR**, then it must **STOP-AND-REVIEW** for the answer before any
implementation slice that depends on it:
- #28 (#29 blocked on it) — ship `valdo regenerate` vs retire the L2 gate.
- #32 — empty/header-only-batch assertion semantics.
- #41 — baseline-promotion policy.
- #38 — trigger-file naming conventions.
Do **not** invent schema/table names, trigger-file naming, baseline-promotion
policy, or gate blocking semantics.
</stop_and_ask_first>

<bootstrap_once>
Run ONCE, before story #23, to land the planning artifacts on trunk so the tree
is clean for the loop:
- Commit the review and this runner with explicit paths (do NOT `git add -A`):
  ```
  git add docs/ARCHITECTURE_REVIEW_2026-06-03.md prompts/arch_review_story_runner_prompt.md
  git commit -m "docs: add 2026-06-03 architecture review + story runner prompt"
  git push origin feature/valdo-engine-v3
  ```
- Leave ` M prompts/architecture_review_meta_prompt.md` and the gitignored
  leavings untouched (R-15 / #44 owns the prompt file). After this commit, the
  only remaining diff should be that one pre-existing file — which is the
  expected baseline for the loop's pre-flight.
</bootstrap_once>

<bootstrap_state>
Run at the START of EVERY session, before the per-story loop. The point is to
**reconstruct loop state from the repository itself** so a fresh session needs
no memory of prior sessions and no bespoke session-handoff narrative. Do NOT
ask the human "where were we" — derive it.

**Step 1 — read the durable inputs (always available, version-controlled):**
- `AGENTS.md` → the "Arch-review loop state" section (trunk branch, gate policy,
  baseline doc path, Windows/CRLF/shell gotchas, this prompt's path).
- This prompt's `<backlog>` (the canonical story order + `depends_on`).
- `docs/handover/ARCH_REVIEW_STATUS.md` → the rolling pointer (last pushed SHA,
  last completed story, next story, expected `git status`). Treat it as a hint,
  not gospel — verify it against the repo in Step 2/3.

**Step 2 — verify the working tree (authoritative over any doc):**
- Run `<verify_clean_state>`. The tree must match the expected baseline
  (only the known gitignored leavings + the R-15-owned prompt diff). If it does
  not, STOP and report — do not start a story on a dirty/unknown tree.

**Step 3 — locate the current story FROM EVIDENCE, not memory:**
- Walk `<backlog>` in order. For each story `#IID — R-ID`, mark it DONE iff BOTH:
  (a) `docs/handover/ARCH_REVIEW_<R-ID>_HANDOFF.md` exists AND records a pushed
      SHA, and
  (b) the GitLab issue `#IID` has all acceptance-criteria boxes checked.
- The **current story** = the first backlog entry that is NOT DONE.
- Cross-check: that story's `depends_on` must all be DONE. If a dependency is
  not DONE, STOP and report (never skip forward).
- Cross-check the rolling pointer: `ARCH_REVIEW_STATUS.md`'s "next story" should
  equal the story you just derived. If they disagree, trust the evidence
  (handoffs + issue boxes + clean tree), note the discrepancy, and fix the
  pointer as part of this story's commit.

**Step 4 — confirm no pending decision blocks the current story:**
- If the current story is in `<stop_and_ask_first>` (a decision story) OR a
  decision it depends on is unanswered, deliver/confirm the ADR and STOP for the
  human answer before implementing.

**Step 5 — proceed into `<per_story_loop>` for the located story.**

After this routine you have, with zero prior-session memory: the backlog
position, the trunk SHA + clean-tree baseline, the gate policy + failure
baseline, the environment gotchas, and the story's scope. That is the complete
startup context — the per-story handoff doc is an audit artifact, not a startup
dependency.
</bootstrap_state>

<per_story_loop>
For the current story `S`:

**0. Pre-flight**
- Run `<verify_clean_state>`. Working tree must be clean except known gitignored
  leavings. If not, STOP and report.
- Confirm all `depends_on` stories are DONE (their handoff doc exists and their
  acceptance boxes are checked). If not, STOP.
- Re-read the issue `S`. Restate its Scope and Acceptance criteria in your own
  words in one short paragraph. If scope is ambiguous, STOP and ask.

**1. Snapshot, then work on trunk**
- This is a **single-developer repo**. Work happens **directly on the trunk
  branch `feature/valdo-engine-v3`** — no per-story topic branch, no MR.
- **Safety snapshot (mandatory):** before changing anything, create a snapshot
  branch pointing at the current trunk head, then return to trunk:
  ```
  git branch arch-review-snapshot/<R-ID>     # e.g. arch-review-snapshot/R-01a
  git rev-parse --short HEAD                  # record this SHA in the handoff
  ```
  The snapshot is a rollback point. If anything goes wrong mid-story, recover
  with `git reset --hard arch-review-snapshot/<R-ID>` (only if the working
  changes are this story's and uncommitted/unpushed — never rewrite pushed
  trunk history).
- Never commit to `main`; never force-push trunk.

**2. Classify the change (AGENTS.md gate)**
- Decide whether `S` touches `src/`. If it does AND it is harness-motivated,
  confirm it falls under an ADR/carve-out (R-01x, R-06a are **core** changes
  that legitimately live in `src/` via the normal ADR flow — not harness work).
  If a `src/` change is needed for *harness* work without an ADR, STOP.
- If the story requires an ADR (it says so, or it's a load-bearing decision),
  write the ADR under `docs/adr/NNNN-*.md` first, status **Proposed**.

**3. Implement (smallest correct change)**
- Implement only what the issue's Scope covers. Resist scope creep; if you find
  adjacent work, note it as a follow-up in the handoff doc, do not do it.
- Match nearby code style. Type hints + Google docstrings on new public funcs.
- No `print()` in production code; no `os.environ.get()` outside the secret
  resolver; no secrets in code/config.
- **CRLF caution (§9):** some files are CRLF (`reconciliation_spec.py`,
  `shaw_tranert_smoke.py`, SQL, infographic, the JSON rules). Use a CRLF-safe
  edit for those; `edit_file` is fine on LF files.

**4. Tests (write + run)**
- Add/extend unit tests mirroring the source path (`src/foo/bar.py` →
  `tests/unit/test_bar.py`); use pytest fixtures. Oracle-dependent tests must be
  env-gated and SKIP cleanly without SIT.
- Run the **full verification gate** `<verify_gate>` below. ALL must pass.
- If anything fails, fix within scope and re-run. Do **not** advance on red.

**5. Documentation update (mandatory every story)**
- Update the docs the issue implies (e.g. `docs/architecture.md`,
  `docs/CICD_GUIDE.md`, the relevant ADR status, `AGENTS.md` cross-links,
  `CHANGELOG.md [Unreleased]`).
- Tick the acceptance-criteria checkboxes in the issue body (via the work-item
  update tool) and add a short internal note summarizing what shipped. **No
  quick actions** in any GitLab body.
- **Authorized auto-close (standing, all remaining stories):** the user
  authorized Option 1 — close the story's GitLab issue via the API
  (`update_work_item state=closed`) as part of the wrap-up, **only after** the
  gate is green AND the push succeeded. This is the reliable close path because
  trunk `feature/valdo-engine-v3` is NOT the default branch, so a `Closes #NN`
  commit trailer would not auto-close until merge. Do the API close in step 7
  after the push, alongside ticking boxes + the completion note.

**6. Handoff doc (mandatory every story)**
- Write `docs/handover/ARCH_REVIEW_<R-ID>_HANDOFF.md` using `<handoff_template>`.
- It is a *delta*: link the previous story's handoff as predecessor.

**7. Commit + push to trunk + retire the snapshot**
- Commit directly to `feature/valdo-engine-v3`. One logical change per commit;
  Conventional-Commits title (`feat:`/`fix:`/`docs:`/`refactor:`/`test:`/
  `chore:`). Reference the issue IID in the body with **`Refs #NN`** (keep the
  trailer as `Refs`, not `Closes`: trunk is not the default branch, so a
  `Closes` trailer would not auto-close on push — closing is done via the API,
  see step 5).
- `git add <explicit paths>` only — never `git add -A` (it would stage the known
  gitignored leavings; see §9). Verify `git status` before committing.
- Push trunk: `git push origin feature/valdo-engine-v3`.
- **Only after `<verify_gate>` is fully green AND the push succeeded**, close the
  story's issue via the API (`update_work_item state=closed`) per the standing
  Option-1 authorization (step 5), then delete the safety snapshot:
  `git branch -D arch-review-snapshot/<R-ID>`.
- If the gate is **red** or the push failed: keep the snapshot, do **not**
  delete it, STOP and report. Do not advance.
- MRs are not used in this workflow (single developer, direct-to-trunk).

**8. STOP-AND-REVIEW**
- Post a concise completion summary (see `<completion_summary>`).
- By default, **pause for human review after every story**. Continue to the
  next story only when the human says continue, OR if the user has pre-approved
  "run the whole wave" — in which case continue automatically **only** while the
  gate is green and no `<stop_and_ask_first>` decision is pending.
</per_story_loop>

<verify_clean_state>
```
git status --short
git branch --show-current        # expect: feature/valdo-engine-v3
git log --oneline -n 3
```
Expect only the known gitignored leavings (L2B_SESSION_11 §9) plus these
pre-existing, unrelated items that are NOT yours to commit unless a story owns
them:
- `?? "config/templates/DAOOperations (2).java"`, `?? "config/templates/TranertMapper (1).java"`,
  `?? prompts/L2B_Session_7_Continue_prompt.txt` — gitignored leavings, leave alone.
- ` M prompts/architecture_review_meta_prompt.md` — a pre-existing uncommitted
  v2→v3 / CRLF diff. **R-15 (#44) owns this file.** Until R-15, do not commit it;
  do not let it ride along on another story's commit (use explicit paths).
- `?? docs/ARCHITECTURE_REVIEW_2026-06-03.md`, `?? prompts/arch_review_story_runner_prompt.md`
  — the review + this runner; commit these once at the start (see Bootstrap).
If anything else is dirty, STOP and report.
</verify_clean_state>

<verify_gate>
A story is **VERIFIED** only when every command below passes (exit 0). Capture
the actual numbers (passed/skipped, coverage %) into the handoff doc.

```
# 1. Format / lint / types (changed files at minimum; whole tree preferred)
black --check src/ tests/ scripts/
flake8 src/ tests/ scripts/
mypy src/

# 2. Full unit + integration suite with the coverage gate (pytest.ini enforces
#    --cov-fail-under=80 over its configured scope). MUST be green.
pytest

# 3. Harness offline subset (no SIT) — must pass / skip cleanly:
pytest tests/unit/test_e2e_run_source.py \
       tests/unit/test_e2e_reconciliation_spec.py \
       tests/unit/test_e2e_db_truth_comparator.py \
       tests/unit/test_e2e_shaw_tranert_sql_smoke.py \
       tests/unit/test_e2e_shaw_tranert_smoke.py \
       tests/unit/test_cross_row_validator.py --no-cov -q
```

Rules:
- **No regression to the test bar** (AGENTS.md: 1,063 unit + 46 E2E, 80%+).
  If your story is #26/#27 (coverage scope) the gate definition itself changes —
  follow the ADR 0011 decision and record the new enforced number; never lower a
  bar silently.
- Live-Oracle (SIT) tests SKIP without credentials — that is expected and OK.
- Windows note (§9): `python -c` stdout is unreliable; write a scratch file and
  read it. `reports/` is gitignored and unreadable by file tools.
</verify_gate>

<advance_criteria>
Advance to the next story ONLY when ALL are true for the current story:
- [ ] Acceptance criteria in the issue are all met and checked.
- [ ] `<verify_gate>` is fully green (numbers recorded).
- [ ] Documentation updated (incl. ADR status where relevant).
- [ ] Handoff doc written at `docs/handover/ARCH_REVIEW_<R-ID>_HANDOFF.md`.
- [ ] Change committed AND pushed to `feature/valdo-engine-v3`.
- [ ] Safety snapshot deleted (only because gate is green + push succeeded).
- [ ] No `<stop_and_ask_first>` decision is left pending for a dependent story.
If any box is unchecked: **do not advance.** Fix or STOP and report the blocker.
</advance_criteria>

<handoff_template>
```markdown
# Arch-Review Story Handoff — <R-ID> (#<IID>)

**Generated:** <YYYY-MM-DD>
**Story:** <title>
**Predecessor:** docs/handover/ARCH_REVIEW_<prev-R-ID>_HANDOFF.md (or "first in series")
**Trunk:** feature/valdo-engine-v3 (commit <short-sha>, pushed: yes/no)
**Safety snapshot:** arch-review-snapshot/<R-ID> @ <pre-story-SHA> — deleted after green: yes/no
**Depends-on satisfied:** <#IIDs or none>

## 1. What shipped (scope delivered)
- <bullet per change, with file path>

## 2. Out of scope / deferred (with follow-up note)
- <bullet, or "none">

## 3. AGENTS.md compliance
- src/ touched? <yes/no>. If yes: ADR <id> authorizes it (core change, not harness) / carve-out ref.
- Secrets: none added. Audit table: not mutated. Quick actions: none used.

## 4. ADR(s)
- <ADR id + status, or "none">

## 5. Verification (actual results)
- black: PASS | flake8: PASS | mypy: PASS
- pytest: <N passed, M skipped>, coverage <X.XX%> (gate <scope>)
- harness offline subset: <N passed/skipped>
- Live-Oracle tests: SKIPPED (no SIT) — expected

## 6. Docs updated
- <paths>

## 7. Acceptance criteria
- [x] <each criterion from the issue, checked>

## 8. Next story
- <next IID — R-ID — title>; blockers/decisions pending: <none / describe>
```
</handoff_template>

<completion_summary>
After each story, post to the human:
- Story done: `#IID R-ID — title`. Trunk commit SHA (pushed); snapshot deleted.
- Verification line: `pytest N passed / M skipped, cov X.XX% — black/flake8/mypy clean`.
- Docs touched + handoff doc path.
- Next up: `#IID R-ID — title`, or the pending decision blocking it.
Keep it to ~6 lines.
</completion_summary>

<hard_rules_recap>
- One story at a time, in backlog order; respect `depends_on`.
- **Single-dev workflow:** commit + push directly to `feature/valdo-engine-v3`;
  no topic branches, no MRs.
- **Snapshot before every story** (`arch-review-snapshot/<R-ID>`); delete it
  **only** after the gate is green and the push succeeded. Keep it on red.
- Never advance on a red gate or with docs/handoff missing.
- No `src/` changes for **harness** work without an ADR; core changes (R-01x,
  R-06a) use the normal ADR + MR flow.
- No quick actions (`/label`, `/assign`, …) in any GitLab title/description/
  comment — use tool parameters instead (403 otherwise).
- No secrets; secret access only via `scripts/e2e_lib/secret_resolver.py`.
- `AUDIT.VALDO_RUN_FAILURES` is append-only — never UPDATE/DELETE.
- Do not push to `main`; do not force-push; open/close MRs and issues only with
  explicit user authorization.
- For decision stories (#28/#32/#38/#41) deliver the write-up/ADR and STOP for
  the answer before implementing dependents.
</hard_rules_recap>

## Changelog
- **v2**: added `<bootstrap_state>` so a fresh session reconstructs loop
  position from the repo (handoffs + issue checkboxes + clean tree) instead of a
  per-session handoff narrative; durable facts (trunk, gate policy, baseline,
  Windows/CRLF/shell gotchas) moved to AGENTS.md "Arch-review loop state"; added
  a rolling pointer `docs/handover/ARCH_REVIEW_STATUS.md`. Per-story handoffs are
  now an audit trail, not a startup dependency.
- **v1** (initial): sequential runner over the 21 arch-review stories (#23–#45)
  in priority+dependency order, with a per-story gate (implement → test →
  document → handoff → verify → advance) and STOP-AND-REVIEW boundaries.
