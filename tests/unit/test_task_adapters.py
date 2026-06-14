from datetime import datetime, timezone

from src.adapters.api_task_adapter import normalize_api_task_request
from src.adapters.cli_task_adapter import normalize_cli_task_request


def test_cli_and_api_adapters_normalize_equivalent_payload_shape():
    deadline = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
    kwargs = dict(
        intent="validate",
        payload={"mapping_id": "p327", "file": "in.txt"},
        task_id="task-1",
        trace_id="trace-1",
        idempotency_key="idem-1",
        priority="high",
        deadline=deadline,
    )

    cli_req = normalize_cli_task_request(**kwargs)
    api_req = normalize_api_task_request(**kwargs)

    cli_obj = cli_req.model_dump()
    api_obj = api_req.model_dump()
    cli_obj.pop("source")
    api_obj.pop("source")
    assert cli_obj == api_obj


def test_adapter_snapshot_stable():
    req = normalize_api_task_request(
        intent="compare",
        payload={"a": 1},
        task_id="task-snap",
        trace_id="trace-snap",
        idempotency_key="idem-snap",
        priority="normal",
        deadline=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert req.model_dump() == {
        "task_id": "task-snap",
        "trace_id": "trace-snap",
        "idempotency_key": "idem-snap",
        "source": "api",
        "intent": "compare",
        "payload": {"a": 1},
        "priority": "normal",
        "deadline": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "metadata": {},
        "version": "v1",
    }
