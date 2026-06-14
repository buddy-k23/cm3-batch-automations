"""Pipeline runner scaffold for source-system regression orchestration."""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass
from src.contracts.pipeline_profile import PipelineProfile
from .sqlloader_adapter import evaluate_sqlloader_stage
from .output_regression_suite import run_output_regression_suite
from .profile_validator import validate_source_profile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


@dataclass
class StepResult:
    """Result of a single pipeline stage execution.

    Attributes:
        name: Stage name (e.g. 'ingest', 'sqlloader').
        status: Outcome string — 'passed', 'failed', 'skipped', or 'dry_run'.
        message: Human-readable detail or error description.
        exit_code: Process exit code (0 = success).
    """

    name: str
    status: str
    message: str = ""
    exit_code: int = 0


class PipelineRunner:
    """Run a source-system pipeline profile.

    Current scope (scaffold):
    - Validate profile shape
    - Optionally execute configured shell commands for stages
    - Emit structured summary for CI usage
    """

    REQUIRED_TOP_LEVEL = ["source_system", "stages"]

    def __init__(self, profile_path: str):
        self.profile_path = Path(profile_path)
        self.profile: Dict[str, Any] = {}

    def load(self) -> Dict[str, Any]:
        """Load and validate the pipeline profile from disk.

        Reads the JSON profile, validates it against ``PipelineProfile``, and
        then runs structural validation via ``_validate_profile``.

        Returns:
            The validated profile as a plain dict.

        Raises:
            ValueError: If the profile is missing required keys or fails
                structural validation.
            json.JSONDecodeError: If the file is not valid JSON.
        """
        raw = json.loads(self.profile_path.read_text())
        try:
            self.profile = PipelineProfile.model_validate(raw).model_dump()
        except Exception as e:
            if "source_system" in str(e):
                raise ValueError("Missing required top-level key: source_system") from e
            raise
        self._validate_profile(self.profile)
        return self.profile

    def _validate_profile(self, profile: Dict[str, Any]) -> None:
        """Validate profile structure and back-fill missing stage defaults.

        Args:
            profile: Parsed profile dict to validate in-place.

        Raises:
            ValueError: If ``validate_source_profile`` reports any issues.
        """
        issues = validate_source_profile(profile)
        if issues:
            raise ValueError("; ".join(issues))

        stages = profile.get("stages", {})
        for key in ["ingest", "sqlloader", "java_batch", "output_validation"]:
            stages.setdefault(key, {"enabled": False})

    def run(self, dry_run: bool = True) -> Dict[str, Any]:
        """Execute (or dry-run) the pipeline stages defined in the profile.

        Stages are processed in the fixed order: ingest → sqlloader →
        java_batch → output_validation.  Execution halts on the first
        failing stage.

        Args:
            dry_run: When True (default), no commands are executed; each
                enabled stage reports what it *would* do.

        Returns:
            Summary dict containing:
                - ``source_system`` — value from the profile.
                - ``profile`` — file path string.
                - ``timestamp`` — ISO-8601 UTC completion time.
                - ``dry_run`` — whether this was a dry run.
                - ``status`` — ``'passed'`` or ``'failed'``.
                - ``steps`` — list of :class:`StepResult` dicts.
        """
        if not self.profile:
            self.load()

        stages = self.profile.get("stages", {})
        ordered = ["ingest", "sqlloader", "java_batch", "output_validation"]
        results: List[StepResult] = []

        for stage_name in ordered:
            stage = stages.get(stage_name, {}) or {}
            enabled = bool(stage.get("enabled", False))
            cmd = stage.get("command")

            if not enabled:
                results.append(StepResult(stage_name, "skipped", "stage disabled"))
                continue

            if dry_run:
                if stage_name == 'sqlloader' and stage.get('log_file'):
                    msg = f"would evaluate sqlloader log: {stage.get('log_file')}"
                elif stage_name == 'output_validation' and stage.get('targets'):
                    msg = f"would run output regression suite for {len(stage.get('targets', []))} target(s)"
                else:
                    msg = f"would run: {cmd}" if cmd else "enabled, no command configured"
                results.append(StepResult(stage_name, "dry_run", msg))
                continue

            if stage_name == 'sqlloader' and stage.get('log_file'):
                eval_out = evaluate_sqlloader_stage(stage)
                if eval_out['status'] == 'passed':
                    results.append(StepResult(stage_name, 'passed', eval_out.get('message', ''), 0))
                    continue
                results.append(StepResult(stage_name, 'failed', eval_out.get('message', ''), 3))
                break

            if stage_name == 'output_validation' and stage.get('targets'):
                out = run_output_regression_suite(stage, dry_run=False)
                if out['status'] == 'passed':
                    results.append(StepResult(stage_name, 'passed', out.get('message', ''), 0))
                    continue
                results.append(StepResult(stage_name, 'failed', out.get('message', ''), 4))
                break

            if not cmd:
                results.append(StepResult(stage_name, "failed", "missing command", 2))
                break

            cmd_args = cmd if isinstance(cmd, list) else shlex.split(str(cmd))
            proc = subprocess.run(cmd_args, text=True, capture_output=True)
            if proc.returncode == 0:
                results.append(StepResult(stage_name, "passed", proc.stdout.strip(), 0))
            else:
                results.append(
                    StepResult(stage_name, "failed", (proc.stderr or proc.stdout).strip(), proc.returncode)
                )
                break

        failed = any(r.status == "failed" for r in results)
        return {
            "source_system": self.profile.get("source_system"),
            "profile": str(self.profile_path),
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "dry_run": dry_run,
            "status": "failed" if failed else "passed",
            "steps": [r.__dict__ for r in results],
        }
