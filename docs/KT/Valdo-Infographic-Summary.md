# Valdo Infographic — Delivery Summary

Generated: 2026-05-25
Repository: `app/APPID-33091157/valdo` on `trgl.gitlab-dedicated.com`
Branch at build time: `feature/issue-11-kill-file-search`

## Deliverables

| File | Purpose |
|---|---|
| `docs/Valdo-Infographic.html` | Self-contained single-file interactive infographic (11 tabs, inline CSS + JS, no external dependencies, opens directly from disk). |
| `docs/KT/Valdo-TestCases.csv` | Companion test-case dataset, RFC&nbsp;4180-quoted and ASCII-safe (arrows replaced with `->`, `<=`, `!=`). |
| `docs/KT/Valdo-Infographic-Summary.md` | This document. |

## Tabs generated

All 11 suggested tabs were generated. None were skipped.

| # | Tab | Source material used |
|---|-----|----------------------|
| 1 | 🏠 Overview | `README.md`, `setup.py`, `requirements.txt`, `docs/architecture.md`, file counts from `src/` and `tests/`. |
| 2 | 🧩 Code Modules | Live `src/` tree (19 packages, 17 commands, 12 routers, 25 services). |
| 3 | 🏛️ Architecture | `docs/architecture.md` (mermaid diagrams), `src/api/main.py`, ADRs 0001 + 0005. |
| 4 | 🖥️ UI / Frontend | `src/reports/static/ui.html` (DOM IDs verbatim), `config/ui.yml`. |
| 5 | 🔐 Auth / Security | `src/api/auth.py`, `auth_ldap.py`, `security/url_validator.py`, `middleware/ip_whitelist.py`, `CHANGELOG.md` (#9 + #10 entries), `config/ui.yml > auth/security/tls`. |
| 6 | 🔌 Integrations / APIs | Endpoint paths pulled directly from `@router.<verb>(...)` decorators in all 12 router files; outbound integrations from `requirements.txt` + `docs/splunk-setup.md` + harness README. |
| 7 | 📦 Supported Sources | `config/mappings/*` inventory, `SHAW_ATOCTRAN.yaml` + `SHAW_TRANERT.yaml` (verbatim), `config/mappings/manifest_scenarios/`. |
| 8 | 🐞 Known Issues | In-repo issue references (#9–#13) from CHANGELOG + ADRs + `git log`; five `TODO(valdo-gap)` markers from the harness README; deferred items from ADR&nbsp;0005 + SHAW handover. |
| 9 | 🧪 Test Cases | 147 rows (101 Current + 46 Future) — Current rows map to real `tests/*/test_*.py` filenames; Future rows derived from gap analysis (missing mirror tests, ADR-0005 deferred items, `TODO(valdo-gap)` markers, undocumented behaviour). |
| 10 | 👥 Team Tasks | Inferred roles (no `CODEOWNERS` at HEAD), tied to ADRs and issue references. RACI matrix and Top-5 priorities. |
| 11 | 📚 Glossary | 66 terms — every one present in the repository or its configuration. Enterprise applications not referenced in the repo are intentionally absent. |

No tabs were omitted.

## Ambiguities resolved by assumption

Every assumption below is flagged with a purple **ASSUMPTION** pill in the HTML at its point of use. Reviewers can grep for `class="pill assumption"` to find them.

1. **Team display name** — chose **"Valdo"** for the brand bar to match the project name in `setup.py` and the canonical install path. Replace if your team has a different official name.

2. **"Supported Models"** — interpreted as **"Supported Sources"** (the source-system mappings under `config/mappings/`), since Valdo has no ML/AI models. The sidebar label and tab title reflect this. The tab covers SHAW ATOCTRAN, SHAW TRANERT, P327, EST-CDS, APP_INT, plus the three universal sample mappings.

3. **Source statuses** — `production` vs `pending` pills were inferred from whether the per-record-type mapping JSON exists in `config/mappings/`. The two `pending` items (TRANERT VR 32030 and CON 32070) are explicitly flagged with `TODO(mapping-pending)` in `SHAW_TRANERT.yaml`, so that distinction is real. Other sources are listed as `production` because their JSON is committed.

4. **Decision flow** — chose the **LDAPS login** sequence for the Architecture tab's decision-flow component (over the validation flow) because it is the most decision-tree-shaped flow in the architecture doc and exercises the gold/purple actor-chip styling. Validation flow is rendered as the snake-grid flow instead.

5. **Inline report panel reference** — the in-UI feature exists (`reportFrame`/`reportPanel` in `ui.html`) and there is a plan doc at `docs/plans/2026-03-04-inline-report-panel.md`, but the doc was not opened during discovery. Marked as ASSUMPTION on the UI tab.

6. **Read-only DB convention for DB Compare** — the repo gates the endpoint with X-API-Key + LDAP session but does **not** state explicitly that the configured Oracle account must be read-only. Surfaced as a policy assumption in the UI tab's RISK callout.

7. **Vault / Azure KV adapters** — documented in `config/ui.yml` and `requirements.txt` references, but the implementations behind `SECRETS_PROVIDER=vault` are stubs. Flagged as ASSUMPTION on the Integrations and Auth tabs.

8. **`DR-CR-AMT-BRT` reconciliation** — TRANERT umbrella YAML defers this with a comment citing "BA clarification pending". Surfaced verbatim on the Sources tab.

9. **Role boundaries** — BSA / Architect / Developer / Tester cards are inferred from repo structure and AGENTS.md hard rules. **No `CODEOWNERS` file is checked in at HEAD**, so per-person ownership could not be derived programmatically. The ASSUMPTION callout at the bottom of the Team Tasks tab notes this.

10. **Future test priorities** — assigned (High / Medium / Low) using a simple heuristic: anything tied to a security control or a known breaking-change gap is High; anything purely additive or cosmetic is Low. Adjust the CSV column if your team uses a different scheme.

11. **Open-issues completeness** — only in-repo issue references (#9, #10, #11, #12, #13) are surfaced. The GitLab issue search API returned HTTP 500 during discovery. See the Open Questions list below.

## Open questions still outstanding

These were called out in STEP 2 of the discovery process and were not resolved during the build. They are also surfaced in the Overview tab's "OPEN QUESTIONS" callout.

1. **Confirm the team display name** ("Valdo" vs. another official name) for the brand bar.

2. **Verify the full open-issues list** against the live tracker at  
   `https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/issues`  
   The infographic surfaces only the five issue IDs referenced inside the repository (#9, #10, #11, #12, #13). Any open issue that does not have a CHANGELOG entry, ADR reference, or in-source comment will be missing.

3. **Decide where `compare_multi_record` lives** — `src/` (per ADR&nbsp;0005's deferred section) or an E2E-harness wrapper. The Top-5 priorities and the ADR-0005 deferred card both note this is open.

4. **Confirm whether GE Checkpoint&nbsp;2 (referential) is in scope** for the next iteration. Only Checkpoint 1 is implemented today; future TC-F-030 is the placeholder.

5. **Confirm the canonical "Supported Sources" list** for the KT — should it include the harness-only sources referenced in `prompts/e2e_batch_testing_README.md` (SRC_A..SRC_E template), or stay focused on the production mapping inventory? The repo does not commit `config/e2e/sources/` at HEAD.

6. **CODEOWNERS** — does the team intend to add one? AGENTS.md references it; absence means the role assignments on the Team Tasks tab can't be tied to individuals.

7. **Release cut timing** — the CHANGELOG `[Unreleased]` section currently contains merged content for issues #9 and #10 (both **breaking changes**). When does the team plan to cut a major version and communicate the breakage downstream?

## How to use these files

- **HTML** — open `docs/Valdo-Infographic.html` directly in any modern browser. No server, no internet access, no build step required. Use ← / → arrow keys to cycle tabs.
- **CSV** — import into Excel, Jira, Xray, qTest, or any RFC&nbsp;4180-compliant tool. Filter on the `Status` column to separate Current from Future tests.
- **This summary** — share with reviewers as the cover note. Each open question above is a single decision that, once answered, unblocks a follow-up edit to the HTML.

## Verification checklist before sharing

- [ ] Open the HTML and click every sidebar item — confirm no console errors and that the breadcrumb updates.
- [ ] Confirm the brand bar team name is correct (open question #1).
- [ ] Spot-check 3–5 cells in the Test Cases table against `tests/unit/`, `tests/integration/`, and `tests/e2e/` — every "Test File" path should resolve to a real file.
- [ ] Skim the Known Issues tab and confirm that no open issue in the live tracker is missing (open question #2).
- [ ] Read the Team Tasks RACI table and substitute real names where the four role columns appear (open question #6).
