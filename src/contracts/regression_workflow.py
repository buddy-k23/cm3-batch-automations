from __future__ import annotations

from pydantic import BaseModel, Field, ConfigDict


class ParseStage(BaseModel):
    enabled: bool = False
    input_file: str | None = None
    mapping: str | None = None
    output: str | None = None
    format: str | None = None
    use_chunked: bool = False
    chunk_size: int = 100000


class ValidateStage(BaseModel):
    enabled: bool = False
    input_file: str | None = None
    mapping: str | None = None
    rules: str | None = None
    output: str | None = None
    detailed: bool = True
    strict_fixed_width: bool = False
    strict_level: str | None = None
    use_chunked: bool = False
    chunk_size: int = 100000


class CompareStage(BaseModel):
    enabled: bool = False
    baseline_file: str | None = None
    current_file: str | None = None
    keys: str | None = None
    mapping: str | None = None
    output: str | None = None
    detailed: bool = True
    use_chunked: bool = False
    chunk_size: int = 100000


class WorkflowGates(BaseModel):
    fail_fast: bool = True


class RegressionWorkflowContract(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = "regression_workflow"
    parse: ParseStage = ParseStage()
    validate_stage: ValidateStage = Field(default_factory=ValidateStage, alias="validate")
    compare: CompareStage = CompareStage()
    gates: WorkflowGates = WorkflowGates()
