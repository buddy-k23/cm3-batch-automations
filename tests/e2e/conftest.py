"""Shared fixtures for E2E Playwright tests."""

import os
import pytest

# Base URL for the running server
BASE_URL = os.getenv("CM3_E2E_BASE_URL", "http://127.0.0.1:8000")


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture
def ui_page(page, base_url):
    """Navigate to UI and wait for it to load."""
    page.goto(f"{base_url}/ui")
    page.wait_for_load_state("networkidle")
    return page


@pytest.fixture
def sample_pipe_file(tmp_path):
    """Create a sample pipe-delimited file."""
    f = tmp_path / "sample.txt"
    f.write_text("1|Alice|100\n2|Bob|200\n3|Charlie|300\n")
    return f


@pytest.fixture
def sample_pipe_file_b(tmp_path):
    """Create a second pipe-delimited file with differences."""
    f = tmp_path / "sample_b.txt"
    f.write_text("1|Alice|100\n2|Bob|999\n3|Charlie|300\n")
    return f


@pytest.fixture
def sample_csv_mapping(tmp_path):
    """Create a simple CSV mapping template."""
    f = tmp_path / "template.csv"
    f.write_text("field_name,data_type,position,length\nid,string,1,10\nname,string,11,20\nvalue,string,31,10\n")
    return f
