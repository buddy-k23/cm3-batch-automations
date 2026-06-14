from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from src.contracts.task_contracts import TaskRequest


def normalize_api_task_request(intent: str, payload: dict[str, Any], *,
                               task_id: str | None = None,
                               trace_id: str | None = None,
                               idempotency_key: str | None = None,
                               priority: str = "normal",
                               deadline: datetime | None = None) -> TaskRequest:
    """Normalize API input into canonical ``TaskRequest``."""
    return TaskRequest(
        task_id=task_id or str(uuid.uuid4()),
        trace_id=trace_id or str(uuid.uuid4()),
        idempotency_key=idempotency_key,
        source="api",
        intent=intent,
        payload=payload,
        priority=priority,
        deadline=deadline or datetime.now(timezone.utc),
    )
