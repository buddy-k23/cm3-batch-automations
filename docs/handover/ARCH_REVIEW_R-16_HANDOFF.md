# Arch-Review Story Handoff — R-16 (#45)

**Generated:** 2026-06-12
**Story:** [arch-review][R-16] Flip ADR 0008/0009 to Accepted and burn down tracked TODOs
**Predecessor:** docs/handover/ARCH_REVIEW_R-15_HANDOFF.md
**Trunk:** feature/valdo-engine-v3 (commit 1518e2b, pushed: yes)
**Safety snapshot:** arch-review-snapshot/R-16 @ b14ca11 — deleted after green: yes
**Depends-on satisfied:** none

## 1. What shipped (scope delivered)

- `docs/adr/0008-extract-multi-record-reader-primitive.md` — Status flipped from
  "Proposed (flip to Accepted on MR merge)" to "Accepted"; accepted date 2026-06-11,
  commits 93ab664 / a20471b. (CRLF-safe patch.)
- `docs/adr/0009-parameterized-cross-row-sequential.md` — Status flipped to Accepted;
  accepted date 2026-06-11, commit cbc4cda.
- `docs/adr/0010-truthsource-backend-abstraction.md` — Status flipped to Accepted;
  accepted date 2026-06-11, commits cbc4cda / d090e79.
- `docs/adr/0014-record-reader-strategy-seam.md` — Status flipped to Accepted;
  accepted date 2026-06-11, commits c3175ea / fe9f898.
- `scripts/e2e_lib/run_source.py` — Removed unused `shlex` and `shutil` imports
  (F401); removed dead `f2s_blocking` and `f2s_policy` variables (F841). (CRLF-safe
  patch.) `flake8` now clean on this file.
- `CHANGELOG.md` — R-16 entry added under `[Unreleased] ### Changed`. (CRLF-safe patch.)
- `docs/handover/ARCH_REVIEW_R-16_HANDOFF.md` — this file.
- `docs/handover/ARCH_REVIEW_STATUS.md` — rolling pointer updated (last story = R-16,
  next = none — backlog complete).
- GitLab issue [#47](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/work_items/47)
  created to track the vestigial-method cleanup (deferred; see §2).

## 2. Out of scope / deferred (with follow-up note)

- **Vestigial `MultiRecordValidator._extract_discriminator` / `_identify_record_type`
  methods** — ADR 0008 marked these with `TODO(#21-followup)`. Deletion requires
  migrating `TestExtractDiscriminator` and `TestIdentifyRecordType` (which call the
  methods directly) and rewiring `_handle_unknown_rows`. This is a test-modification
  change that is out of scope for R-16 (a doc + lint story). Tracked in follow-up
  issue [#47](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/work_items/47).

## 3. AGENTS.md compliance

- src/ touched? No. Changes are ADR docs, a scripts/ lint fix, CHANGELOG, and handoff docs.
- Secrets: none added. Audit table: not mutated. Quick actions: none used.

## 4. ADR(s)

- ADR 0008 — now Accepted (2026-06-11)
- ADR 0009 — now Accepted (2026-06-11)
- ADR 0010 — now Accepted (2026-06-11)
- ADR 0014 — now Accepted (2026-06-11)

## 5. Verification (actual results)

- black: pre-existing whole-codebase reformatting needed — not a regression; no new
  files introduced by this story. Changed files are `.md` and `run_source.py` (lint
  removal only).
- flake8 `scripts/e2e_lib/run_source.py`: PASS (0 violations, was 3 F401/F841).
- mypy: pre-existing errors (all pre-existing, zero new).
- pytest (unit): 30 failed / 2737 passed / 12 skipped — matches R-15 baseline exactly;
  no regression. Coverage: 80.28% (gate: 80% required — PASS).
- harness offline subset: 267 passed / 1 skipped — matches baseline exactly.
- Live-Oracle tests: SKIPPED (no SIT) — expected.

## 6. Docs updated

- `docs/adr/0008-extract-multi-record-reader-primitive.md` (Status → Accepted)
- `docs/adr/0009-parameterized-cross-row-sequential.md` (Status → Accepted)
- `docs/adr/0010-truthsource-backend-abstraction.md` (Status → Accepted)
- `docs/adr/0014-record-reader-strategy-seam.md` (Status → Accepted)
- `CHANGELOG.md [Unreleased]` (entry added)
- `docs/handover/ARCH_REVIEW_R-16_HANDOFF.md` (this file)
- `docs/handover/ARCH_REVIEW_STATUS.md` (rolling pointer updated)

## 7. Acceptance criteria

- [x] ADR 0008/0009 marked Accepted with merge dates.
- [x] ADR 0010/0014 marked Accepted with merge (trunk-landing) dates.
- [x] `run_source.py` lint clean.
- [x] Vestigial-method decision recorded (explicitly deferred via linked issue #47).

## 8. Next story

- Backlog complete — all 21 arch-review stories (#23–#45) are DONE.
- Follow-up: #47 — remove vestigial validator methods (not part of the arch-review
  backlog; tracked separately).
