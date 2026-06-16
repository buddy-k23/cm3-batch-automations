# Sprint 11 — Kickoff

**Sprint goal:** Implement the two `--env` seams that Sprint 10 stubbed in `scripts/valdo-setup.sh`. Ship a docker-compose **full-stack** local environment (Valdo app + Postgres) for testing closer to prod without external infra, and an **INT** scaffold-and-validate path that produces/checks the INT-region config so an operator can complete the bring-up. One unified setup script now covers `local` → `full-stack` → `int`.

**Duration:** 2 weeks
**Capacity:** 5 points (3 × S) — at the cap.
**Demo:** Internal. End state: `bash scripts/valdo-setup.sh --env full-stack` brings up Postgres + the app via docker-compose, migrations apply, the app reports healthy and serves the UI; `--env int` produces a validated `.env.int` (or tells the operator precisely what's missing). The Sprint 10 `local` path is unchanged.

**Context:** Sprint 10 consolidated local setup into one script with an `--env <local|int|full-stack>` seam (local implemented; int/full-stack printed "deferred — see roadmap"). This sprint fills the two deferred seams. The repo already has a `postgresql` adapter and `db_url.py` Postgres wiring, but no `docker-compose` anywhere; INT has no live environment here, so it is scaffold + validate, not a live connect.

---

## Stories

| ID | GitHub | Title | Size | Owner | Track |
|---|---|---|---|---|---|
| **S11-1** | [#399](https://github.com/buddy-k23/valdo/issues/399) | docker-compose full-stack — Valdo app + Postgres | S | Dev | DevEx |
| **S11-2** | [#400](https://github.com/buddy-k23/valdo/issues/400) | `valdo-setup.sh --env full-stack` — drive the compose stack | S | Dev | DevEx |
| **S11-3** | [#401](https://github.com/buddy-k23/valdo/issues/401) | `valdo-setup.sh --env int` — INT scaffold + config validation | S | Dev | DevEx |

**Total: 5 pts (at cap).**

---

## Scope decisions (this sprint)

- **Full-stack DB = Postgres.** The `postgresql` adapter and `db_url.py` wiring already exist (`DB_ADAPTER=postgresql` + `DB_USER/PASSWORD/HOST/PORT/NAME`). Postgres is the light, sensible local full-stack DB. An Oracle-XE variant is a possible future option, not this sprint.
- **INT is scaffold + validate, not a live deploy.** There is no live INT environment in this workspace. `--env int` produces/validates `.env.int`, runs migrations + a smoke check **only if** the Oracle DSN is reachable, otherwise prints precise next steps and exits 0.
- **GitLab is still a separate, deferred track.** Not in this sprint. (The team reaches GitLab DAP via VS Code agentic chat / GitLab Duo, no GitLab API access — a future integration leans on the MCP/Duo path. See `docs/mcp-clients/gitlab-duo/`.) Candidate for Sprint 12.
- **Production `--env prod`** is out of scope — the S9-1 runbook + RPM/systemd already cover prod deploy; a `--env prod` scaffold can follow INT once INT is proven.

---

## Daily sequencing — sequential, commit-per-story

Stories share `scripts/valdo-setup.sh`, docs, and `CHANGELOG.md`. Run in order, one commit each, clean tree between agents (proven Sprints 9–10). **Docker daemon is available in this workspace**, so the full-stack stack is verified live, not just reasoned.

| Day | Story | Why this order |
|---|---|---|
| 1–4 | **S11-1** (#399 compose) | The compose stack is the foundation `--env full-stack` drives. Build + verify it standalone (`docker compose up`, migrations to Postgres, healthcheck green) before wiring the script. |
| 5–7 | **S11-2** (#400 wiring) | Depends on S11-1. Replace the `--env full-stack` stub: preflight docker, `compose up -d --build`, wait healthy, ensure migrations, smoke, teardown note. |
| 8–9 | **S11-3** (#401 int) | Independent of compose. `.env.int.example` + `config/int.json`, validate required INT vars, conditional migrate/smoke when reachable. |
| 10 | Buffer / sprint review | Kickoff doc lands as final commit; push. Plan Sprint 12 (GitLab/Duo track or prod scaffold). |

---

## Definition of Ready

- [x] AC testable per story (compose up + healthy; one-command full-stack; INT validate reports missing vars precisely)
- [x] Dependencies explicit (S11-2 → S11-1; S11-3 independent; serialized by commit-per-story)
- [x] Each story ≤ ~1.5 days for one owner
- [x] No live-prod / live-INT dependency (INT is scaffold+validate)

## Definition of Done

- [ ] `docker compose config` validates; `docker compose up` brings up Postgres + app; migrations apply to Postgres; app healthcheck healthy; UI at :8000/ui (S11-1) — **verified live**
- [ ] `bash scripts/valdo-setup.sh --env full-stack` works end-to-end with a clear error if Docker is absent (S11-2)
- [ ] `--env int` produces/validates `.env.int`, reports missing vars precisely, conditional migrate/smoke (S11-3)
- [ ] `pytest tests/unit/` passes with **zero new failures vs the known ~27–28 env-bound baseline** (suite needs `VALDO_SESSION_SIGNING_KEY`; run with the live-run `.env`/`valdo.db` set aside to match baseline conditions); `tests/unit/test_no_shell_true.py` still passes
- [ ] **No secrets committed** — `.env.int.example` is placeholders only; `.env`/`.env.int`/`valdo.db` stay gitignored
- [ ] **Documentation updated**: README Quick Start (the three `--env` paths), `docs/INSTALL.md`, `docs/DEPLOYMENT_OPTIONS.md`/`PRODUCTION_DEPLOYMENT.md` for full-stack + INT; one-line `CHANGELOG.md` per story
- [ ] Each story = one conventional commit on `valdo-version-v4` referencing `(S11-<m>, #<issue>)`; #399/#400/#401 closed
- [ ] Kickoff doc lands as the **final commit**; push to `origin/valdo-version-v4`

---

## Sprint risks

| Risk | L | I | Mitigation |
|---|---|---|---|
| `.env.int.example` accidentally carries a real secret | L | **H** | Placeholders only; reviewer checks the diff; `.env*` (except `*.example`) gitignored. Add a test/grep that the committed example has no high-entropy values. |
| docker-compose migration ordering — app serves before migrations apply, hitting an empty schema | M | M | A one-shot `migrate` step gates the app (compose `depends_on: condition: service_completed_successfully`, or an entrypoint that runs `alembic upgrade head` before `serve`). Verify the app comes up against a migrated Postgres. |
| Postgres driver (`psycopg2`) missing from the image | M | M | Confirm it's in `requirements.txt`; add if absent. `docker compose build` proves it at image-build time. |
| Full-stack verification can't run in CI (no Docker there) | M | L | Verify live in this workspace (daemon is up); the committed artifacts (`docker-compose.yml`, script path) are also statically validated (`docker compose config`). State live-vs-reasoned clearly in the commit. |
| A stray runtime artifact (`.env`, `valdo.db`, postgres volume, `.env.int`) gets committed | M | M | All gitignored (Sprint 10 added `/*.db`); each commit stages explicit paths and excludes the kickoff; verify `git diff --cached --name-only` before every commit. |
| Shared-file contention across stories | M | M | Strictly sequential, commit-per-story, clean tree between agents. |

---

## Out of scope — do not pull in

- **Local GitLab / GitLab Duo integration** — separate deferred track (no GitLab API; MCP/Duo path). Candidate Sprint 12.
- **`--env prod`** — RPM/systemd + S9-1 runbook already cover prod; a prod scaffold follows INT later.
- **Oracle-XE full-stack variant** — Postgres only this sprint.
- **pydantic-settings refactor** of the split config (`db_config.py` dataclass + `config/<env>.json`) — separate refactor.
- **JSON/XML parser implementation** (#395, #396) — engine-breadth backlog.
- **DB-to-DB comparator** — filed only on a concrete BA request per ADR 0020.

---

## Roles

- **Sprint owner / PM:** you (self-paced)
- **Dev (all three stories):** delegated to `senior-fullstack-fintech-dev` agent
- **Reviewer:** you + Claude Code suggestions on every commit
- **Demo audience:** internal
- **Environment:** local dev (Docker available) + INT scaffold (no live INT)

---

## After Sprint 11

| Sprint | Focus | Issues |
|---|---|---|
| **12 (candidate)** | Local GitLab / GitLab Duo integration (MCP/Duo path, no GitLab API) | new issues |
| **(candidate)** | `--env prod` scaffold once INT is proven | new issue |
| **(unscheduled)** | JSON parser implementation | #395 |
| **(unscheduled)** | XML parser implementation | #396 |
| **(conditional)** | DB-to-DB comparator | filed on concrete BA request, per ADR 0020 |
