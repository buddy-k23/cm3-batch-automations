"""Service layer for loading and executing suite-based validation runs.

Suites are defined as YAML files stored in ``config/suites/``.  Each file
maps to a :class:`~src.pipeline.suite_config.SuiteDefinition`.  This service
provides three public entry points:

* :func:`load_suite_definitions` — parse all YAML files in a directory.
* :func:`list_suites` — return lightweight metadata for the UI / CLI list.
* :func:`run_suite_by_name` — execute one named suite and return a result dict.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

import yaml

from src.pipeline.suite_config import SuiteDefinition, StepDefinition
from src.services.notification_service import notify_suite_result
from src.services.validate_service import run_validate_service

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_YAML_SUFFIXES = {".yaml", ".yml"}


def _default_suites_dir() -> str:
    """Return the canonical suites directory path relative to the project root.

    The project root is inferred as three levels above this file:
    ``src/services/scheduler_service.py`` → ``src/services/`` → ``src/`` → root.

    Returns:
        Absolute path string for ``<project_root>/config/suites``.
    """
    return str(Path(__file__).parent.parent.parent / "config" / "suites")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_suite_definitions(suites_dir: str | None = None) -> list[SuiteDefinition]:
    """Parse all YAML suite files in *suites_dir* and return validated models.

    Files that cannot be parsed (invalid YAML or schema errors) are skipped
    with a WARNING log entry so the application remains operational.

    Args:
        suites_dir: Filesystem path to the directory containing ``*.yaml`` /
            ``*.yml`` suite files.  Defaults to ``config/suites/`` relative to
            the project root when ``None``.

    Returns:
        List of :class:`~src.pipeline.suite_config.SuiteDefinition` instances,
        one per successfully parsed YAML file.  Returns ``[]`` when the
        directory does not exist or contains no valid suite files.
    """
    resolved = Path(suites_dir) if suites_dir is not None else Path(_default_suites_dir())

    if not resolved.exists():
        return []

    suites: list[SuiteDefinition] = []
    for path in sorted(resolved.iterdir()):
        if path.suffix not in _YAML_SUFFIXES:
            continue
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("Top-level YAML value must be a mapping")
            suites.append(SuiteDefinition.model_validate(raw))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping suite file %s — %s", path.name, exc)

    return suites


def list_suites(suites_dir: str | None = None) -> list[dict[str, Any]]:
    """Return lightweight metadata for all configured suites.

    Suitable for populating list views in the UI or CLI.

    Args:
        suites_dir: Optional override for the suites directory path.
            Defaults to ``config/suites/``.

    Returns:
        List of dicts, each containing:

        * ``name`` — suite identifier string.
        * ``description`` — description string or ``None``.
        * ``step_count`` — number of steps declared in the suite.
    """
    definitions = load_suite_definitions(suites_dir=suites_dir)
    return [
        {
            "name": suite.name,
            "description": suite.description,
            "step_count": len(suite.steps),
        }
        for suite in definitions
    ]


def run_suite_by_name(
    suite_name: str,
    suites_dir: str | None = None,
) -> dict[str, Any]:
    """Execute the named suite and return a structured result dict.

    Looks up *suite_name* among all YAML files in *suites_dir*, then runs
    each step sequentially.  Validation steps call
    :func:`~src.services.validate_service.run_validate_service`.

    The suite is considered **passed** when every step completes with
    ``error_count == 0``.  Any step with ``error_count > 0`` or that raises
    an exception marks the suite as **failed**.

    Args:
        suite_name: Name of the suite to run (matches the ``name`` field in
            the YAML file, not the filename).
        suites_dir: Optional override for the suites directory path.
            Defaults to ``config/suites/``.

    Returns:
        Dict with the following keys:

        * ``suite_name`` — echoed from the request.
        * ``run_id`` — UUID string generated for this execution.
        * ``status`` — ``"passed"``, ``"failed"``, or ``"error"``.
        * ``message`` — human-readable summary (populated on error/not-found).
        * ``step_results`` — list of per-step result dicts.
    """
    definitions = load_suite_definitions(suites_dir=suites_dir)
    matched = next((s for s in definitions if s.name == suite_name), None)

    if matched is None:
        return {
            "suite_name": suite_name,
            "run_id": "",
            "status": "error",
            "message": f"Suite '{suite_name}' not found in {suites_dir or _default_suites_dir()}",
            "step_results": [],
        }

    run_id = str(uuid.uuid4())
    step_results: list[dict[str, Any]] = []
    overall_failed = False

    for step in matched.steps:
        step_result = _execute_step(step)
        step_results.append(step_result)
        if step_result["status"] in ("failed", "error"):
            overall_failed = True

    result = {
        "suite_name": suite_name,
        "run_id": run_id,
        "status": "failed" if overall_failed else "passed",
        "message": "",
        "step_results": step_results,
    }

    # Send notifications if configured (errors are caught internally).
    notify_suite_result(suite_name, result, matched.notifications)

    return result


# ---------------------------------------------------------------------------
# Internal step executor
# ---------------------------------------------------------------------------


def _execute_step(step: StepDefinition) -> dict[str, Any]:
    """Run a single :class:`~src.pipeline.suite_config.StepDefinition`.

    Dispatches to the appropriate service based on ``step.type``.

    Args:
        step: The step configuration to execute.

    Returns:
        Dict containing:

        * ``name`` — step name.
        * ``type`` — step type (``"validate"`` or ``"compare"``).
        * ``status`` — ``"passed"``, ``"failed"``, or ``"error"``.
        * ``error_count`` — number of validation errors (0 for non-validate).
        * ``total_rows`` — rows processed (0 on error).
        * ``detail`` — error message string (empty on success).
    """
    base: dict[str, Any] = {
        "name": step.name,
        "type": step.type,
        "status": "passed",
        "error_count": 0,
        "total_rows": 0,
        "detail": "",
    }

    try:
        if step.type == "validate":
            svc_result = run_validate_service(
                file=step.file_pattern,
                mapping=step.mapping,
                rules=step.rules,
            )
            base["error_count"] = svc_result.get("error_count", 0) or 0
            base["total_rows"] = svc_result.get("total_rows", 0) or 0
            if base["error_count"] > 0:
                base["status"] = "failed"
                base["detail"] = f"{base['error_count']} validation error(s)"
        elif step.type == "compare":
            # Compare steps are reserved for future implementation.
            # Log a warning and mark the step as skipped rather than failing.
            logger.warning(
                "Step '%s': compare type is not yet implemented — skipping.", step.name
            )
            base["status"] = "passed"
            base["detail"] = "compare step: not yet implemented, skipped"
    except Exception as exc:  # noqa: BLE001
        base["status"] = "error"
        base["detail"] = str(exc)
        logger.exception("Step '%s' raised an exception: %s", step.name, exc)

    return base
