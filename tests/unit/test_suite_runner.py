"""Unit tests for the SuiteRunner production-grade orchestrator (issue #94).

These tests are written BEFORE implementation (TDD red phase).
They cover the five acceptance criteria from the issue:
  - test_suite_all_steps_pass
  - test_suite_middle_step_fails_continues
  - test_suite_fail_fast_stops_early
  - test_suite_retry_succeeds
  - test_suite_timeout_triggers
"""

from __future__ import annotations

import time
from typing import Any, Callable
from unittest.mock import MagicMock, call, patch

import pytest

from src.pipeline.suite_runner import (
    StepConfig,
    StepResult,
    StepStatus,
    SuiteConfig,
    SuiteResult,
    SuiteRunner,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _passing_step(name: str, retries: int = 0, timeout_seconds: float = 30.0) -> StepConfig:
    """Build a StepConfig whose callable always succeeds."""
    return StepConfig(
        name=name,
        callable=lambda: {"status": "ok", "rows": 10},
        retries=retries,
        timeout_seconds=timeout_seconds,
    )


def _failing_step(name: str, retries: int = 0, timeout_seconds: float = 30.0) -> StepConfig:
    """Build a StepConfig whose callable always raises RuntimeError."""
    def _always_fail():
        raise RuntimeError(f"step {name} failed deliberately")
    return StepConfig(
        name=name,
        callable=_always_fail,
        retries=retries,
        timeout_seconds=timeout_seconds,
    )


def _slow_step(name: str, sleep_seconds: float, timeout_seconds: float = 1.0) -> StepConfig:
    """Build a StepConfig whose callable sleeps longer than the timeout."""
    def _sleep_fn():
        time.sleep(sleep_seconds)
        return {"status": "ok"}
    return StepConfig(
        name=name,
        callable=_sleep_fn,
        timeout_seconds=timeout_seconds,
    )


# ---------------------------------------------------------------------------
# test_suite_all_steps_pass
# ---------------------------------------------------------------------------

class TestSuiteAllStepsPass:
    """All steps pass — result status must be 'passed' and all step statuses passed."""

    def test_overall_status_passed(self):
        config = SuiteConfig(
            name="all_pass",
            steps=[
                _passing_step("ingest"),
                _passing_step("validate"),
                _passing_step("compare"),
            ],
        )
        runner = SuiteRunner(config)
        result = runner.run()

        assert isinstance(result, SuiteResult)
        assert result.status == StepStatus.PASSED

    def test_all_step_statuses_passed(self):
        config = SuiteConfig(
            name="all_pass",
            steps=[
                _passing_step("ingest"),
                _passing_step("validate"),
            ],
        )
        result = SuiteRunner(config).run()
        for step in result.steps:
            assert step.status == StepStatus.PASSED

    def test_step_results_contain_output(self):
        config = SuiteConfig(
            name="with_output",
            steps=[_passing_step("step_a")],
        )
        result = SuiteRunner(config).run()
        assert result.steps[0].output is not None
        assert result.steps[0].output["rows"] == 10

    def test_all_steps_executed(self):
        tracker = []
        def _track(name):
            def _fn():
                tracker.append(name)
                return {}
            return _fn

        config = SuiteConfig(
            name="track_suite",
            steps=[
                StepConfig(name="a", callable=_track("a")),
                StepConfig(name="b", callable=_track("b")),
                StepConfig(name="c", callable=_track("c")),
            ],
        )
        SuiteRunner(config).run()
        assert tracker == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# test_suite_middle_step_fails_continues
# ---------------------------------------------------------------------------

class TestSuiteMiddleStepFailsContinues:
    """When fail_fast=False (default), a middle failure must not stop later steps."""

    def test_later_steps_still_run(self):
        tracker = []
        def _track(name):
            def _fn():
                tracker.append(name)
                return {}
            return _fn

        config = SuiteConfig(
            name="middle_fail",
            fail_fast=False,
            steps=[
                StepConfig(name="step_a", callable=_track("step_a")),
                _failing_step("step_b"),
                StepConfig(name="step_c", callable=_track("step_c")),
            ],
        )
        result = SuiteRunner(config).run()
        assert "step_c" in tracker, "step_c must execute even after step_b fails"

    def test_overall_status_failed_when_any_step_fails(self):
        config = SuiteConfig(
            name="middle_fail",
            fail_fast=False,
            steps=[
                _passing_step("a"),
                _failing_step("b"),
                _passing_step("c"),
            ],
        )
        result = SuiteRunner(config).run()
        assert result.status == StepStatus.FAILED

    def test_partial_results_present(self):
        """Completed steps must appear in result even when a later step fails."""
        config = SuiteConfig(
            name="partial",
            fail_fast=False,
            steps=[
                _passing_step("ok_step"),
                _failing_step("bad_step"),
            ],
        )
        result = SuiteRunner(config).run()
        step_names = [s.name for s in result.steps]
        assert "ok_step" in step_names
        assert "bad_step" in step_names

    def test_failed_step_has_error_message(self):
        config = SuiteConfig(
            name="error_msg",
            fail_fast=False,
            steps=[_failing_step("boom")],
        )
        result = SuiteRunner(config).run()
        boom = next(s for s in result.steps if s.name == "boom")
        assert boom.status == StepStatus.FAILED
        assert boom.error is not None
        assert "boom" in boom.error


# ---------------------------------------------------------------------------
# test_suite_fail_fast_stops_early
# ---------------------------------------------------------------------------

class TestSuiteFailFastStopsEarly:
    """When fail_fast=True, execution must halt after the first failure."""

    def test_subsequent_steps_not_executed(self):
        tracker = []
        def _track(name):
            def _fn():
                tracker.append(name)
                return {}
            return _fn

        config = SuiteConfig(
            name="fail_fast",
            fail_fast=True,
            steps=[
                StepConfig(name="step_a", callable=_track("step_a")),
                _failing_step("step_b"),
                StepConfig(name="step_c", callable=_track("step_c")),
            ],
        )
        SuiteRunner(config).run()
        assert "step_c" not in tracker, "step_c must NOT execute when fail_fast=True"

    def test_overall_status_failed(self):
        config = SuiteConfig(
            name="fail_fast",
            fail_fast=True,
            steps=[
                _failing_step("first"),
                _passing_step("second"),
            ],
        )
        result = SuiteRunner(config).run()
        assert result.status == StepStatus.FAILED

    def test_only_completed_and_failed_in_results(self):
        """Steps after the failing step must not appear in results at all."""
        config = SuiteConfig(
            name="fail_fast",
            fail_fast=True,
            steps=[
                _passing_step("a"),
                _failing_step("b"),
                _passing_step("c"),
            ],
        )
        result = SuiteRunner(config).run()
        step_names = [s.name for s in result.steps]
        assert "c" not in step_names


# ---------------------------------------------------------------------------
# test_suite_retry_succeeds
# ---------------------------------------------------------------------------

class TestSuiteRetrySucceeds:
    """A step that fails transiently must be retried and eventually pass."""

    def test_retry_success_on_third_attempt(self):
        call_count = {"n": 0}

        def _flaky():
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise ConnectionError("transient db error")
            return {"status": "ok"}

        config = SuiteConfig(
            name="retry_suite",
            steps=[StepConfig(name="flaky_step", callable=_flaky, retries=3)],
        )
        result = SuiteRunner(config).run()
        assert result.status == StepStatus.PASSED
        assert call_count["n"] == 3

    def test_step_passes_after_retry(self):
        call_count = {"n": 0}

        def _flaky():
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise ValueError("first attempt fails")
            return {"rows": 5}

        config = SuiteConfig(
            name="retry_once",
            steps=[StepConfig(name="one_retry", callable=_flaky, retries=2)],
        )
        result = SuiteRunner(config).run()
        step = result.steps[0]
        assert step.status == StepStatus.PASSED
        assert step.attempts == 2

    def test_exhausted_retries_marks_failed(self):
        """When all retries are exhausted the step status must be FAILED."""
        config = SuiteConfig(
            name="exhaust",
            steps=[_failing_step("always_fail", retries=2)],
        )
        result = SuiteRunner(config).run()
        step = result.steps[0]
        assert step.status == StepStatus.FAILED
        assert step.attempts == 3  # initial + 2 retries

    def test_retry_attempt_count_recorded(self):
        call_count = {"n": 0}

        def _fn():
            call_count["n"] += 1
            raise RuntimeError("always")

        config = SuiteConfig(
            name="attempt_count",
            steps=[StepConfig(name="s", callable=_fn, retries=4)],
        )
        result = SuiteRunner(config).run()
        assert result.steps[0].attempts == 5


# ---------------------------------------------------------------------------
# test_suite_timeout_triggers
# ---------------------------------------------------------------------------

class TestSuiteTimeoutTriggers:
    """A step that exceeds its timeout must be marked TIMED_OUT."""

    def test_slow_step_marked_timed_out(self):
        config = SuiteConfig(
            name="timeout_suite",
            steps=[_slow_step("slow_step", sleep_seconds=5.0, timeout_seconds=0.1)],
        )
        result = SuiteRunner(config).run()
        step = result.steps[0]
        assert step.status == StepStatus.TIMED_OUT

    def test_timed_out_step_has_error_context(self):
        config = SuiteConfig(
            name="timeout_context",
            steps=[_slow_step("late_step", sleep_seconds=5.0, timeout_seconds=0.1)],
        )
        result = SuiteRunner(config).run()
        step = result.steps[0]
        assert step.error is not None
        assert "timeout" in step.error.lower() or "timed out" in step.error.lower()

    def test_overall_status_failed_on_timeout(self):
        config = SuiteConfig(
            name="timeout_fail",
            steps=[_slow_step("s", sleep_seconds=5.0, timeout_seconds=0.1)],
        )
        result = SuiteRunner(config).run()
        assert result.status == StepStatus.FAILED

    def test_subsequent_step_runs_after_timeout_no_fail_fast(self):
        """With fail_fast=False, steps after a timed-out step must still run."""
        tracker = []

        def _track():
            tracker.append("ran")
            return {}

        config = SuiteConfig(
            name="timeout_continue",
            fail_fast=False,
            steps=[
                _slow_step("slow", sleep_seconds=5.0, timeout_seconds=0.1),
                StepConfig(name="next", callable=_track),
            ],
        )
        SuiteRunner(config).run()
        assert "ran" in tracker


# ---------------------------------------------------------------------------
# test_progress_callbacks
# ---------------------------------------------------------------------------

class TestProgressCallbacks:
    """on_step_start and on_step_complete callbacks must fire in order."""

    def test_on_step_start_called_per_step(self):
        events = []

        def on_start(step_name: str) -> None:
            events.append(("start", step_name))

        config = SuiteConfig(
            name="callbacks",
            steps=[_passing_step("a"), _passing_step("b")],
        )
        SuiteRunner(config, on_step_start=on_start).run()
        assert ("start", "a") in events
        assert ("start", "b") in events

    def test_on_step_complete_called_with_result(self):
        results_received = []

        def on_complete(result: StepResult) -> None:
            results_received.append(result)

        config = SuiteConfig(
            name="callbacks",
            steps=[_passing_step("x")],
        )
        SuiteRunner(config, on_step_complete=on_complete).run()
        assert len(results_received) == 1
        assert results_received[0].name == "x"

    def test_callbacks_fire_in_order(self):
        events = []

        def on_start(name):
            events.append(f"start:{name}")

        def on_complete(result):
            events.append(f"complete:{result.name}")

        config = SuiteConfig(
            name="order",
            steps=[_passing_step("first"), _passing_step("second")],
        )
        SuiteRunner(config, on_step_start=on_start, on_step_complete=on_complete).run()
        assert events == [
            "start:first", "complete:first",
            "start:second", "complete:second",
        ]


# ---------------------------------------------------------------------------
# test_suite_result_shape
# ---------------------------------------------------------------------------

class TestSuiteResultShape:
    """SuiteResult must carry expected metadata fields."""

    def test_result_has_suite_name(self):
        config = SuiteConfig(name="my_suite", steps=[_passing_step("s")])
        result = SuiteRunner(config).run()
        assert result.suite_name == "my_suite"

    def test_result_has_started_at_and_finished_at(self):
        config = SuiteConfig(name="timing", steps=[_passing_step("s")])
        result = SuiteRunner(config).run()
        assert result.started_at is not None
        assert result.finished_at is not None
        assert result.finished_at >= result.started_at

    def test_result_has_duration_seconds(self):
        config = SuiteConfig(name="dur", steps=[_passing_step("s")])
        result = SuiteRunner(config).run()
        assert result.duration_seconds >= 0.0

    def test_step_result_has_duration(self):
        config = SuiteConfig(name="step_dur", steps=[_passing_step("s")])
        result = SuiteRunner(config).run()
        assert result.steps[0].duration_seconds >= 0.0
