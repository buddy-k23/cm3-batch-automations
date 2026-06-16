# Sprint 16 — Kickoff (Robustness & Completeness)

**Sprint goal:** Make the app behave correctly under bad input and edge cases — eliminate the "biases toward false valid/healthy" the architecture review found, fix the extract output-formatting issue the Postgres smoke surfaced, complete reconcile-all across all surfaces, kill the dead config-layer operator trap, and remove the chunked/single-pass divergence root cause. This is the first of four sprints driving the open backlog to "100% functional, no issues."

**Duration:** 1–2 weeks · **Capacity:** 5 points (5 × S) · **Demo:** internal.

**Context:** Per the user goal (2026-06-16) to make the app fully functional and immediately usable. These five are the highest functional-correctness items in the P2 backlog from `docs/ARCHITECTURE_REVIEW.md`. Multi-worker scaling (#419/#420), coverage (#432), structural cleanups (P3), and the JSON/XML features (#395/#396) follow in Sprints 17–19.

---

## Stories

| ID | GitHub | Title | Size | Track |
|---|---|---|---|---|
| **S16-1** | [#425](https://github.com/buddy-k23/valdo/issues/425) | Fix fail-open/false-valid biases (unreadable input, silent coercion, /health, secrets) | S | Correctness |
| **S16-2** | [#426](https://github.com/buddy-k23/valdo/issues/426) | `extract_to_file` delimiter/newline escaping (+ NUMERIC formatting) | S | Correctness |
| **S16-3** | [#421](https://github.com/buddy-k23/valdo/issues/421) | Extract `reconcile_all_service` (reconcile-all-with-drift via REST/MCP) | S | Completeness |
| **S16-4** | [#424](https://github.com/buddy-k23/valdo/issues/424) | Delete-or-wire dead `config/<env>.json` + thread `SECRETS_PROVIDER` through adapters | S | Correctness |
| **S16-5** | [#418](https://github.com/buddy-k23/valdo/issues/418) | Collapse chunked cross-row onto the `_DISPATCH` registry | S | Quality |

**Total: 5 pts (at cap).**

---

## Sequencing — sequential, commit-per-story

| Day | Story | Notes |
|---|---|---|
| 1–2 | **S16-1** (#425) | The functional core: unreadable multi-record → `valid:False`; cross_type sum/count don't silently drop uncoercible values; `/health` does a real DB check; secrets fail **closed**. Each with a test. |
| 3 | **S16-2** (#426) | Escape via the `csv` module in `extract_to_file` (3 adapters + extractor); a value with the delimiter/newline round-trips; reconcile the NUMERIC trailing-zero render diff from the PG smoke. |
| 4–5 | **S16-3** (#421) | Move reconcile-all + the baseline drift-diff out of `main.py` into `src/services/reconcile_all_service.py`; CLI delegates; behavior parity; (optionally surface via REST/MCP if cheap). |
| 6 | **S16-4** (#424) | Delete or wire `config/<env>.json` (it's read only in tests today); unify duplicated DB-config defaults; have adapters resolve via `SECRETS_PROVIDER` so Vault/Azure applies. |
| 7 | **S16-5** (#418) | Route chunked cross-row collect/merge/evaluate through the same registry as single-pass — one source of truth per check (prevents future drift like #413). |
| 8 | Buffer / review | Kickoff lands as final commit; push; close issues; checkpoint. |

---

## Definition of Done

- [ ] **S16-1:** unreadable input → `valid:False`/hard error; sum/count coercion failures surface (violation/typed warning affecting `valid`); `/health` reflects real DB connectivity; secrets provider fails closed — each tested
- [ ] **S16-2:** delimiter/newline-in-value round-trips through `extract_to_file` (all 3 adapters + extractor); NUMERIC formatting consistent; test proves a `|`/newline value survives
- [ ] **S16-3:** reconcile-all + drift live in a service; CLI delegates; behavior unchanged; (REST/MCP reuse if added)
- [ ] **S16-4:** no dead config layer (deleted or wired); one source of DB defaults; `SECRETS_PROVIDER` applies to adapter connections — tested
- [ ] **S16-5:** chunked cross-row dispatches via the registry; adding a check is one entry; parity tests stay green
- [ ] `pytest tests/unit/` **green (0 failed)** under CI conditions; parameterized SQL only; no new required deps; no secrets committed
- [ ] Each story = one conventional commit on `valdo-version-v4` `(S16-<m>, #<issue>)`; #425/#426/#421/#424/#418 closed
- [ ] Kickoff lands as the **final commit**; push

---

## Risks

| Risk | Mitigation |
|---|---|
| Making `/health` do a DB check slows the LB probe or fails when DB is down by design | Keep it a cheap bounded check; distinguish liveness (process up) from readiness (DB reachable) — consider `/health` (liveness) vs a readiness signal; don't make a transient DB blip flap the LB. Document the semantics. |
| Secrets failing closed breaks local dev where a provider isn't configured | `env` provider (default) returns configured values normally; "fail closed" applies to a configured Vault/Azure provider that errors — not to an unset optional var. Be precise; test both. |
| `reconcile_all_service` extraction changes reconcile-all output | Parity test: same inputs → identical summary + drift before/after. |
| Escaping changes break existing extract output consumers | Use standard `csv` quoting (minimal/quote-when-needed); test that simple values are unchanged and only delimiter/newline values get quoted. |
| Chunked registry collapse regresses a cross-row check | Keep all existing cross-row + chunked parity tests green; refactor behind them. |
| Shared-file contention (CHANGELOG, validators) | Strictly sequential, commit-per-story. |

---

## Out of scope
Multi-worker shared backends (#419/#420), coverage→80% (#432), P3 cleanups (#427–#431), JSON/XML (#395/#396) — Sprints 17–19.

## Roles
Dev: `senior-fullstack-fintech-dev` per story. Owner/PM: you. Env: local + SQLite (+ Docker if a check needs it).
