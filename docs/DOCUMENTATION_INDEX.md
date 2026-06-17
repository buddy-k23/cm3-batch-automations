# Documentation Index (Canonical)

This is the **single source of truth** for docs navigation.

## Start Here
- `README.md` — quick start and project overview
- `docs/USAGE_AND_OPERATIONS_GUIDE.md` — **canonical, comprehensive usage, API, CI, and operations guide** (CLI, Web UI, REST API, CI/CD, Docker, monitoring)
- `docs/USAGE_GUIDE.md` — quick-reference cheat sheet (most-used commands; points to the comprehensive guide for full detail)
- `docs/FUNCTIONALITY_MATRIX.md` — capability matrix (CLI/API/inputs/outputs)
- `docs/architecture.md` — architecture and flow diagrams

## Core Operational Docs
- `docs/E2E_TESTING_GUIDE.md` — **end-to-end walkthrough** (CLI, Web UI, CI/CD triggers, api_check)
- `docs/TESTING_GUIDE.md` — test strategy and unit/integration commands
- `docs/CICD_GUIDE.md` — CI/CD setup and behavior
- `docs/CI_INTEGRATION_GUIDE.md` — reusable CI templates (GitHub Actions, Azure DevOps, GitLab CI) and integration recipes
- `docs/PIPELINE_REGRESSION_GUIDE.md` — pipeline regression flows
- `docs/GREAT_EXPECTATIONS_CHECKPOINT1.md` — BA-friendly GE usage

## Test Suites
- `docs/INSTALL.md` — setup guide for first-time users
- See `valdorun-tests --help` and `valdoconvert-suite --help` for CLI reference
- Example suite YAML: `config/test_suites/` directory

## Multi-Record-Type Validation
- `docs/USAGE_AND_OPERATIONS_GUIDE.md#multi-record-type-file-validation` — full reference (YAML config format, cross-type rules, API endpoint, CLI usage)
- `docs/ADD_RECORD_TYPE_PLAYBOOK.md` — **operator playbook** for adding a new record type to an existing multi-record umbrella (config + scripts only; no `src/` changes)
- `config/multi-record/example_atoctran.yaml` — example multi-record YAML config
- `src/config/multi_record_config.py` — Pydantic models
- `src/validators/multi_record_validator.py` — orchestrator
- `src/validators/cross_type_validator.py` — 7 cross-type check implementations

## Data & Mapping
- `docs/MAPPING_QUICKSTART.md`
- `docs/UNIVERSAL_MAPPING_GUIDE.md`
- `docs/MAPPING_SCHEMA.md`
- `docs/VALIDATION_RULES.md`
- `docs/FIXED_WIDTH_MAPPING_CHECKLIST.md` — checklist + failure playbook
- `docs/FIXED_WIDTH_MULTITYPE_IMPLEMENTATION_CHECKLIST.md` — architect-gated task plan (tests + review + docs)
- `docs/TRANSFORMATION_TYPES.md`

## Deployment
- `docs/DEPLOYMENT_OPTIONS.md` — **deployment hub**: start here for the option comparison and "which packaging method to choose" (RHEL 8.9, no Docker)
  - `docs/RHEL_DEPLOYMENT.md` — traditional virtual-environment install (detailed procedure)
  - `docs/PEX_DEPLOYMENT.md` — single-file PEX executable (detailed procedure)
  - `docs/RPM_DEPLOYMENT.md` — RPM package for yum/dnf-managed fleets (detailed procedure)
- `docs/PRODUCTION_DEPLOYMENT.md` — MCP production runbook: TLS + nginx reverse proxy, X-Forwarded-* (S9-1), and later S9 hardening sections
- `docs/ORACLE_RHEL_SETUP.md` — install and configure Oracle on RHEL for Valdo's DB integration
- `docs/ORACLE_SCHEMA.md` — Oracle schema Valdo expects (run history and related tables)
- `docs/INSTALL.md` — local installation guide (Windows, Linux, VSCode)

## API
- `docs/API_UPLOAD_GUIDE.md`
- `docs/USAGE_AND_OPERATIONS_GUIDE.md#api-tester-tab` — API Tester: proxy-based REST tester with suite runner and assertions
- `docs/USAGE_AND_OPERATIONS_GUIDE.md#using-a-secrets-provider-for-passwords` — secret provider configuration (env, Vault, Azure Key Vault) for Oracle credentials

## Security
- `docs/USAGE_AND_OPERATIONS_GUIDE.md#authentication` — `X-API-Key` header,
  RBAC roles (`tester`, `mapping_owner`, `admin`), fail-closed `503` when
  `API_KEYS` is unset, opaque `error_id` from the global exception handler
- `docs/USAGE_AND_OPERATIONS_GUIDE.md#ip-whitelisting` — `security.ip_whitelist`,
  `security.trust_proxy`, and `security.trusted_proxies` (right-to-left
  X-Forwarded-For parsing, fall back to direct peer)
- `docs/USAGE_AND_OPERATIONS_GUIDE.md#webhook-async-validation` — webhook
  callback safety: SSRF scheme/IP-range blocks and the
  `security.callback_allowlist` hostname allowlist
- `src/api/security/url_validator.py` — SSRF validator used by the webhook
  endpoint to reject `file://` / `gopher://` / `javascript:` URLs and
  loopback / link-local / RFC1918 IP ranges
- GitLab issue [#9](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/issues/9) — authentication hardening (mandatory `API_KEYS`, Referer-bypass removal, opaque `error_id`)
- GitLab issue [#10](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/issues/10) — network/SSRF hardening (`trusted_proxies`, `callback_allowlist`, file-upload path-traversal fixes, TLS key chmod `0o600`)

## Change Management
- `docs/CHANGE_MANAGEMENT.md` — configuration change approval workflow (CODEOWNERS, CI validation, audit trail)

## Observability
- `docs/splunk-setup.md` — Splunk integration: audit log path, Universal Forwarder config, sample SPL queries

## Contracts
- `docs/contracts/business_rules_v1.md`
- `docs/contracts/validation_result_v1.md`
- `docs/contracts/fixed_width_multitype_v2.md`
- `docs/contracts/task_contracts_v1.md`

---

## Redundancy Policy
When two docs overlap, keep details in the canonical doc above and reduce other files to short pointers.
