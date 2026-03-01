"""Tests for POST /api/v1/rules/upload endpoint."""

import json
from pathlib import Path
from fastapi.testclient import TestClient
from src.api.main import app

client = TestClient(app)


def test_upload_rules_rejects_invalid_extension():
    files = {"file": ("bad.txt", b"x", "text/plain")}
    r = client.post("/api/v1/rules/upload", files=files)
    assert r.status_code == 400


def test_upload_rules_rejects_invalid_rules_type():
    files = {"file": ("rules.csv", b"x", "text/csv")}
    r = client.post("/api/v1/rules/upload?rules_type=garbage", files=files)
    assert r.status_code == 422


def test_upload_rules_ba_friendly_csv_success(monkeypatch, tmp_path):
    class DummyConverter:
        rules_config = {"metadata": {"name": "test_rules"}, "rules": []}
        def from_csv(self, path):
            return self.rules_config
        def save(self, output_path):
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w") as f:
                json.dump(self.rules_config, f)

    monkeypatch.setattr("src.api.routers.rules.BARulesTemplateConverter", DummyConverter)
    monkeypatch.setattr("src.api.routers.rules.RULES_DIR", tmp_path)

    files = {"file": ("rules.csv", b"Rule ID,Rule Name\n", "text/csv")}
    r = client.post("/api/v1/rules/upload?rules_name=test_rules&rules_type=ba_friendly", files=files)
    assert r.status_code == 200
    payload = r.json()
    assert payload["rules_id"] == "test_rules"
    assert "created" in payload["message"].lower()
    assert (tmp_path / "test_rules.json").exists()


def test_upload_rules_technical_xlsx_success(monkeypatch, tmp_path):
    class DummyConverter:
        rules_config = {"metadata": {"name": "tech_rules"}, "rules": []}
        def from_excel(self, path):
            return self.rules_config
        def save(self, output_path):
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w") as f:
                json.dump(self.rules_config, f)

    monkeypatch.setattr("src.api.routers.rules.RulesTemplateConverter", DummyConverter)
    monkeypatch.setattr("src.api.routers.rules.RULES_DIR", tmp_path)

    files = {"file": ("rules.xlsx", b"PK", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    r = client.post("/api/v1/rules/upload?rules_name=tech_rules&rules_type=technical", files=files)
    assert r.status_code == 200
    assert r.json()["rules_id"] == "tech_rules"
    assert (tmp_path / "tech_rules.json").exists()


def test_upload_rules_defaults_to_ba_friendly(monkeypatch, tmp_path):
    called_with = {}

    class DummyConverter:
        rules_config = {"metadata": {"name": "default_rules"}, "rules": []}
        def from_csv(self, path):
            called_with["converter"] = "ba_friendly"
            return self.rules_config
        def save(self, output_path):
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w") as f:
                json.dump({}, f)

    monkeypatch.setattr("src.api.routers.rules.BARulesTemplateConverter", DummyConverter)
    monkeypatch.setattr("src.api.routers.rules.RULES_DIR", tmp_path)

    files = {"file": ("rules.csv", b"x", "text/csv")}
    r = client.post("/api/v1/rules/upload?rules_name=default_rules", files=files)
    assert r.status_code == 200
    assert called_with.get("converter") == "ba_friendly"


def test_download_rules_not_found():
    """GET /api/v1/rules/missing.json must return 404."""
    r = client.get("/api/v1/rules/definitely_missing_rules.json")
    assert r.status_code == 404


def test_download_rules_success(tmp_path, monkeypatch):
    """GET /api/v1/rules/<id>.json must return the JSON file."""
    import json as _json
    import src.api.routers.rules as rules_mod

    # Write a test rules file to tmp_path
    test_file = tmp_path / "dl_test_rules.json"
    test_file.write_text(_json.dumps({"rules": []}))

    monkeypatch.setattr(rules_mod, "RULES_DIR", tmp_path)

    r = client.get("/api/v1/rules/dl_test_rules.json")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
