"""Production-grade suite orchestrator with error recovery (issue #94).

Provides:
- SuiteConfig / StepConfig — Pydantic models describing a suite and its steps.
- StepResult / SuiteResult  — Pydantic models capturing execution outcomes.
- SuiteRunner               — Orchestrates step execution with isolation,
  retry/back-off, per-step timeouts, progress callbacks, and partial results.
"""

from __future__ import annotations

import concurrent.futures
import logging
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_RETRY_BACKOFF_BASE: float = 0.5   # seconds; exponential base
DEFAULT_TIMEOUT_SECONDS: float = 30.0


# ---------------------------------------------------------------------------
# Public enums
# ---------------------------------------------------------------------------

class StepStatus(str, Enum):
    """Execution outcome for a single pipeline step.

    Attributes:
        PASSED: Step completed successfully.
        FAILED: Step raised an unrecoverable exception.
        TIMED_OUT: Step exceeded its configured timeout.
        SKIPPED: Step was not executed (e.g. stopped by fail_fast).
    """

    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    SKIPPED = "skipped"


# ---------------------------------------------------------------------------
# Pydantic models — configuration
# ---------------------------------------------------------------------------

class StepConfig(BaseModel):
    """Configuration for a single pipeline step.

    Attributes:
        name: Human-readable identifier for the step.
        callable: Zero-argument function to execute for this step.
            Must return a JSON-serialisable dict (or any value).
        retries: Number of *additional* attempts after the first failure.
            A value of 2 means up to 3 total attempts. Defaults to 0.
        backoff_base: Base seconds for exponential back-off between retries.
            Actual delay for attempt *n* is ``backoff_base * 2 ** (n - 1)``.
            Defaults to 0.5.
        timeout_seconds: Per-attempt wall-clock timeout in seconds.
            Defaults to 30.0.
    """

    model_config = {"arbitrary_types_allowed": True}

    name: str
    callable: Callable[[], Any]
    retries: int = Field(default=0, ge=0)
    backoff_base: float = Field(default=DEFAULT_RETRY_BACKOFF_BASE, ge=0.0)
    timeout_seconds: float = Field(default=DEFAULT_TIMEOUT_SECONDS, gt=0.0)


class SuiteConfig(BaseModel):
    """Configuration for an entire test suite.

    Attributes:
        name: Suite identifier used in result metadata.
        steps: Ordered list of step configurations.
        fail_fast: When True the runner stops immediately after the first
            step failure. Remaining steps are not executed and will not
            appear in the result. Defaults to False.
    """

    name: str
    steps: List[StepConfig] = Field(default_factory=list)
    fail_fast: bool = False


# ---------------------------------------------------------------------------
# Pydantic models — results
# ---------------------------------------------------------------------------

class StepResult(BaseModel):
    """Outcome record for a single executed step.

    Attributes:
        name: Step name copied from StepConfig.
        status: Final execution status.
        attempts: Total number of attempts made (1 = no retries needed).
        output: Return value from the step callable, or None on failure.
        error: Error message string on FAILED or TIMED_OUT, else None.
        started_at: UTC timestamp when execution began.
        finished_at: UTC timestamp when execution completed.
        duration_seconds: Wall-clock execution time in seconds.
    """

    name: str
    status: StepStatus
    attempts: int = 1
    output: Optional[Any] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_seconds: float = 0.0


class SuiteResult(BaseModel):
    """Outcome record for a complete suite execution.

    Attributes:
        suite_name: Name copied from SuiteConfig.
        status: Overall status — PASSED only if every executed step passed,
            FAILED otherwise.
        steps: Ordered list of StepResult for each step that was executed.
        started_at: UTC timestamp when the suite began.
        finished_at: UTC timestamp when the suite completed.
        duration_seconds: Total wall-clock time in seconds.
    """

    suite_name: str
    status: StepStatus
    steps: List[StepResult] = Field(default_factory=list)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_seconds: float = 0.0


# ---------------------------------------------------------------------------
# SuiteRunner
# ---------------------------------------------------------------------------

class SuiteRunner:
    """Production-grade pipeline suite orchestrator.

    Executes steps in order with:
    - **Step isolation** — one step's failure never raises into subsequent steps
      (unless ``fail_fast=True``).
    - **Retry logic** — transient failures are retried up to ``StepConfig.retries``
      extra times with exponential back-off.
    - **Per-step timeouts** — each attempt is run in a thread and cancelled when
      it exceeds ``StepConfig.timeout_seconds``.
    - **Progress callbacks** — ``on_step_start`` and ``on_step_complete`` are
      called synchronously before and after each step.
    - **Partial results** — ``SuiteResult.steps`` always contains every step
      that was started, even if a later step fails.
    - **Structured logging** — a named logger emits INFO/WARNING/ERROR entries
      for each step, tagged with suite name and attempt count.

    Args:
        config: Fully-configured SuiteConfig describing the suite.
        on_step_start: Optional callback invoked with the step name just before
            the step begins executing.  Must not raise.
        on_step_complete: Optional callback invoked with the StepResult once a
            step has finished (success, failure, or timeout).  Must not raise.
    """

    def __init__(
        self,
        config: SuiteConfig,
        on_step_start: Optional[Callable[[str], None]] = None,
        on_step_complete: Optional[Callable[[StepResult], None]] = None,
    ) -> None:
        self._config = config
        self._on_step_start = on_step_start
        self._on_step_complete = on_step_complete
        self._log = logging.getLogger(f"{__name__}.{config.name}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> SuiteResult:
        """Execute all configured steps and return an aggregated SuiteResult.

        Steps are executed in order.  When ``fail_fast=True`` the runner stops
        after the first non-passing step and the remaining steps are omitted
        from the result.  When ``fail_fast=False`` every step runs regardless
        of earlier failures and all results are collected.

        Returns:
            SuiteResult containing the aggregate status and per-step outcomes.
        """
        suite_start = _utcnow()
        self._log.info("Suite '%s' starting (%d steps)", self._config.name, len(self._config.steps))

        step_results: List[StepResult] = []
        any_failed = False

        for step_config in self._config.steps:
            # Fire the "start" callback before we begin.
            self._fire_on_start(step_config.name)

            step_result = self._run_step_with_retries(step_config)
            step_results.append(step_result)

            # Fire the "complete" callback unconditionally.
            self._fire_on_complete(step_result)

            # Audit log for each step completion.
            try:
                from src.utils.audit_logger import get_audit_logger
                get_audit_logger().emit(
                    "suite_step_completed",
                    triggered_by="suite_runner",
                    suite=self._config.name,
                    step=step_config.name,
                    status=step_result.status.value,
                    attempts=step_result.attempts,
                    duration_seconds=step_result.duration_seconds,
                    error=step_result.error,
                )
            except Exception:  # noqa: BLE001
                self._log.debug("Audit emit failed for step '%s'", step_config.name)

            if step_result.status != StepStatus.PASSED:
                any_failed = True
                if self._config.fail_fast:
                    self._log.warning(
                        "Suite '%s' stopping early (fail_fast=True) after step '%s' %s",
                        self._config.name,
                        step_config.name,
                        step_result.status.value,
                    )
                    break

        suite_end = _utcnow()
        overall = StepStatus.FAILED if any_failed else StepStatus.PASSED
        duration = (suite_end - suite_start).total_seconds()

        self._log.info(
            "Suite '%s' finished in %.3fs — %s",
            self._config.name,
            duration,
            overall.value,
        )

        return SuiteResult(
            suite_name=self._config.name,
            status=overall,
            steps=step_results,
            started_at=suite_start,
            finished_at=suite_end,
            duration_seconds=duration,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _run_step_with_retries(self, step: StepConfig) -> StepResult:
        """Execute a single step, retrying on transient failure.

        Attempts the step callable up to ``1 + step.retries`` times.  On each
        failure (except the last) it waits ``step.backoff_base * 2 ** attempt``
        seconds before retrying.  Timeout failures are treated identically to
        exception failures for retry purposes.

        Args:
            step: The step configuration to execute.

        Returns:
            A StepResult with the final outcome of all attempts.
        """
        max_attempts = 1 + step.retries
        last_error: Optional[str] = None
        last_status: StepStatus = StepStatus.FAILED
        step_output: Optional[Any] = None
        step_start = _utcnow()

        for attempt in range(1, max_attempts + 1):
            self._log.info(
                "Step '%s' — attempt %d/%d",
                step.name,
                attempt,
                max_attempts,
            )

            outcome, output, error_msg = self._execute_once(step)

            if outcome == StepStatus.PASSED:
                step_end = _utcnow()
                return StepResult(
                    name=step.name,
                    status=StepStatus.PASSED,
                    attempts=attempt,
                    output=output,
                    error=None,
                    started_at=step_start,
                    finished_at=step_end,
                    duration_seconds=(step_end - step_start).total_seconds(),
                )

            # Track last failure details.
            last_status = outcome
            last_error = error_msg
            step_output = output

            if attempt < max_attempts:
                backoff = step.backoff_base * (2 ** (attempt - 1))
                self._log.warning(
                    "Step '%s' failed (attempt %d/%d): %s — retrying in %.3fs",
                    step.name,
                    attempt,
                    max_attempts,
                    error_msg,
                    backoff,
                )
                if backoff > 0:
                    time.sleep(backoff)
            else:
                self._log.error(
                    "Step '%s' permanently failed after %d attempt(s): %s",
                    step.name,
                    attempt,
                    error_msg,
                )

        step_end = _utcnow()
        return StepResult(
            name=step.name,
            status=last_status,
            attempts=max_attempts,
            output=step_output,
            error=last_error,
            started_at=step_start,
            finished_at=step_end,
            duration_seconds=(step_end - step_start).total_seconds(),
        )

    def _execute_once(
        self,
        step: StepConfig,
    ) -> tuple[StepStatus, Optional[Any], Optional[str]]:
        """Execute the step callable exactly once with timeout enforcement.

        The callable is executed in a ``ThreadPoolExecutor`` so that the
        timeout is enforced via ``Future.result(timeout=...)``.  The thread
        is allowed to finish in the background; we do not forcibly kill it.

        Args:
            step: Step configuration providing the callable and timeout.

        Returns:
            A three-tuple: (status, output_or_None, error_message_or_None).
        """
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(step.callable)
            try:
                output = future.result(timeout=step.timeout_seconds)
                return (StepStatus.PASSED, output, None)
            except concurrent.futures.TimeoutError:
                msg = (
                    f"step '{step.name}' timed out after {step.timeout_seconds}s"
                )
                return (StepStatus.TIMED_OUT, None, msg)
            except Exception as exc:  # noqa: BLE001
                return (StepStatus.FAILED, None, str(exc))

    # ------------------------------------------------------------------
    # Callback helpers — never let a callback crash the runner
    # ------------------------------------------------------------------

    def _fire_on_start(self, step_name: str) -> None:
        """Invoke on_step_start callback, swallowing any exceptions.

        Args:
            step_name: Name of the step about to start.
        """
        if self._on_step_start is None:
            return
        try:
            self._on_step_start(step_name)
        except Exception:  # noqa: BLE001
            self._log.exception("on_step_start callback raised for step '%s'", step_name)

    def _fire_on_complete(self, step_result: StepResult) -> None:
        """Invoke on_step_complete callback, swallowing any exceptions.

        Args:
            step_result: The completed StepResult to pass to the callback.
        """
        if self._on_step_complete is None:
            return
        try:
            self._on_step_complete(step_result)
        except Exception:  # noqa: BLE001
            self._log.exception(
                "on_step_complete callback raised for step '%s'", step_result.name
            )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    """Return the current UTC datetime with timezone info.

    Returns:
        Timezone-aware datetime object for UTC now.
    """
    return datetime.now(tz=timezone.utc)
