# ADR 0004: CI/CD Trigger Patterns

- Status: Accepted
- Date: 2026-03-01

## Context
Post-batch validation needs to be triggered automatically after the Java ETL process
completes, without tight coupling between the Java process and cm3-batch.

## Decision
Support three trigger mechanisms with no coupling requirement:
1. **Trigger file**: Java drops `batch_complete_YYYYMMDD.trigger` — cm3-batch polls and processes.
2. **Webhook**: `POST /api/v1/runs/trigger` for pipeline-native triggering.
3. **Manual**: `cm3-batch run-tests` continues to work unchanged.

## Consequences
- Java batch needs only file-write or HTTP-call capability — no direct dependency on cm3-batch.
- Watcher is long-running; deploy as a systemd service or container alongside the API.
- In-memory run store in the webhook handler is ephemeral — acceptable for lower environments.
