# Sprint 12 — Kickoff

**Sprint goal:** Begin making Valdo's DB-integration features backend-agnostic so they work on Postgres/SQLite, not just Oracle. This sprint lands the load-bearing, correctness-sensitive half per ADR 0022: a portable column-metadata + canonical-type seam, and the reconciliation engine rewritten onto it (proven against SQLite). The lower-risk half — extract, db-compare, service-fallback, dispositions — follows in Sprint 13.

**Duration:** 2 weeks
**Capacity:** 5 points (ADR S + 2 × S impl) — at the cap. ~640 LOC of implementation.
**Demo:** Internal. End state: `valdo reconcile`/`reconcile-all` runs end-to-end against a **SQLite** database using the same mapping that worked on Oracle — proving the catalog reads and type comparison are no longer Oracle-locked. Oracle behavior is preserved.

**Context:** Sprint 11's Postgres full-stack runs the app/UI/MCP but NOT the DB-integration features (`db-compare`, `extract`, `reconcile`, `onboard-source`) — they hard-wire `oracledb` + Oracle catalog SQL (`all_tab_columns`, `user_tab_columns`, `all_tables`) + Oracle DDL types. ADR 0022 (Accepted, S12-1, #402) decided to route them through the **existing** `DatabaseAdapter` factory (the ABC + all three adapters already exist; the factory was wired into only one service). ADR 0022 judged the full refactor **spans two sprints** and split it at a clean dependency seam.

---

## Stories

| ID | GitHub | Title | Size | Owner | Track |
|---|---|---|---|---|---|
| **S12-1** | [#402](https://github.com/buddy-k23/valdo/issues/402) | ADR 0022 — adapter-agnostic DB integration | S | Architect | Engine portability |
| **S12-2** | [#403](https://github.com/buddy-k23/valdo/issues/403) | Extend `DatabaseAdapter` with `get_column_metadata` + `CanonicalType` (ADR 0022 S12-1a) | S | Dev | Engine portability |
| **S12-3** | [#404](https://github.com/buddy-k23/valdo/issues/404) | Route reconciliation onto the adapter + canonical type matching (ADR 0022 S12-1b) | S | Dev | Engine portability |

**Total: 5 pts (at cap).** S12-1 is already drafted+committed; S12-2 and S12-3 are the implementation.

---

## Scope decisions (this sprint)

Per ADR 0022's two-sprint split:

- **In Sprint 12 (high-risk half, SQLite-proven):** the metadata/type abstraction (S12-2) and the reconciliation rewrite onto it (S12-3). These are the correctness-sensitive pieces — the canonical type model and the reconcile comparison logic.
- **Deferred to Sprint 13 (lower-risk half):** S12-1c — `extract` + `db-compare` + the `db_file_compare_service.py` OracleConnection-fallback fix (+ the latent `run_tests_command.py:141` `extract_to_file(params=…)` signature bug ADR 0022 flagged); S12-1d — disposition of `connection.py`/`truth_source.py`/`transaction.py`, docs, and the Postgres-full-stack smoke. Issues filed at Sprint 12 close.
- **Stays Oracle-only (per ADR 0022):** `transaction.py` VARCHAR2 DDL (demo/test table creation, no in-scope consumer) and `truth_source.py` (the ADR-0010 L2b/E2E seam — a different consumer, not merged here). `OracleConnection` stays as oracle-adapter internals, deprecated as a direct entry point.

---

## Daily sequencing — sequential, commit-per-story

S12-3 depends on S12-2 (it consumes `get_column_metadata` + `CanonicalType`). Run in order, one commit each.

| Day | Story | Why this order |
|---|---|---|
| 1–2 | **S12-1** (#402 ADR) | Done — drafted + committed. Sets the design all impl follows. |
| 3–6 | **S12-2** (#403 metadata/types) | Foundation. Additive: extend the ABC + add `CanonicalType` + implement `get_column_metadata` + per-dialect normalization in all three adapters, with the type-matrix unit tests. No consumer rewired yet — zero behavior change. |
| 7–9 | **S12-3** (#404 reconcile) | Depends on S12-2. Rewrite `reconciliation.py` (+ `query_executor.py` catalog reads) onto the adapter + canonical matrix; boolean/UNKNOWN become dialect-neutral advisories. **Prove it: reconcile against SQLite end-to-end.** Preserve Oracle behavior. |
| 10 | Buffer / sprint review | File the Sprint 13 follow-up issues (S12-1c, S12-1d); kickoff lands as final commit; push. |

---

## Definition of Ready

- [x] ADR 0022 makes the design call (canonical type model, abstraction shape, story breakdown)
- [x] Dependencies explicit (S12-3 → S12-2)
- [x] Each impl story ≤ ~1.5 days, ≤ ~340 LOC
- [x] Portability provable without Oracle (SQLite fixture)

## Definition of Done

- [ ] S12-2: ABC `get_column_metadata` + `ColumnMeta` + `CanonicalType` added; all three adapters implement it; per-adapter normalization unit tests (Oracle/PG/SQLite type matrices incl. boolean/UNKNOWN); **additive only — no consumer rewired**
- [ ] S12-3: `reconcile`/`reconcile-all` runs against a **SQLite** fixture end-to-end with the same mapping that worked on Oracle; Oracle behavior preserved; no raw Oracle catalog SQL left in reconciliation's portable path
- [ ] `pytest tests/unit/` passes with **zero new failures vs the known env-bound baseline** (run with the local `.env`/`valdo.db` set aside to match baseline conditions; suite needs `VALDO_SESSION_SIGNING_KEY`); `tests/unit/test_no_shell_true.py` still passes
- [ ] **Parameterized SQL only**; no new runtime deps
- [ ] Docs: `docs/sphinx/modules.rst` for any new public module (`make html` in CI); one-line `CHANGELOG.md` per story; note in the reconcile docs that SQLite/Postgres are now supported for reconcile
- [ ] Each story = one conventional commit on `valdo-version-v4` referencing `(S12-<m>, #<issue>)`; #402/#403/#404 closed
- [ ] Kickoff doc lands as the **final commit**; push to `origin/valdo-version-v4`

---

## Sprint risks

| Risk | L | I | Mitigation |
|---|---|---|---|
| The canonical type matrix is subtly wrong → reconcile gives false pass/fail on a real Oracle mapping | M | **H** | The type matrix is the load-bearing artifact — unit-test every mapping-type→canonical and raw→canonical cell per dialect (S12-2). S12-3's SQLite end-to-end test uses a mapping that previously reconciled on Oracle; assert identical field-level verdicts. |
| Rewriting `reconciliation.py` regresses Oracle reconcile behavior | M | **H** | Preserve the Oracle path via the oracle adapter; keep/extend existing reconciliation tests (mock or oracle-adapter) and assert verdict parity before/after. |
| SQLite's dynamic typing makes some columns `UNKNOWN`, weakening the proof | M | M | ADR 0022 treats UNKNOWN as compatible+note (not error); the SQLite fixture uses declared types with clear affinity so the proof is meaningful, and the test asserts the advisory (not silent pass). |
| Scope bleed — pulling extract/db-compare into this sprint | L | M | Explicitly Sprint 13 per ADR 0022. S12-3 touches `query_executor.py` only for the catalog reads reconciliation uses; extract/compare stay untouched. |
| Stray runtime artifact committed (`.env`, `valdo.db`, SQLite fixture DB) | M | M | All gitignored; stage explicit paths; verify `git diff --cached --name-only`; put test fixture DBs under tmp or gitignored paths. |

---

## Out of scope — do not pull in

- **S12-1c / S12-1d** (extract, db-compare, service fallback, dispositions, docs, PG smoke) — Sprint 13 per ADR 0022
- **`transaction.py` VARCHAR2 DDL** and **`truth_source.py`** — stay Oracle-only per ADR 0022
- **GitLab / GitLab Duo track** — still deferred
- **`--env prod` scaffold** — after INT is proven
- **JSON/XML parser implementation** (#395, #396) — engine-breadth backlog
- **DB-to-DB comparator** — filed only on a concrete BA request per ADR 0020

---

## Roles

- **Sprint owner / PM:** you (self-paced)
- **Architect (ADR 0022):** `principal-enterprise-architect` agent (done)
- **Dev (S12-2, S12-3):** `senior-fullstack-fintech-dev` agent
- **Reviewer:** you + Claude Code suggestions on every commit
- **Demo audience:** internal
- **Environment:** local dev (SQLite for the portability proof; Postgres full-stack available)

---

## After Sprint 12

| Sprint | Focus | Issues |
|---|---|---|
| **13** | Adapter-agnostic DB integration cont. (ADR 0022 S12-1c + S12-1d): extract + db-compare + service fallback + the `extract_to_file(params)` bug; OracleConnection/truth_source/transaction disposition; docs; Postgres full-stack smoke | [#405](https://github.com/buddy-k23/valdo/issues/405), [#406](https://github.com/buddy-k23/valdo/issues/406) |
| **(candidate)** | GitLab / GitLab Duo integration (MCP/Duo path, no GitLab API) | new issues |
| **(unscheduled)** | JSON / XML parser implementation | #395, #396 |
| **(conditional)** | DB-to-DB comparator | filed on concrete BA request, per ADR 0020 |
