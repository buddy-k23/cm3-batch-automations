from __future__ import annotations

from pydantic import ValidationError

from src.contracts.task_contracts import ContractValidationError, TaskRequest


def validate_task_request(payload: dict) -> tuple[TaskRequest | None, list[ContractValidationError]]:
    """Validate raw payload into a canonical ``TaskRequest``.

    Args:
        payload: Raw request payload from CLI/API ingress.

    Returns:
        A tuple of ``(task_request, errors)``. Exactly one side is populated.
    """
    try:
        return TaskRequest.model_validate(payload), []
    except ValidationError as exc:
        errors = [
            ContractValidationError(
                code="CONTRACT_VALIDATION_ERROR",
                message=err.get("msg", "invalid payload"),
                path=".".join(str(p) for p in err.get("loc", [])) or None,
            )
            for err in exc.errors()
        ]
        return None, errors
