# CI Integration Guide

This guide covers integrating Valdo into your continuous integration pipelines. Whether you use GitHub Actions, Azure DevOps, GitLab CI, or another platform, Valdo provides reusable templates and a CLI interface designed for automation.

---

## Table of Contents

1. [Overview](#overview)
2. [Quick Start](#quick-start)
3. [Integration Patterns](#integration-patterns)
4. [Configuration Reference](#configuration-reference)
5. [Common Recipes](#common-recipes)
6. [Troubleshooting](#troubleshooting)

---

## Overview

### What Valdo Provides

Valdo validates, parses, and compares batch data files against mapping schemas and business rules. In a CI context it answers the question: **does this batch file conform to the expected schema and pass all business rules?**

Key capabilities available in CI:

- **Schema validation** -- verify file structure against a mapping JSON
- **Business rules** -- apply rule sets (nullability, ranges, patterns, cross-field checks)
- **Threshold gates** -- fail the pipeline if error counts or percentages exceed limits
- **Machine-readable reports** -- JSON output for downstream processing
- **HTML reports** -- self-contained reports attached as build artifacts

### When to Use

| Scenario | Recommended approach |
|----------|---------------------|
| PR gate -- validate sample files on every push | GitHub Action or CLI in CI |
| Nightly regression -- validate full production extracts | Scheduled pipeline with CLI |
| Post-deploy smoke test -- verify API is healthy | `curl` against the running API |
| Docker-based validation in isolated environments | `docker run` with mounted files |

---

## Quick Start

### GitHub Actions (10 lines)

```yaml
- name: Validate batch file
  uses: ./.github/actions/cm3-validate
  with:
    file: data/batch/customers.txt
    mapping: config/mappings/customers.json
    rules: config/rules/customers_rules.json
    threshold-max-errors: '50'
    threshold-max-error-pct: '5'
    fail-on-threshold: 'true'
```

### Docker (3 lines)

```bash
docker build -t valdo .
docker run --rm -v "$(pwd)/data:/data" -v "$(pwd)/config:/config" valdo \
  valdo validate -f /data/customers.txt -m /config/mappings/customers.json -o /data/report.json
```

### curl (API smoke test)

```bash
curl -s http://localhost:8000/api/v1/health | python3 -c "
import json, sys
data = json.load(sys.stdin)
assert data['status'] == 'ok', f'Health check failed: {data}'
print('API is healthy')
"
```

---

## Integration Patterns

### Pattern 1: CLI in CI

Install the package and run the CLI directly. This is the most flexible approach and works on any CI platform with Python available.

```yaml
# Generic CI steps (adapt to your platform)
steps:
  - run: pip install valdo-automations
  - run: |
      valdo validate \
        --file data/customers.txt \
        --mapping config/mappings/customers.json \
        --rules config/rules/customers_rules.json \
        --output reports/validation.json \
        --no-progress
```

The `--no-progress` flag suppresses the interactive progress bar, which is recommended for CI log output.

### Pattern 2: Docker-Based

Mount your data and config directories into the container. Useful when you cannot install Python or want reproducible environments.

```yaml
steps:
  - run: |
      docker run --rm \
        -v "${{ github.workspace }}/data:/data" \
        -v "${{ github.workspace }}/config:/config" \
        -v "${{ github.workspace }}/reports:/reports" \
        valdo:latest \
        valdo validate \
          -f /data/customers.txt \
          -m /config/mappings/customers.json \
          -o /reports/validation.json \
          --no-progress
```

### Pattern 3: API-Based

Start the CM3 API server and use HTTP requests for validation. This is useful for integration testing or when you need programmatic access to results.

```bash
# Start server in background
uvicorn src.api.main:app --host 0.0.0.0 --port 8000 &

# Wait for server
for i in $(seq 1 30); do
  curl -sf http://localhost:8000/api/v1/health && break
  sleep 1
done

# Upload and validate via API
curl -X POST http://localhost:8000/api/v1/validate \
  -F "file=@data/customers.txt" \
  -F "mapping=@config/mappings/customers.json" \
  -o reports/validation.json
```

### Pattern 4: GitHub Action (Reusable)

Use the composite action provided in `.github/actions/cm3-validate/`. This wraps the CLI with input/output handling, threshold evaluation, and artifact uploading.

```yaml
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Validate customers file
        id: validate
        uses: ./.github/actions/cm3-validate
        with:
          file: data/batch/customers.txt
          mapping: config/mappings/customers.json
          rules: config/rules/customers_rules.json
          threshold-max-errors: '50'
          threshold-max-error-pct: '5'

      - name: Check results
        run: |
          echo "Valid: ${{ steps.validate.outputs.valid }}"
          echo "Total rows: ${{ steps.validate.outputs.total-rows }}"
          echo "Error count: ${{ steps.validate.outputs.error-count }}"
```

### Pattern 5: Azure DevOps Template

Use the step template in `ci/templates/azure-cm3-validate.yml`:

```yaml
steps:
  - template: ci/templates/azure-cm3-validate.yml
    parameters:
      file: data/batch/customers.txt
      mapping: config/mappings/customers.json
      rules: config/rules/customers_rules.json
      thresholdMaxErrors: 50
      thresholdMaxErrorPct: 5
      outputFormat: json
```

### Pattern 6: GitLab CI Template

Include the template from `ci/templates/gitlab-cm3-validate.yml`:

```yaml
include:
  - local: 'ci/templates/gitlab-cm3-validate.yml'

validate-customers:
  extends: .cm3-validate
  variables:
    CM3_FILE: data/batch/customers.txt
    CM3_MAPPING: config/mappings/customers.json
    CM3_RULES: config/rules/customers_rules.json
    CM3_THRESHOLD_MAX_ERRORS: "50"
    CM3_THRESHOLD_MAX_ERROR_PCT: "5"
```

---

## Configuration Reference

### CLI Flags

| Flag | Short | Description | Default |
|------|-------|-------------|---------|
| `--file` | `-f` | Batch file to validate | (required) |
| `--mapping` | `-m` | Mapping JSON for schema validation | (none) |
| `--rules` | `-r` | Business rules JSON file | (none) |
| `--output` | `-o` | Output report path (.json or .html) | stdout |
| `--detailed` / `--basic` | | Include detailed field analysis | `--detailed` |
| `--use-chunked` | | Enable chunked processing for large files | off |
| `--chunk-size` | | Rows per chunk | 100000 |
| `--progress` / `--no-progress` | | Show progress bar (disable in CI) | `--progress` |
| `--strict-fixed-width` | | Enable strict fixed-width checks | off |
| `--strict-level` | | Validation depth: basic, format, all | format |
| `--workers` | | Parallel worker processes | 1 |

### CI Template Equivalents

| CLI Flag | GitHub Action Input | Azure Parameter | GitLab Variable |
|----------|-------------------|-----------------|-----------------|
| `--file` | `file` | `file` | `CM3_FILE` |
| `--mapping` | `mapping` | `mapping` | `CM3_MAPPING` |
| `--rules` | `rules` | `rules` | `CM3_RULES` |
| `--output` format | `output-format` | `outputFormat` | `CM3_OUTPUT_FORMAT` |
| (threshold) | `threshold-max-errors` | `thresholdMaxErrors` | `CM3_THRESHOLD_MAX_ERRORS` |
| (threshold) | `threshold-max-error-pct` | `thresholdMaxErrorPct` | `CM3_THRESHOLD_MAX_ERROR_PCT` |
| (fail gate) | `fail-on-threshold` | (always fails) | (always fails) |
| Python version | `python-version` | `pythonVersion` | (image-based) |
| Package version | `cm3-version` | `cm3Version` | `CM3_VERSION` |

---

## Common Recipes

### Nightly Regression

Run validation against full production extracts on a schedule.

```yaml
# GitHub Actions
name: Nightly Validation
on:
  schedule:
    - cron: '0 2 * * *'  # 2 AM UTC

jobs:
  validate:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        file:
          - { name: p327, file: data/p327_extract.txt, mapping: config/mappings/p327.json }
          - { name: eac, file: data/eac_extract.txt, mapping: config/mappings/eac.json }
    steps:
      - uses: actions/checkout@v4
      - uses: ./.github/actions/cm3-validate
        with:
          file: ${{ matrix.file.file }}
          mapping: ${{ matrix.file.mapping }}
          threshold-max-error-pct: '1'
```

### PR Validation Gate

Validate sample files on every pull request to catch mapping regressions.

```yaml
name: PR Validation
on:
  pull_request:
    paths:
      - 'config/mappings/**'
      - 'config/rules/**'
      - 'data/samples/**'

jobs:
  validate-samples:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: ./.github/actions/cm3-validate
        with:
          file: data/samples/customers_sample.txt
          mapping: config/mappings/customers.json
          rules: config/rules/customers_rules.json
          threshold-max-errors: '0'
```

### Post-Deploy Smoke Test

Verify the CM3 API is healthy after deployment.

```yaml
# GitHub Actions
post-deploy:
  runs-on: ubuntu-latest
  steps:
    - name: Health check
      run: |
        STATUS=$(curl -sf "${{ secrets.CM3_API_URL }}/api/v1/health" | python3 -c "
        import json, sys
        data = json.load(sys.stdin)
        print(data.get('status', 'unknown'))
        ")
        if [ "$STATUS" != "ok" ]; then
          echo "::error::CM3 API health check failed: status=$STATUS"
          exit 1
        fi
        echo "CM3 API is healthy"

    - name: Validate test file via API
      run: |
        curl -sf -X POST "${{ secrets.CM3_API_URL }}/api/v1/validate" \
          -F "file=@data/samples/smoke_test.txt" \
          -F "mapping=@config/mappings/smoke_test.json" \
          -o /tmp/smoke-result.json

        ERRORS=$(python3 -c "
        import json
        with open('/tmp/smoke-result.json') as f:
            data = json.load(f)
        print(data.get('summary', data).get('error_count', data.get('total_errors', -1)))
        ")
        echo "Smoke test errors: $ERRORS"
        [ "$ERRORS" -eq 0 ] || exit 1
```

---

## Troubleshooting

### "valdo: command not found"

The CLI entry point is registered by pip during installation. Ensure:

1. You installed the package: `pip install valdo-automations` or `pip install -e .`
2. The pip scripts directory is on `PATH`. In CI this is usually handled automatically.
3. If using a virtual environment, it is activated before running commands.

### "No mapping file specified"

The `--mapping` / `-m` flag is required for schema validation. If you only want format detection, use `valdo detect` instead.

### Large file timeouts

For files with millions of rows, enable chunked processing:

```bash
valdo validate -f large_file.txt -m mapping.json --use-chunked --chunk-size 50000 --workers 4
```

In CI, increase the job timeout accordingly. GitHub Actions default is 6 hours; Azure DevOps default is 60 minutes.

### JSON report parsing fails

Ensure the output format is JSON (`--output report.json` or `output-format: json`). HTML reports cannot be parsed for threshold evaluation. The templates default to JSON for this reason.

### Threshold evaluation shows 0 rows / 0 errors

If the validation command fails before producing output, the threshold evaluation step will see zeros. Check the validation step logs for the actual error. Common causes:

- File not found (wrong path or missing checkout step)
- Invalid mapping JSON (syntax error or missing fields)
- Missing dependencies (ensure `pip install` completed successfully)

### Progress bar garbles CI logs

Always use `--no-progress` in CI environments. The reusable templates set this automatically.

### Permission denied on report directory

Ensure the report output directory exists and is writable:

```bash
mkdir -p cm3-reports
valdo validate -f data.txt -m mapping.json -o cm3-reports/report.json
```

The reusable templates create this directory automatically.
