"""Tests for GET /api/v1/system/ui-config endpoint."""

from fastapi.testclient import TestClient
from src.api.main import app


def test_ui_config_all_false_when_empty():
    app.state.ui_config = {}
    r = TestClient(app).get("/api/v1/system/ui-config")
    assert r.status_code == 200
    tabs = r.json()["tabs"]
    assert tabs["quick"] is False
    assert tabs["downloader"] is False


def test_ui_config_returns_configured_values():
    app.state.ui_config = {
        "tabs": {"quick": True, "runs": True, "mapping": False,
                 "tester": True, "dbcompare": True, "downloader": True},
        "downloader": {"enabled": True},
    }
    r = TestClient(app).get("/api/v1/system/ui-config")
    assert r.status_code == 200
    tabs = r.json()["tabs"]
    assert tabs["quick"] is True
    assert tabs["mapping"] is False
    assert tabs["downloader"] is True


def test_ui_config_downloader_forced_false_when_flag_off():
    """downloader tab is False when downloader.enabled is False in ui.yml."""
    app.state.ui_config = {
        "tabs": {"downloader": True},
        "downloader": {"enabled": False},
    }
    r = TestClient(app).get("/api/v1/system/ui-config")
    assert r.json()["tabs"]["downloader"] is False


def test_ui_config_downloader_false_when_enabled_missing():
    """downloader tab is False when downloader section is absent from ui.yml."""
    app.state.ui_config = {"tabs": {"downloader": True}}
    r = TestClient(app).get("/api/v1/system/ui-config")
    assert r.json()["tabs"]["downloader"] is False
