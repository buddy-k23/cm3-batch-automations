# ADR 0002: Validation and Reporting Boundaries

- Status: Accepted
- Date: 2026-02-21

## Context
Validation behavior diverged across chunked/non-chunked paths and reporting modules, creating inconsistent outputs and metadata gaps.

## Decision
- Validation engines own defect detection and canonical result models.
- Adapters normalize mode-specific outputs into a common report contract.
- Renderers (`src/reports/renderers/*`) only transform normalized contracts into presentation formats (HTML/CSV sidecars).

## Consequences
- Consistent output semantics across validation modes.
- Cleaner separation between detection, normalization, and rendering.
- Easier migration and compatibility shims across namespaces.
