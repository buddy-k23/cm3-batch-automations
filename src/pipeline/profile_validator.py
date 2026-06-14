"""Validation for source-system pipeline profile configuration."""

from __future__ import annotations

from typing import Dict, Any, List


REQUIRED_TOP_LEVEL = ["source_system", "stages"]
REQUIRED_STAGES = ["ingest", "sqlloader", "java_batch", "output_validation"]


def validate_source_profile(profile: Dict[str, Any]) -> List[str]:
    """Return a list of profile validation issues (empty list means valid)."""
    issues: List[str] = []

    for key in REQUIRED_TOP_LEVEL:
        if key not in profile:
            issues.append(f"Missing required top-level key: {key}")

    if "stages" not in profile or not isinstance(profile.get("stages"), dict):
        issues.append("'stages' must be an object")
        return issues

    stages = profile["stages"]
    for s in REQUIRED_STAGES:
        if s not in stages:
            issues.append(f"Missing required stage: {s}")

    for name, stage in stages.items():
        if not isinstance(stage, dict):
            issues.append(f"stage '{name}' must be an object")
            continue
        if "enabled" in stage and not isinstance(stage.get("enabled"), bool):
            issues.append(f"stage '{name}.enabled' must be boolean")

    # sqlloader checks
    sqlloader = stages.get("sqlloader", {}) if isinstance(stages, dict) else {}
    if sqlloader.get("enabled") and sqlloader.get("log_file"):
        for k in ["max_rejected", "max_discarded"]:
            if k in sqlloader:
                try:
                    int(sqlloader[k])
                except Exception:
                    issues.append(f"sqlloader.{k} must be integer")

    # output_validation checks
    outv = stages.get("output_validation", {}) if isinstance(stages, dict) else {}
    targets = outv.get("targets")
    if outv.get("enabled") and targets is not None:
        if not isinstance(targets, list):
            issues.append("output_validation.targets must be an array")
        else:
            for i, t in enumerate(targets):
                if not isinstance(t, dict):
                    issues.append(f"output_validation.targets[{i}] must be an object")
                    continue
                if "file" not in t:
                    issues.append(f"output_validation.targets[{i}].file is required")
                if "mapping" not in t:
                    issues.append(f"output_validation.targets[{i}].mapping is required")

    return issues
