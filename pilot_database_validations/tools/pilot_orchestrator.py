#!/usr/bin/env python3
"""Pilot orchestrator: deterministic 5-round validation across onboarded tables.

For each round and each onboarded table, executes:
- positive scenario (expected PASS)
- negative scenario (expected FAIL)
- edge scenario (expected PASS with WARN pre-gate)

Artifacts are written per round and as a consolidated trend report.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from tools.promotion_gate import PromotionInputs, PromotionPolicy, build_promotion_evidence
except ModuleNotFoundError:
    from promotion_gate import PromotionInputs, PromotionPolicy, build_promotion_evidence


@dataclass(frozen=True)
class ScenarioDef:
    name: str
    expected_decision: str
    expected_pre_gate: str


SCENARIOS = [
    ScenarioDef("positive", "PASS", "SUCCESS"),
    ScenarioDef("negative", "FAIL", "FAILED"),
    ScenarioDef("edge", "PASS", "WARN"),
]


def load_tables(index_path: Path) -> list[str]:
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    tables = payload.get("tables", [])
    return [str(t) for t in tables]


def deterministic_inputs(round_no: int, scenario: str) -> dict[str, Any]:
    # Deterministic variation by round while preserving expected behavior.
    if scenario == "positive":
        return {
            "pre_gate_status": "SUCCESS",
            "hard_error_count": 0,
            "open_warn_count": 0,
            "template_completeness_ba": 99.0,
            "template_completeness_qa": 99.0,
            "required_columns_completeness": 100.0,
            "warn_signoff_ba": True,
            "warn_signoff_qa": True,
            "warn_waiver_age_days": 0,
        }
    if scenario == "negative":
        return {
            "pre_gate_status": "FAILED",
            "hard_error_count": 1 + (round_no % 2),
            "open_warn_count": 0,
            "template_completeness_ba": 94.0,
            "template_completeness_qa": 94.0,
            "required_columns_completeness": 99.0,
            "warn_signoff_ba": False,
            "warn_signoff_qa": False,
            "warn_waiver_age_days": None,
        }
    # edge
    return {
        "pre_gate_status": "WARN",
        "hard_error_count": 0,
        "open_warn_count": 20 if round_no % 2 == 1 else 18,
        "template_completeness_ba": 96.0,
        "template_completeness_qa": 96.0,
        "required_columns_completeness": 100.0,
        "warn_signoff_ba": True,
        "warn_signoff_qa": True,
        "warn_waiver_age_days": 1,
    }


def run_round(round_no: int, tables: list[str], out_root: Path, policy: PromotionPolicy) -> dict[str, Any]:
    round_dir = out_root / f"round-{round_no:02d}"
    round_dir.mkdir(parents=True, exist_ok=True)

    scenario_results: list[dict[str, Any]] = []
    for table in tables:
        for scenario in SCENARIOS:
            obs = deterministic_inputs(round_no, scenario.name)
            evidence = build_promotion_evidence(
                run_id=f"pilot-r{round_no:02d}-{table.lower()}-{scenario.name}",
                policy=policy,
                enabled=True,
                inputs=PromotionInputs(**obs),
            )
            decision = evidence["evaluation"]["decision"]
            pre_gate = evidence["evaluation"]["preGateStatus"]
            passed_expectation = decision == scenario.expected_decision and pre_gate == scenario.expected_pre_gate

            rec = {
                "round": round_no,
                "table": table,
                "scenario": scenario.name,
                "expectedDecision": scenario.expected_decision,
                "expectedPreGateStatus": scenario.expected_pre_gate,
                "observedDecision": decision,
                "observedPreGateStatus": pre_gate,
                "errors": int(obs["hard_error_count"]),
                "warnings": int(obs["open_warn_count"]),
                "expectationMet": passed_expectation,
                "reasons": evidence["evaluation"].get("reasons", []),
            }
            scenario_results.append(rec)

            table_dir = round_dir / table.lower() / scenario.name
            table_dir.mkdir(parents=True, exist_ok=True)
            (table_dir / "promotion-evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            (table_dir / "scenario-result.json").write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    total = len(scenario_results)
    mismatches = sum(1 for r in scenario_results if not r["expectationMet"])
    total_errors = sum(r["errors"] for r in scenario_results)
    total_warnings = sum(r["warnings"] for r in scenario_results)
    pass_count = sum(1 for r in scenario_results if r["observedDecision"] == "PASS")
    fail_count = sum(1 for r in scenario_results if r["observedDecision"] == "FAIL")

    if mismatches > 0:
        status = "FAIL"
        promotion_decision = "NO-GO"
    elif fail_count > 0:
        # Expected negatives are present but all expectations met.
        status = "WARN"
        promotion_decision = "CONDITIONAL"
    else:
        status = "PASS"
        promotion_decision = "GO"

    summary = {
        "round": round_no,
        "timestamp": datetime.now(UTC).isoformat(),
        "totals": {
            "records": total,
            "passDecisions": pass_count,
            "failDecisions": fail_count,
            "errors": total_errors,
            "warnings": total_warnings,
            "expectationMismatches": mismatches,
        },
        "roundStatus": status,
        "promotionDecision": promotion_decision,
    }

    (round_dir / "scenario-results.json").write_text(json.dumps(scenario_results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (round_dir / "round-summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def write_consolidated(out_root: Path, round_summaries: list[dict[str, Any]]) -> Path:
    trend_rows = []
    for s in round_summaries:
        t = s["totals"]
        trend_rows.append(
            {
                "round": s["round"],
                "status": s["roundStatus"],
                "errors": t["errors"],
                "warnings": t["warnings"],
                "promotionDecision": s["promotionDecision"],
            }
        )

    consolidated = {
        "orchestrator": "tools/pilot_orchestrator.py",
        "generatedAt": datetime.now(UTC).isoformat(),
        "rounds": round_summaries,
        "trendTable": trend_rows,
    }
    out = out_root / "consolidated-pilot-report.json"
    out.write_text(json.dumps(consolidated, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic pilot orchestrator for onboarded source tables")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--index", default="generated/pilot_contracts/index.json")
    parser.add_argument("--out-root", default="generated/trials/pilot-orchestrator")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    tables = load_tables(root / args.index)
    out_root = root / args.out_root
    out_root.mkdir(parents=True, exist_ok=True)

    policy = PromotionPolicy(
        warn_acceptance_mode="review-required",
        warn_acceptance_max_open=20,
        warn_acceptance_expiry_days=7,
        min_template_completeness_ba=95.0,
        min_template_completeness_qa=95.0,
        min_template_completeness_required_columns=100.0,
        derivation_default_transaction_code="disabled",
        derivation_default_source_field="disabled",
        derivation_mode_on_enable="placeholder",
    )

    round_summaries = [run_round(i, tables, out_root, policy) for i in range(1, args.rounds + 1)]
    consolidated = write_consolidated(out_root, round_summaries)

    print(json.dumps({
        "status": "OK",
        "tables": len(tables),
        "rounds": args.rounds,
        "outRoot": str(out_root),
        "consolidated": str(consolidated),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
