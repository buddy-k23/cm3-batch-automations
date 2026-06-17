# Sprint 17 — Kickoff (Multi-worker & Quality)

**Sprint goal:** Make the MCP security controls correct under multiple gunicorn workers (so the single-worker pin from S14-1 can be lifted), bound the queue-claim scan, and raise unit coverage to ≥80% so the CI gate enforces it. Second of four sprints driving the backlog to "100% functional."

**Duration:** 1–2 weeks · **Capacity:** ~5 points (4 × S) · **Demo:** internal.

**Context:** The architecture review found the MCP rate-limiter and revocation cache are per-process in-memory — under N workers the caps are N× loose and a revoked token lingers a TTL per worker. The seams (Protocol/store) already exist; this sprint ships the shared backends (Redis **optional**, in-memory stays default). Plus the unbounded `claim_next` scan and the ~72% coverage that keeps the CI coverage gate disabled (#432).

---

## Stories

| ID | GitHub | Title | Size | Track |
|---|---|---|---|---|
| **S17-1** | [#419](https://github.com/buddy-k23/valdo/issues/419) | Shared (Redis) RateLimitBackend — caps hold across N workers | S | Multi-worker |
| **S17-2** | [#420](https://github.com/buddy-k23/valdo/issues/420) | Cross-worker revocation propagation + key limiter on `jti` | S | Multi-worker |
| **S17-3** | [#430](https://github.com/buddy-k23/valdo/issues/430) | Bound `claim_next`/`reap_stuck` (FETCH FIRST 1 / SKIP LOCKED) | S | Scale |
| **S17-4** | [#432](https://github.com/buddy-k23/valdo/issues/432) | Raise unit coverage to ≥80% + re-enable the CI coverage gate | S* | Quality |

**Total: ~5 pts.** `*` #432 is iterative (coverage-raising) — scoped to reach the gate, not gold-plate.

---

## Sequencing — sequential, commit-per-story

| Day | Story | Notes |
|---|---|---|
| 1–2 | **S17-1** (#419) | Redis-backed `hit()` behind the existing `RateLimitBackend` Protocol; env-selected (`VALDO_MCP_RATE_LIMIT_BACKEND=redis` + URL); in-memory default. Test with fakeredis (or skip-if-unavailable + a backend unit test). |
| 3 | **S17-2** (#420) | Add `jti` to `MCPPrincipal`; key the per-token limiter on `jti`; propagate revocations across workers via a shared store (reuse the Redis seam or the `MCP_REVOKED_TOKENS` table with a short shared cache). In-memory default preserved. |
| 4 | **S17-3** (#430) | `claim_next` selects ONE candidate (`FETCH FIRST 1 ROW ONLY`; `SKIP LOCKED` where the dialect supports it) instead of scanning all queued; bound `reap_stuck`. Both backends; keep the multi-worker no-double-claim test green. |
| 5–7 | **S17-4** (#432) | Raise `tests/unit/` coverage to ≥80% under the CI `--cov=src` scope by adding tests to the largest gaps (use `--cov-report=term-missing` to target); reconcile the cov scope (CI `--cov=src` vs pytest.ini's narrower set); remove `--cov-fail-under=0` from `.github/workflows/unit-tests.yml` so coverage is enforced. |
| 8 | Buffer / review | Kickoff final commit; push; close. After this, the S14-1 single-worker pin can be revisited (note it, don't necessarily flip the default). |

---

## Definition of Done

- [ ] **S17-1:** Redis `RateLimitBackend` behind the Protocol; env-selected; in-memory remains default; a test proves caps hold across simulated multi-worker state with the shared backend
- [ ] **S17-2:** `jti` on `MCPPrincipal`; per-token bucket keyed on `jti`; a revoked token is rejected across workers within seconds (shared store); in-memory default preserved
- [ ] **S17-3:** `claim_next` pulls one candidate (bounded); `reap_stuck` bounded; multi-worker no-double-claim test still green
- [ ] **S17-4:** unit coverage ≥80% under CI `--cov=src`; CI workflow enforces the gate (no `--cov-fail-under=0`); local `pytest.ini` gate consistent
- [ ] `pytest tests/unit/` **green (0 failed)**; no new REQUIRED deps (Redis/fakeredis optional/test-only); no secrets committed
- [ ] Each story = one conventional commit `(S17-<m>, #<issue>)`; #419/#420/#430/#432 closed
- [ ] Kickoff lands as final commit; push

---

## Risks

| Risk | Mitigation |
|---|---|
| Adding Redis as a hard dep against the minimal-dep ethos | Redis stays OPTIONAL behind the Protocol/env; in-memory is the default and the only required path. fakeredis is a test-only dep (or skip-if-unavailable). |
| Coverage-raising becomes a time sink / writes low-value tests | Target the biggest `term-missing` gaps with meaningful behavior tests; reconcile the `--cov` scope (CI's `--cov=src` is wider than pytest.ini) — aligning scope may close part of the gap. Stop at the gate, don't gold-plate. |
| `SKIP LOCKED` isn't supported on all dialects (SQLite) | Use `FETCH FIRST 1 ROW ONLY` universally + `SKIP LOCKED` only where supported (Oracle/PG); the guarded-UPDATE compare-and-swap remains the correctness backstop on SQLite. |
| Revocation propagation adds latency to the auth hot path | Shared lookup must be cached with a short TTL; the signature gate still runs first; fail-soft on the shared store being unreachable (degrade to local, log). |
| Shared-file contention (auth.py, rate_limit.py, run_registry.py, CHANGELOG) | Strictly sequential, commit-per-story. |

## Out of scope
Structural cleanups (#427–#431 less #430), set-based comparator (#423), JSON/XML (#395/#396) — Sprints 18–19. Flipping the gunicorn worker default to >1 — note it as enabled-but-not-defaulted.

## Roles
Dev: `senior-fullstack-fintech-dev` per story. Owner/PM: you.
