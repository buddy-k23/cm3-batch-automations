#!/usr/bin/env python3
"""Run an end-to-end regression workflow: parse -> validate -> compare.

Configuration is provided via JSON (see config/pipeline/regression_workflow.sample.json).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from src.contracts.regression_workflow import RegressionWorkflowContract
from src.workflows.engine import (
    build_compare_cmd,
    build_parse_cmd,
    build_validate_cmd,
    resolve_path,
    run_subprocess,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _resolve(path_value: str | None) -> Path | None:
    return resolve_path(path_value, PROJECT_ROOT)


def _parse_stage(py: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    out = _resolve(cfg.get("output")) if cfg.get("output") else None
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
    cmd = build_parse_cmd(
        py=py,
        input_file=str(_resolve(cfg["input_file"])),
        mapping=str(_resolve(cfg["mapping"])),
        output=str(out) if out else None,
        fmt=str(cfg["format"]) if cfg.get("format") else None,
        use_chunked=bool(cfg.get("use_chunked")),
        chunk_size=int(cfg.get("chunk_size", 100000)),
    )
    return run_subprocess(cmd, PROJECT_ROOT)


def _validate_stage(py: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    out = _resolve(cfg.get("output")) if cfg.get("output") else None
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
    cmd = build_validate_cmd(
        py=py,
        input_file=str(_resolve(cfg["input_file"])),
        mapping=str(_resolve(cfg["mapping"])),
        rules=str(_resolve(cfg["rules"])) if cfg.get("rules") else None,
        output=str(out) if out else None,
        detailed=bool(cfg.get("detailed", True)),
        strict_fixed_width=bool(cfg.get("strict_fixed_width", False)),
        strict_level=str(cfg.get("strict_level")) if cfg.get("strict_level") else None,
        use_chunked=bool(cfg.get("use_chunked", False)),
        chunk_size=int(cfg.get("chunk_size", 100000)),
        progress=False,
    )
    return run_subprocess(cmd, PROJECT_ROOT)


def _compare_stage(py: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    out = _resolve(cfg.get("output")) if cfg.get("output") else None
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
    cmd = build_compare_cmd(
        py=py,
        baseline_file=str(_resolve(cfg["baseline_file"])),
        current_file=str(_resolve(cfg["current_file"])),
        keys=str(cfg["keys"]) if cfg.get("keys") else None,
        mapping=str(_resolve(cfg["mapping"])) if cfg.get("mapping") else None,
        output=str(out) if out else None,
        detailed=bool(cfg.get("detailed", True)),
        use_chunked=bool(cfg.get("use_chunked", False)),
        chunk_size=int(cfg.get("chunk_size", 100000)),
    )
    return run_subprocess(cmd, PROJECT_ROOT)


def _require(cfg: Dict[str, Any], fields: List[str], stage: str) -> None:
    missing = [f for f in fields if not cfg.get(f)]
    if missing:
        raise ValueError(f"{stage}: missing required field(s): {', '.join(missing)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/pipeline/regression_workflow.sample.json", help="Workflow config JSON path")
    parser.add_argument("--summary-out", default="reports/regression_workflow/summary.json", help="Summary output JSON path")
    args = parser.parse_args()

    cfg_path = _resolve(args.config)
    if not cfg_path or not cfg_path.exists():
        print(f"❌ Config not found: {args.config}")
        return 1

    raw_config = json.loads(cfg_path.read_text(encoding="utf-8"))
    config = RegressionWorkflowContract.model_validate(raw_config).model_dump(by_alias=True)
    py = str(PROJECT_ROOT / ".venv" / "bin" / "python")

    results: Dict[str, Any] = {
        "workflow_name": config.get("name", "regression_workflow"),
        "config_file": str(cfg_path.relative_to(PROJECT_ROOT)) if cfg_path.is_relative_to(PROJECT_ROOT) else str(cfg_path),
        "started_at_epoch": int(time.time()),
        "stages": {},
    }

    fail_fast = bool(config.get("gates", {}).get("fail_fast", True))
    overall_pass = True

    try:
        parse_cfg = config.get("parse", {})
        if parse_cfg.get("enabled"):
            _require(parse_cfg, ["input_file", "mapping"], "parse")
            print("▶ Running parse stage...")
            r = _parse_stage(py, parse_cfg)
            results["stages"]["parse"] = r
            print(r["output"])
            if r["exit_code"] != 0:
                overall_pass = False
                if fail_fast:
                    raise RuntimeError("parse stage failed")

        validate_cfg = config.get("validate", {})
        if validate_cfg.get("enabled"):
            _require(validate_cfg, ["input_file", "mapping"], "validate")
            print("▶ Running validate stage...")
            r = _validate_stage(py, validate_cfg)
            results["stages"]["validate"] = r
            print(r["output"])
            if r["exit_code"] != 0:
                overall_pass = False
                if fail_fast:
                    raise RuntimeError("validate stage failed")

        compare_cfg = config.get("compare", {})
        if compare_cfg.get("enabled"):
            _require(compare_cfg, ["baseline_file", "current_file"], "compare")
            if compare_cfg.get("use_chunked") and not compare_cfg.get("keys"):
                raise ValueError("compare: keys are required when use_chunked=true")
            print("▶ Running compare stage...")
            r = _compare_stage(py, compare_cfg)
            results["stages"]["compare"] = r
            print(r["output"])
            if r["exit_code"] != 0:
                overall_pass = False
                if fail_fast:
                    raise RuntimeError("compare stage failed")

    except Exception as exc:
        results["error"] = str(exc)
        overall_pass = False

    results["overall_status"] = "PASS" if overall_pass else "FAIL"
    results["completed_at_epoch"] = int(time.time())

    summary_path = _resolve(args.summary_out)
    assert summary_path is not None
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    print(f"\n📄 Summary: {summary_path}")
    print(f"🏁 Overall: {results['overall_status']}")
    return 0 if overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
