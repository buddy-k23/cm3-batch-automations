#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "generated" / "e2e_cm3int"
OUT = ROOT / "generated" / "e2e_cm3int" / "wave_artifacts"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    validation_csv = SRC / "validation_results.csv"
    rows = list(csv.DictReader(validation_csv.open("r", encoding="utf-8")))
    total = len(rows)
    failed_rows = [r for r in rows if r.get("status") != "PASS"]
    failed = len(failed_rows)
    passed = total - failed

    # Build detail violations
    detail_csv = OUT / "detail-violations.csv"
    with detail_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["runId", "row_number", "status", "ruleId", "severity", "message"],
        )
        w.writeheader()
        for r in failed_rows:
            w.writerow(
                {
                    "runId": "cm3int-e2e-001",
                    "row_number": r.get("row_number"),
                    "status": "FAIL",
                    "ruleId": "E2E_ROW_MATCH",
                    "severity": "ERROR",
                    "message": "Final file row mismatch against source-derived expected output",
                }
            )

    summary = {
        "runId": "cm3int-e2e-001",
        "status": "SUCCESS" if failed == 0 else "FAILED",
        "summary": {
            "validated": total,
            "passed": passed,
            "failed": failed,
            "warned": 0,
            "topFailingRules": ([{"ruleId": "E2E_ROW_MATCH", "count": failed}] if failed else []),
        },
        "artifacts": {
            "summaryJson": str((OUT / "summary-report.json").resolve()),
            "detailCsv": str(detail_csv.resolve()),
            "telemetryJson": str((OUT / "telemetry.json").resolve()),
            "sourceCsv": str((SRC / "source_query_output.csv").resolve()),
            "finalFile": str((SRC / "final_output.txt").resolve()),
        },
    }

    telemetry = {
        "runId": "cm3int-e2e-001",
        "timestamp": "1970-01-01T00:00:00+00:00",
        "mappingVersion": "1.0.0",
        "rulePackVersion": "1.0.0",
        "source": {
            "system": "oracle-cm3int",
            "queryHash": "cm3int-demo-query-v1",
            "rowCount": total,
        },
        "phases": {
            "extractMs": 0,
            "validateMs": 0,
            "reportMs": 0,
            "totalMs": 0,
        },
        "quality": {
            "validated": total,
            "passed": passed,
            "failed": failed,
            "warned": 0,
            "byRule": ([{"ruleId": "E2E_ROW_MATCH", "severity": "ERROR", "failCount": failed}] if failed else []),
        },
        "system": {
            "peakMemoryMb": 0,
            "recordsPerSecond": 0,
        },
    }

    promotion = {
        "policyVersion": "w5.1",
        "enabled": True,
        "preGateStatus": summary["status"],
        "decision": "PASS" if failed == 0 else "FAIL",
        "reasonCodes": ([] if failed == 0 else ["HARD_ERRORS_PRESENT"]),
        "metrics": {
            "errors": failed,
            "warnings": 0,
            "validated": total,
        },
    }

    (OUT / "summary-report.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (OUT / "telemetry.json").write_text(json.dumps(telemetry, indent=2), encoding="utf-8")
    (OUT / "promotion-evidence.json").write_text(json.dumps(promotion, indent=2), encoding="utf-8")

    print("Wave artifacts generated:")
    print(OUT / "summary-report.json")
    print(OUT / "telemetry.json")
    print(OUT / "promotion-evidence.json")
    print(detail_csv)


if __name__ == "__main__":
    main()
