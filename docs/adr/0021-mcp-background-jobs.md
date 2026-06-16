# ADR 0021 — MCP background jobs: decouple validate_file from the request path

- Status: Accepted
- Date: 2026-06-16
- Sprint: 9 (S9-5, [#391](https://github.com/buddy-k23/valdo/issues/391))
- Decision: **Option A — the existing run-registry table is the job queue; a polling `valdo run-job-worker` process executes runs out-of-band. Zero new runtime dependencies.**
- Related:
  [ADR 0010](0010-truthsource-backend-abstraction.md),
  [ADR 0018](0018-json-parser-design.md),
  [ADR 0020](0020-db-to-db-disposition.md),
  `src/mcp/action_tools.py` (`validate_file_payload`, `_run_validate_synchronously`, `get_run_status_payload`),
  `src/mcp/run_registry.py` (`RunRegistry`, `DatabaseRunRegistry`, `make_run_registry`),
  `alembic/versions/0004_app_mcp_run_registry.py` (`APP_MCP_RUN_REGISTRY`),
  `src/services/validate_service.py` (`run_validate_service`),
  `src/database/engine.py` (shared SQLAlchemy engine + pool),
  `src/commands/` (CLI command-handler convention),
  `docs/PRODUCTION_DEPLOYMENT.md` (S9-1 runbook — the systemd-unit home for the worker),
  `docs/sprints/SPRINT_9_KICKOFF.md` (S9-5, sprint risk + out-of-scope steer).

## Context

`validate_file` (the `validate_file_payload` MCP tool in `src/mcp/action_tools.py`)
runs `run_validate_service()` **synchronously inside the MCP request**. The flow
today is: mint a `run_id`, write `status=queued` to the run registry, then
immediately call `_run_validate_synchronously` — which flips the record to
`running`, blocks on the engine call, and writes the terminal `completed` /
`failed` record before the tool returns (`action_tools.py:509-525`,
`:407-455`). The agent receives the `run_id` only after the whole validation
has finished.

For a small file this is sub-second and harmless — it is why EF-S4 shipped it
as a deliberate carve-out (the module docstring at `action_tools.py:20-32` and
the `VALIDATE_FILE_DESCRIPTION` text both flag "EF-S5 will move it to a
background worker"). For a 10M-row file it is a **30–60s tie-up of one
gunicorn worker per validation**. With four workers and four concurrent
validations the `/mcp/` endpoint is fully blocked: no `initialize`, no
`get_run_status` poll, no `list_sources` — nothing dispatches until a
validation frees a worker. This directly undermines the Sprint 9 goal of a
production-deployable MCP server behind the bank edge, and it makes the
`get_run_status` polling contract a lie (there is nothing to poll; the run is
already terminal by the time the agent has the id).

The fix is to make `validate_file` **enqueue and return**, and move the engine
call to a separate process. The substrate for this already exists. S6-1 (#386)
replaced the EF-S4 in-process dict with a persistent run registry
(`APP_MCP_RUN_REGISTRY`, Alembic `0004`) whose schema already carries:
`run_id` (PK), `source`, `file_path`, `status`, `started_at`, `finished_at`,
`violation_count`, `payload` (JSON CLOB), `created_ts`. The `status` column is
already documented and indexed for exactly the four lifecycle states this story
needs — `queued` / `running` / `completed` / `failed` (`0004:18-22`,
`run_registry.py:97`) — and there is already a composite index
`IDX_MCP_RUN_INFLIGHT (source, file_path, status)` plus a `status` index
`IDX_MCP_RUN_STATUS`. **The registry was built as a state machine that already
durably represents a queued-but-not-yet-run job.** Nothing about the table
needs to change for it to be a queue; it already is one, minus the consumer.

This is the planned hardening EF-S4 carved out, scoped by the kickoff as
**ADR + thin skeleton this sprint, full worker as a fast-follow**.

## Options considered

### Option A — run-registry table as the queue, polling worker (no new deps)

`validate_file` writes `status=queued` and returns the `run_id` immediately
(it already does the write; the change is to *stop* calling
`_run_validate_synchronously` inline). A separate long-lived process,
`valdo run-job-worker`, polls `APP_MCP_RUN_REGISTRY` for the oldest `queued`
row, **atomically claims it** (`queued` → `running` via a guarded
`UPDATE ... WHERE status='queued'`), runs `run_validate_service`, and writes
the terminal record — i.e. it performs exactly the body of the existing
`_run_validate_synchronously`, just out-of-band. `get_run_status` is unchanged:
it already reads the record's `status` straight from the registry and reports
`queued` / `running` / `completed` / `failed`.

**Pros:**
- **Zero new runtime dependencies.** Uses the SQLAlchemy engine + pool that
  already power the registry, run-history, baselines, and reconciliation
  (`src/database/engine.py`). No broker to provision, secure, patch, or
  monitor inside the bank edge.
- **Cross-dialect for free.** Works on Oracle / PostgreSQL / SQLite exactly
  like the registry does today (`run_registry.py:26-27`), so dev (SQLite) and
  INT/prod (Oracle) use one code path. No "Redis in dev but not really" split.
- **Restart-pickup is inherent.** A `queued` row is durable. If the worker (or
  the whole host) restarts, the row is still `queued` and the next poll picks
  it up. No work is lost on deploy/rotation — the exact durability property
  S6-1 was built to provide.
- **Smallest diff.** The enqueue path already writes `queued`; the worker loop
  is the existing `_run_validate_synchronously` body lifted into a process. The
  agent-visible contract (`run_id` + poll) does not change at all.
- **Aligns with the house ethos.** ADR 0020 explicitly killed ~4.7k LOC rather
  than absorb a parallel stack and refused to take on a `jsonschema` dep that
  pydantic already covered. Adding a broker for a single producer/consumer of
  long-running jobs is the same speculative complexity that ADR established we
  reject.

**Cons / costs:**
- **Polling latency + DB load.** A poll loop adds a small steady query load
  (one indexed `SELECT ... WHERE status='queued' ORDER BY started_at` per
  interval) and a pickup latency bounded by the poll interval (target ~1–2s,
  negligible against 30–60s validations). The `IDX_MCP_RUN_STATUS` index
  already exists to keep this cheap.
- **No native at-least-once/retry/visibility-timeout semantics** — we
  hand-roll the claim and a stuck-`running` reaper. This is real work, but
  small and well-understood (see Skeleton scope), and we control the
  semantics rather than bending a broker's to our needs.
- **Concurrency is DB-row-claim, not broker fan-out.** Multiple workers are
  supported via the atomic claim, but throughput scaling is "add worker
  processes," not "broker prefetch tuning." For the INT pilot's expected
  volume (a handful of BA-triggered validations) this is more than enough.
- **The current `put()` is a full upsert, not a guarded claim.** The worker
  needs a new `claim_next()` primitive (guarded `UPDATE`) the registry does
  not expose today — a deliberate, named addition (Skeleton scope), not a
  hidden cost.

**Dependency cost:** **none.** No new entry in `pyproject.toml`. No new
process-supervision tech beyond the systemd unit the S9-1 runbook already
establishes for the gunicorn service.

### Option B — Dramatiq + Redis

`validate_file` becomes a Dramatiq actor send; a Dramatiq worker process
consumes from a Redis broker and runs the validation. Dramatiq gives
battle-tested at-least-once delivery, automatic retries with backoff, a
results backend, and a clean actor API.

**Pros:**
- Mature, proven background-job semantics out of the box (retry, dead-letter,
  rate-limit middleware, prometheus middleware).
- Lower bespoke-code surface than Option A's hand-rolled claim/reaper.
- Horizontal worker scaling and prefetch tuning are first-class.

**Cons / costs:**
- **Adds Redis as a runtime dependency.** CLAUDE.md (the Fabric workspace
  parent) and the Valdo posture treat Redis as **optional / pluggable, disabled
  by default** — it is *not* currently required to boot anything in Valdo.
  Making the core `validate_file` path depend on a live Redis broker promotes
  it to a hard, always-on dependency inside the bank edge: a new network
  service to provision, TLS/auth-harden, patch, back up, monitor, and threat-
  model. That is a security and SRE cost the INT pilot does not need to pay.
- **Two new Python deps** (`dramatiq`, `redis`) plus their transitive surface,
  against a project that just (ADR 0020) refused a single `jsonschema` dep.
- **Dev/prod parity erodes.** Dev currently runs on SQLite with no broker.
  Option B forces either Redis-in-dev (a new local-dev burden) or a
  divergent "sync in dev, queued in prod" code path — the kind of split that
  hides bugs until prod.
- **Durability across restart still needs Redis persistence configured**
  (AOF/RDB) and operated correctly — i.e. we take on broker durability
  engineering to replace a durability property the DB row already gives us.

**Dependency cost:** Redis service (new infra, new attack surface, new SRE
runbook) + `dramatiq` + `redis` Python packages + their transitives.

### Option C — Celery + Redis (or RabbitMQ)

The heaviest option: Celery as the task framework, Redis or RabbitMQ as broker,
optionally a separate results backend.

**Pros:**
- The most feature-rich: scheduling (beat), chords/groups, mature ecosystem,
  extensive monitoring (Flower).
- Industry-standard for large multi-queue, multi-worker fan-out.

**Cons / costs:**
- **Largest install + operational surface of the three.** Celery's dependency
  tree and configuration surface are substantial; it is engineered for a scale
  and topology (many queues, many task types, scheduled beat jobs) Valdo's MCP
  layer does not have — there is exactly **one** long-running task type today.
- Same Redis/RabbitMQ hard-dependency cost as Option B, amplified (RabbitMQ is
  an even heavier broker to operate in a bank environment).
- Highest mismatch with the kickoff's "5 points at the cap, #391 is
  deliberately scoped down" reality — Celery is a multi-sprint integration,
  not a thin skeleton.

**Dependency cost:** Redis **or** RabbitMQ service + `celery` (+ optionally
`flower`, a results backend) + transitives. The biggest of the three.

### Dependency-cost comparison

| | New Python deps | New runtime service | Dev/prod parity | Restart-pickup | Bespoke code | Bank-edge attack surface |
|---|---|---|---|---|---|---|
| **A — registry queue + poller** | **0** | **0** (reuses DB) | **same path everywhere** | **inherent** (durable row) | claim + reaper + worker loop (small) | **none added** |
| **B — Dramatiq + Redis** | 2 (`dramatiq`, `redis`) | Redis (hard dep) | sync-vs-queued or Redis-in-dev | needs Redis persistence configured | minimal | Redis broker |
| **C — Celery + Redis/RabbitMQ** | 1–3 (`celery`, opt. `flower`, backend) | Redis or RabbitMQ (hard dep) | same split as B, heavier | needs broker persistence | minimal | broker (Redis/RabbitMQ) |

## Decision

**Picked: Option A — the run-registry table is the job queue; a polling
`valdo run-job-worker` process is the consumer. No new dependencies.**

Reasoning, in priority order:

1. **The substrate already exists and already persists the exact state we
   need.** `APP_MCP_RUN_REGISTRY` (Alembic `0004`) was built by S6-1/#386 with
   a `status` column whose documented domain is precisely
   `queued`/`running`/`completed`/`failed`, indexed for status lookups, with
   `payload` carrying the full run record across restarts. `validate_file`
   *already writes* `status=queued` (`action_tools.py:515`). We are not
   building a queue — we are pointing a consumer at the queue we already have
   and deleting the inline `_run_validate_synchronously` call. The
   `get_run_status` contract needs **no change**: it reads `status` from the
   registry today (`action_tools.py:586-600`).

2. **Minimal-dependency is the project's established constitution, not a
   preference.** ADR 0020 killed a 4.7k-LOC prototype rather than absorb a
   parallel stack, and declined a `jsonschema` dependency because pydantic
   already covered it. CLAUDE.md treats Redis as optional/pluggable and
   disabled by default. Promoting Redis to a hard runtime dependency of the
   core `validate_file` path — for a single producer/single-task-type
   workload — is exactly the speculative complexity those decisions reject. The
   Sprint 9 risk table makes the same call explicitly (Option B/C "would add
   Redis as a hard dep, against the project's minimal-dep direction").

3. **Restart-pickup — the headline requirement — is free with Option A and
   *additional engineering* with B/C.** A `queued` row is durable in Oracle
   (and SQLite/PG). Worker restart, host restart, or deploy rotation leaves the
   row `queued`; the next poll claims it. With B/C we would be configuring and
   operating broker persistence (Redis AOF/RDB) to *recreate* a durability
   property the relational row already guarantees — paying to replace something
   we already own.

4. **Dev/prod parity stays intact.** One code path runs on SQLite in dev and
   Oracle in INT/prod, mirroring the registry's existing cross-dialect design.
   B/C force either Redis-in-dev or a sync-in-dev/queued-in-prod split — the
   classic source of "works on my machine" prod bugs.

5. **It fits the sprint.** With Option A the skeleton is *most* of the feature
   (the enqueue side is a deletion + return; the worker is a lifted function),
   leaving only the worker loop + claim/reaper hardening as a clean fast-follow
   — exactly what the kickoff scoped (#391 = "ADR + skeleton"; full worker = a
   follow-up M, and "with Option A the worker is the fast-follow").

The honest cost we accept: we hand-roll the atomic claim and a stuck-`running`
reaper rather than inherit a broker's retry/visibility-timeout machinery. This
is small, well-understood SQL, and it keeps the semantics under our control and
the bank-edge attack surface unchanged. Redis remains available as an
**optional, pluggable** backend for a future scale-out (a different
`RunRegistry`-style adapter could front a broker) — it is explicitly **not**
foreclosed, just **not required to boot**, per the kickoff's out-of-scope line.

Status `Accepted`.

## Skeleton scope — what S9-5 ships this sprint

S9-5 delivers the ADR (this document) plus a **thin, reviewable skeleton** that
establishes the seam without the production-grade worker loop. Concretely:

1. **Decouple the enqueue path (`src/mcp/action_tools.py`).**
   `validate_file_payload` stops calling `_run_validate_synchronously` inline.
   After writing the `queued` record it returns `{run_id, started_at}`
   immediately — the enqueue write already happens at `action_tools.py:518`, so
   this is removing the inline drive call and letting the worker pick the row
   up. **AC: `validate_file` returns within 100ms** (it does no engine work — it
   resolves artefacts, does the idempotency lookup, writes one row, returns).
   The idempotency guard (`_existing_run` → `find_inflight`) is unchanged and
   now does double duty as "don't enqueue a duplicate."
   - **Feature gate for safe rollout:** behind an env flag
     (`VALDO_MCP_ASYNC_VALIDATE`, default on once the worker exists; off keeps
     the legacy synchronous behaviour) so the synchronous path remains a
     fallback if no worker is deployed in a given environment. The skeleton
     ships the flag and both branches; the worker that makes the async branch
     useful is the fast-follow below.

2. **A new `claim_next()` primitive on the registry adapter
   (`src/mcp/run_registry.py`).** The current `put()` is a full upsert and
   cannot be used to atomically claim a job (two workers would both "win"). Add
   a narrow method to the `RunRegistry` protocol and both backends that
   performs a guarded transition:
   `UPDATE ... SET status='running', started_at=... WHERE run_id IN (oldest queued) AND status='queued'`,
   returning the claimed `RunRecord` or `None`. Cross-dialect (the existing
   `_qualified`/`text()` style), one transaction, relying on row-level locking
   so concurrent workers cannot double-claim. This is the one genuinely new bit
   of registry code; it is small and unit-testable against SQLite.

3. **The `valdo run-job-worker` CLI command shell
   (`src/commands/run_job_worker.py`, registered thin in `src/main.py`).**
   Per CLAUDE.md architecture principle #1 (logic lives in `src/commands/` /
   `src/services/`, never inline in `main.py`). The skeleton ships the command
   with: argument surface (`--poll-interval`, `--once` for a single
   drain-and-exit used by tests/CI, `--max-runs` bound), a **single-iteration**
   `claim_next` → run-the-existing-`_run_validate_synchronously`-body →
   write-terminal cycle, and a clean `--once` exit. The continuous loop with
   signal handling is stubbed/minimal here and hardened in the fast-follow.

4. **Graceful-shutdown + restart-pickup semantics (defined here, minimally
   wired in the skeleton):**
   - **Shutdown:** the worker traps `SIGTERM`/`SIGINT`, finishes the run it is
     currently executing (or, if interrupted mid-run, the row is left
     `running` and the reaper recovers it — see below), and exits without
     claiming new work. systemd `TimeoutStopSec` must exceed the worst-case
     single-file validation time; the runbook documents this.
   - **Restart-pickup:** `queued` rows are durable, so a restarted worker
     resumes draining the backlog automatically. **No work is lost on deploy or
     rotation** — the property S6-1 was built for.
   - **Stuck-`running` reaper:** a run claimed by a worker that died mid-flight
     is left `running` forever. The skeleton *defines* the reaper contract — a
     row in `running` with `started_at` older than a configurable threshold
     (e.g. 2× the max expected validation time) is reset to `queued` (or
     `failed` after N attempts). The reaper *implementation* is part of the
     fast-follow; the skeleton documents it and leaves a single hook.

5. **Tests:** unit coverage for `claim_next` atomicity (two simulated claimers,
   one winner) against SQLite; a `--once` worker integration test that enqueues
   a record via `validate_file_payload`, drains it with the worker command, and
   asserts the record reaches `completed`; a test asserting `validate_file`
   returns in `queued` state (does not execute inline) when the async flag is
   on. Per CLAUDE.md, run the full `tests/unit/` for the coverage gate.

6. **Docs:** a "Background validation worker" section appended to
   `docs/PRODUCTION_DEPLOYMENT.md` (the S9-1 runbook) describing the
   `valdo run-job-worker` **systemd unit** (separate from the gunicorn unit),
   the `--poll-interval` / `TimeoutStopSec` guidance, and the restart-pickup
   guarantee; `docs/MCP_SERVER.md` updated to state `validate_file` is now
   asynchronous (the `VALIDATE_FILE_DESCRIPTION` string in `action_tools.py`
   updated to drop "runs synchronously inside this call today"); a one-line
   `CHANGELOG.md` entry; `src/commands/run_job_worker.py` registered in
   `docs/sphinx/modules.rst` if it exposes a public module.

**Explicitly NOT in the S9-5 skeleton (it is the fast-follow):** the
production-grade continuous poll loop with backoff and metrics, the
stuck-`running` reaper implementation, multi-worker concurrency hardening
beyond the `claim_next` atomicity test, and per-run retry/attempt-count
columns if the reaper needs them.

## Consequences

### Positive

- **The MCP endpoint stops blocking.** `validate_file` becomes a sub-100ms
  enqueue; gunicorn workers are never tied up by a 10M-row validation. The
  Sprint 9 goal — a server deployable behind the bank edge that "no longer
  ties up a worker" — is met.
- **The `get_run_status` polling contract becomes real.** Agents now observe
  genuine `queued` → `running` → terminal transitions instead of receiving a
  `run_id` that is already terminal. EF-S4's documented intent is fulfilled.
- **Zero new dependencies, zero new runtime services, zero new bank-edge attack
  surface.** Nothing extra to provision, harden, patch, or monitor inside the
  edge. Consistent with ADR 0020's minimal-dependency posture.
- **Durable across restart by construction** — `queued`/`running` rows survive
  worker and host restarts; deploy rotation loses no work.
- **One code path dev → prod** (SQLite → Oracle), preserving parity and the
  cross-dialect design S6-1 already validated.
- **Smallest possible diff to a security-sensitive path** — the enqueue side is
  largely a *deletion* (the inline drive call) plus a feature flag; the worker
  reuses the already-reviewed `_run_validate_synchronously` body verbatim.

### Negative / risks

- **Risk: poll latency / DB load at higher volume.** Mitigation: the
  `IDX_MCP_RUN_STATUS` index already exists; the poll is a single bounded
  indexed query at a ~1–2s interval — negligible against 30–60s validations and
  the INT pilot's low concurrency. If volume ever justifies it, a broker-backed
  `RunRegistry` adapter is the documented scale-out (Redis stays pluggable).
- **Risk: a worker dies mid-run, leaving a row stuck in `running`.**
  Mitigation: the stuck-`running` reaper contract is defined in this ADR and
  scoped into the fast-follow; the skeleton leaves the hook and the runbook
  notes the manual reset until the reaper lands.
- **Risk: hand-rolled `claim_next` has a concurrency bug (double-claim).**
  Mitigation: the guarded `UPDATE ... WHERE status='queued'` relies on
  row-level locking; the skeleton ships a two-claimer atomicity unit test as an
  AC. Keeping multi-worker deployment out of scope until that test is green
  bounds the blast radius.
- **Risk: an environment runs the MCP server with the async flag on but no
  worker deployed — validations would sit `queued` forever.** Mitigation: the
  `VALDO_MCP_ASYNC_VALIDATE` flag defaults to the synchronous path until a
  worker is present; the runbook makes "deploy the `run-job-worker` systemd
  unit *before* enabling async" an explicit step, and `/mcp/health` (S9-2) can
  be extended in a later story to report worker liveness.
- **Risk: no native retry/dead-letter semantics (vs Option B/C).** Mitigation:
  accepted deliberately — the workload is a single long task type, not a
  fan-out; the reaper covers the only failure mode (dead worker) that matters
  for the pilot. Retry/attempt-count columns are a documented fast-follow
  addition if operational data shows they are needed.

### Follow-ups

- **Worker full implementation (M, to be filed — not filed by this story):**
  title `feat(mcp): valdo run-job-worker — continuous poll loop + claim + stuck-run reaper (follow-up to ADR 0021)`.
  Scope: the production continuous poll loop with backoff + structured
  logging/metrics; the stuck-`running` reaper implementation (threshold-based
  reset, attempt-count column via a new Alembic migration if needed);
  multi-worker concurrency hardening; systemd unit file shipped in
  `packaging/` alongside the gunicorn unit; flipping `VALDO_MCP_ASYNC_VALIDATE`
  default to async once the worker is the supported path. Starting estimate
  anchors on the existing `_run_validate_synchronously` body and the
  `claim_next` primitive this story ships. **Sprint 10 candidate** per the
  kickoff's "After Sprint 9" table.
- **Optional broker-backed adapter (deferred, conditional):** if INT/prod
  volume ever outgrows the polling model, add a broker-backed `RunRegistry`-style
  consumer behind the same enqueue contract. **Do not file speculatively** —
  wait for concrete throughput evidence, per the ADR 0020 discipline. Redis
  stays optional/pluggable; this ADR does not foreclose it.
- **`/mcp/health` worker-liveness probe (S, conditional):** extend the S9-2
  health endpoint to surface whether a worker has claimed/heartbeated recently,
  so an LB / SRE can detect "MCP up but no worker draining the queue." File only
  if the pilot shows the failure mode is real.
