"""Tests for mapping and rules upload via API — issue #101.

Covers CSV mapping upload, listing uploaded mappings, and CSV rules upload.
"""

import json
import os
from pathlib import Path

os.environ.setdefault("API_KEYS", "test-key:admin")

import pytest
from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app)
AUTH = {"X-API-Key": "test-key"}


class TestMappingUpload:
    """API upload tests for issue #101."""

    def test_upload_csv_mapping_template(self, monkeypatch, tmp_path):
        """POST /api/v1/mappings/upload with a CSV template should succeed."""
        mapping_output = {
            "mapping_name": "csv_upload_test",
            "source": {"format": "csv"},
            "fields": [{"name": "col1"}, {"name": "col2"}],
        }

        class DummyConverter:
            def from_csv(self, path, mapping_name=None, file_format=None):
                return {"mapping_name": mapping_name or "csv_upload_test"}

            def save(self, output_path):
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(mapping_output, f)

        monkeypatch.setattr(
            "src.api.routers.mappings.TemplateConverter", DummyConverter
        )
        monkeypatch.setattr("src.api.routers.mappings.MAPPINGS_DIR", tmp_path)

        files = {"file": ("template.csv", b"col1,col2\n1,2\n", "text/csv")}
        r = client.post(
            "/api/v1/mappings/upload?mapping_name=csv_upload_test&file_format=csv",
            files=files,
            headers=AUTH,
        )
        assert r.status_code == 200
        payload = r.json()
        assert payload["mapping_id"] == "csv_upload_test"
        assert "converted" in payload["message"].lower()
        assert (tmp_path / "csv_upload_test.json").exists()

    def test_list_mappings_includes_uploaded(self, monkeypatch, tmp_path):
        """GET /api/v1/mappings/ should include a previously saved mapping."""
        mapping_data = {
            "mapping_name": "listed_mapping",
            "version": "1.0.0",
            "source": {"format": "pipe_delimited"},
            "fields": [{"name": "a"}, {"name": "b"}],
            "metadata": {"created_date": "2026-03-25"},
        }
        (tmp_path / "listed_mapping.json").write_text(
            json.dumps(mapping_data), encoding="utf-8"
        )
        monkeypatch.setattr("src.api.routers.mappings.MAPPINGS_DIR", tmp_path)

        r = client.get("/api/v1/mappings/", headers=AUTH)
        assert r.status_code == 200
        items = r.json()
        names = [item["mapping_name"] for item in items]
        assert "listed_mapping" in names

    def test_upload_csv_rules_template(self, monkeypatch, tmp_path):
        """POST /api/v1/rules/upload with a CSV template should succeed."""

        class DummyConverter:
            rules_config = {"metadata": {"name": "csv_rules"}, "rules": []}

            def from_csv(self, path):
                return self.rules_config

            def save(self, output_path):
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(self.rules_config, f)

        monkeypatch.setattr(
            "src.api.routers.rules.BARulesTemplateConverter", DummyConverter
        )
        monkeypatch.setattr("src.api.routers.rules.RULES_DIR", tmp_path)

        files = {"file": ("rules.csv", b"Rule ID,Rule Name\nR1,Test\n", "text/csv")}
        r = client.post(
            "/api/v1/rules/upload?rules_name=csv_rules&rules_type=ba_friendly",
            files=files,
            headers=AUTH,
        )
        assert r.status_code == 200
        payload = r.json()
        assert payload["rules_id"] == "csv_rules"
        assert (tmp_path / "csv_rules.json").exists()
