# Valdo — Quick Reference

> **Cheat sheet only.** For the full reference — Web UI walkthrough, complete
> CLI and REST API documentation, configuration schemas, CI/CD recipes,
> database integration, masking, transforms, and operations — see the
> comprehensive **[Usage and Operations Guide](USAGE_AND_OPERATIONS_GUIDE.md)**.

## Start the server

```bash
source .venv/bin/activate
valdo serve                       # FastAPI app on http://localhost:8000
# Swagger UI:  http://localhost:8000/docs
# Web UI:      http://localhost:8000/ui
```

## Most-used CLI commands

```bash
valdo detect -f <file>                       # Detect file format
valdo parse -f <file> -m <mapping>           # Parse a file with a mapping
valdo validate -f <file> -m <mapping>        # Validate against a mapping
valdo validate -f <file> --multi-record -c <config.yaml>   # Multi-record-type file
valdo compare -f1 <fileA> -f2 <fileB>        # Compare two files row-by-row
valdo db-compare -t <table> -f <file>        # Compare Oracle extract vs file
valdo extract -t <table> -o <file>           # Extract DB table/query to file
valdo reconcile -m <mapping>                 # Reconcile mapping vs DB schema
valdo mask -f <file> -r <rules>              # Mask PII in a batch file
valdo run-tests --suite <suite.yaml>         # Run a test suite
valdo run-etl-pipeline --config <pipeline.yaml>   # Run ETL validation gates
valdo gx-checkpoint1 --targets ... --expectations ...   # Great Expectations checks
valdo list-runs                              # List archived suite runs
valdo get-run <run_id>                       # Inspect a specific run
valdo submit-task --intent validate --payload '<json>'  # Canonical task ingest
```

Add `--help` to any command for its full option list, or see the
[CLI Reference](USAGE_AND_OPERATIONS_GUIDE.md#4-cli-reference).

## Most-used REST endpoints

```
GET  /api/v1/system/health        # Health check (no auth)
POST /api/v1/mappings/upload      # Upload Excel/CSV template
GET  /api/v1/mappings/            # List mappings
POST /api/v1/files/detect         # Detect file format
POST /api/v1/files/parse          # Parse file
POST /api/v1/files/compare        # Compare files
POST /api/v1/runs/trigger         # Trigger a suite run (async)
GET  /api/v1/runs/{run_id}        # Poll run status
POST /api/v1/tasks/submit         # Submit a canonical task
GET  /api/v1/tasks/{task_id}      # Task lifecycle state
POST /api/v1/webhook/validate     # Async webhook validation
```

Full API docs (auth, request/response schemas, examples):
[API Reference](USAGE_AND_OPERATIONS_GUIDE.md#5-api-reference).

## CI/CD triggers

```bash
# Pull-based: watch a directory for batch_complete_*.trigger files
valdo watch --dir /batch/triggers --suites config/test_suites/ \
  --env dev --output-dir reports --interval 30

# Push-based: call the API after the batch completes (GitLab/Azure)
curl -X POST http://app-server:8000/api/v1/runs/trigger \
  -H "Content-Type: application/json" \
  -d '{"suite": "config/test_suites/p327_uat.yaml", "params": {"run_date": "20260301"}, "env": "dev"}'
```

See [CI Pipeline Integration](USAGE_AND_OPERATIONS_GUIDE.md#7-ci-pipeline-integration).

## Web UI (5 tabs)

Quick Test · Recent Runs · Mapping Generator · DB Compare · **API Tester**
(an interactive, Postman-style REST client with a suite runner and assertions).
Hover any control for a contextual tooltip. Full walkthrough:
[Web UI Guide](USAGE_AND_OPERATIONS_GUIDE.md#3-web-ui-guide).

## Where to find more

| Topic | See |
|---|---|
| Everything (comprehensive) | [USAGE_AND_OPERATIONS_GUIDE.md](USAGE_AND_OPERATIONS_GUIDE.md) |
| Test suite YAML format | [Suite YAML Format](USAGE_AND_OPERATIONS_GUIDE.md#suite-yaml-format) |
| Multi-record-type validation | [Multi-Record-Type File Validation](USAGE_AND_OPERATIONS_GUIDE.md#multi-record-type-file-validation) |
| Database integration | [Database Integration](USAGE_AND_OPERATIONS_GUIDE.md#8-database-integration) |
| Transform engine | [Transform Engine](USAGE_AND_OPERATIONS_GUIDE.md#95-transform-engine) |
| Great Expectations (BA-friendly) | [GREAT_EXPECTATIONS_CHECKPOINT1.md](GREAT_EXPECTATIONS_CHECKPOINT1.md) |
| Running tests | [TESTING_GUIDE.md](TESTING_GUIDE.md) |
| Deployment options | [DEPLOYMENT_OPTIONS.md](DEPLOYMENT_OPTIONS.md) |
| All docs | [DOCUMENTATION_INDEX.md](DOCUMENTATION_INDEX.md) |

**Need help?** The interactive [Swagger UI](http://localhost:8000/docs)
documents every endpoint.
