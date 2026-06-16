# Sprint 10 — Kickoff

**Sprint goal:** Finish the background-job worker that Sprint 9 stubbed (#397, per ADR 0021) so async validation is production-real, and ship a single local setup script (#398) so a developer goes from a fresh clone to a running Valdo with one command. Two tracks: close prod-throughput debt, and pay down developer-onboarding friction.

**Duration:** 2 weeks
**Capacity:** 5 points — at the cap. #397 splits into two S stories (runtime + cutover); the setup script is one S.
**Demo:** Internal. End state: a worker process drains the queue with graceful shutdown + a stuck-run reaper, multiple workers never double-claim, the systemd unit ships in the RPM, async validation is on by default; and `bash scripts/valdo-setup.sh` stands up a working local Valdo on SQLite with zero external infra.

**Context:** Sprint 9 landed ADR 0021 (Option A — the run-registry table is the queue) + the skeleton: atomic `claim_next()`, `valdo run-job-worker --once`, and the `VALDO_MCP_ASYNC_VALIDATE` flag (off by default, sync the fallback). #397 is the follow-up that makes it real. Separately, the team flagged onboarding friction: three divergent per-OS setup scripts, SQLite never wired for zero-infra local, and the new `VALDO_MCP_*` vars missing from `.env.example`.

---

## Stories

| ID | GitHub | Title | Size | Owner | Track |
|---|---|---|---|---|---|
| **S10-1** | [#397](https://github.com/buddy-k23/valdo/issues/397) | Background worker runtime — poll loop + graceful shutdown + reaper | S | Dev | Throughput |
| **S10-2** | [#397](https://github.com/buddy-k23/valdo/issues/397) | Worker packaging + async cutover — multi-worker, systemd, flip default | S | Dev | Throughput |
| **S10-3** | [#398](https://github.com/buddy-k23/valdo/issues/398) | Single local setup script + `.env.example` completion | S | Dev | DevEx |

**Total: 5 pts (at cap).** #397 is one M issue executed as two cohesive commits (runtime, then packaging/cutover) for reviewability.

---

## Scope decisions (this sprint)

The original ask was "scripts to set up Valdo in multiple environments including local GitLab." Narrowed with the PO:

- **Local standalone setup only.** The script is *architected* with an `--env <local|int|full-stack>` seam, but only `local` is implemented; `int` / `full-stack` exit with a "deferred — see roadmap" message.
- **GitLab is a separate future track** — deferred. (The team reaches GitLab DAP via VS Code agentic chat / GitLab Duo, with no GitLab API access, so a future integration leans on the MCP/Duo path, not GitLab's REST API.)
- **INT region + local full-stack (docker-compose)** — deferred to a future sprint.

---

## Daily sequencing — sequential, commit-per-story

Like Sprint 9, stories share `CHANGELOG.md`, docs, and `src/mcp/` / `src/commands/`. Run in order, one commit each, clean tree between agents.

| Day | Story | Why this order |
|---|---|---|
| 1–4 | **S10-1** (#397 runtime) | The worker loop + reaper is the substantive new code. Build on Sprint 9's `claim_next()` + `--once`. Implement `reap_stuck_running()` (currently `NotImplementedError`) — likely needs a heartbeat/attempt column → Alembic `0006`. Signals + backoff + `--poll-interval`/`--max-runs`. |
| 5–7 | **S10-2** (#397 cutover) | Depends on S10-1. Prove N concurrent workers never double-claim under load; package the `valdo-run-job-worker.service` systemd unit in the RPM spec; flip `VALDO_MCP_ASYNC_VALIDATE` default ON **without breaking local dev** (see risks). Closes #397. |
| 8–9 | **S10-3** (#398 setup) | Independent of the worker. Single `scripts/valdo-setup.sh`, SQLite zero-infra default, `--env` seam, `.env.example` + VALDO_MCP_* vars, README/INSTALL. |
| 10 | Buffer / sprint review | Kickoff doc lands as final commit; push. Plan Sprint 11. |

---

## Definition of Ready

- [x] AC testable per story (concurrency test, reaper test, one-command setup verify)
- [x] Dependencies explicit (S10-2 → S10-1; S10-3 independent; all serialized by commit-per-story)
- [x] Each story ≤ ~1.5 days for one owner
- [x] ADR 0021 already made the design call for #397

## Definition of Done

- [ ] `pytest tests/unit/` passes with **zero new failures vs the known-28 env-bound baseline** (suite needs `VALDO_SESSION_SIGNING_KEY` set); `tests/unit/test_no_shell_true.py` still passes
- [ ] New tests: reaper reclaims a stuck `running` row (S10-1); N-worker no-double-claim under concurrency (S10-2); fresh-clone one-command setup yields a passing `valdo info` (S10-3)
- [ ] **Documentation updated** per backlog standard:
  - `docs/PRODUCTION_DEPLOYMENT.md` — worker systemd unit + reaper ops; flip the async-flag note from "off" to "on by default"
  - `docs/MCP_SERVER.md` if the `validate_file` contract note changes
  - `README.md` Quick Start → the single setup command; `docs/INSTALL.md`
  - `.env.example` documents all `VALDO_MCP_*` vars
  - One-line `CHANGELOG.md` entry per story; `docs/sphinx/modules.rst` if a new public module lands
- [ ] Each story = one conventional commit on `valdo-version-v4` referencing `(S10-<m>, #<issue>)`; #397 closed after S10-2
- [ ] Kickoff doc lands as the **final commit**; push to `origin/valdo-version-v4`

---

## Sprint risks

| Risk | L | I | Mitigation |
|---|---|---|---|
| Flipping `VALDO_MCP_ASYNC_VALIDATE` ON by default silently breaks local dev — `validate_file` enqueues but no worker runs, so jobs never complete | **H** | **H** | S10-2 must not strand local users. Options the dev evaluates: (a) keep sync fallback when no worker has checked in recently (heartbeat-aware), (b) default ON only under the systemd/prod profile while local stays sync, (c) `valdo serve` auto-spawns an in-process worker in dev. Pick one, document it, and add a test that a no-worker environment still completes a validation. |
| Reaper reclaims a job that's actually still running on a live worker (false-positive on a long 10M-row job) | M | H | Heartbeat column updated periodically by the running worker; reaper only reclaims rows whose heartbeat is older than a generous multiple of the poll interval. Test both: stale row reclaimed, fresh-heartbeat row left alone. |
| Multi-worker double-claim under real concurrency despite the `claim_next` guard | L | H | S10-2 ships a concurrency test spinning N threads/processes against one queue; assert each job runs exactly once. The `UPDATE ... WHERE status='queued'` + rowcount==1 guard from Sprint 9 is the backstop. |
| Alembic `0006` (heartbeat/attempt column) must be cross-dialect (Oracle + Postgres + SQLite) | M | M | Mirror `0004`/`0005` patterns; run the migration against SQLite in-test. Keep the column nullable with a default so it's backward-compatible with in-flight `0005` databases. |
| Setup script's SQLite default diverges from the Oracle-assumed runtime and masks Oracle-only bugs | L | M | SQLite is the documented *local-dev* default only; the script's `--env int` seam (deferred impl) is where Oracle wiring lands. README is explicit that local = SQLite, INT/prod = Oracle. |
| Shared-file contention across stories | M | M | Strictly sequential, commit-per-story, clean tree between agents (proven in Sprint 9). |

---

## Out of scope — do not pull in

- **Local GitLab provisioning** (server / gitlab-runner / `.gitlab-ci.yml`) — separate future GitLab track; team uses GitLab Duo agentic chat, no API access
- **INT region setup** — `--env int` seam only this sprint
- **Local full stack** (docker-compose app + Postgres) — `--env full-stack` seam only
- **JSON/XML parser implementation** (#395, #396) — engine-breadth backlog
- **DB-to-DB comparator** — filed only on a concrete BA request per ADR 0020
- **Distributed/multi-node queue coordination** — single-DB-queue (Option A) is enough for INT pilot; revisit only at scale-out
- **pydantic-settings refactor** of the split config (`db_config.py` dataclass + `config/<env>.json`) — tempting alongside the setup script, but a separate refactor; not this sprint

---

## Roles

- **Sprint owner / PM:** you (self-paced)
- **Dev (all three stories):** delegated to `senior-fullstack-fintech-dev` agent
- **Reviewer:** you + Claude Code suggestions on every commit
- **Demo audience:** internal
- **Environment:** local dev + INT region (no live prod)

---

## After Sprint 10

| Sprint | Focus | Issues |
|---|---|---|
| **11 (candidate)** | Setup epic cont. — local full-stack (docker-compose app + Postgres) + INT `--env` impl | new issues |
| **(separate track)** | Local GitLab / GitLab Duo integration (MCP/Duo path, no GitLab API) | new issues |
| **(unscheduled)** | JSON parser implementation | #395 |
| **(unscheduled)** | XML parser implementation | #396 |
| **(conditional)** | DB-to-DB comparator | filed on concrete BA request, per ADR 0020 |
