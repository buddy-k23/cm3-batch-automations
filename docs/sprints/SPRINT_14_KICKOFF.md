# Sprint 14 — Kickoff (Deploy & Correctness)

**Sprint goal:** Make the deployment story coherent and kill the two silent chunked-vs-non-chunked divergences before any new formats land — then turn the CI test gate from informational into blocking. All five are S-sized do-now items from the architecture review (`docs/ARCHITECTURE_REVIEW.md`).

**Duration:** 1–2 weeks
**Capacity:** 5 points (5 × S) — at the cap.
**Demo:** Internal. End state: an RPM install brings up a service that actually serves on :8000 behind nginx; one Python baseline across RPM + Docker with a documented canonical topology; `valdo validate` gives identical results chunked vs non-chunked (sequential start/step + header'd files); and the CI workflow blocks on test/coverage failure (not informational).

**Context:** Sprint 13.5 closed the SOX/security do-now gaps. The architecture review's remaining P1s split into two themes — *deploy* (the RPM runs the CLI not a server; Python 3.9 vs 3.11; no canonical topology) and *correctness* (chunked `sequential` ignores start/step; the non-chunked delimited parser ignores `has_header`). Both correctness bugs route through the same dispatch the planned JSON/XML parsers will reuse, so they're prerequisites. Finally, S13.5 shipped the CI gate as informational; #416 promotes it once the ~27 env-bound failures are triaged.

---

## Stories

| ID | GitHub | Title | Size | Owner | Track |
|---|---|---|---|---|---|
| **S14-1** | [#412](https://github.com/buddy-k23/valdo/issues/412) | RPM `valdo.service` must serve (gunicorn ExecStart) | S | Dev | Deploy |
| **S14-2** | [#417](https://github.com/buddy-k23/valdo/issues/417) | Align Python baseline + document canonical nginx→gunicorn topology | S | Dev | Deploy |
| **S14-3** | [#413](https://github.com/buddy-k23/valdo/issues/413) | Chunked `sequential` honors start/step (parity) | S | Dev | Correctness |
| **S14-4** | [#414](https://github.com/buddy-k23/valdo/issues/414) | Non-chunked delimited parser `has_header` parity | S | Dev | Correctness |
| **S14-5** | [#416](https://github.com/buddy-k23/valdo/issues/416) | Triage the ~27 env-bound failures + promote the CI gate to blocking | S | Dev | Process |

**Total: 5 pts (at cap).**

---

## Daily sequencing — sequential, commit-per-story

Deploy stories share `packaging/valdo.spec` + `docs/PRODUCTION_DEPLOYMENT.md`; all share `CHANGELOG.md`. S14-5 must be last (it makes the gate blocking, which requires the suite green after the other fixes land). One commit each, clean tree between agents.

| Day | Story | Why this order |
|---|---|---|
| 1–2 | **S14-1** (#412) | RPM serve fix — the foundation of the deploy story. Add gunicorn; `ExecStart` binds :8000. |
| 3 | **S14-2** (#417) | Same files as S14-1 (spec + deployment doc) → after it. Pick one Python version; document the canonical topology. |
| 4–5 | **S14-3** (#413) | Correctness, independent of deploy. Honor start/step in chunked merged-state eval; parity test. |
| 6 | **S14-4** (#414) | Correctness. `has_header` plumbed into the non-chunked delimited parser; parity test. |
| 7 | **S14-5** (#416) | Last — triage the ~27 into xfail/skip (fix trivial real ones) so the suite is green, then drop `continue-on-error` so the gate blocks. |
| 8 | Buffer / review | Kickoff lands as final commit; push; close issues. |

---

## Definition of Ready

- [x] Each story maps to a single S-sized review finding with file:line evidence
- [x] Dependencies explicit (S14-2 → S14-1; S14-5 last, after the green-able fixes)
- [x] No live-infra dependency (RPM/serve verifiable via spec + a local serve smoke; correctness via SQLite/fixtures; CI on push)

## Definition of Done

- [ ] **S14-1:** RPM `valdo.service` `ExecStart` serves the app (gunicorn w/ uvicorn worker class, or `valdo serve`) binding :8000 with a pinned worker count; gunicorn is a declared dependency; a local `serve` smoke confirms :8000 responds
- [ ] **S14-2:** one Python version across RPM + Docker; `docs/PRODUCTION_DEPLOYMENT.md` documents the canonical nginx→gunicorn topology and the nginx upstream matches the worker pool
- [ ] **S14-3:** chunked `sequential` honors configurable start/step; a parity test asserts identical verdicts chunked vs non-chunked for a non-default rule
- [ ] **S14-4:** `has_header` honored in non-chunked delimited parsing; a parity test asserts identical results chunked vs non-chunked for a header'd CSV/TSV
- [ ] **S14-5:** the ~27 env-bound failures are xfail/skip with documented reasons (trivial real failures fixed); the unit suite is **green** under baseline conditions; the CI workflow drops `continue-on-error` so it blocks on failure (note: marking it a *required* status check is a GitHub branch-protection setting — flagged for repo admin)
- [ ] `pytest tests/unit/` is **green (0 unexpected failures)** by end of sprint (S14-3/4 add tests; S14-5 cleans the baseline); `tests/unit/test_no_shell_true.py` still passes
- [ ] Each story = one conventional commit on `valdo-version-v4` referencing `(S14-<m>, #<issue>)`; #412/#417/#413/#414/#416 closed
- [ ] Kickoff doc lands as the **final commit**; push to `origin/valdo-version-v4`

---

## Sprint risks

| Risk | L | I | Mitigation |
|---|---|---|---|
| Adding gunicorn changes the runtime footprint / the worker model interacts with the per-process MCP rate-limit + revocation (still in-memory) | M | M | Document the worker count and that multi-worker rate-limit/revocation correctness needs the shared backend (#419/#420) — pin a single worker as the safe default until then, matching the S13.5 guidance. |
| Python version bump (3.9→3.11 or pinning) breaks an RPM-targeted dep (e.g. oracledb/pandas wheels on RHEL) | M | M | Pin to the version already proven in Docker (3.11) and confirm the RPM's deps resolve; if RHEL is stuck on 3.9, document that as the baseline and align Docker down instead — pick the one that actually deploys. |
| Triaging the 27 hides a real regression as "env-bound" | M | H | Each xfail/skip must carry a specific reason (timezone/https/network/alembic-schema/missing-signing-key); anything not clearly env-bound gets fixed, not masked. Re-run with/without the env to confirm each is truly env-coupled. |
| Making the gate blocking turns PRs red on day one | M | M | Only flip `continue-on-error` AFTER the baseline is green (S14-5 ordering); the "required status check" branch-protection toggle is left to repo admin so we don't block mid-sprint. |
| Chunked start/step fix must not regress the default (1,step 1) case | L | M | Parity test covers BOTH the default and a non-default (e.g. descending / start≠1) rule; assert identical verdicts to the single-pass path. |
| Shared-file contention (`valdo.spec`, PRODUCTION_DEPLOYMENT.md, CHANGELOG) | M | M | Strictly sequential, commit-per-story, clean tree between agents. |

---

## Out of scope — do not pull in

- **The chunked cross-row registry refactor (#418)** — S14-3 only fixes the start/step bug; collapsing the map-reduce onto `_DISPATCH` is the separate P2.
- **ADR-0022 portability (#405/#406)** — still queued; not displaced permanently.
- **The P2 seams** (reconcile_all_service #421, shared rate-limit/revocation #419/#420, set-based comparator #423, config dead-layer #424, robustness #425, extract escaping #426).
- **The P3 cleanups** (#427–#431) and the larger refactors (ui.js modularization, >500-line file splits).
- **GitLab/Duo track · JSON/XML parser impls (#395/#396).**

---

## Roles

- **Sprint owner / PM:** you (self-paced)
- **Dev (all five stories):** `senior-fullstack-fintech-dev` agent
- **Reviewer:** you + Claude Code suggestions on every commit
- **Demo audience:** internal
- **Environment:** local dev (serve smoke) + CI

---

## After Sprint 14

| Sprint | Focus | Issues |
|---|---|---|
| **13 (queued)** | ADR-0022 portability — extract/db-compare onto the adapter | #405, #406 |
| **(P2)** | Structural seams | #418–#426 |
| **(P3)** | Cleanups | #427–#431 |
| **(candidate)** | GitLab/Duo track · JSON/XML parser impls | #395, #396 |
