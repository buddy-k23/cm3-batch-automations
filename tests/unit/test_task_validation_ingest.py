import json
import os
import uuid

from click.testing import CliRunner
from fastapi.testclient import TestClient

os.environ.setdefault("API_KEYS", "test-key:admin")

from src.api.main import app
from src.main import cli


client = TestClient(app)
AUTH_HEADERS = {"X-API-Key": "test-key"}


def test_api_task_submit_invalid_contract_returns_structured_4xx():
    response = client.post("/api/v1/tasks/submit", json={"payload": {"a": 1}}, headers=AUTH_HEADERS)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "errors" in detail
    assert detail["errors"][0]["code"] == "CONTRACT_VALIDATION_ERROR"


def test_api_task_submit_idempotency_deduplicates_and_propagates_trace():
    idem = f"idem-{uuid.uuid4()}"
    payload = {
        "intent": "validate",
        "idempotency_key": idem,
        "payload": {"mapping_id": "p327"},
    }

    first = client.post(
        "/api/v1/tasks/submit",
        json=payload,
        headers={**AUTH_HEADERS, "x-trace-id": "trace-from-header"},
    )
    assert first.status_code == 200
    first_body = first.json()
    assert first.headers["x-trace-id"] == "trace-from-header"

    second = client.post("/api/v1/tasks/submit", json=payload, headers=AUTH_HEADERS)
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["task_id"] == first_body["task_id"]
    assert "duplicate idempotency key" in second_body["warnings"]


def test_cli_submit_task_invalid_json_machine_errors_nonzero():
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "submit-task",
            "--intent",
            "validate",
            "--payload",
            "{not-json}",
            "--machine-errors",
        ],
    )
    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["errors"][0]["code"] == "INVALID_JSON"
