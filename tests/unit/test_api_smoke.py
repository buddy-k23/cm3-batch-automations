import os

from fastapi.testclient import TestClient

from src.api.main import app


_api_keys = os.getenv("API_KEYS", "")
if "dev-key" not in {k.split(":", 1)[0].strip() for k in _api_keys.split(",") if k.strip()}:
    os.environ["API_KEYS"] = f"{_api_keys},dev-key:admin" if _api_keys else "dev-key:admin"

client = TestClient(app)
API_HEADERS = {"X-API-Key": "dev-key"}


def test_root_endpoint():
    r = client.get("/")
    assert r.status_code == 200
    payload = r.json()
    assert payload["name"] == "Valdo API"
    assert payload["docs"] == "/docs"


def test_system_health_endpoint():
    r = client.get("/api/v1/system/health")
    assert r.status_code == 200
    payload = r.json()
    assert payload["status"] == "healthy"
    assert "timestamp" in payload


def test_system_info_endpoint():
    r = client.get("/api/v1/system/info", headers=API_HEADERS)
    assert r.status_code == 200
    payload = r.json()
    assert payload["api_version"] == "1.0.0"
    assert "pipe_delimited" in payload["supported_formats"]


def test_files_detect_endpoint_with_pipe_file():
    content = b"customer_id|name\n1|Alice\n2|Bob\n"
    files = {"file": ("sample.txt", content, "text/plain")}
    r = client.post("/api/v1/files/detect", files=files, headers=API_HEADERS)
    assert r.status_code == 200
    payload = r.json()
    assert "format" in payload
    assert "confidence" in payload
