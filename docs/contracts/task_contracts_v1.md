# Task Contracts v1

Canonical contracts for orchestration requests/results.

## TaskRequest (v1)
Required fields:
- `task_id`
- `trace_id`
- `idempotency_key` (optional)
- `source` (`cli|api|internal`)
- `intent`
- `payload`
- `priority` (`low|normal|high|urgent`)
- `deadline` (ISO date-time)

Example:
```json
{
  "task_id": "task-123",
  "trace_id": "trace-xyz",
  "idempotency_key": "idem-1",
  "source": "api",
  "intent": "validate",
  "payload": {"mapping_id": "p327_mapping"},
  "priority": "normal",
  "deadline": "2026-03-20T12:00:00Z",
  "version": "v1"
}
```

## TaskResult (v1)
Required fields:
- `task_id`
- `trace_id`
- `status`
- `result`

Example:
```json
{
  "task_id": "task-123",
  "trace_id": "trace-xyz",
  "status": "queued",
  "result": {"accepted": true},
  "errors": [],
  "warnings": [],
  "version": "v1"
}
```

## Schema files
- `contracts/task_request_v1.schema.json`
- `contracts/task_result_v1.schema.json`
