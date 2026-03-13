from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TaskSource = Literal["cli", "api", "internal"]
TaskPriority = Literal["low", "normal", "high", "urgent"]
TaskStatus = Literal["queued", "running", "succeeded", "failed", "dead-letter", "cancelled"]


class ContractValidationError(BaseModel):
    """Machine-readable contract validation error payload."""

    code: str
    message: str
    path: str | None = None


class TaskRequest(BaseModel):
    """Canonical task request for all ingest boundaries."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    trace_id: str
    idempotency_key: str | None = None
    source: TaskSource
    intent: str
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: TaskPriority = "normal"
    deadline: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)
    version: Literal["v1"] = "v1"


class TaskResult(BaseModel):
    """Canonical task result emitted by orchestration components."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    trace_id: str
    status: TaskStatus
    result: dict[str, Any] = Field(default_factory=dict)
    errors: list[ContractValidationError] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    version: Literal["v1"] = "v1"
