import unittest

from tools.rule_promotion import plan_promotion


class RulePromotionTests(unittest.TestCase):
    def test_blocks_when_unresolved_or_in_review(self):
        extraction = {
            "rulesExtracted": [
                {
                    "ruleId": "W4A_TXN_A_FIELD_A_DEFAULT_11111111",
                    "scope": "field",
                    "severity": "INFO",
                    "priority": 900,
                    "expression": "FIELD_A == '001'",
                    "messageTemplate": "FIELD_A should default to 001",
                    "sourceLocation": {"sheet": "Map", "row": 2},
                }
            ],
            "unresolved": [{"unresolvedMarker": "MANUAL_RULE_REQUIRED", "targetField": "FIELD_B"}],
        }
        pack = {"rulePackId": "sample", "version": "0.1.0", "status": "candidate", "rules": []}

        result = plan_promotion(extraction_artifact=extraction, rule_pack=pack)

        self.assertEqual(result["decision"], "BLOCKED")
        self.assertTrue(result["blocks"]["unresolvedPresent"])
        self.assertTrue(result["blocks"]["candidatesInReview"])

    def test_promotes_approved_non_conflicting_rules(self):
        extraction = {
            "rulesExtracted": [
                {
                    "ruleId": "W4A_TXN_A_FIELD_A_DEFAULT_11111111",
                    "scope": "field",
                    "severity": "INFO",
                    "priority": 900,
                    "expression": "FIELD_A == '001'",
                    "messageTemplate": "FIELD_A should default to 001",
                    "sourceLocation": {"sheet": "Map", "row": 2},
                }
            ],
            "unresolved": [],
        }
        pack = {"rulePackId": "sample", "version": "0.1.0", "status": "candidate", "rules": []}
        decisions = {"decisions": {"W4A_TXN_A_FIELD_A_DEFAULT_11111111": "approved"}}

        result = plan_promotion(
            extraction_artifact=extraction,
            rule_pack=pack,
            decisions_payload=decisions,
            require_unresolved_resolved=True,
        )

        self.assertEqual(result["decision"], "READY")
        self.assertEqual(result["summary"]["promotedCount"], 1)
        self.assertEqual(result["updatedRulePack"]["status"], "approved")
        self.assertEqual(len(result["updatedRulePack"]["rules"]), 1)
        self.assertIn("traceability", result["updatedRulePack"]["rules"][0])

    def test_blocks_on_rule_id_conflict(self):
        extraction = {
            "rulesExtracted": [
                {
                    "ruleId": "CONFLICT_RULE",
                    "scope": "field",
                    "severity": "INFO",
                    "priority": 900,
                    "expression": "FIELD_A == '001'",
                    "messageTemplate": "FIELD_A should default to 001",
                }
            ],
            "unresolved": [],
        }
        pack = {
            "rulePackId": "sample",
            "version": "0.1.0",
            "status": "candidate",
            "rules": [
                {
                    "ruleId": "CONFLICT_RULE",
                    "scope": "field",
                    "severity": "ERROR",
                    "priority": 10,
                    "expression": "FIELD_A != ''",
                    "messageTemplate": "FIELD_A required",
                }
            ],
        }
        decisions = {"decisions": {"CONFLICT_RULE": "approved"}}

        result = plan_promotion(extraction_artifact=extraction, rule_pack=pack, decisions_payload=decisions)

        self.assertEqual(result["decision"], "BLOCKED")
        self.assertTrue(result["blocks"]["ruleIdConflicts"])
        self.assertEqual(result["summary"]["promotedCount"], 0)


if __name__ == "__main__":
    unittest.main()
