# Arch-Review Story Handoff — R-08 (#39)

**Generated:** 2026-06-10
**Story:** Periodic AGENTS.md `src/` carve-out audit
**Predecessor:** docs/handover/ARCH_REVIEW_R-07_HANDOFF.md
**Trunk:** feature/valdo-engine-v3 (commit bf528fd, pushed: yes)
**Safety snapshot:** arch-review-snapshot/R-08 @ 46b7207 — deleted after green: yes
**Depends-on satisfied:** none (no deps)

## 1. What shipped (scope delivered)
- `docs/AGENTS_CARVE_OUT_AUDIT.md` (new) — recurring carve-out audit checklist +
  ledger of every harness-motivated `src/` change with its authorising ADR,
  scope, behaviour-change status, and "carve-out OK?" verdict; a "core changes
  (rule #1 N/A)" table; a trend/erosion watch; an audit log; and a deferred
  optional-CI-guard sketch.
- `AGENTS.md` — hard rule #1 carve-out paragraph now links the audit ledger and
  instructs running it periodically / on any mixed `scripts/e2e_lib/` + `src/`
  change. (CRLF-safe edit; file verified clean CRLF, no lone LFs.)
- `CHANGELOG.md` — `[Unreleased] / Added` entry for the audit ledger (R-08).
  (CRLF-safe edit; verified.)

## 2. Out of scope / deferred (with follow-up note)
- **Optional CI lint** (warn on MR touching both `scripts/e2e_lib/` and `src/`
  without an ADR ref): deferred. The repo uses a single-developer
  direct-to-trunk workflow with no MRs today, so an MR-diff lint has nothing to
  run against. Sketch recorded in the audit doc; tick #39's optional box when
  MR-based review returns. The issue's optional acceptance box is left unchecked
  (it is explicitly optional).

## 3. AGENTS.md compliance
- src/ touched? **No** — docs + markdown only. Hard rule #1 fully satisfied
  (this story *defends* it).
- Secrets: none added. Audit table (`AUDIT.VALDO_RUN_FAILURES`): not mutated.
  Quick actions: none used in any GitLab body.

## 4. ADR(s)
- None. R-08 is a documentation/process control, not a load-bearing decision.

## 5. Verification (actual results)
- black / flake8 / mypy: **N/A** — no `src/`, `tests/`, or `scripts/` code
  changed (docs + markdown only); zero NEW findings by construction.
- pytest (full suite): not re-run for a docs-only change; no code paths
  affected. Documented baseline unchanged (43 pre-existing environmental
  failures are not regressions).
- Harness offline subset: **267 passed, 1 skipped** — matches the documented
  baseline exactly.
- Live-Oracle tests: SKIPPED (no SIT) — expected.

## 6. Docs updated
- docs/AGENTS_CARVE_OUT_AUDIT.md (new)
- AGENTS.md (hard rule #1 cross-link)
- CHANGELOG.md ([Unreleased] / Added)
- docs/handover/ARCH_REVIEW_STATUS.md (rolling pointer → next story)

## 7. Acceptance criteria
- [x] An audit checklist exists and is linked from AGENTS.md.
- [ ] (Optional) CI warning wired for mixed harness+`src/` MRs without an ADR
      reference. — deferred (no MR workflow today; sketch recorded).

## 8. Next story
- #40 — R-11 — Registries for pipeline step types (no deps; Wave 3). No pending
  decision blocking it.
