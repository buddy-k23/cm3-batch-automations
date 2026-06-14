# Sprint 1 — Kickoff

**Sprint goal:** Strip noise from source YAMLs (Moves 1 + 2) and stand up the MCP scaffold so agents can read existing state.

**Duration:** 2 weeks
**Capacity:** 12 points (S=1, M=2, L=3)
**Demo:** End of Week 2

---

## Stories

| ID | Title | Size | Owner | Track |
|---|---|---|---|---|
| EA-S1 | Promote strict/tolerance/thresholds to Pydantic defaults | S | Dev A | Move |
| EA-S2 | Emit defaults as YAML comments in generated pipeline | M | Dev A | Move |
| EA-S3 | Strip explicit defaults from committed source YAMLs | S | Dev A | Move |
| EB-S1 | Infer `multi_record` from mapping extension | M | Dev A | Move |
| EB-S2 | Migrate existing sources off explicit `multi_record` flags | S | Dev A | Move |
| EF-S1 | MCP scaffold on FastAPI `/mcp` | M | Dev B | MCP |
| EF-S2 | Read-only tools (`list_sources`, `get_source_spec`, `list_recent_runs`) | M | Dev B | MCP |
| EF-S3 | Resources `taxonomy://violations` + `taxonomy://rules` | S | Dev B | MCP |

**Total: 12 pts** (Move track 7, MCP track 5)

---

## Daily sequencing

| Day | Move track (Dev A) | MCP track (Dev B) |
|---|---|---|
| 1–2 | EA-S1 — defaults in Pydantic | EF-S1 — scaffold MCP at `/mcp` |
| 3–4 | EB-S1 — multi_record inference | EF-S1 (finish), EF-S3 — taxonomy resources |
| 5–6 | EA-S2 — emit `# default:` comments | EF-S2 — read-only tools |
| 7–8 | EB-S2, EA-S3 — strip committed YAMLs | EF-S2 (finish) |
| 9 | Cross-track integration test | Cross-track integration test |
| 10 | Demo prep + buffer | Demo prep + buffer |

---

## Demo (end of sprint)

1. Show diff on `config/e2e/sources/SHAW.yml` — ~80 lines lighter, no semantic change.
2. Show generated pipeline YAML with `# default:` annotations for traceability.
3. `valdo serve` starts; `curl https://localhost:8443/mcp/initialize` returns capabilities advertising `tools`, `resources`, `prompts`.
4. From an MCP client (Claude Desktop or `mcp-cli`), call `list_sources` and `get_source_spec("SHAW")` — receive bundle of existing artefacts.
5. Fetch resource `taxonomy://violations` — receive live rule taxonomy.

---

## Definition of Ready (every story before sprint start)

- [ ] Acceptance criteria testable with a named test file + case
- [ ] Files touched listed; ≤4 paths
- [ ] ADR ID reserved if touching layer boundary or `src/` from harness motivation
- [ ] Dependencies explicit; transitive deps surfaced
- [ ] Completable by one dev in ≤3 days

## Definition of Done (every story before merge)

- [ ] `pytest tests/unit/` passes; coverage ≥80% (per `pytest.ini`)
- [ ] `tests/unit/test_no_shell_true.py` still passes (R-14 guard)
- [ ] `CHANGELOG.md` `[Unreleased]` updated
- [ ] If under AGENTS.md hard rule #1 carve-out, ledger entry added to `docs/AGENTS_CARVE_OUT_AUDIT.md`
- [ ] Conventional commit landed (`feat/fix/docs(scope): description`)

---

## Sprint risks

| Risk | L | I | Mitigation |
|---|---|---|---|
| `generate_pipeline_yaml.py --check` golden fixtures drift if other unrelated changes land mid-sprint | M | M | Land all EA stories together on Day 5–6; freeze pipeline YAML edits during that window |
| MCP Python SDK version incompatible with existing FastAPI version pin | M | M | One-hour spike on Day 1 of EF-S1; pin compatible version in `requirements-api.txt` |
| Pydantic default change breaks a downstream consumer outside the test set | L | M | Add `tests/integration/test_existing_sources.py` in EA-S1 covering every committed source YAML round-trip |

---

## Out of scope — do not pull into this sprint

- Workbook emitter (`valdo onboard-source`) — Sprint 2
- Reconciliation auto-derivation — Sprint 3
- UI wizard — Sprint 4
- MCP auth bridge (`valdo mcp-login`) — Sprint 4
- DuckDB engine swap — de-scoped from program
- New consolidated `SourceSpec` Pydantic schema — de-scoped from program
- Hygiene items (root script moves, file renames, `src/main.py` split) — de-scoped from program

If a PR introduces any of the above, send it back as scope creep.

---

## Roles

- **Move track owner:** Dev A
- **MCP track owner:** Dev B
- **Tech lead / reviewer:** Principal Architect
- **PO check-in:** Mid-sprint, Day 5
- **Stand-up:** 15 min daily, async OK if blockers are surfaced in writing
