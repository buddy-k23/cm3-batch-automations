# Splunk Integration for Valdo

## Overview

Valdo emits structured JSON audit events to a JSONL file (`logs/audit.jsonl`
by default). These events are designed for ingestion by Splunk Universal
Forwarder with zero transformation.

## Audit Log Location

| Setting             | Default             | Override                      |
|---------------------|---------------------|-------------------------------|
| Log file path       | `logs/audit.jsonl`  | `AUDIT_LOG_PATH` env var      |
| Environment tag     | `DEV`               | `CM3_ENVIRONMENT` env var     |

## Splunk Universal Forwarder Configuration

Add a monitor stanza to `inputs.conf`:

```ini
[monitor:///opt/cm3-batch-automations/logs/audit.jsonl]
sourcetype = _json
index = cm3_audit
disabled = false
```

### Recommended Index Settings

Create a dedicated index in Splunk (`indexes.conf` or via the web UI):

| Parameter          | Value         |
|--------------------|---------------|
| Index name         | `cm3_audit`   |
| Sourcetype         | `_json`       |
| Max size           | 10 GB         |
| Retention          | 90 days       |

Using `sourcetype=_json` tells Splunk to auto-parse each line as a JSON
object, making every field immediately searchable without custom
transforms.

## Event Schema

Every audit event contains at minimum:

```json
{
  "event": "test_run_started",
  "timestamp": "2026-03-25T14:30:00.123456+00:00",
  "run_id": "a1b2c3d4e5f6...",
  "environment": "DEV",
  "triggered_by": "cli"
}
```

### Event Types

| Event                  | Description                              |
|------------------------|------------------------------------------|
| `test_run_started`     | Validation or comparison run began       |
| `test_run_completed`   | Run finished (includes result summary)   |
| `file_uploaded`        | File uploaded via API                    |
| `file_cleanup`         | Stale files removed by cleanup job       |
| `auth_failure`         | API key authentication failed            |
| `suite_step_completed` | Pipeline suite step finished             |

## Sample Splunk Searches

### All events in the last 24 hours

```spl
index=cm3_audit earliest=-24h
| table timestamp event environment triggered_by
```

### Failed validation runs

```spl
index=cm3_audit event="test_run_completed" valid=false
| table timestamp run_id file error_count total_rows
```

### Auth failures (potential security concern)

```spl
index=cm3_audit event="auth_failure"
| stats count by environment, triggered_by
| sort -count
```

### Pipeline suite step durations

```spl
index=cm3_audit event="suite_step_completed"
| stats avg(duration_seconds) max(duration_seconds) by suite, step
```

### Validation error rate over time

```spl
index=cm3_audit event="test_run_completed"
| timechart span=1h count AS total
```

### File hash tracking (data integrity)

```spl
index=cm3_audit event="test_run_started" file_hash=*
| table timestamp run_id file file_hash mapping_hash
```
