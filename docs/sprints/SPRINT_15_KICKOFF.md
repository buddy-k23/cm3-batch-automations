# Sprint 15 — Kickoff (ADR-0022 Portability: extract / db-compare)

**Sprint goal:** Finish making Valdo's DB-integration features backend-agnostic. Sprint 12 made `reconcile` run on Oracle/Postgres/SQLite; this sprint does the same for `extract` and `db-compare` by routing them off the hard-wired `OracleConnection` onto the existing `get_database_adapter()` factory — and proves the whole set against the Sprint-11 Postgres full-stack. Completes the two-sprint ADR-0022 plan.

**Duration:** 1–2 weeks
**Capacity:** ~3 points (3 × S) — under cap; the work is ~420 LOC per ADR 0022's S12-1c/d estimate.
**Demo:** Internal. End state: `valdo extract` and `valdo db-compare` run against a SQLite (and Postgres) database via `DB_ADAPTER`, not just Oracle; the `db_file_compare_service` no longer falls back to `OracleConnection.from_env()`; and a Postgres full-stack smoke runs reconcile + extract + db-compare end-to-end.

**Context:** ADR 0022 (Accepted, Sprint 12) routes the DB-integration features through the adapter factory and split the work in two: Sprint 12 landed the high-risk half (the `CanonicalType` model + reconcile, SQLite-proven). This is the deferred lower-risk half — S12-1c (extract + db-compare + the `db_file_compare_service` fallback + the latent `run_tests_command.py:141` `extract_to_file(params=…)` signature bug) and S12-1d (disposition of `OracleConnection`/`truth_source`/`transaction` + docs + Postgres smoke). After this, the architecture review's "good seam, bypassed path" finding for the DB layer is closed for the user-facing features. Note: S13.5-4 already hardened the extractor's SQL (bound limit, allow-listed identifiers, dropped raw `where`) — that hardening must be preserved as we route through the adapter.

---

## Stories

| ID | GitHub | Title | Size | Owner | Track |
|---|---|---|---|---|---|
| **S15-1** | [#405](https://github.com/buddy-k23/valdo/issues/405) | Route `extract` onto the adapter (+ fix the `extract_to_file(params)` bug) | S | Dev | Portability |
| **S15-2** | [#405](https://github.com/buddy-k23/valdo/issues/405) | Route `db-compare` / `db_file_compare_service` onto the factory | S | Dev | Portability |
| **S15-3** | [#406](https://github.com/buddy-k23/valdo/issues/406) | Dispositions (OracleConnection/truth_source/transaction) + docs + Postgres full-stack smoke | S | Dev | Portability |

**Total: ~3 pts.** #405 is executed as two cohesive commits (extract, then db-compare) for reviewability; #406 closes the loop.

---

## Daily sequencing — sequential, commit-per-story

S15-2 builds on the adapter-routing pattern S15-1 establishes; S15-3 documents + smoke-tests the whole set. All share `CHANGELOG.md`. One commit each, clean tree between agents.

| Day | Story | Why this order |
|---|---|---|
| 1–3 | **S15-1** (#405 extract) | The substantive change: `DataExtractor`/the `extract` command use `get_database_adapter().extract_to_file` + adapter catalog reads instead of `OracleConnection`. Fix the `run_tests_command.py:141` `extract_to_file(params=…)` signature mismatch. Preserve the S13.5-4 SQL hardening. Prove on SQLite. |
| 4–5 | **S15-2** (#405 db-compare) | Fix the `db_file_compare_service.py` non-Oracle branch that calls `OracleConnection.from_env()` → use the factory, so `db-compare` honors `DB_ADAPTER`. Reuses S15-1's extract path. Prove on SQLite. |
| 6–7 | **S15-3** (#406) | Finalize dispositions per ADR 0022; update the DB-integration docs (which features are backend-agnostic vs Oracle-only); **Postgres full-stack smoke**: bring up the Sprint-11 docker-compose stack and run reconcile + extract + db-compare against Postgres. |
| 8 | Buffer / review | Kickoff lands as final commit; push; close #405/#406. |

---

## Definition of Ready

- [x] ADR 0022 already made the design call (factory routing, canonical types, dispositions)
- [x] Dependencies explicit (S15-2 reuses S15-1's adapter path; S15-3 last)
- [x] Provable on SQLite without Oracle; Postgres provable via the Sprint-11 full-stack (Docker available)
- [x] S13.5-4 extractor hardening + the existing `extract_to_file` contract understood before changing

## Definition of Done

- [ ] **S15-1:** `valdo extract` runs against SQLite (proven by test) via the adapter; Oracle path preserved; the `run_tests_command.py:141` `extract_to_file(params=…)` signature bug fixed (the helper accepts/binds params); the S13.5-4 hardening (bound limit, allow-listed identifiers, no raw `where`) is preserved on the adapter path
- [ ] **S15-2:** `db_file_compare_service` uses `get_database_adapter()` (no `OracleConnection.from_env()` fallback); `valdo db-compare` runs against SQLite end-to-end (proven by test); Oracle path preserved
- [ ] **S15-3:** dispositions finalized + documented — `OracleConnection` deprecated as a direct entry point (kept as oracle-adapter internals), `truth_source.py` unchanged with a documented rationale (ADR 0010 seam), `transaction.py` labeled Oracle-only/quarantined per ADR 0022; the DB-integration docs clearly state backend-agnostic (reconcile/extract/db-compare) vs Oracle-only features; a **Postgres full-stack smoke** of reconcile + extract + db-compare passes (run live via docker-compose; report what ran)
- [ ] `pytest tests/unit/` stays **green (0 failed)** under CI conditions (`.env`/`valdo.db` aside, `VALDO_SESSION_SIGNING_KEY` set); new SQLite extract/db-compare tests added; `test_no_shell_true` still passes
- [ ] **Parameterized SQL only**; no new runtime deps; no secrets committed
- [ ] Each story = one conventional commit on `valdo-version-v4` referencing `(S15-<m>, #<issue>)`; #405/#406 closed
- [ ] Kickoff doc lands as the **final commit**; push to `origin/valdo-version-v4`

---

## Sprint risks

| Risk | L | I | Mitigation |
|---|---|---|---|
| Routing extract onto the adapter loses the S13.5-4 SQL hardening (re-introducing the injection surface) | M | **H** | The hardening (limit binding, identifier allow-listing, no raw `where`) must be preserved/re-applied on the adapter path; keep the S13.5-4 injection tests green and add coverage that the adapter path is equally guarded. |
| `extract_to_file` semantics differ across adapters (query vs whole-table, chunking, fixed-width output) | M | M | ADR 0022 flagged this; verify each adapter's `extract_to_file` covers what `DataExtractor` needs; if a gap exists, extend the ABC method consistently (mirror the S12-2 approach) rather than special-casing Oracle. |
| `extract_to_file` output corruption (delimiter/newline in a value) — the #426 finding lives in this exact path | L | M | Out of scope here (#426 is a separate P2), but if a test trips it, note it; don't silently regress. Flag #426 as the natural adjacent follow-up. |
| Oracle `db-compare`/`extract` behavior regresses when the path changes | M | H | Preserve the Oracle path via the oracle adapter; keep/extend existing Oracle tests (mock/adapter) and assert parity before/after, mirroring the S12-3 reconcile-parity approach. |
| Postgres full-stack smoke flakes on bring-up (TLS/health/timing) | M | L | Reuse the proven `valdo-setup.sh --env full-stack` path + the S11 healthcheck wait; if a step can't run live, capture what did and state it clearly. |
| Shared-file contention (CHANGELOG, the DB modules) | M | M | Strictly sequential, commit-per-story, clean tree between agents. |

---

## Out of scope — do not pull in

- **`extract_to_file` delimiter/newline escaping (#426)** — separate P2; adjacent but not this sprint (flag if a test trips it).
- **The other P2 seams** (#418 registry collapse, #419/#420 shared MCP backends, #421 reconcile_all_service, #424 config dead-layer, #425 robustness) and **#432 coverage**.
- **P3 cleanups** (#427–#431) — except the `transaction.py` disposition, which ADR 0022 folds into S15-3 (#431 overlaps; coordinate so we don't double-handle).
- **GitLab/Duo track · JSON/XML parser impls (#395/#396).**

---

## Roles

- **Sprint owner / PM:** you (self-paced)
- **Dev (all three stories):** `senior-fullstack-fintech-dev` agent
- **Reviewer:** you + Claude Code suggestions on every commit
- **Demo audience:** internal
- **Environment:** local dev (SQLite) + Postgres full-stack (docker-compose, Docker available)

---

## After Sprint 15

| Sprint | Focus | Issues |
|---|---|---|
| **(P2)** | Structural seams — registry collapse, shared MCP backends, reconcile_all_service, config dead-layer, robustness, coverage, extract escaping | #418–#426, #432 |
| **(P3)** | Cleanups | #427–#431 (less #431 if folded into S15-3) |
| **(candidate)** | GitLab/Duo track · JSON/XML parser impls | #395, #396 |
