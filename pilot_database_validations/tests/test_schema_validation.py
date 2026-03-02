import json
import unittest
from pathlib import Path

from tools.schema_validation import SchemaValidationError, validate_payload


class SchemaValidationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.schemas = self.root / "schemas"

    def test_mapping_schema_passes_valid_payload(self):
        payload = json.loads((self.root / "generated" / "mapping.json").read_text(encoding="utf-8"))
        validate_payload(payload, self.schemas / "mapping.schema.json", "mapping")

    def test_mapping_schema_fails_on_additional_property(self):
        payload = json.loads((self.root / "generated" / "mapping.json").read_text(encoding="utf-8"))
        payload["unexpected"] = True
        with self.assertRaises(SchemaValidationError) as ex:
            validate_payload(payload, self.schemas / "mapping.schema.json", "mapping")
        self.assertTrue(any("additional property" in issue.message for issue in ex.exception.issues))

    def test_report_and_telemetry_pass_and_fail(self):
        report = {
            "runId": "run-1",
            "status": "SUCCESS",
            "summary": {"validated": 1, "passed": 1, "failed": 0, "warned": 0},
            "artifacts": {
                "summaryJson": "summary-report.json",
                "detailCsv": "detail-violations.csv",
                "telemetryJson": "telemetry.json",
            },
        }
        telemetry = {
            "runId": "run-1",
            "timestamp": "2026-02-24T00:00:00+00:00",
            "mappingVersion": "0.1.0",
            "rulePackVersion": "0.1.0",
            "phases": {"extractMs": 0, "validateMs": 0, "reportMs": 0, "totalMs": 0},
            "quality": {"validated": 1, "passed": 1, "failed": 0, "warned": 0, "byRule": []},
        }
        validate_payload(report, self.schemas / "report.schema.json", "report")
        validate_payload(telemetry, self.schemas / "telemetry.schema.json", "telemetry")

        invalid_report = dict(report)
        del invalid_report["runId"]
        with self.assertRaises(SchemaValidationError):
            validate_payload(invalid_report, self.schemas / "report.schema.json", "report")

        invalid_telemetry = dict(telemetry)
        invalid_telemetry["timestamp"] = "not-a-date"
        with self.assertRaises(SchemaValidationError):
            validate_payload(invalid_telemetry, self.schemas / "telemetry.schema.json", "telemetry")


if __name__ == "__main__":
    unittest.main()
