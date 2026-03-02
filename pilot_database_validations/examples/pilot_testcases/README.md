# Pilot Testcase Templates

This folder contains explicit positive / negative / edge-case template fixtures for each pilot source contract listed in `generated/pilot_contracts/index.json`.

Per-source files:
- `mapping-positive.csv`, `mapping-negative.csv`, `mapping-edge.csv`
- `rules-positive.csv`, `rules-negative.csv`, `rules-edge.csv`
- `file-config-positive.csv`, `file-config-negative.csv`, `file-config-edge.csv`

Governance fixtures:
- `governance/promotion-input-positive.json`
- `governance/promotion-input-negative.json`
- `governance/promotion-input-edge.json`

Expected results index:
- `expected-outcomes.json`

See `docs/pilot-testcase-matrix.md` for full matrix and execution notes.
