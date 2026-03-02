# Documentation Index (Canonical)

This is the **single source of truth** for docs navigation.

## Start Here
- `README.md` — quick start and project overview
- `docs/USAGE_GUIDE.md` — practical command usage
- `docs/FUNCTIONALITY_MATRIX.md` — capability matrix (CLI/API/inputs/outputs)
- `docs/architecture.md` — architecture and flow diagrams

## Core Operational Docs
- `docs/E2E_TESTING_GUIDE.md` — **end-to-end walkthrough** (CLI, Web UI, CI/CD triggers, api_check)
- `docs/TESTING_GUIDE.md` — test strategy and unit/integration commands
- `docs/CICD_GUIDE.md` — CI/CD setup and behavior
- `docs/PIPELINE_REGRESSION_GUIDE.md` — pipeline regression flows
- `docs/GREAT_EXPECTATIONS_CHECKPOINT1.md` — BA-friendly GE usage

## Test Suites
- `docs/INSTALL.md` — setup guide for first-time users
- See `cm3-batch run-tests --help` and `cm3-batch convert-suite --help` for CLI reference
- Example suite YAML: `config/test_suites/` directory

## Data & Mapping
- `docs/MAPPING_QUICKSTART.md`
- `docs/UNIVERSAL_MAPPING_GUIDE.md`
- `docs/MAPPING_SCHEMA.md`
- `docs/VALIDATION_RULES.md`
- `docs/FIXED_WIDTH_MAPPING_CHECKLIST.md` — checklist + failure playbook
- `docs/FIXED_WIDTH_MULTITYPE_IMPLEMENTATION_CHECKLIST.md` — architect-gated task plan (tests + review + docs)
- `docs/TRANSFORMATION_TYPES.md`

## Deployment
- `docs/DEPLOYMENT_OPTIONS.md`
- `docs/RHEL_DEPLOYMENT.md`
- `docs/RPM_DEPLOYMENT.md`
- `docs/PEX_DEPLOYMENT.md`
- `docs/INSTALL.md` — local installation guide (Windows, Linux, VSCode)

## API
- `docs/API_UPLOAD_GUIDE.md`

## Contracts
- `docs/contracts/business_rules_v1.md`
- `docs/contracts/validation_result_v1.md`
- `docs/contracts/fixed_width_multitype_v2.md`

---

## Redundancy Policy
When two docs overlap, keep details in the canonical doc above and reduce other files to short pointers.
