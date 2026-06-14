# Arch-Review Story Handoff — R-04b (#31)

**Generated:** 2026-06-03
**Story:** Define explicit UAT and CI mode adapters
**Predecessor:** docs/handover/ARCH_REVIEW_R-04a_HANDOFF.md
**Trunk:** feature/valdo-engine-v3 (commit <set on push>, pushed: yes)
**Safety snapshot:** arch-review-snapshot/R-04b @ e8ef153 — deleted after green: yes
**Depends-on satisfied:** #30 (R-04a)

## 1. What shipped (scope delivered)
- `scripts/run_e2e_mode.sh` — a thin, mode-aware wrapper:
  `--mode {integration|uat|ci}` selects mode-appropriate **defaults only** and
  `exec`s the same engine (`python -m scripts.e2e_lib.run_source`). It adds NO
  validation/comparison logic. Mode contracts documented inline:
  - `integration` — default historical behaviour (blocking gates fail the run).
  - `uat` — same engine; UAT data/baselines via per-source config + baseline
    pinning on an approved env (NOT a new environment).
  - `ci` — exit-code contract (0/2/3); Java shell-outs default OFF
    (`VALDO_E2E_DISABLE_JAVA=1`).
  - `--env` validated against `sit|ait` only; any other value is rejected.
- `tests/unit/test_mode_parity.py` — 2 new tests (now 8 total):
  the wrapper (and `run_e2e_source.sh`) delegate to `scripts.e2e_lib.run_source`;
  the wrapper does not invent a `uat`/`prod` env.
- `docs/CICD_GUIDE.md` — new "E2E execution modes" section (table + usage).

## 2. Scope decision (per user direction: sit/ait only)
The user confirmed **only `sit`/`ait`** this iteration. So R-04b does NOT create
a UAT environment. "UAT" is modelled as a **mode/profile over the same engine**
on an approved env, with data/baselines selected by config — honouring AGENTS.md
"do not guess on environment". The wrapper hard-rejects any `--env` other than
`sit`/`ait`, so a UAT/prod env cannot be silently introduced. If a real UAT env
is sanctioned later, only the `--env` allowlist + paths.yml need extending.

## 3. AGENTS.md compliance
- src/ touched? **No** (a new `scripts/` wrapper + a test + docs).
- No new environment invented (sit/ait only, enforced in the wrapper).
- Secrets: none. Audit table: untouched. Quick actions: none.
- `*.sh` is LF (`.gitattributes` rule from #27); exec bit set on commit.

## 4. ADR(s)
- None.

## 5. Verification (actual results, baseline policy)
- Targeted: `tests/unit/test_mode_parity.py` → **8 passed** (6 from R-04a + 2 new).
- black --check: clean. flake8: clean.
- The `.sh` is bash-only (not executable on the Windows dev box); its behaviour
  is pinned by the parity tests and it mirrors `run_e2e_source.sh`'s delegation
  exactly. Runs on the RHEL target.
- Full unit+integration failure count unchanged vs. baseline (43, buckets A–G);
  0 new failures.

## 6. Docs updated
- `docs/CICD_GUIDE.md` (E2E execution modes section)
- `CHANGELOG.md`

## 7. Acceptance criteria
- [x] UAT and CI entry points exist and are documented (in `docs/CICD_GUIDE.md`).
      Modelled as modes over the same engine; UAT is not a new environment
      (sit/ait only, per user direction).
- [x] Both are covered by the R-04a parity test (delegation + no-new-env guards).
- [x] No new validation/comparison logic — adapter (defaults + delegation) only.

## 8. Next story / wave status
- **Wave 1 COMPLETE** (#23, #24, #25, #26, #27, #30, #31).
- Next is **Wave 2**, starting with **#28 — R-03a — Decide L2 regeneration
  (ADR)**, which is a DECISION story (`<stop_and_ask_first>`): deliver the ADR,
  then STOP for the user's decision before implementing #29.
