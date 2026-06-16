# Sprint 13.5 — Kickoff (Security & SOX Hardening)

**Sprint goal:** Close the do-now control gaps the architecture review (`docs/ARCHITECTURE_REVIEW.md`, 2026-06-16) flagged as SOX/security-critical, plus stand up the missing test gate in CI. These are cheap, high-leverage, and domain-critical for a banking system — they take priority over the in-flight ADR-0022 portability work (#405/#406, still queued).

**Duration:** 1–2 weeks
**Capacity:** 5 points (5 × S) — at the cap.
**Demo:** Internal / security audience. End state: the audit log is tamper-evident and covers auth failures + config mutations; the MCP sample config is no longer auth-bypassed and the API-key check is constant-time; the extractor has no SQL-injection construction site; and CI runs the unit suite + coverage gate.

**Context:** The 5-lane architecture review found a coherent debt theme ("good seam, bypassed path") plus four do-now control gaps for the banking context: a non-tamper-evident audit log, an auth sample that ships bypassed, a timing-attackable API-key compare, and an f-string SQL extractor. It also found the test/coverage gate runs nowhere in CI. This sprint takes the four security items + the CI gate. The structural "seams" (reconcile_all_service, shared rate-limit/revocation backends, set-based comparator) are P2 → a later sprint.

---

## Stories

| ID | GitHub | Title | Size | Owner | Track |
|---|---|---|---|---|---|
| **S13.5-1** | [#408](https://github.com/buddy-k23/valdo/issues/408) | Tamper-evident audit records (hash-chain/HMAC + sequence) | S | Dev | Security/SOX |
| **S13.5-2** | [#415](https://github.com/buddy-k23/valdo/issues/415) | Audit coverage — auth failures + config/rule/mapping mutations | S | Dev | Security/SOX |
| **S13.5-3** | [#409](https://github.com/buddy-k23/valdo/issues/409) | MCP/API auth hardening — kill `dev` default, constant-time key compare | S | Dev | Security |
| **S13.5-4** | [#410](https://github.com/buddy-k23/valdo/issues/410) | Parameterize/allow-list extractor SQL | S | Dev | Security |
| **S13.5-5** | [#411](https://github.com/buddy-k23/valdo/issues/411) | pytest + 80% coverage gate as a CI job (informational-first) | S | Dev | Process |

**Total: 5 pts (at cap).**

---

## Daily sequencing — sequential, commit-per-story

S13.5-1/2/3 all touch `audit_logger.py` and/or `auth.py`; all five touch `CHANGELOG.md`. Run in order, one commit each, clean tree between agents (proven Sprints 9–12).

| Day | Story | Why this order |
|---|---|---|
| 1–2 | **S13.5-1** (#408 tamper-evidence) | Foundation for the audit theme — the record format/sequence the coverage story emits into. |
| 3 | **S13.5-2** (#415 coverage) | Depends on S13.5-1's record path; adds auth-failure + mutation events. |
| 4 | **S13.5-3** (#409 auth hardening) | Touches `auth.py` (overlaps 1/2) → after them. Quick: `.env.example` flip + `compare_digest` + opt-in for dev. |
| 5 | **S13.5-4** (#410 extractor SQL) | Independent. Bind `limit`, allow-list identifiers, validate `where`; injection test. |
| 6 | **S13.5-5** (#411 CI gate) | Last, so the new tests from 1–4 are included. Add the workflow as **informational (`continue-on-error`)**; promotion to required is tracked by #416 (triage of the ~27 env-bound failures). |
| 7 | Buffer / review | Kickoff lands as final commit; push; close issues. |

---

## Definition of Ready

- [x] Each story maps to a single S-sized review finding with file:line evidence
- [x] Dependencies explicit (S13.5-2 → S13.5-1; 1/2/3 serialize on `auth.py`/`audit_logger.py`)
- [x] No live-infra dependency (SQLite/local; CI runs the unit suite)

## Definition of Done

- [ ] **S13.5-1:** audit records are tamper-evident (hash-chain or HMAC + monotonic sequence); a verifier detects an edited/removed record (test); write failures no longer silent
- [ ] **S13.5-2:** audit events emitted for API-key + MCP auth failures and config/mapping/rule/masking mutations
- [ ] **S13.5-3:** a fresh `.env` from `.env.example` is NOT auth-bypassed (dev mode requires an explicit opt-in env); API-key compare uses `compare_digest`; sample signing keys are non-functional placeholders; affected tests updated
- [ ] **S13.5-4:** no f-string-interpolated values in extractor SQL; identifiers allow-listed; an injection attempt is rejected (test)
- [ ] **S13.5-5:** a CI workflow runs `tests/unit/` + the coverage gate with `VALDO_SESSION_SIGNING_KEY` set and local `.env`/`valdo.db` excluded; informational until #416 makes it required
- [ ] `pytest tests/unit/` stays at **27 failed / zero new** vs the env-bound baseline (run with local `.env`/`valdo.db` set aside); `tests/unit/test_no_shell_true.py` still passes
- [ ] **No secrets committed**; `.env.example` placeholders only
- [ ] Each story = one conventional commit on `valdo-version-v4` referencing `(S13.5-<m>, #<issue>)`; #408/#415/#409/#410/#411 closed
- [ ] Kickoff doc lands as the **final commit**; push to `origin/valdo-version-v4`

---

## Sprint risks

| Risk | L | I | Mitigation |
|---|---|---|---|
| Flipping `VALDO_MCP_AUTH` off-by-default breaks dev/test workflows that relied on `dev` mode | M | M | Keep `dev` mode available behind an explicit `VALDO_ALLOW_DEV_AUTH=1` (or equivalent) opt-in; update the tests/fixtures that assumed the old default; document the change in PRODUCTION_DEPLOYMENT.md + the MCP client docs. |
| Audit hash-chain design adds latency or complicates concurrent writes | L | M | Keep it simple + deterministic (HMAC per record + prev-hash + sequence); single-writer append; benchmark is not required at pilot volume. Prefer HMAC over a full Merkle structure. |
| Constant-time API-key change breaks the `key:role` multi-key format | M | M | Iterate keys with `compare_digest` preserving the existing `key:role` mapping; test multi-key + role resolution unchanged. |
| Extractor identifier allow-listing rejects legitimate table/column names | M | L | Validate against the adapter catalog (real tables/columns) rather than a static regex where possible; clear error on reject; cover a legitimate-name case in tests. |
| CI gate goes red on the ~27 known env-bound failures and blocks PRs | M | M | Ship the job `continue-on-error: true` (informational) this sprint; promotion to a required check is gated on #416 (triage/xfail). Mirrors the Sprint 8 drift-check informational-first pattern. |
| Shared-file contention (`auth.py`, `audit_logger.py`, CHANGELOG) | M | M | Strictly sequential, commit-per-story, clean tree between agents. |

---

## Out of scope — do not pull in

- **The "seams" P2 work** — reconcile_all_service (#421), shared rate-limit/revocation backends (#419/#420), set-based comparator (#423), config dead-layer (#424). A later sprint.
- **Other P1s not in this sprint** — RPM serve (#412), Python/topology (#417), chunked sequential (#413), has_header parity (#414), the #416 triage (fast-follow to #411). Schedule next.
- **ADR-0022 portability continuation** (#405/#406) — still queued; not displaced permanently, just after the security do-now.
- **Larger refactors** — ui.js modularization, splitting the >500-line files. Not S.

---

## Roles

- **Sprint owner / PM:** you (self-paced)
- **Dev (all five stories):** `senior-fullstack-fintech-dev` agent
- **Reviewer:** you + Claude Code suggestions on every commit; consider a `/security-review` pass on the auth/audit commits
- **Demo audience:** internal security
- **Environment:** local dev + CI

---

## After Sprint 13.5

| Sprint | Focus | Issues |
|---|---|---|
| **(next)** | Remaining P1s — RPM serve (#412), Python/topology (#417), chunked sequential (#413), has_header (#414), CI-gate-to-required (#416) | grouped next sprint |
| **13 (queued)** | ADR-0022 portability cont. — extract/db-compare onto the adapter | #405, #406 |
| **(P2)** | The structural seams | #418–#426 |
| **(candidate)** | GitLab/Duo track · JSON/XML parser impls (#395/#396) | — |
