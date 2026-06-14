import unittest

from tools.rules_extraction import extract_rules_from_mapping_rows, normalize_transform_logic_to_rule


class RulesExtractionTests(unittest.TestCase):
    def test_extract_default_rule_from_transform_logic(self):
        row = {
            "transactionCode": "32010",
            "targetField": "TRN-COD-ERT",
            "sourceField": "SRC",
            "transformLogic": "Default to '32010'",
            "sourceLocation": {"sheet": "Sheet1", "row": 9},
        }
        result = normalize_transform_logic_to_rule(row)
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["meta"]["kind"], "default")
        self.assertIn("32010", result["rule"]["expression"])

    def test_extract_nullable_rule(self):
        row = {
            "transactionCode": "32010",
            "targetField": "REF-NUM-ERT",
            "transformLogic": "Nullable --> Leave Blank",
            "sourceLocation": {"sheet": "Sheet1", "row": 6},
        }
        result = normalize_transform_logic_to_rule(row)
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["meta"]["kind"], "nullable")

    def test_marks_complex_if_then_as_unresolved(self):
        row = {
            "transactionCode": "32010",
            "targetField": "OGL-TRM-ORI",
            "transformLogic": "IF (ORG-TERM > 999) THEN 999; ELSE ORG-TERM",
            "sourceLocation": {"sheet": "Sheet1", "row": 15},
        }
        result = normalize_transform_logic_to_rule(row)
        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(result["meta"]["unresolvedMarker"], "MANUAL_RULE_REQUIRED")

    def test_bulk_extraction_reports_summary(self):
        rows = [
            {
                "transactionCode": "T1",
                "targetField": "A",
                "sourceField": "SA",
                "transformLogic": "Default to '001'",
                "sourceLocation": {"sheet": "S", "row": 2},
            },
            {
                "transactionCode": "T1",
                "targetField": "B",
                "sourceField": "SB",
                "transformLogic": "Leave Blank",
                "sourceLocation": {"sheet": "S", "row": 3},
            },
            {
                "transactionCode": "T1",
                "targetField": "C",
                "sourceField": "SC",
                "transformLogic": "If x then y else z",
                "sourceLocation": {"sheet": "S", "row": 4},
            },
        ]
        extracted = extract_rules_from_mapping_rows(rows)
        self.assertEqual(extracted["summary"]["resolvedCount"], 2)
        self.assertEqual(extracted["summary"]["unresolvedCount"], 1)
        self.assertEqual(extracted["summary"]["warningCount"], 1)


if __name__ == "__main__":
    unittest.main()
