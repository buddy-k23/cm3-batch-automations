# AGENTS.md — Guidance for AI Coding Agents

> This file is read automatically by AI coding agents (GitLab Duo, Claude Code,
> Copilot Workspace, Cursor) before they begin work in this repository. It
> tells the agent which prompts, conventions, and gates are authoritative.

## Project: Valdo

Valdo is an automated batch file validation, comparison, and ETL testing tool
with multi-database support, a REST API, and a modern web UI. See `README.md`
and `docs/architecture.md` for the full picture.

- **Languages**: Python 3.9+ (primary), bash + PowerShell for RHEL/Windows
  wrappers, SQL for Oracle DDL.
- **Test bar**: 1,063 unit tests + 46 E2E tests, 80%+ coverage. Do not regress.
- **Run tests before declaring done**: `pytest` from repo root.
- **Lint/format**: `black src/ tests/`, `flake8 src/ tests/`, `mypy src/`.

## Active prompts (authoritative task definitions)

Agents working on the following topics MUST read the linked prompt before doing
anything else. The prompt overrides any contradictory assumption.

| Topic | Prompt | When to use |
|---|---|---|
| End-to-end batch testing harness on RHEL with CA ESP | [`prompts/e2e_batch_testing_prompt.md`](prompts/e2e_batch_testing_prompt.md) | Any task touching `scripts/run_e2e_*`, `config/e2e/`, `baselines/`, or `AUDIT.VALDO_RUN_FAILURES`. Start at milestone **M1** unless an earlier milestone is already complete. |
| Mapping CSV generation from Excel/PDF specs | [`prompts/generate-mapping-csv.md`](prompts/generate-mapping-csv.md) | Producing mapping CSVs from source-system specification documents. |
| Business rules CSV generation | [`prompts/generate-rules-csv.md`](prompts/generate-rules-csv.md) | Producing business rules CSVs. |
| Both mapping + rules in one pass | [`prompts/generate-both.md`](prompts/generate-both.md) | When a spec document contains both layout and rules. |

### Operator playbooks (procedural, not LLM prompts)

| Topic | Playbook | When to use |
|---|---|---|
| Add a new record type to an existing multi-record umbrella | [`docs/ADD_RECORD_TYPE_PLAYBOOK.md`](docs/ADD_RECORD_TYPE_PLAYBOOK.md) | Adding a new `TRANSACTION-CODE` (or equivalent discriminator value) to a source whose umbrella YAML already exists. Pure config + scripts; no `src/` changes. |

If a user request matches one of the topics above, **follow the linked prompt's
`<implementation_order>` and `<acceptance_criteria>` sections**. Do not skip
milestones. Stop and report at each milestone boundary unless explicitly told
to continue.

## Repository conventions

### Layout
- `src/` — production code (CLI, API, parsers, validators, services).
- `tests/unit/` — unit tests, one file per module under test, prefix `test_`.
- `tests/integration/` — FastAPI `TestClient`-based integration tests.
- `scripts/` — operator wrappers, setup scripts, one-shot tools.
- `config/` — JSON/YAML configuration (mappings, rules, pipelines, suites).
- `config/e2e/` — **reserved for the E2E batch testing harness** (see prompt).
- `baselines/` — **reserved for golden output baselines** (see prompt).
- `docs/` — architecture, ADRs, deployment, testing guides.
- `prompts/` — LLM prompts checked into the repo (this is one).

### Coding style
- Match the existing style of nearby files — do not introduce a new framework,
  config format, or dependency without an ADR.
- Imports: standard library, third-party, local — separated by blank lines.
- Type hints required on all new public functions.
- Docstrings: Google style, matching existing modules in `src/services/` and
  `src/pipeline/`.
- No `print()` in production code — use `src/utils/logger.py`.
- No `os.environ.get()` outside dedicated secret-resolution helpers.

### Tests
- Every new public function gets a unit test.
- Mirror the source path: `src/foo/bar.py` → `tests/unit/test_bar.py`.
- Use `pytest` fixtures; do not roll your own setup/teardown.
- Integration tests that hit Oracle must be gated by an env-var check and
  skipped when the DB is unavailable.

### Commits & MRs
- One logical change per commit.
- MR titles follow Conventional Commits: `feat:`, `fix:`, `docs:`, `chore:`,
  `refactor:`, `test:`.
- CI must be green before requesting review.

## Hard rules (do not violate)

1. **Do not modify Valdo internals** (anything under `src/`) when working on
   the E2E batch testing harness. That work is **scripts + config only**. If a
   genuine internal gap exists, leave a clearly-marked `TODO(valdo-gap)` and a
   wrapper that achieves the behavior externally.

   **Carve-out:** When `scripts/e2e_lib/` work reveals a reusable primitive
   that already exists *inside* `src/` but is not currently exposed as a
   primitive (e.g. it lives as a private method on a larger class), it is
   acceptable to refactor `src/` to expose the primitive, provided: (a) the
   change is a pure refactor with no behaviour change for existing consumers;
   (b) an ADR documents the refactor and its rationale; (c) the refactor
   lands as its own MR, separate from the harness change that motivated it.

   **Carve-out audit:** every harness-motivated `src/` change is logged in
   [`docs/AGENTS_CARVE_OUT_AUDIT.md`](docs/AGENTS_CARVE_OUT_AUDIT.md). Run
   that checklist periodically (and whenever a change touches both
   `scripts/e2e_lib/` and `src/`) to confirm each carve-out has an ADR and
   stays minimal/additive, so the carve-out does not erode this rule.
2. **No quick actions** (lines starting with `/`) in any title, description,
   or comment body when calling GitLab tools — they cause 403 errors.
3. **No secrets in code or config files**. Use env vars (current) or the
   `scripts/e2e_lib/secret_resolver.py` interface (preferred, Vault-ready).
   Note: shared helpers live under `scripts/e2e_lib/` (not `scripts/lib/`)
   because the repo's `.gitignore` excludes any `lib/` directory.
4. **No PII redaction work** — data in `sit`/`ait` is already privatized.
5. **Never `UPDATE` or `DELETE`** rows in `AUDIT.VALDO_RUN_FAILURES` — it is
   append-only.
6. **Stop at milestone boundaries** when a prompt defines `<implementation_order>`.
   Wait for human review.

## Environment notes

- Target runtime: **RHEL 8.9+** with Python 3.11 in a `venv`.
- Oracle is reached via `python-oracledb` in thin mode by default.
- The Valdo CLI is installed via `pip install -e .` and invoked as `valdo …`.
- Two environments only for E2E work: `sit` and `ait`. No `prod` in this
  iteration.

## Arch-review loop state (durable facts for a fresh session)

These rarely change between stories, so they live here (auto-read every
session) instead of being re-narrated in each handoff. A new session needs
no prior-session memory: read this, then run the runner prompt's
`<bootstrap_state>` to locate the current story from the repo itself.

- **Runner prompt (owns the loop):** `prompts/arch_review_story_runner_prompt.md`.
  Run its `<bootstrap_state>` FIRST in any session.
- **Source of intent:** `docs/ARCHITECTURE_REVIEW_2026-06-03.md` (R-NN ids).
- **Rolling pointer (last SHA / next story):** `docs/handover/ARCH_REVIEW_STATUS.md`.
- **Trunk branch:** `feature/valdo-engine-v3` — commit + push directly; no
  topic branches, no MRs (single-developer repo). Never push `main`; never
  force-push trunk. Snapshot before each story (`arch-review-snapshot/<R-ID>`),
  delete only after the gate is green AND the push succeeded.
- **Gate policy:** `pytest` green with no NEW failures vs the documented
  baseline in `docs/handover/ARCH_REVIEW_TEST_BASELINE_2026-06-03.md`
  (43 pre-existing environmental failures are NOT regressions; harness
  offline subset = 267 passed / 1 skipped). black/flake8/mypy: zero NEW
  findings vs HEAD on changed files.
- **Progress = repo evidence, not a doc:** a story is DONE iff its
  `docs/handover/ARCH_REVIEW_<R-ID>_HANDOFF.md` records a pushed SHA AND the
  GitLab issue's acceptance boxes are all checked. The current story is the
  first backlog entry that is not DONE.
- **Config schemas:** `docs/CONFIG_SCHEMA_REGISTRY.md` (artifact → schema
  authority → load-validated).
- **Issue auto-close (standing authorization, Option 1):** when a story's
  gate is green AND the push succeeded, close its GitLab issue via the API
  (`update_work_item state=closed`). Commit trailers stay `Refs #NN` (trunk
  is not the default branch, so `Closes` would not auto-close on push).

### Windows / shell gotchas on the dev box (IMPORTANT)
- **CRLF/BOM files break `edit_file`** (e.g. `AGENTS.md`, `CHANGELOG.md`,
  `cross_type_validator.py` (CRLF+BOM), `SHAW_TRANERT.yaml`, several ADRs).
  Patch them with a CRLF/BOM-safe scratch script (read_bytes -> decode ->
  replace on LF-normalized text -> re-encode as CRLF, preserve BOM), then
  verify on disk. `edit_file` is fine on LF files.
- **`python -c "..."` with multi-line/backticks silently fails in cmd** —
  write a `_scratch.py` file, run it, and verify the on-disk result before
  committing.
- **No `bash`/`grep`/`tail`/`;` chaining** — chain with `&` in cmd; use
  Python scratch files to inspect output.
- **`reports/` is gitignored AND unreadable by the file tools** — dump
  readable artifacts to a `_*` dir at repo root instead.
- **Explicit-path commits only** (`git add <paths>`); never `git add -A`
  (it would stage the gitignored leavings and the R-15-owned prompt diff).
- `git push` prints a harmless `git: 'credential-manager-core' is not a git
  command` line; the push still succeeds (check the `..` ref-update line).
- SIT is reachable via `.env` (`ORACLE_DSN_SIT` = APPSSIT1); live L2b
  reconcile works. Live-Oracle tests SKIP cleanly without SIT.

## When in doubt

- Read the relevant prompt in `prompts/` first.
- Read `docs/architecture.md` second.
- Read `README.md` third.
- For any config artifact (mapping/rules JSON+CSV, umbrella YAML, reconcile
  YAML, source YAML, `paths.yml`, pipeline YAML), consult
  `docs/CONFIG_SCHEMA_REGISTRY.md` for its schema authority and whether it is
  load-validated before editing or adding a new config format.
- Then ask the user a focused clarifying question. Do not guess on:
  trigger-file naming, baseline-promotion policy, gate blocking semantics, or
  Oracle schema/table names.
