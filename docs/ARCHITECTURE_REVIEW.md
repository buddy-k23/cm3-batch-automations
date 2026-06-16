# Valdo — Architecture Review

- **Date:** 2026-06-16
- **Branch reviewed:** `valdo-version-v4`
- **Method:** Five parallel read-only deep-dives (overall structure & boundaries; data/persistence; MCP server; engine/parsers/config; frontend & cross-cutting), synthesized.
- **Scope:** structure & layering, DB/persistence, MCP subsystem, validation/parsers/config, frontend, and cross-cutting security/testing/deploy/observability.

> Tracking issues created from this review are prefixed `[P1]`/`[P2]`/`[P3]` and reference this document. ADR 0022 follow-ups (extract/db-compare onto the adapter) are tracked separately as #405/#406.

---

## Verdict

Valdo is a **well-architected system with disciplined bones and one recurring failure mode**. The intended design — CLI/API → commands/routers → services → parsers/validators/db/reporting — genuinely holds at the lower layers: clean downward dependencies, real shared services, zero `shell=True`, a proper path-traversal guard, and a sound DB-adapter abstraction. The ADRs are unusually honest about their own partial implementations.

The recurring problem is **"good seam, bypassed path"**: Valdo repeatedly designs the correct abstraction, then leaves the high-traffic code routed around it. That single pattern explains most of the serious findings. None of it is a redesign — it's *finishing the wiring* the architecture already implies. For a **banking/SOX** context, a few findings cross from tech-debt into control-gap and should lead the backlog.

---

## What's genuinely well-built

- **Layer separation is real.** No file in `services/`/`parsers/`/`validators/` imports upward into `api`/`commands`/`mcp` (one benign DTO exception). `reconcile_service` is consumed identically by CLI, REST, and the MCP tool — the single-implementation-point ADR 0001 demands.
- **DB adapter core is portable & correct.** `DatabaseAdapter` ABC + `CanonicalType` model; cross-dialect, idempotent, guarded Alembic migrations; parameterized SQLAlchemy Core in `run_history` and the MCP registry; `reconcile` is genuinely backend-agnostic post-ADR 0022.
- **Concurrency primitive is sound.** `claim_next`'s guarded `UPDATE ... WHERE status='queued'` + `rowcount==1` is a correct compare-and-swap; the heartbeat/reaper pair distinguishes a slow job from a dead worker.
- **MCP crypto & fail-soft.** HMAC + `compare_digest`, signature-before-DB ordering, `jti` bound into the signature only when present, 0600 token files; infra blips degrade rather than lock agents out.
- **Real accessibility & XSS hygiene in the UI**; clean parser contract (`__source_row__` line addressability, extension-first routing) ready for the JSON/XML ADRs.

---

## The defining theme: good seams, bypassed paths

| Designed seam | Bypassed by | Consequence |
|---|---|---|
| `get_database_adapter()` factory | `extract`, `db-compare`, `run-tests`, `system.py` still call `OracleConnection.from_env()` | `DB_ADAPTER=postgres/sqlite` silently breaks those features (ADR 0022 S13 / #405 fixes part) |
| `reconcile_service` | `reconcile-all` inlines ~190 lines in `main.py` incl. the only copy of the drift-diff | REST/MCP can't do reconcile-all-with-drift; parity hazard |
| `RateLimitBackend` / revocation cache Protocols | no shared backend ships; both per-process in-memory | under N gunicorn workers, rate caps are N× too loose; revocation lags a TTL per worker |
| `ConfigLoader` + `config/<env>.json` | read only in tests; prod reads env vars | editing `config/int.json` has zero runtime effect — operator trap |

Finishing each seam (wire it, delete the bypass) resolves a large fraction of this review.

---

## Risks ranked by severity

### HIGH — SOX/fintech-critical
1. **Audit log not tamper-evident** (`audit_logger.py:181-184`): plain append, no hash-chain/HMAC/sequence, write failures swallowed. API-key auth failures only `logger.warning` (never audited); config/mapping/rule mutations emit no audit event. → **[P1]**
2. **`VALDO_MCP_AUTH=dev` grants zero-credential `admin` and ships enabled in `.env.example:76`** (`mcp/auth.py:809-816`); sample signing keys are functional. → **[P1]**
3. **API-key comparison not constant-time** (`auth.py:80` dict lookup) while the MCP bearer path uses `compare_digest`. → **[P1]**
4. **SQL injection by construction in the extractor** (`extractor.py:42-48`, `:166-182`): f-string `SELECT/WHERE/ROWNUM`, fed by `valdo extract --table/--columns/--where`, reachable from `db_file_compare_service`. → **[P1]**

### HIGH — process & deploy
5. **Tests + 80% coverage gate never run in CI**; ~27 known unit failures undocumented (no xfail list). → **[P1]**
6. **Three incompatible deploy topologies**: Docker single-uvicorn no-nginx; RPM `valdo.service` runs `python -m src.main` (CLI, no `serve`) so nothing binds :8000; nginx assumes a gunicorn pool; Python 3.9 (RPM) vs 3.11 (Docker). → **[P1]**
7. **Multi-worker security controls are per-process** (MCP): in-memory rate limiter (effective cap N×) and revocation cache (revoked token accepted by N−1 workers for up to the TTL). Seams exist; Redis backend not implemented. → **[P2]**

### HIGH — correctness
8. **Chunked `sequential` ignores `start`/`step`** (`cross_row_validator.py:727`): non-default rules pass single-file but flag every group under `--use-chunked`. Root cause: the chunked cross-row map-reduce bypasses the `_DISPATCH` registry (three parallel if-chains). → **[P1]**
9. **Non-chunked delimited parser ignores `has_header`** (`pipe_delimited_parser.py:91` always `header=None`) while the chunked parser defaults `has_header=True` — different row counts/violations per path; spurious row-1 violation. → **[P1]**
10. **`main.py` is a 1020-line god-module** breaking its own "thin CLI" principle (`reconcile-all`/`extract`/`submit-task` inlined). → **[P2]**

### MED
- **Row-iteration comparator won't scale to 10M** (`file_comparator.py` `.iterrows()` on full frames); the chunked path does one SQLite SELECT per row, truncates diffs to 1000, returns empty `only_in_file1/2`. Set-based chunked JOIN is the post-DuckDB answer. → **[P2]**
- **Engine biases toward false "valid"/"healthy"**: unreadable multi-record → `valid:True`; sum checks silently drop uncoercible values; `/health` hardcodes `database_connected=False`; secrets fail open to empty strings. → **[P2]**
- **Dead duplicate config layer** (`config/<env>.json`); duplicated DB-config defaults; adapters bypass `SECRETS_PROVIDER`. → **[P2]**
- **`extract_to_file` does no delimiter/newline escaping** — a `|` in a value corrupts output and breaks downstream compares. → **[P2]**
- **Fat routers** (`files.py` 890, `onboarding.py` 1083) do response-shaping/connection-resolution/git orchestration; ~20 files exceed 500 lines (`validation_renderer.py` 1689, `enhanced_validator.py` 1178). → **[P3]**
- **`ui.js` is a 5772-line single-namespace monolith** (~11.5× the principle), 45 raw `fetch()` with no central `apiFetch()`/error layer, mixed `/api/v1`+`/api/v2`. → **[P3]**
- **Unbounded `claim_next`/`reap_stuck` queue scans** (no `FETCH FIRST 1`/`SKIP LOCKED`). → **[P3]**

### LOW / cleanup
- Dead Oracle-only `transaction.py` with f-string DDL — remove or quarantine.
- Workspace-level `/Users/pavankanduri/claude-ws/CLAUDE.md` describes a Java/Spring "Fabric Platform" unrelated to this Python repo — reconcile to stop misleading agents.

---

## Prioritized recommendations

1. **Close the SOX/security control gaps first** (#1–#4): tamper-evident audit log + coverage; auth defaults & constant-time compare; parameterize the extractor SQL. Small, high-leverage, domain-critical.
2. **Finish the "bypassed seam" wiring**: ADR 0022 S13 (#405/#406) + extractor parameterization; `reconcile_all_service`; Redis-backed rate-limit + revocation; delete-or-wire the dead config layer.
3. **Make the pipeline real**: pytest + 80% gate in CI with a tracked xfail list; fix the RPM `ExecStart`/gunicorn topology and Python-version parity.
4. **Fix the silent-divergence correctness bugs** (#8, #9) before JSON/XML land — both new formats route through the same dispatch and would inherit the defects. Convert the chunked cross-row map-reduce and the `rule_engine` operator chain to registry dispatch.
5. **Structural cleanups**: thin `main.py` + fat routers into services; break `ui.js` into ES modules behind one `apiFetch()`; set-based comparator for scale.

**Bottom line:** the architecture is sound and the discipline is visible. The debt is concentrated and coherent (one theme, not chaos), which makes it tractable. For the banking context, audit-log tamper-evidence and the auth defaults are do-now regardless of roadmap.
