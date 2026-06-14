"""Pytest configuration and fixtures."""

import pytest
import tempfile
import os
import pandas as pd
from datetime import datetime
import uuid
from dotenv import load_dotenv

# Load .env file for database credentials
load_dotenv()


@pytest.fixture
def sample_pipe_delimited_file():
    """Create a temporary pipe-delimited file."""
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write("id|name|value\n")
        f.write("1|Alice|100\n")
        f.write("2|Bob|200\n")
        f.write("3|Charlie|300\n")
        temp_file = f.name
    
    yield temp_file
    
    # Cleanup
    if os.path.exists(temp_file):
        os.unlink(temp_file)


@pytest.fixture
def sample_fixed_width_file():
    """Create a temporary fixed-width file."""
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write("001  Alice    100\n")
        f.write("002  Bob      200\n")
        f.write("003  Charlie  300\n")
        temp_file = f.name
    
    yield temp_file
    
    # Cleanup
    if os.path.exists(temp_file):
        os.unlink(temp_file)


@pytest.fixture
def sample_dataframe():
    """Create a sample DataFrame for testing."""
    return pd.DataFrame({
        'id': [1, 2, 3, 4, 5],
        'name': ['Alice', 'Bob', 'Charlie', 'Diana', 'Edward'],
        'value': [100, 200, 300, 400, 500],
        'status': ['ACTIVE', 'ACTIVE', 'INACTIVE', 'ACTIVE', 'SUSPENDED']
    })


@pytest.fixture
def sample_mapping_dict():
    """Create a sample mapping dictionary."""
    return {
        'mapping_name': 'test_mapping',
        'version': '1.0.0',
        'description': 'Test mapping for unit tests',
        'source': {
            'type': 'file',
            'format': 'pipe_delimited'
        },
        'target': {
            'type': 'database',
            'table_name': 'TEST_TABLE'
        },
        'mappings': [
            {
                'source_column': 'id',
                'target_column': 'ID',
                'data_type': 'number',
                'required': True,
                'transformations': [],
                'validation_rules': [{'type': 'not_null'}]
            },
            {
                'source_column': 'name',
                'target_column': 'NAME',
                'data_type': 'string',
                'required': True,
                'transformations': [{'type': 'trim'}, {'type': 'upper'}],
                'validation_rules': [
                    {'type': 'not_null'},
                    {'type': 'max_length', 'parameters': {'length': 50}}
                ]
            }
        ],
        'key_columns': ['id']
    }


# Global variables to track test run
_test_run_id = None
_test_results = []


def pytest_sessionstart(session):
    """Called at the start of the test session."""
    global _test_run_id, _test_results
    _test_run_id = str(uuid.uuid4())
    _test_results = []


def pytest_runtest_logreport(report):
    """Called after each test phase (setup, call, teardown)."""
    if report.when == "call":  # Only log the actual test execution
        _test_results.append({
            "name": report.nodeid,
            "type": "e2e" if "e2e" in report.nodeid else "unit",
            "status": "passed" if report.passed else ("failed" if report.failed else "skipped"),
            "duration_secs": round(report.duration, 2),
            "rows_processed": None,
            "error_count": 0 if report.passed else 1,
            "report_path": None,
        })


def pytest_sessionfinish(session, exitstatus):
    """Called at the end of the test session - write to database."""
    global _test_run_id, _test_results
    
    if not _test_results:
        return
    
    try:
        from src.services.run_history_service import write_run_to_db
        
        # Count results
        pass_count = sum(1 for r in _test_results if r["status"] == "passed")
        fail_count = sum(1 for r in _test_results if r["status"] == "failed")
        skip_count = sum(1 for r in _test_results if r["status"] == "skipped")
        
        # Create run entry
        run_entry = {
            "run_id": _test_run_id,
            "suite_name": "pytest",
            "environment": os.getenv("TEST_ENV", "local"),
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "status": "passed" if fail_count == 0 else "failed",
            "pass_count": pass_count,
            "fail_count": fail_count,
            "skip_count": skip_count,
            "total_count": len(_test_results),
            "report_url": None,
            "archive_path": None,
            "quality_score": round((pass_count / len(_test_results) * 100), 2) if _test_results else 0.0,
        }
        
        # Write to database
        write_run_to_db(run_entry, _test_run_id, _test_results)
        print(f"\n[OK] Test results written to database (run_id: {_test_run_id})")

    except Exception as e:
        print(f"\n[WARN] Failed to write test results to database: {e}")
