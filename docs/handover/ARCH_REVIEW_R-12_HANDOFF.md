# Arch-Review Story Handoff — R-12 (#41)

**Generated:** 2026-06-11
**Story:** Document baseline-promotion policy and add a drift guard
**Predecessor:** docs/handover/ARCH_REVIEW_R-11_HANDOFF.md
**Trunk:** feature/valdo-engine-v3 (commit 9e1c8f0, pushed: yes)
**Safety snapshot:** arch-review-snapshot/R-12 @ 3f9bf77 — deleted after green: yes
**Depends-on satisfied:** none (no deps)

## 1. What shipped (scope delivered)

- `docs/adr/0015-baseline-promotion-policy.md` — ADR documenting the five
  policy decisions (approver allowlist, approval workflow, drift guard Option A,
  `--force` requires `--reason`, config-driven envs). Status: Accepted.
- `config/e2e/promotion_policy.yml` — new required config artifact; declares
  the `approvers` allowlist. Schema authority: ADR 0015 / `load_promotion_policy`.
- `scripts/e2e_lib/promote_baseline.py` — three new guards:
  1. **Approver validation** (`load_promotion_policy` + `validate_approver`):
     `--approved-by` must match an entry in `promotion_policy.yml`; rejects
     unknown approvers with a clear error listing known approvers.
  2. **Drift check** (`files_are_identical` + `check_drift`): before promoting,
     byte-compares the candidate against the existing baseline file. If they
     differ and `--force` is not set, promotion is blocked. First-time
     promotions (no existing baseline) pass silently.
  3. **`--force` requires `--reason`**: when `--force` is used, `--reason` is
     also required; the reason is recorded as `force_reason` in the manifest
     entry. `promote_one` and `promote` both accept the new `reason` parameter.
  - `_DEFAULT_POLICY_PATH` constant added; `--policy` CLI flag added.
  - `promote()` signature extended: `policy_path`, `reason` parameters.
  - Environments remain config-driven via `PathResolver.known_envs()` (no
    change needed — this was already the case).
- `tests/unit/test_promote_baseline.py` — 25 new unit tests covering:
  `load_promotion_policy` (6), `validate_approver` (4), `files_are_identical`
  (5), `check_drift` (6), `promote()` integration guards (4).
- `tests/unit/test_e2e_promote_baseline.py` — updated existing harness fixture
  to include a `policy` key (tmp `promotion_policy.yml` with `qa@example.com`);
  patched all `promote()` and `main()` calls to pass `policy_path`/`--policy`;
  added `reason` to `force=True` calls.
- `docs/CONFIG_SCHEMA_REGISTRY.md` — added `promotion_policy.yml` row.
- `CHANGELOG.md` — added R-12 entry under `[Unreleased] / Added`.
- `docs/handover/ARCH_REVIEW_STATUS.md` — rolling pointer updated.

## 2. Out of scope / deferred (with follow-up note)

- **`schema_version` enforcement in `load_promotion_policy`**: the loader
  checks the key exists but does not assert `== 1`. Consistent with the
  existing pattern for `paths.yml` and source YAMLs (flagged in the registry
  as a known gap). No follow-up story needed unless the registry tightening
  work is revisited.
- **Unknown-key rejection in `promotion_policy.yml`**: silently ignored,
  consistent with other harness YAMLs. Same caveat as above.

## 3. AGENTS.md compliance

- src/ touched? **No** — all changes are in `scripts/e2e_lib/`,
  `config/e2e/`, `tests/unit/`, and `docs/`. Hard rule #1 is satisfied.
- Secrets: none added. Audit table (`AUDIT.VALDO_RUN_FAILURES`): not mutated.
  Quick actions: none used in any GitLab body. No `print()`; no
  `os.environ.get()` added.

## 4. ADR(s)

- ADR 0015 (`docs/adr/0015-baseline-promotion-policy.md`) — Status: Accepted.

## 5. Verification (actual results)

- black --check (changed files): no NEW deviations vs HEAD.
- flake8 (changed files): no NEW findings vs HEAD.
- mypy src/: no NEW findings vs HEAD (no `src/` changes).
- New tests: `pytest tests/unit/test_promote_baseline.py --no-cov` →
  **25 passed**.
- Both test files: `pytest tests/unit/test_e2e_promote_baseline.py tests/unit/test_promote_baseline.py --no-cov` → **45 passed**.
- Full unit+integration (`pytest tests/unit tests/integration --no-cov`) →
  **43 failed, 2768 passed, 12 skipped** — failure count == documented
  baseline (43), all in pre-existing environmental buckets; **zero** failures
  attributable to R-12. (2768 vs prior 2743: +25 new tests.)
- Harness offline subset: **267 passed, 1 skipped** — matches the documented
  baseline exactly.
- Live-Oracle tests: SKIPPED (no SIT) — expected.

## 6. Docs updated

- `docs/adr/0015-baseline-promotion-policy.md` (new)
- `config/e2e/promotion_policy.yml` (new)
- `docs/CONFIG_SCHEMA_REGISTRY.md` (promotion_policy.yml row added)
- `CHANGELOG.md` ([Unreleased] / Added — R-12 drift guard)
- `docs/handover/ARCH_REVIEW_STATUS.md` (rolling pointer → next story)
- This handoff doc.

## 7. Acceptance criteria

- [x] Promotion policy documented (ADR 0015 + `config/e2e/promotion_policy.yml`).
- [x] `promote_baseline.py` enforces the agreed guard with tests (approver
      allowlist, drift check Option A, `--force` requires `--reason`).
- [x] Open question raised to the user if any policy detail is unconfirmed —
      all five policy questions were answered by the user before implementation;
      no guessing occurred.

## 8. Next story

- #42 — R-13 — Link multi-record reports into global rollup (Wave 3, no deps).
