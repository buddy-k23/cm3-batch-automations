# ADR 0001: CLI-to-Service Boundary

- Status: Accepted
- Date: 2026-02-21

## Context
Command workflows were duplicated between CLI handlers and API routers, causing behavior drift and repeated bug fixes.

## Decision
- Keep `src/commands/*` as thin CLI wrappers.
- Move reusable orchestration/business logic into `src/services/*`.
- API routers must call shared services rather than re-implementing command logic.

## Consequences
- Better parity between CLI and API behaviors.
- Faster defect fixes (single implementation point).
- Requires contract-focused tests at the service layer.
