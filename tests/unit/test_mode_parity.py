"""Mode-parity guard: the execution surfaces share ONE validation core.

Arch-review R-04a (#30). The "one engine, four modes" thesis
(`docs/ARCHITECTURE_REVIEW_2026-06-03.md`) requires that the ad-hoc (CLI),
integration/UAT batch (pipeline), and CI surfaces exercise the SAME
validation/comparison core rather than divergent re-implementations. Nothing
guarded that property before; the 2026-02-20 review's finding #5 was exactly a
CLI/API drift. These tests fail fast if a surface stops routing through the
shared core.

What this test pins (the convergence that exists today)
--------------------------------------------------------
* The **API** router (`src/api/routers/files.py`) and the **pipeline** runner
  (`src/pipeline/etl_pipeline_runner.py`) both call the shared services
  `run_validate_service` and `run_multi_record_validate_service`.
* `run_multi_record_validate_service` delegates to the shared
  `MultiRecordValidator` core — the SAME core the CLI multi-record command
  (`src/commands/multi_record_command.py`) and the harness multi-record report
  (`scripts/render_multi_record_html.py`) use directly.

Documented divergence (intentional, pinned so it cannot silently widen)
-----------------------------------------------------------------------
* The single-record **CLI** path (`run_validate_command`) drives the lower-level
  `EnhancedFileValidator` / `ChunkedFileValidator` + `ValidationReporter`
  directly rather than `run_validate_service`. Those validators are the same
  primitives the service composes, so the *core* is shared even though the CLI
  does not go through the service wrapper. R-04b (#31) revisits whether to route
  the CLI through the service for full parity; until then this test records the
  fact so a NEW divergence is visible.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]


def _calls_in_source(src_path: Path) -> set[str]:
    """Return the set of bare function names called anywhere in a module."""
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


# --------------------------------------------------------------------------- #
# API + pipeline route through the shared services
# --------------------------------------------------------------------------- #


def test_api_files_router_calls_shared_validate_services():
    calls = _calls_in_source(_REPO / "src" / "api" / "routers" / "files.py")
    assert "run_validate_service" in calls, (
        "API single-record validate must route through the shared "
        "run_validate_service (parity guard)."
    )
    assert "run_multi_record_validate_service" in calls, (
        "API multi-record validate must route through the shared "
        "run_multi_record_validate_service (parity guard)."
    )


def test_pipeline_runner_calls_shared_validate_services():
    calls = _calls_in_source(_REPO / "src" / "pipeline" / "etl_pipeline_runner.py")
    assert (
        "run_validate_service" in calls
    ), "Pipeline 'validate' step must route through run_validate_service."
    assert "run_multi_record_validate_service" in calls, (
        "Pipeline 'validate_multi_record' step must route through "
        "run_multi_record_validate_service."
    )


# --------------------------------------------------------------------------- #
# The shared service delegates to the shared validator core
# --------------------------------------------------------------------------- #


def test_multi_record_service_delegates_to_shared_validator():
    from src.services import multi_record_validate_service as svc

    source = inspect.getsource(svc.run_multi_record_validate_service)
    assert "MultiRecordValidator" in source, (
        "run_multi_record_validate_service must delegate to the shared "
        "MultiRecordValidator core (the same core the CLI and the harness "
        "multi-record report use)."
    )


def test_cli_multi_record_uses_same_validator_core():
    calls = _calls_in_source(_REPO / "src" / "commands" / "multi_record_command.py")
    assert (
        "MultiRecordValidator" in calls
    ), "CLI multi-record command must use the shared MultiRecordValidator core."


def test_harness_multi_record_report_uses_same_validator_core():
    calls = _calls_in_source(_REPO / "scripts" / "render_multi_record_html.py")
    assert "MultiRecordValidator" in calls, (
        "The harness multi-record report must use the shared "
        "MultiRecordValidator core (no parallel validation implementation)."
    )


# --------------------------------------------------------------------------- #
# Documented divergence: the single-record CLI path (pinned, not asserted-away)
# --------------------------------------------------------------------------- #


def test_single_record_cli_divergence_is_documented_and_pinned():
    """The single-record CLI path does NOT (yet) call run_validate_service.

    This is the known, intentional divergence (see module docstring + R-04b).
    The test pins the current shape: the CLI drives EnhancedFileValidator
    directly. If someone routes the CLI through the service (closing the gap in
    R-04b) OR introduces a *new* third validation path, this assertion changes
    and forces a conscious update — preventing silent drift in either direction.
    """
    calls = _calls_in_source(_REPO / "src" / "commands" / "validate_command.py")
    # Today: CLI uses the lower-level validators directly, not the service.
    assert "run_validate_service" not in calls, (
        "Single-record CLI now calls run_validate_service — parity with "
        "API/pipeline improved. Update this test and the module docstring "
        "(this is the R-04b convergence)."
    )
    assert (
        "EnhancedFileValidator" in calls or "ChunkedFileValidator" in calls
    ), "CLI validate is expected to drive the EnhancedFileValidator/ChunkedFileValidator core."


# --------------------------------------------------------------------------- #
# Mode adapters delegate to the same orchestration core (R-04b)
# --------------------------------------------------------------------------- #


def test_mode_adapter_delegates_to_shared_orchestrator():
    """The mode-aware wrapper (R-04b) must delegate to the SAME engine.

    `scripts/run_e2e_mode.sh` (integration|uat|ci) and the historical
    `scripts/run_e2e_source.sh` must both exec the one orchestrator
    (`scripts.e2e_lib.run_source`) — no mode may fork into a parallel
    orchestration path.
    """
    for wrapper in ("run_e2e_mode.sh", "run_e2e_source.sh"):
        text = (_REPO / "scripts" / wrapper).read_text(encoding="utf-8")
        assert "scripts.e2e_lib.run_source" in text, (
            f"{wrapper} must delegate to the shared scripts.e2e_lib.run_source "
            "orchestrator (mode-parity guard)."
        )


def test_mode_adapter_does_not_invent_a_new_environment():
    """R-04b must not introduce a `uat`/`prod` environment (AGENTS.md: sit/ait).

    The wrapper accepts a UAT *mode* but only sit/ait *environments*; it must
    explicitly reject any other --env value rather than silently inventing one.
    """
    text = (_REPO / "scripts" / "run_e2e_mode.sh").read_text(encoding="utf-8")
    assert "sit|ait)" in text, (
        "run_e2e_mode.sh must validate --env against sit|ait only "
        "(no invented uat/prod environment)."
    )
