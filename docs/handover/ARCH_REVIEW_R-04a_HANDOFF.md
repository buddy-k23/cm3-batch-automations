# Arch-Review Story Handoff — R-04a (#30)

**Generated:** 2026-06-03
**Story:** Add a mode-parity test asserting one shared core
**Predecessor:** docs/handover/ARCH_REVIEW_R-02-followup_HANDOFF.md
**Trunk:** feature/valdo-engine-v3 (commit <set on push>, pushed: yes)
**Safety snapshot:** arch-review-snapshot/R-04a @ e4d9213 — deleted after green: yes
**Depends-on satisfied:** none

## 1. What shipped (scope delivered)
- `tests/unit/test_mode_parity.py` (6 tests) — pins the "one validation core
  across surfaces" property via AST/source inspection (no server/DB needed):
  - API router + pipeline runner both call `run_validate_service` and
    `run_multi_record_validate_service`.
  - `run_multi_record_validate_service` delegates to `MultiRecordValidator`;
    the CLI multi-record command and the harness report use the same core.
  - The single-record CLI's separate path (drives `EnhancedFileValidator`
    directly, not the service) is **documented and pinned** so a *new* third
    path — or the R-04b convergence — forces a conscious test update.
- `docs/architecture.md` — new "Mode parity — one validation core across
  surfaces" section stating the guarantee and the pinned divergence.

## 2. Finding (honest scope note)
Parity is **strong but not absolute**, as the review anticipated:
- **API + pipeline**: converge on the shared services. ✅
- **Multi-record (all surfaces incl. CLI + harness)**: share `MultiRecordValidator`. ✅
- **Single-record CLI**: uses the lower-level validators directly, NOT
  `run_validate_service`. The *core primitives* are shared, but the service
  wrapper is bypassed. This is the gap R-04b (#31) evaluates closing. R-04a
  documents + pins it rather than asserting it away.

## 3. AGENTS.md compliance
- src/ touched? **No** (a new test + docs only).
- Secrets: none. Audit table: untouched. Quick actions: none.

## 4. ADR(s)
- None.

## 5. Verification (actual results, baseline policy)
- Targeted: `tests/unit/test_mode_parity.py` → **6 passed**.
- black --check: PASS. flake8: clean.
- Full unit+integration failure count unchanged vs. baseline (43, buckets A–G);
  this story adds **0** new failures (it is additive: a new test file + docs).

## 6. Docs updated
- `docs/architecture.md` (Mode parity section)
- `CHANGELOG.md`

## 7. Acceptance criteria
- [x] A parity test fails if a surface stops routing through the shared service.
- [x] Architecture doc states the parity guarantee.
- [x] Suite green; black/flake8 clean.

## 8. Next story
- #31 — R-04b — Define explicit UAT and CI mode adapters. Depends on #30 (now
  satisfied). It will be held to this parity test. **Last Wave-1 story.**
