# Sprint 9 — Kickoff

**Sprint goal:** Production-harden the MCP server so it can run behind the bank's edge in the INT region. Add TLS/nginx termination, rate limiting, per-token revocation, an MCP-specific health probe, and decouple long validations from the request path. These are build-ahead artefacts — none require a live prod environment to develop or test; they make the server *deployable* when the greenlight lands.

**Duration:** 2 weeks
**Capacity:** 5 points (5 × S) — **at the cap.** Two stories are honestly borderline-M (#389 revocation, #391 queue); mitigations below.
**Demo:** Internal — SRE/security audience. End state: an SRE can follow `docs/PRODUCTION_DEPLOYMENT.md` from zero to a TLS-terminated `https://valdo.bank.internal/mcp/initialize` handshake, with rate limits, a revocation lever, a load-balancer health probe, and validations that no longer tie up a worker.

**Context:** Sprints 1–7 built the MCP server + BA UX; Sprint 8/8.5 paid engine-hygiene debt and scoped the format roadmap. Sprint 9 was gated on prod greenlight (per the roadmap) — greenlit now. EF-S7 brought production auth; EF-S4 deliberately carved out the synchronous-validation problem (#391). This sprint closes those prod gaps.

---

## Stories

| ID | GitHub | Title | Size | Owner | Track |
|---|---|---|---|---|---|
| **S9-1** | [#387](https://github.com/buddy-k23/valdo/issues/387) | TLS + nginx reverse-proxy for `/mcp/` | S | Dev | Edge/deploy |
| **S9-2** | [#390](https://github.com/buddy-k23/valdo/issues/390) | `/mcp/health` endpoint for LB probes | S | Dev | Observability |
| **S9-3** | [#388](https://github.com/buddy-k23/valdo/issues/388) | Rate limiting on `/mcp/` (per-token + per-IP) | S | Dev | Resilience |
| **S9-4** | [#389](https://github.com/buddy-k23/valdo/issues/389) | Per-token revocation blocklist (jti-based) | S* | Dev | Security |
| **S9-5** | [#391](https://github.com/buddy-k23/valdo/issues/391) | ADR 0021 + background-queue skeleton | S* | Architect + Dev | Throughput |

**Total: 5 pts (at cap).** `*` = borderline-M, scoped down (see risks).

---

## Dependency sequencing — why this is a *sequential* sprint

Every story touches the same shared files: a **new `docs/PRODUCTION_DEPLOYMENT.md`**, `CHANGELOG.md`, and `src/mcp/`. #390 edits the `packaging/nginx/valdo.conf` that #387 creates. Running agents in parallel against one working tree would stomp these. So stories run **in order, one conventional commit each**, the tree clean between them:

| Day | Story | Why this order |
|---|---|---|
| 1–2 | **S9-1** (#387) | **Foundation.** Creates `packaging/nginx/valdo.conf` + `docs/PRODUCTION_DEPLOYMENT.md` (the runbook every later story appends to) + `X-Forwarded-*` handling. No engine code. |
| 3 | **S9-2** (#390) | `/mcp/health` exercises handshake + a resource read + tool count. Then updates the nginx conf (from S9-1) to use it as the upstream probe. Depends on S9-1. |
| 4–5 | **S9-3** (#388) | `src/mcp/rate_limit.py` token-bucket. Independent code; appends its section to the runbook. |
| 6–8 | **S9-4** (#389) | Biggest story: `jti` in token format + 24h grace, `MCP_REVOKED_TOKENS` table + Alembic `0005`, `POST /api/v2/mcp/revoke`, `valdo mcp-revoke` CLI, 60s-TTL cache in `auth.py`. |
| 9 | **S9-5** (#391) | ADR `0021-mcp-background-jobs.md` recommends **Option A** (existing run-history table as queue — zero new deps) + a thin enqueue skeleton. Full worker impl is a follow-up M. |
| 10 | Buffer / sprint review | Kickoff doc lands as final commit; push. Plan Sprint 10. |

---

## Definition of Ready

- [x] AC testable per story (named test files in each issue)
- [x] Dependencies explicit (S9-2 → S9-1; all share the runbook, serialized by commit-per-story)
- [x] Each story completable by one owner in ≤ ~1.5 days
- [x] No live-prod dependency — all artefacts testable in dev/INT

## Definition of Done

- [ ] `pytest tests/unit/` passes (no regression vs the known pre-existing 28 env-bound failures); `tests/unit/test_no_shell_true.py` still passes
- [ ] New integration tests pass: rate-limit 429 (S9-3), token revocation (S9-4), `/mcp/health` 200/503 (S9-2)
- [ ] **Documentation updated** per backlog standard:
  - `docs/PRODUCTION_DEPLOYMENT.md` — created in S9-1, each later story appends its operational section
  - `docs/MCP_SERVER.md` for any MCP surface change (health endpoint, revoke endpoint, rate-limit headers)
  - `docs/USAGE_AND_OPERATIONS_GUIDE.md` for the new `valdo mcp-revoke` CLI
  - One-line `CHANGELOG.md` entry per story under `[Unreleased]`
  - `docs/sphinx/modules.rst` if a new public module lands (`rate_limit.py`); `cd docs/sphinx && make html` succeeds
- [ ] Each story = one conventional commit on `valdo-version-v4` referencing `(S9-<m>, #<issue>)`
- [ ] **For ADR 0021:** status `Accepted` (it makes the queue-option call) with reasoning + dependency cost
- [ ] Kickoff doc lands as the **final commit**; push to `origin/valdo-version-v4`

---

## Sprint risks

| Risk | L | I | Mitigation |
|---|---|---|---|
| 5 stories at the cap with two borderline-M items overruns the sprint | M | M | #391 is deliberately ADR + skeleton only (full worker = follow-up M). #389 is the watch item — if grace-window + migration + CLI + cache exceeds a clean S, split the CLI (`valdo mcp-revoke`) into a fast-follow and keep the endpoint + table + auth-path check as the S core. |
| #389 token-format change (`jti`) breaks existing EF-S7 tokens | M | H | 24-hour grace window in the AC — tokens minted without `jti` still validate during grace. Integration test must cover both an old (no-jti) and new token. |
| `/mcp/health` (#390) must work **without auth** but sits behind the same router as authed MCP routes | M | M | Register the health route explicitly outside the token-auth dependency; integration test asserts a no-token 200. Keep it <100ms — no DB round-trip, exercise in-process session manager only. |
| Shared-file contention (`PRODUCTION_DEPLOYMENT.md`, `CHANGELOG.md`, nginx conf) | M | M | Strictly sequential execution, commit-per-story, clean tree between agents. No parallel agents this sprint. |
| #391 Option B/C would add Redis as a hard dep, against the project's minimal-dep direction | L | M | ADR steers to **Option A** (run-history table already exists — `src/mcp/run_registry.py` + Alembic `0004`). Redis stays an optional pluggable backend, never required to boot. |
| Can't truly validate TLS/nginx without the bank's PKI | M | L | S9-1 ships a self-signed-cert validation path in the runbook for INT; the real-cert step is documented as an SRE/PKI handoff, not a code dependency. |

---

## Out of scope — do not pull in

- **Full background-worker implementation** (#391 beyond ADR + skeleton) — follow-up M, filed if ADR picks Option B/C; with Option A the worker (`valdo run-job-worker`) is the fast-follow
- **Redis as a required dependency** — stays optional/pluggable per ADR 0021
- **Real bank PKI cert issuance** — SRE/PKI handoff documented in the runbook, not built here
- **JSON/XML parser implementation** (#395, #396) — engine-breadth backlog, separate sprint
- **DB-to-DB comparator** — filed only on a concrete BA request per ADR 0020
- **Multi-node/distributed rate-limit state** — process-memory (+ optional Redis) is enough for the INT pilot; distributed coordination is a scale-out follow-up

---

## Roles

- **Sprint owner / PM:** you (self-paced)
- **Dev (S9-1..S9-4, S9-5 skeleton):** delegated to `senior-fullstack-fintech-dev` agent
- **Architect (ADR 0021):** delegated to `principal-enterprise-architect` agent
- **Reviewer:** you + Claude Code suggestions on every commit
- **Demo audience:** internal SRE/security
- **Environment:** local dev + INT region (no live prod)

---

## After Sprint 9

| Sprint | Focus | Issues |
|---|---|---|
| **10** | Background-worker full impl (ADR 0021 → Option A; poll loop + reaper + flip async default) | [#397](https://github.com/buddy-k23/valdo/issues/397) |
| **(unscheduled)** | JSON parser implementation | #395 |
| **(unscheduled)** | XML parser implementation | #396 |
| **(conditional)** | DB-to-DB comparator | filed on concrete BA request, per ADR 0020 |
