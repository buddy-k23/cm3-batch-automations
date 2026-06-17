"""Unit tests for ThresholdEvaluator branches + report (S17-4, #432)."""

from src.validators.threshold import (
    Threshold,
    ThresholdEvaluator,
    ThresholdResult,
)


def _t(**overrides):
    base = dict(name="n", metric="missing_rows")
    base.update(overrides)
    return Threshold(**base)


class TestEvaluateThreshold:
    def test_max_value_fails(self):
        ev = ThresholdEvaluator({"x": _t(max_value=5)})
        out = ev._evaluate_threshold(value=10, total=100, threshold=_t(max_value=5))
        assert out["result"] == ThresholdResult.FAIL
        assert "exceeds max" in out["reason"]

    def test_max_percent_fails(self):
        out = ThresholdEvaluator()._evaluate_threshold(
            value=5, total=100, threshold=_t(max_percent=0.02)
        )
        assert out["result"] == ThresholdResult.FAIL
        assert "Percent" in out["reason"]

    def test_warning_value(self):
        out = ThresholdEvaluator()._evaluate_threshold(
            value=6, total=100, threshold=_t(warning_value=5)
        )
        assert out["result"] == ThresholdResult.WARNING
        assert "warning" in out["reason"]

    def test_warning_percent(self):
        out = ThresholdEvaluator()._evaluate_threshold(
            value=6, total=100, threshold=_t(warning_percent=0.05)
        )
        assert out["result"] == ThresholdResult.WARNING

    def test_pass_within_limits(self):
        out = ThresholdEvaluator()._evaluate_threshold(
            value=1, total=100, threshold=_t(max_value=10, warning_value=5)
        )
        assert out["result"] == ThresholdResult.PASS
        assert "acceptable" in out["reason"]

    def test_zero_total_no_divide_error(self):
        out = ThresholdEvaluator()._evaluate_threshold(
            value=0, total=0, threshold=_t(max_percent=0.5)
        )
        assert out["percent"] == 0


class TestEvaluate:
    def _comparison(self, missing=0, extra=0, diffs=0):
        return {
            "total_rows_file1": 100,
            "only_in_file1": list(range(missing)),
            "only_in_file2": list(range(extra)),
            "differences": list(range(diffs)),
        }

    def test_overall_pass(self):
        out = ThresholdEvaluator().evaluate(self._comparison())
        assert out["passed"] is True
        assert out["overall_result"] == ThresholdResult.PASS

    def test_overall_fail_when_over_max(self):
        out = ThresholdEvaluator().evaluate(self._comparison(missing=50))
        assert out["overall_result"] == ThresholdResult.FAIL
        assert out["passed"] is False

    def test_overall_warning(self):
        # missing=6 trips warning_value(5) but not max_value(10)
        out = ThresholdEvaluator().evaluate(self._comparison(missing=6))
        assert out["overall_result"] == ThresholdResult.WARNING

    def test_disabled_threshold_skipped(self):
        thresholds = {"missing_rows": _t(max_value=1, enabled=False)}
        out = ThresholdEvaluator(thresholds).evaluate(self._comparison(missing=50))
        # disabled => not evaluated => overall PASS
        assert out["overall_result"] == ThresholdResult.PASS


class TestGenerateReport:
    def test_report_pass(self):
        ev = ThresholdEvaluator()
        results = ev.evaluate(
            {
                "total_rows_file1": 100,
                "only_in_file1": [],
                "only_in_file2": [],
                "differences": [],
            }
        )
        report = ev.generate_report(results)
        assert "THRESHOLD EVALUATION REPORT" in report
        assert "OVERALL: PASS" in report
        assert "Total Rows: 100" in report

    def test_report_fail(self):
        ev = ThresholdEvaluator()
        results = ev.evaluate(
            {
                "total_rows_file1": 100,
                "only_in_file1": list(range(50)),
                "only_in_file2": [],
                "differences": [],
            }
        )
        report = ev.generate_report(results)
        assert "OVERALL: FAIL" in report
