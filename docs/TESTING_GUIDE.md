# Testing Guide

## Overview

This guide explains how to run tests for Valdo, including **unit tests**, **integration tests**, and **API tests** for the new REST API.

## Test Structure

```
tests/
├── unit/              # Unit tests for individual components
├── integration/      # Integration tests for workflows
├── api/              # API endpoint tests (new!)
├── fixtures/         # Test fixtures and sample data
└── conftest.py       # Pytest configuration and shared fixtures
```

## Running Tests

### Quick Start

```bash
# Run all tests
pytest

# Or use the test runner script
./run_tests.sh
```

### Using Test Runner Script

```bash
# Make executable
chmod +x run_tests.sh

# Run all tests
./run_tests.sh

# Run only unit tests
./run_tests.sh --unit

# Run with coverage
./run_tests.sh --coverage

# Run with verbose output
./run_tests.sh --verbose

# Combine options
./run_tests.sh --unit --coverage --verbose
```

### Using pytest Directly

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/unit/test_format_detector.py

# Run specific test class
pytest tests/unit/test_format_detector.py::TestFormatDetector

# Run specific test method
pytest tests/unit/test_format_detector.py::TestFormatDetector::test_detect_pipe_delimited

# Run tests matching pattern
pytest -k "pipe"

# Run only unit tests
pytest tests/unit

# Run only integration tests
pytest tests/integration
```

## Coverage Reports

### Generate Coverage Report

```bash
# HTML coverage report
pytest --cov=src --cov-report=html

# Terminal coverage report
pytest --cov=src --cov-report=term-missing

# Both HTML and terminal
pytest --cov=src --cov-report=html --cov-report=term-missing

# With minimum coverage requirement
pytest --cov=src --cov-fail-under=80
```

### View Coverage Report

```bash
# Open HTML report in browser
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux
start htmlcov/index.html  # Windows
```

## Test Categories

### Unit Tests

Test individual components in isolation:

- `test_pipe_delimited_parser.py` - Pipe-delimited parser
- `test_fixed_width_parser.py` - Fixed-width parser
- `test_format_detector.py` - Format detection
- `test_validator.py` - File and schema validation
- `test_mapping_parser.py` - Mapping document parser
- `test_universal_mapping_parser.py` - Universal mapping parser (new!)
- `test_template_converter.py` - Template converter (new!)
- `test_file_comparator.py` - File comparison
- `test_config_loader.py` - Configuration loader
- `test_html_reporter.py` - HTML report generation
- `test_transaction.py` - Transaction management
- `test_threshold.py` - Threshold evaluation

### Integration Tests

Test component interactions and workflows:

- End-to-end file processing
- Database connectivity tests
- Complete comparison workflows
- Mapping application workflows
- Universal mapping workflows (new!)

### API Tests (New!)

Test REST API endpoints:

- `test_api_mappings.py` - Mapping endpoints
- `test_api_files.py` - File operation endpoints
- `test_api_system.py` - System endpoints

**Running API tests:**
```bash
# Run all API tests
pytest tests/api -v

# Run specific API test file
pytest tests/api/test_api_mappings.py -v

# Test with running server
pytest tests/api --api-url=http://localhost:8000
```

## Test Fixtures

Shared fixtures are defined in `tests/conftest.py`:

- `sample_pipe_delimited_file` - Temporary pipe-delimited file
- `sample_fixed_width_file` - Temporary fixed-width file
- `sample_dataframe` - Sample pandas DataFrame
- `sample_mapping_dict` - Sample mapping configuration

### Using Fixtures

```python
def test_with_fixture(sample_dataframe):
    """Test using a fixture."""
    assert len(sample_dataframe) == 5
    assert 'id' in sample_dataframe.columns
```

## Markers

Tests can be marked for selective execution:

```python
import pytest

@pytest.mark.unit
def test_something():
    pass

@pytest.mark.integration
def test_integration():
    pass

@pytest.mark.slow
def test_slow_operation():
    pass
```

Run marked tests:

```bash
# Run only unit tests
pytest -m unit

# Run only integration tests
pytest -m integration

# Skip slow tests
pytest -m "not slow"
```

## Mocking Database Connections

For tests that require database connections:

```python
from unittest.mock import Mock, patch

@patch('src.database.connection.cx_Oracle')
def test_with_mock_db(mock_cx_oracle):
    """Test with mocked database."""
    mock_conn = Mock()
    mock_cx_oracle.connect.return_value = mock_conn
    
    # Your test code here
    conn = OracleConnection('user', 'pass', 'dsn')
    # ...
```

## Continuous Testing

### Watch Mode (requires pytest-watch)

```bash
# Install pytest-watch
pip install pytest-watch

# Run tests on file changes
ptw

# Watch specific directory
ptw tests/unit
```

### Pre-commit Hook

Create `.git/hooks/pre-commit`:

```bash
#!/bin/bash
echo "Running tests before commit..."
pytest tests/unit -q
if [ $? -ne 0 ]; then
    echo "Tests failed. Commit aborted."
    exit 1
fi
```

## Debugging Tests

### Run with Debug Output

```bash
# Show print statements
pytest -s

# Show local variables on failure
pytest -l

# Drop into debugger on failure
pytest --pdb

# Stop on first failure
pytest -x

# Show full diff on assertion failures
pytest -vv
```

### Using pytest.set_trace()

```python
def test_with_debugging():
    value = some_function()
    pytest.set_trace()  # Debugger will stop here
    assert value == expected
```

## Performance Testing

### Show Slowest Tests

```bash
# Show 10 slowest tests
pytest --durations=10

# Show all test durations
pytest --durations=0
```

## Test Output

### Customize Output

```bash
# Quiet mode (minimal output)
pytest -q

# Verbose mode
pytest -v

# Very verbose mode
pytest -vv

# Show test summary
pytest -ra

# No capture (show print statements)
pytest -s
```

## API Testing

### Testing REST API Endpoints

The REST API can be tested using multiple approaches:

#### 1. Swagger UI (Interactive Testing)

```bash
# Start the API server
uvicorn src.api.main:app --reload --port 8000

# Open Swagger UI in browser
open http://localhost:8000/docs
```

**Features:**
- Try out endpoints interactively
- See request/response schemas
- Test file uploads
- View validation errors

#### 2. pytest with TestClient

```python
from fastapi.testclient import TestClient
from src.api.main import app

client = TestClient(app)

def test_health_check():
    response = client.get("/api/v1/system/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"

def test_list_mappings():
    response = client.get("/api/v1/mappings/")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
```

#### 3. curl Commands

```bash
# Health check
curl http://localhost:8000/api/v1/system/health

# List mappings
curl http://localhost:8000/api/v1/mappings/

# Upload template
curl -X POST "http://localhost:8000/api/v1/mappings/upload" \
  -F "file=@template.xlsx" \
  -F "mapping_name=test_mapping"

# Detect file format
curl -X POST "http://localhost:8000/api/v1/files/detect" \
  -F "file=@data.txt"
```

#### 4. Python requests Library

```python
import requests

# Health check
response = requests.get("http://localhost:8000/api/v1/system/health")
print(response.json())

# Upload template
with open("template.xlsx", "rb") as f:
    response = requests.post(
        "http://localhost:8000/api/v1/mappings/upload",
        files={"file": f},
        params={"mapping_name": "my_mapping"}
    )
print(response.json())
```

### API Test Coverage

**System Endpoints:**
- ✅ Health check
- ✅ System information

**Mapping Endpoints:**
- ✅ Upload template (Excel/CSV)
- ✅ List all mappings
- ✅ Get mapping by ID
- ✅ Validate mapping
- ✅ Delete mapping

**File Endpoints:**
- ✅ Detect file format
- ✅ Parse file with mapping
- ✅ Compare two files

### Running API Tests

```bash
# Install test dependencies
pip install pytest httpx

# Run API tests
pytest tests/api -v

# Run with coverage
pytest tests/api --cov=src.api --cov-report=html

# Test specific endpoint
pytest tests/api/test_api_mappings.py::test_upload_template -v
```

## Great Expectations Regression Checks (Checkpoint 1)

For BA-configurable regression checks without Python coding, use:

```bash
valdo gx-checkpoint1 \
  --targets config/gx/targets.sample.csv \
  --expectations config/gx/expectations.sample.csv \
  --output reports/gx_checkpoint1_summary.json
```

Behavior:
- Returns non-zero exit code on failed expectations (CI-friendly)
- Supports schema/order, non-null, uniqueness, allowed set, ranges, row count
- Driven by CSV templates in `config/templates/csv/`

See: `docs/GREAT_EXPECTATIONS_CHECKPOINT1.md`.

## CI/CD Integration

### GitLab CI Example

```yaml
test:
  stage: test
  script:
    - pip install -r requirements.txt
    - pytest --cov=src --cov-report=xml --cov-report=term
  coverage: '/TOTAL.*\s+(\d+%)$/'
  artifacts:
    reports:
      coverage_report:
        coverage_format: cobertura
        path: coverage.xml
```

## Troubleshooting

### Tests Not Found

```bash
# Check pytest can find tests
pytest --collect-only

# Verify test discovery
pytest --collect-only -q
```

### Import Errors

```bash
# Ensure PYTHONPATH is set
export PYTHONPATH=$PWD

# Or install package in development mode
pip install -e .
```

### Database Connection Errors

- Ensure tests use mocked connections
- Check that `@patch` decorators are correct
- Verify mock setup in test fixtures

## Best Practices

1. **Isolate tests** - Each test should be independent
2. **Use fixtures** - Reuse common test data
3. **Mock external dependencies** - Don't rely on actual databases
4. **Test edge cases** - Empty files, null values, errors
5. **Keep tests fast** - Unit tests should run in milliseconds
6. **Clear test names** - Describe what is being tested
7. **One assertion per test** - Or closely related assertions
8. **Clean up** - Remove temporary files and resources

## Current Test Coverage

### Unit Tests (tests/unit/)

- ✅ `test_pipe_delimited_parser.py` - 7 tests
- ✅ `test_fixed_width_parser.py` - 5 tests
- ✅ `test_format_detector.py` - 5 tests
- ✅ `test_validator.py` - 5 tests
- ✅ `test_mapping_parser.py` - 5 tests
- ✅ `test_universal_mapping_parser.py` - 8 tests (new!)
- ✅ `test_template_converter.py` - 6 tests (new!)
- ✅ `test_file_comparator.py` - 6 tests
- ✅ `test_config_loader.py` - 4 tests
- ✅ `test_html_reporter.py` - 2 tests
- ✅ `test_transaction.py` - 3 tests
- ✅ `test_threshold.py` - 5 tests

**Total: 61+ unit tests**

### API Tests (tests/api/) - New!

- ✅ `test_api_system.py` - 2 tests
- ✅ `test_api_mappings.py` - 5 tests
- ✅ `test_api_files.py` - 3 tests

**Total: 10+ API tests**

### Integration Tests (tests/integration/)

- ✅ End-to-end file processing
- ✅ Universal mapping workflows
- ✅ API integration tests

## Quick Reference

```bash
# Most common commands
pytest                              # Run all tests
pytest -v                           # Verbose
pytest --cov=src                    # With coverage
pytest tests/unit                   # Unit tests only
pytest -k "parser"                  # Tests matching "parser"
pytest -x                           # Stop on first failure
pytest --lf                         # Run last failed tests
pytest --ff                         # Run failures first
./run_tests.sh --coverage --verbose # Using test runner
```
