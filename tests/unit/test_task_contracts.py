from datetime import datetime, timezone

from src.contracts.task_contracts import ContractValidationError, TaskRequest, TaskResult


def test_task_request_minimal_shape():
    req = TaskRequest(
        task_id="task-1",
        trace_id="trace-1",
        source="cli",
        intent="validate",
        payload={"mapping_id": "p327"},
        priority="normal",
        deadline=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert req.task_id == "task-1"
    assert req.intent == "validate"
    assert req.version == "v1"


def test_task_result_with_error_payload():
    result = TaskResult(
        task_id="task-1",
        trace_id="trace-1",
        status="failed",
        result={},
        errors=[ContractValidationError(code="VALIDATION_FAILED", message="input invalid")],
    )
    assert result.status == "failed"
    assert len(result.errors) == 1
    assert result.errors[0].code == "VALIDATION_FAILED"
