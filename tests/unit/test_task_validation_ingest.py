import json

from click.testing import CliRunner
from fastapi import HTTPException

from src.api.routers.tasks import submit_task
from src.main import cli


def test_api_task_submit_invalid_contract_returns_structured_4xx():
    try:
        import asyncio
        asyncio.run(submit_task({"payload": {"a": 1}}))
        assert False, "expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 422
        detail = exc.detail
        assert "errors" in detail
        assert detail["errors"][0]["code"] == "CONTRACT_VALIDATION_ERROR"


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
