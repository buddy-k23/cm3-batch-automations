#!/usr/bin/env python3
"""Wave 5 multi-workbook validation harness.

Runs e2e pipeline across multiple workbook/template scenarios and emits a
consolidated run report for promotion evidence and go-live readiness review.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Scenario:
    name: str
    input_path: str
    rules_input: str | None
    file_config_input: str | None
    derive_missing: bool = False
    derive_tx_mode: str = "sheet_name"
    derive_source_mode: str = "definition"
    promotion_gate: bool = True
    warn_acceptance_mode: str = "review-required"
    warn_acceptance_max_open: int = 0
    warn_signoff_ba: bool = False
    warn_signoff_qa: bool = False
    warn_waiver_age_days: int | None = None
    completeness_ba: float = 100.0
    completeness_qa: float = 100.0
    completeness_required: float = 100.0


def read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def bool_flag(enabled: bool, true_flag: str, false_flag: str) -> list[str]:
    return [true_flag if enabled else false_flag]


def run_scenario(base_dir: Path, out_root: Path, scenario: Scenario) -> dict[str, Any]:
    scenario_out = out_root / scenario.name
    scenario_out.mkdir(parents=True, exist_ok=True)

    cmd = [
        "python3",
        "tools/e2e_runner.py",
        "--input",
        scenario.input_path,
        "--out-dir",
        str(scenario_out),
        "--warn-acceptance-mode",
        scenario.warn_acceptance_mode,
        "--warn-acceptance-max-open",
        str(scenario.warn_acceptance_max_open),
        "--template-completeness-ba",
        str(scenario.completeness_ba),
        "--template-completeness-qa",
        str(scenario.completeness_qa),
        "--required-columns-completeness",
        str(scenario.completeness_required),
        "--derive-transaction-code-mode",
        scenario.derive_tx_mode,
        "--derive-source-field-mode",
        scenario.derive_source_mode,
    ]

    if scenario.rules_input:
        cmd.extend(["--rules-input", scenario.rules_input])
    if scenario.file_config_input:
        cmd.extend(["--file-config-input", scenario.file_config_input])

    cmd.extend(bool_flag(scenario.derive_missing, "--derive-missing", "--no-derive-missing"))
    cmd.extend(bool_flag(scenario.promotion_gate, "--promotion-gate", "--no-promotion-gate"))
    cmd.extend(bool_flag(scenario.warn_signoff_ba, "--warn-signoff-ba", "--no-warn-signoff-ba"))
    cmd.extend(bool_flag(scenario.warn_signoff_qa, "--warn-signoff-qa", "--no-warn-signoff-qa"))

    if scenario.warn_waiver_age_days is not None:
        cmd.extend(["--warn-waiver-age-days", str(scenario.warn_waiver_age_days)])

    completed = subprocess.run(cmd, cwd=base_dir, capture_output=True, text=True)

    (scenario_out / "harness.stdout.log").write_text(completed.stdout)
    (scenario_out / "harness.stderr.log").write_text(completed.stderr)

    conversion = read_json(scenario_out / "conversion-report.json") or {}
    promotion = read_json(scenario_out / "promotion-evidence.json") or {}
    summary = read_json(scenario_out / "summary-report.json") or {}
    template_ingest = read_json(scenario_out / "template-ingest.json") or {}

    pre_gate_status = (promotion.get("evaluation") or {}).get("preGateStatus")
    gate_decision = (promotion.get("evaluation") or {}).get("decision")
    reasons = (promotion.get("evaluation") or {}).get("reasons") or []

    return {
        "scenario": scenario.name,
        "input": scenario.input_path,
        "exitCode": completed.returncode,
        "conversionStatus": conversion.get("status"),
        "preGateStatus": pre_gate_status,
        "promotionDecision": gate_decision,
        "errorCount": ((conversion.get("summary") or {}).get("errors", 0)),
        "warnCount": ((conversion.get("summary") or {}).get("warnings", 0)),
        "mappingRows": ((conversion.get("summary") or {}).get("mappingRows", 0)),
        "ruleRows": ((conversion.get("summary") or {}).get("ruleRows", 0)),
        "templateSections": (template_ingest.get("sections") or {}),
        "goLiveReadiness": {
            "promotionPass": gate_decision == "PASS",
            "noHardErrors": ((conversion.get("summary") or {}).get("errors", 0) == 0),
            "warnsWithinPolicy": not any(r.get("blocking") for r in reasons if r.get("severity") != "INFO"),
            "completenessProvided": all(
                v is not None
                for v in [scenario.completeness_ba, scenario.completeness_qa, scenario.completeness_required]
            ),
        },
        "blockingReasons": [r for r in reasons if r.get("blocking")],
        "artifacts": {
            "scenarioOutDir": str(scenario_out),
            "conversionReport": str(scenario_out / "conversion-report.json"),
            "promotionEvidence": str(scenario_out / "promotion-evidence.json"),
            "summaryReport": str(scenario_out / "summary-report.json"),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Wave 5 multi-workbook validation scenarios")
    parser.add_argument("--out-root", default="generated/trials/wave5", help="Output root for scenario runs")
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent.parent
    out_root = (base_dir / args.out_root).resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    scenarios = [
        Scenario(
            name="mapping-t-external",
            input_path="/Users/buddy/.openclaw/workspace/fabric-platform/docs/mapping-docs-excel/mapping-t.xlsx",
            rules_input=None,
            file_config_input="examples/templates/file-config-template.csv",
            derive_missing=True,
            derive_tx_mode="sheet_name",
            derive_source_mode="lineage_hardening",
            promotion_gate=True,
            warn_acceptance_mode="review-required",
            warn_acceptance_max_open=20,
            warn_signoff_ba=True,
            warn_signoff_qa=True,
            warn_waiver_age_days=1,
            completeness_ba=96.0,
            completeness_qa=96.0,
            completeness_required=100.0,
        ),
        Scenario(
            name="fixture-clean-pass",
            input_path="tests/fixtures/wave5/mapping-clean-template.csv",
            rules_input="tests/fixtures/wave5/rules-clean-template.csv",
            file_config_input="examples/templates/file-config-template.csv",
            derive_missing=False,
            promotion_gate=True,
            warn_acceptance_mode="review-required",
            warn_acceptance_max_open=0,
            warn_signoff_ba=False,
            warn_signoff_qa=False,
            completeness_ba=100.0,
            completeness_qa=100.0,
            completeness_required=100.0,
        ),
        Scenario(
            name="fixture-warn-pass-with-signoff",
            input_path="tests/fixtures/wave5/mapping-warn-template.csv",
            rules_input="tests/fixtures/wave5/rules-warn-template.csv",
            file_config_input="examples/templates/file-config-template.csv",
            derive_missing=True,
            derive_tx_mode="placeholder",
            derive_source_mode="placeholder",
            promotion_gate=True,
            warn_acceptance_mode="review-required",
            warn_acceptance_max_open=20,
            warn_signoff_ba=True,
            warn_signoff_qa=True,
            warn_waiver_age_days=1,
            completeness_ba=97.0,
            completeness_qa=96.0,
            completeness_required=100.0,
        ),
    ]

    results = [run_scenario(base_dir, out_root, s) for s in scenarios]

    totals = {
        "scenarios": len(results),
        "promotionPass": sum(1 for r in results if r.get("promotionDecision") == "PASS"),
        "promotionFail": sum(1 for r in results if r.get("promotionDecision") == "FAIL"),
        "preGateWarn": sum(1 for r in results if r.get("preGateStatus") == "WARN"),
        "preGateFailed": sum(1 for r in results if r.get("preGateStatus") == "FAILED"),
        "preGateSuccess": sum(1 for r in results if r.get("preGateStatus") == "SUCCESS"),
        "totalWarnings": sum(int(r.get("warnCount", 0) or 0) for r in results),
        "totalErrors": sum(int(r.get("errorCount", 0) or 0) for r in results),
    }

    consolidated = {
        "wave": "Wave 5",
        "harness": "tools/wave5_multiworkbook_harness.py",
        "outRoot": str(out_root),
        "totals": totals,
        "scenarios": results,
        "goLiveChecklist": {
            "allPromotionPass": totals["promotionFail"] == 0,
            "hardErrorsResolved": totals["totalErrors"] == 0,
            "warnsGoverned": all(r["goLiveReadiness"]["warnsWithinPolicy"] for r in results),
            "multiWorkbookCoverage": totals["scenarios"] >= 3,
            "includesExternalWorkbook": any("mapping-t" in r["scenario"] for r in results),
        },
    }

    (out_root / "consolidated-run-report.json").write_text(json.dumps(consolidated, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
