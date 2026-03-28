"""Tests for the Web UI router: /ui and /api/v1/runs/history endpoints."""

import json
import re
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Return a TestClient with the full FastAPI app."""
    # Point run-history path to a temp dir so tests are isolated
    import src.api.routers.ui as ui_mod

    monkeypatch.setattr(ui_mod, "_RUN_HISTORY_PATH", tmp_path / "run_history.json")
    from src.api.main import app

    return TestClient(app)


@pytest.fixture()
def client_with_history(tmp_path, monkeypatch):
    """Return a TestClient that has a pre-populated run_history.json."""
    import src.api.routers.ui as ui_mod

    history_file = tmp_path / "run_history.json"
    # Write 25 entries so we can test the cap-at-20 logic
    entries = [
        {
            "run_id": f"run-{i:03d}",
            "suite_name": f"Suite {i}",
            "environment": "UAT",
            "timestamp": f"2024-01-{i:02d}T12:00:00Z",
            "status": "PASS",
            "report_url": f"/uploads/suite_{i}.html",
            "pass_count": 5,
            "fail_count": 0,
            "skip_count": 0,
            "total_count": 5,
        }
        for i in range(1, 26)
    ]
    history_file.write_text(json.dumps(entries), encoding="utf-8")
    monkeypatch.setattr(ui_mod, "_RUN_HISTORY_PATH", history_file)
    from src.api.main import app

    return TestClient(app)


# ---------------------------------------------------------------------------
# /ui endpoint
# ---------------------------------------------------------------------------


def test_ui_returns_200(client):
    """GET /ui must return HTTP 200."""
    response = client.get("/ui")
    assert response.status_code == 200


def test_ui_content_type_is_html(client):
    """GET /ui must return text/html content-type."""
    response = client.get("/ui")
    assert "text/html" in response.headers["content-type"]


def test_ui_contains_quick_test_text(client):
    """The /ui page must contain the text 'Quick Test'."""
    response = client.get("/ui")
    assert "Quick Test" in response.text


def test_ui_contains_recent_runs_text(client):
    """The /ui page must contain the text 'Recent Runs'."""
    response = client.get("/ui")
    assert "Recent Runs" in response.text


def test_ui_has_no_https_urls(client):
    """The /ui page must NOT contain any https:// URLs (no CDN dependencies)."""
    response = client.get("/ui")
    assert "https://" not in response.text


def test_ui_has_no_external_script_src(client):
    """The /ui page must NOT have <script src='http...'>  or external script tags."""
    response = client.get("/ui")
    html = response.text
    # Find all <script src="..."> tags
    external_scripts = re.findall(r'<script[^>]+src=["\']https?://', html, re.IGNORECASE)
    assert external_scripts == [], f"Found external script tags: {external_scripts}"


def test_ui_has_no_external_link_href(client):
    """The /ui page must NOT have <link href='http...'> pointing to external CDN."""
    response = client.get("/ui")
    html = response.text
    # Find all <link href="http..."> tags
    external_links = re.findall(r'<link[^>]+href=["\']https?://', html, re.IGNORECASE)
    assert external_links == [], f"Found external link tags: {external_links}"


# ---------------------------------------------------------------------------
# /api/v1/runs/history endpoint
# ---------------------------------------------------------------------------


def test_run_history_returns_200(client):
    """GET /api/v1/runs/history must return HTTP 200."""
    response = client.get("/api/v1/runs/history")
    assert response.status_code == 200


def test_run_history_returns_json_list(client):
    """GET /api/v1/runs/history must return a JSON array."""
    response = client.get("/api/v1/runs/history")
    data = response.json()
    assert isinstance(data, list)


def test_run_history_empty_when_no_file(client):
    """GET /api/v1/runs/history returns empty list when history file does not exist."""
    response = client.get("/api/v1/runs/history")
    assert response.json() == []


def test_run_history_caps_at_20(client_with_history):
    """GET /api/v1/runs/history returns at most 20 entries even when history has more."""
    response = client_with_history.get("/api/v1/runs/history")
    data = response.json()
    assert len(data) == 20


def test_run_history_most_recent_first(client_with_history):
    """GET /api/v1/runs/history returns most-recent entries first."""
    response = client_with_history.get("/api/v1/runs/history")
    data = response.json()
    # The 25 entries are numbered 1..25; most recent (25) should be first
    assert data[0]["run_id"] == "run-025"
    assert data[-1]["run_id"] == "run-006"


def test_run_history_returns_empty_on_corrupt_file(tmp_path, monkeypatch):
    """GET /api/v1/runs/history returns [] when the history file is corrupt JSON."""
    import src.api.routers.ui as ui_mod

    bad_file = tmp_path / "run_history.json"
    bad_file.write_text("NOT VALID JSON", encoding="utf-8")
    monkeypatch.setattr(ui_mod, "_RUN_HISTORY_PATH", bad_file)
    from src.api.main import app

    client = TestClient(app)
    response = client.get("/api/v1/runs/history")
    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# Mapping Generator tab smoke tests
# ---------------------------------------------------------------------------

def test_ui_contains_mapping_generator_tab(client):
    """GET /ui must contain the Mapping Generator tab button."""
    response = client.get("/ui")
    assert response.status_code == 200
    assert b"Mapping Generator" in response.content


def test_ui_contains_generate_mapping_button(client):
    """GET /ui must contain a Generate Mapping button."""
    response = client.get("/ui")
    assert b"Generate Mapping" in response.content


def test_ui_contains_generate_rules_button(client):
    """GET /ui must contain a Generate Rules button."""
    response = client.get("/ui")
    assert b"Generate Rules" in response.content


def test_ui_contains_rules_type_dropdown(client):
    """GET /ui must contain the BA-friendly rules type option."""
    response = client.get("/ui")
    assert b"BA-friendly" in response.content


# ---------------------------------------------------------------------------
# Tooltip tests
# ---------------------------------------------------------------------------

def _load_ui_html() -> str:
    """Read ui.html + ui.css + ui.js for structural checks."""
    static_dir = Path(__file__).resolve().parent.parent.parent / "src" / "reports" / "static"
    content = (static_dir / "ui.html").read_text(encoding="utf-8")
    css_path = static_dir / "ui.css"
    js_path = static_dir / "ui.js"
    if css_path.exists():
        content += "\n" + css_path.read_text(encoding="utf-8")
    if js_path.exists():
        content += "\n" + js_path.read_text(encoding="utf-8")
    return content


class TestTooltips:
    def test_tooltip_css_rule_present(self):
        html = _load_ui_html()
        assert "[data-tooltip]" in html, "CSS tooltip rule missing"

    def test_validate_button_has_tooltip(self):
        html = _load_ui_html()
        match = re.search(r'<(?:button|select|div|a)[^>]*id="btnValidate"[^>]*>', html, re.DOTALL)
        assert match, "btnValidate element not found"
        assert 'data-tooltip=' in match.group(0), "btnValidate must carry a data-tooltip attribute"

    def test_compare_button_has_tooltip(self):
        html = _load_ui_html()
        match = re.search(r'<(?:button|select|div|a)[^>]*id="btnToggleCompare"[^>]*>', html, re.DOTALL)
        assert match, "btnToggleCompare element not found"
        assert 'data-tooltip=' in match.group(0), "btnToggleCompare must carry a data-tooltip attribute"

    def test_mapping_select_has_tooltip(self):
        html = _load_ui_html()
        match = re.search(r'<(?:button|select|div|a)[^>]*id="mappingSelect"[^>]*>', html, re.DOTALL)
        assert match, "mappingSelect element not found"
        assert 'data-tooltip=' in match.group(0), "mappingSelect must carry a data-tooltip attribute"

    def test_generate_mapping_button_has_tooltip(self):
        html = _load_ui_html()
        match = re.search(r'<(?:button|select|div|a)[^>]*id="btnGenMapping"[^>]*>', html, re.DOTALL)
        assert match, "btnGenMapping element not found"
        assert 'data-tooltip=' in match.group(0), "btnGenMapping must carry a data-tooltip attribute"

    def test_generate_rules_button_has_tooltip(self):
        html = _load_ui_html()
        match = re.search(r'<(?:button|select|div|a)[^>]*id="btnGenRules"[^>]*>', html, re.DOTALL)
        assert match, "btnGenRules element not found"
        assert 'data-tooltip=' in match.group(0), "btnGenRules must carry a data-tooltip attribute"

    def test_rules_type_select_has_tooltip(self):
        html = _load_ui_html()
        match = re.search(r'<(?:button|select|div|a)[^>]*id="rulesTypeSelect"[^>]*>', html, re.DOTALL)
        assert match, "rulesTypeSelect element not found"
        assert 'data-tooltip=' in match.group(0), "rulesTypeSelect must carry a data-tooltip attribute"

    def test_status_badges_have_tooltip_in_js(self):
        """The JS that builds the runs table must add data-tooltip to badges."""
        html = _load_ui_html()
        assert "setAttribute('data-tooltip'" in html or 'setAttribute("data-tooltip"' in html
        assert "All tests passed or were skipped" in html
        assert "One or more tests failed" in html
        assert "Some tests passed and some failed" in html
