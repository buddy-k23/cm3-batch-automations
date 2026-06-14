# Arch-Review Story Runner — Session 1 Handoff

**Generated:** 2026-06-03
**Branch / trunk:** `feature/valdo-engine-v3`, head **`544935e`**, **pushed to origin**.
**Driver prompt:** `prompts/arch_review_story_runner_prompt.md` (sequential runner).
**Source of intent:** `docs/ARCHITECTURE_REVIEW_2026-06-03.md` (review, R-NN ids).
**Backlog:** 21 stories `#23`–`#45` (see the runner prompt `<backlog>` for order).

---

## 1. Headline outcome

**Wave 1 (must-fix) is COMPLETE — all 7 stories done, green, pushed.** The
pre-existing coverage blocker surfaced during baseline triage is resolved.
Next up is **Wave 2 (should-fix)**, which **starts with a DECISION story
(`#28` R-03a)** that must STOP for a user decision before its implementation
(`#29`) can run.

### Commits this session (oldest → newest, all on `feature/valdo-engine-v3`, pushed)

| Commit | Story | Title |
|---|---|---|
| `88673f9` | bootstrap | docs: add 2026-06-03 architecture review + story runner prompt |
| `cbc4cda` | #23 R-01a | feat(database): add TruthSource backend abstraction + OracleTruthSource (ADR 0010) |
| `8e544e7` | #24 R-01b | refactor(e2e): open L2b Oracle connection via OracleTruthSource (ADR 0010) |
| `d090e79` | #25 R-01c | feat(e2e): thread optional SqlDialect through reconcile; document dialect coupling (ADR 0010) |
| `41db6cc` | #26 R-02 | test: widen coverage gate scope to the core engine (ADR 0011) |
| `e4d9213` | #27 R-02-fu | test(e2e): add independent scripts/e2e_lib coverage gate (ADR 0011) |
| `e8ef153` | #30 R-04a | test: add mode-parity guard for the shared validation core (R-04a) |
| `544935e` | #31 R-04b | feat(e2e): add mode-aware wrapper (integration|uat|ci) over the shared orchestrator (R-04b) |

---

## 2. Stories completed (Wave 1) — status & artifacts

All issues have ticked acceptance boxes + a "DONE" note; each has a per-story
handoff under `docs/handover/ARCH_REVIEW_<R-ID>_HANDOFF.md`.

| # | R-ID | What shipped | Key files |
|---|---|---|---|
| 23 | R-01a | `TruthSource` ABC + `SqlDialect` + `OracleTruthSource` | `src/database/truth_source.py`, `tests/unit/test_truth_source.py`, ADR 0010 |
| 24 | R-01b | L2b opener delegates to `OracleTruthSource`; `oracledb` no longer imported by the orchestrator | `scripts/e2e_lib/run_source.py`, `tests/unit/test_e2e_run_source.py` |
| 25 | R-01c | optional `dialect` param on `reconcile()`; Oracle-coupling inventory | `scripts/e2e_lib/db_truth_comparator.py`, `tests/unit/test_e2e_db_truth_comparator.py`, ADR 0010 (R-01c section) |
| 26 | R-02 | coverage `--cov` widened to `validators/services/pipeline/database`; bar kept 80% | `pytest.ini`, ADR 0011 |
| 27 | R-02-fu | independent harness coverage gate | `scripts/run_harness_coverage.sh`, `.gitattributes`, `docs/TEST_EXECUTION.md` |
| 30 | R-04a | mode-parity guard test + arch doc | `tests/unit/test_mode_parity.py`, `docs/architecture.md` |
| 31 | R-04b | mode-aware wrapper (integration/uat/ci) over the shared orchestrator | `scripts/run_e2e_mode.sh`, `docs/CICD_GUIDE.md` |

**ADRs added (all Proposed):** `0010-truthsource-backend-abstraction.md`,
`0011-coverage-gate-scope.md`. (They flip to Accepted under **#45 R-16**.)

---

## 3. The local test baseline (READ THIS before running the gate)

The repo's published bar (1,063 unit + 46 E2E, 80%+) is for the **RHEL 8.9 /
Python 3.11 / Oracle / running-server** target. This dev box is **Windows /
Python 3.14 / no Oracle / no server**, so the full suite has **43 pre-existing
environmental failures** (NOT regressions). The gate policy for the runner is
therefore **"no NEW failures vs. the documented baseline"**.

- **Authority:** `docs/handover/ARCH_REVIEW_TEST_BASELINE_2026-06-03.md`
  (failure buckets A–G + the per-story verification recipe).
- Baseline: **43 failed, ~2669 passed, ~12 skipped** on
  `pytest tests/unit tests/integration --no-cov`.
- Coverage gate: now **PASSES at 80.23%** (core scope) after #26 (was 78%).
- Harness gate: `scripts/run_harness_coverage.sh` → **~84%**.

### Per-story verification used this session (targeted + baseline)

```
# the story's own tests (must be fully green):
python -m pytest <changed test files> --no-cov -q -p no:cacheprovider
# format/lint/types on changed files:
python -m black --check <changed>   ;  python -m flake8 <changed>
python -m mypy <changed file> --follow-imports=silent --ignore-missing-imports
# sanity: full count must stay <= baseline (43), all in buckets A-G:
python -m pytest tests/unit tests/integration -q   # check coverage verdict too
```

---

## 4. Next story: #28 (R-03a) — DECISION, then STOP

**Do not implement #29 until the user decides.** #28's deliverable is an ADR
weighing:

- **(a) Ship `valdo regenerate`** — expose a CLI front-door over
  `src/pipeline/oracle_expected_generator.generate_expected_from_oracle`, then
  replace `_run_l2_regeneration_skipped` in `run_source.py` and flip the
  `VALDO_E2E_ENABLE_L2` default. *(closes the L2 `TODO(valdo-gap)` properly)*
- **(b) Retire the `L2_regeneration` gate** — L2b SQL-truth + L3 baseline already
  cover the truth/diff axes; remove the gate from `_OUTPUT_PHASE_GATES`, the
  source `gates:` blocks, and docs.

The user was asked for a leaning at end of session 1 (no answer captured yet).
**Other Wave-2/3 decision-gated stories** (same STOP rule): `#32` (empty/
header-only-batch semantics), `#41` (baseline-promotion policy), `#38`
(trigger-file naming).

### Remaining backlog (in runner order)

- **Wave 2 (should-fix):** `#28` R-03a (decide) → `#29` R-03b (impl, depends #28)
  → `#32` R-05 (decide+impl) → `#33` R-10a → `#34` R-10b (depends #33) →
  `#35` R-09.
- **Wave 3 (nice-to-have):** `#36` R-06a → `#37` R-06b (depends #36) → `#38`
  R-07 (decide) → `#39` R-08 → `#40` R-11 → `#41` R-12 (decide) → `#42` R-13 →
  `#43` R-14 → `#44` R-15 → `#45` R-16.

---

## 5. Verify state before doing anything (session 2)

```
git branch --show-current        # expect: feature/valdo-engine-v3
git log --oneline -n 3           # expect 544935e at HEAD (see §1)
git status --short
git branch --list "arch-review-snapshot/*"   # expect EMPTY (all retired)
```

Expected `git status` (pre-existing, do NOT commit unless a story owns it):
- ` M prompts/architecture_review_meta_prompt.md` — a pre-existing v2→v3 / CRLF
  diff. **Owned by #44 (R-15).** Leave it until then; never let it ride along on
  another story's commit (always `git add <explicit paths>`).
- `?? "config/templates/DAOOperations (2).java"`, `?? "config/templates/TranertMapper (1).java"`,
  `?? prompts/L2B_Session_7_Continue_prompt.txt` — gitignored leavings; leave alone.

---

## 6. Workflow rules in force (single-developer, this repo)

- **Commit + push directly to `feature/valdo-engine-v3`.** No topic branches, no MRs.
- **Snapshot before every story:** `git branch arch-review-snapshot/<R-ID>`;
  delete it **only** after the gate is green AND the push succeeded. Keep it on red.
- **Explicit-path commits only** (`git add <paths>`); never `git add -A`.
- One story at a time, in backlog order; respect `depends_on`; never advance on red.
- Decision stories (`#28/#32/#38/#41`): deliver the ADR/write-up, then STOP.
- `AGENTS.md` hard rules apply (no quick actions in GitLab bodies; secrets only via
  the resolver; `AUDIT.VALDO_RUN_FAILURES` append-only; harness work = scripts+config,
  core `src/` changes go through ADR+MR flow).

### Carried-forward Windows/shell gotchas (this box)

- **No `bash`, no `grep`/`tail`/`head`/`;` chaining** — use `&` to chain in cmd,
  and Python one-liners / scratch files to inspect output. `.sh` scripts can't be
  executed here (RHEL target); validate via their underlying Python command.
- **CRLF/BOM files break `edit_file`** — `db_truth_comparator.py` is CRLF;
  `test_e2e_db_truth_comparator.py` is BOM+CRLF; `reconciliation_spec.py`,
  `shaw_tranert_smoke.py`, the SQL, the rules JSON, the infographic are CRLF. Use
  a CRLF/BOM-safe Python patch script for those (read_bytes → decode → replace on
  LF-normalized text → re-encode). `edit_file` is fine on LF files.
- **`*.sh` must be LF** — enforced by the new `.gitattributes` (`*.sh text eol=lf`,
  added in #27). Set the exec bit with `git update-index --chmod=+x <script>`.
- **`reports/` is gitignored** and unreadable by the file tools.
- Console encoding is cp1252 — printing files with emoji/replacement chars raises
  `UnicodeEncodeError`; read with `errors='replace'` or via the `read_file` tool.
- The `git push` prints a harmless `git: 'credential-manager-core' is not a git
  command` line; the push still succeeds (check the `..` ref-update line).

---

## 7. Reading list for session 2 (priority order)

1. **This document.**
2. `prompts/arch_review_story_runner_prompt.md` — the loop + gate + backlog order.
3. `docs/handover/ARCH_REVIEW_TEST_BASELINE_2026-06-03.md` — the gate policy.
4. `docs/ARCHITECTURE_REVIEW_2026-06-03.md` — R-NN intent (esp. R-03 for #28).
5. `scripts/e2e_lib/run_source.py` — the L2 skip path (`_run_valdo_phase`,
   `ENV_ENABLE_L2`, the `TODO(valdo-gap)` in the module docstring) for #28/#29.
6. `src/pipeline/oracle_expected_generator.py` — the candidate `regenerate` core.
7. `AGENTS.md` — hard rules.
