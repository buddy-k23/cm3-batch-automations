from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class StageConfig(BaseModel):
    enabled: bool = False
    command: str | list[str] | None = None
    log_file: str | None = None
    targets: list[dict[str, Any]] | None = None


class PipelineProfile(BaseModel):
    source_system: str
    stages: dict[str, StageConfig] = Field(default_factory=dict)
