# Pilot Database Validations Package

This folder contains the migrated pilot orchestration work from the `database-validations` worktree.

## Included
- `docs/` pilot specs, runbooks, and trial notes
- `schemas/` mapping/rules/report/telemetry contracts
- `tools/` parser, contract generator, e2e runner, promotion gate, pilot harness
- `scripts/` shell/powershell/batch run scripts
- `examples/` starter templates, pilot source templates, pilot testcases
- `tests/` pilot-focused tests

## Excluded
- large generated runtime artifacts (`generated/`)
- Python cache artifacts

## Notes
Use this as the integration surface into `cm3-batch-automations` mainline. Wire tools gradually into existing `src/` and CI flows.
