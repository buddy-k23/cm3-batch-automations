import os
from io import BytesIO

from fastapi.testclient import TestClient

os.environ.setdefault("API_KEYS", "test-key:admin")

from src.api.main import app


client = TestClient(app)
AUTH_HEADERS = {"X-API-Key": "test-key"}


def test_upload_template_rejects_invalid_extension():
    files = {"file": ("bad.txt", b"x", "text/plain")}
    r = client.post("/api/v1/mappings/upload", files=files, headers=AUTH_HEADERS)
    assert r.status_code == 400


def test_upload_template_csv_success(monkeypatch):
    class DummyConverter:
        def from_csv(self, path, mapping_name=None, file_format=None):
            return {"mapping_name": mapping_name or "dummy_map"}

        def save(self, output_path):
            with open(output_path, "w", encoding="utf-8") as f:
                f.write('{"mapping_name":"dummy_map","source":{"format":"pipe_delimited"},"fields":[]}')

    monkeypatch.setattr("src.api.routers.mappings.TemplateConverter", DummyConverter)

    files = {"file": ("template.csv", b"a,b\n1,2\n", "text/csv")}
    r = client.post(
        "/api/v1/mappings/upload?mapping_name=dummy_map&file_format=pipe_delimited",
        files=files,
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["mapping_id"] == "dummy_map"

    # cleanup generated mapping
    import os
    p = "config/mappings/dummy_map.json"
    if os.path.exists(p):
        os.remove(p)


def test_get_mapping_not_found():
    r = client.get("/api/v1/mappings/definitely_missing_mapping", headers=AUTH_HEADERS)
    assert r.status_code == 404


def test_delete_mapping_not_found():
    r = client.delete("/api/v1/mappings/definitely_missing_mapping", headers=AUTH_HEADERS)
    assert r.status_code == 404
