"""Output regression orchestration for multi-target validation/compare."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Dict, Any, List


def _run(cmd: List[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, text=True, capture_output=True)
    text = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
    return p.returncode, text.strip()


def run_output_regression_suite(config: Dict[str, Any], dry_run: bool = True) -> Dict[str, Any]:
    """Run output validation/compare across configured targets.

    Config shape:
    {
      "targets": [
        {
          "name": "p327",
          "file": "out/current/p327.txt",
          "mapping": "config/mappings/p327.json",
          "rules": "config/rules/p327_rules.json",          # optional
          "strict_fixed_width": true,                         # optional
          "strict_level": "all",                            # optional
          "report": "reports/p327_validation.html",         # optional
          "baseline_file": "out/baseline/p327.txt",         # optional
          "compare_report": "reports/p327_compare.html"      # optional
        }
      ]
    }
    """
    targets = config.get("targets", []) or []
    if not targets:
        return {"status": "failed", "message": "no output targets configured", "targets": []}

    results: List[Dict[str, Any]] = []
    failed = False

    for t in targets:
        name = t.get("name") or Path(t.get("file", "")).name
        file_path = t.get("file")
        mapping = t.get("mapping")
        if not file_path or not mapping:
            results.append({"name": name, "status": "failed", "message": "file and mapping are required"})
            failed = True
            continue

        report = t.get("report", f"reports/{name}_validation.html")
        rules = t.get("rules")
        strict = bool(t.get("strict_fixed_width", False))
        strict_level = t.get("strict_level", "all")

        cmd = ["python", "-m", "src.main", "validate", "-f", str(file_path), "-m", str(mapping), "-o", str(report)]
        if rules:
            cmd.extend(["-r", str(rules)])
        if strict:
            cmd.extend(["--strict-fixed-width", "--strict-level", str(strict_level)])

        if dry_run:
            item = {"name": name, "status": "dry_run", "validate_command": " ".join(cmd)}
        else:
            rc, out = _run(cmd)
            item = {
                "name": name,
                "status": "passed" if rc == 0 else "failed",
                "validate_command": " ".join(cmd),
                "validate_exit_code": rc,
                "validate_output": out,
            }
            if rc != 0:
                failed = True

        baseline = t.get("baseline_file")
        if baseline:
            compare_report = t.get("compare_report", f"reports/{name}_compare.html")
            compare_cmd = [
                "python",
                "-m",
                "src.main",
                "compare",
                "-f1",
                str(baseline),
                "-f2",
                str(file_path),
                "-o",
                str(compare_report),
            ]
            if dry_run:
                item["compare_status"] = "dry_run"
                item["compare_command"] = " ".join(compare_cmd)
            else:
                rc2, out2 = _run(compare_cmd)
                item["compare_status"] = "passed" if rc2 == 0 else "failed"
                item["compare_command"] = " ".join(compare_cmd)
                item["compare_exit_code"] = rc2
                item["compare_output"] = out2
                if rc2 != 0:
                    failed = True

        results.append(item)

    return {
        "status": "failed" if failed else "passed",
        "message": "output regression suite complete",
        "targets": results,
    }
