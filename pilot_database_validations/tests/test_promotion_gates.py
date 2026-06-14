import unittest

from tools.promotion_gate import PromotionInputs, PromotionPolicy, evaluate_promotion_gate


class PromotionGateTests(unittest.TestCase):
    def test_gate_disabled_preserves_legacy_warn(self):
        result = evaluate_promotion_gate(
            policy=PromotionPolicy(),
            enabled=False,
            inputs=PromotionInputs(
                pre_gate_status="WARN",
                hard_error_count=0,
                open_warn_count=2,
                template_completeness_ba=100.0,
                template_completeness_qa=100.0,
                required_columns_completeness=100.0,
                warn_signoff_ba=False,
                warn_signoff_qa=False,
                warn_waiver_age_days=None,
            ),
        )
        self.assertEqual(result["decision"], "PASS")
        self.assertFalse(result["enabled"])

    def test_review_required_needs_signoff_and_waiver_age(self):
        result = evaluate_promotion_gate(
            policy=PromotionPolicy(warn_acceptance_mode="review-required", warn_acceptance_max_open=3),
            enabled=True,
            inputs=PromotionInputs(
                pre_gate_status="WARN",
                hard_error_count=0,
                open_warn_count=1,
                template_completeness_ba=100.0,
                template_completeness_qa=100.0,
                required_columns_completeness=100.0,
                warn_signoff_ba=False,
                warn_signoff_qa=True,
                warn_waiver_age_days=None,
            ),
        )
        self.assertEqual(result["decision"], "FAIL")
        codes = {r["code"] for r in result["reasons"]}
        self.assertIn("WARN_SIGNOFF_REQUIRED", codes)
        self.assertIn("WARN_WAIVER_AGE_MISSING", codes)

    def test_auto_accept_within_threshold_passes(self):
        result = evaluate_promotion_gate(
            policy=PromotionPolicy(warn_acceptance_mode="auto-accept", warn_acceptance_max_open=5),
            enabled=True,
            inputs=PromotionInputs(
                pre_gate_status="WARN",
                hard_error_count=0,
                open_warn_count=2,
                template_completeness_ba=99.0,
                template_completeness_qa=99.0,
                required_columns_completeness=100.0,
                warn_signoff_ba=False,
                warn_signoff_qa=False,
                warn_waiver_age_days=None,
            ),
        )
        self.assertEqual(result["decision"], "PASS")


if __name__ == "__main__":
    unittest.main()
