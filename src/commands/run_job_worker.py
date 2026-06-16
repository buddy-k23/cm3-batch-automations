"""``valdo run-job-worker`` — background validation worker skeleton (S9-5, #391).

Implements the thin, reviewable skeleton defined by **ADR 0021**
(``docs/adr/0021-mcp-background-jobs.md``, Option A — the
``APP_MCP_RUN_REGISTRY`` table *is* the job queue; this process is the
consumer). It decouples long validations from the MCP request path:
``validate_file`` (when ``VALDO_MCP_ASYNC_VALIDATE`` is on) only writes a
``queued`` row and returns within 100ms; this worker claims that row out
of band and runs the engine.

What this module ships
----------------------
* :func:`drain_once` — claim ONE queued job via the registry's atomic
  ``claim_next()`` primitive, run the existing validate body
  (``_run_validate_synchronously`` from ``src/mcp/action_tools.py``) with a
  background heartbeat thread keeping the run fresh, and write the terminal
  record. Returns the number of jobs drained (0 or 1).
* :func:`run_worker_loop` — the production continuous poll loop: claim/run,
  reap stuck rows on idle, capped backoff, ``--max-runs`` bound, and a
  shutdown flag. Pure and importable (clock + sleep + shutdown injected) so
  it is deterministically testable; signal handling lives in the CLI entry.
* :func:`reap_stuck_running` — the stuck-``running`` reaper (S10-1): computes
  the stale cutoff and delegates the parameterised reclaim to the registry.
* The ``valdo run-job-worker`` CLI command: ``--once`` for a single
  drain-and-exit cycle (cron / manual backlog drain / CI), or the default
  continuous mode with ``SIGTERM``/``SIGINT`` graceful shutdown.

Lifecycle semantics (ADR 0021 §4)
---------------------------------
* **Graceful shutdown.** The continuous loop traps ``SIGTERM``/``SIGINT``
  (registered in the CLI entry), finishes the run it is currently executing,
  and exits 0 without claiming new work. systemd ``TimeoutStopSec`` MUST
  exceed the worst-case single validation time so a clean stop is not
  ``SIGKILL``'d mid-run.
* **Restart-pickup.** ``queued`` rows are durable in the database backend.
  A worker, host, or deploy restart leaves the row ``queued``; the next
  ``drain_once`` (or poll loop) claims it. No work is lost on rotation.
* **Heartbeat + stuck-``running`` reaper.** While a job runs, a daemon
  thread heartbeats it every ``poll_interval`` seconds, so even a multi-hour
  validation stays out of the reaper's reach. A run claimed by a worker that
  *died* mid-flight stops heartbeating; once its last heartbeat is older than
  ``poll_interval * reap_multiple`` the reaper resets it to ``queued`` and
  increments ``attempt_count`` so the backlog drains.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import click

from src.mcp.action_tools import _resolve_artefacts, _run_validate_synchronously
from src.mcp.run_registry import RunRegistry, make_run_registry

logger = logging.getLogger(__name__)

__all__ = [
    "run_job_worker",
    "drain_once",
    "reap_stuck_running",
    "run_worker_loop",
]

# Backoff is capped so an idle worker still polls often enough to pick up
# newly-enqueued work promptly. The cap is a multiple of the poll interval.
_MAX_BACKOFF_MULTIPLIER = 8


class _ShutdownFlag:
    """Thread-safe boolean set by signal handlers to stop the poll loop.

    The flag is checked at the top of each loop iteration and before each
    claim. An in-flight job always runs to completion — graceful shutdown
    stops *claiming new work*, it does not interrupt the current run (ADR
    0021 §4; systemd ``TimeoutStopSec`` must exceed the worst-case single
    validation so the clean stop is not ``SIGKILL``'d mid-run).
    """

    def __init__(self) -> None:
        """Initialise the flag in the not-set state."""
        self._event = threading.Event()

    def set(self) -> None:
        """Mark shutdown as requested."""
        self._event.set()

    def is_set(self) -> bool:
        """Return True once shutdown has been requested."""
        return self._event.is_set()


def _utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def drain_once(registry: RunRegistry, heartbeat_interval: float = 30.0) -> int:
    """Claim and run at most one queued validation job.

    The single-iteration core of the worker. It performs exactly the
    ADR 0021 cycle: ``claim_next`` (atomic ``queued`` → ``running``) →
    re-resolve the source artefacts → run the existing
    ``_run_validate_synchronously`` body (which writes the terminal
    ``completed`` / ``failed`` record) → return.

    While the synchronous validate body runs, a lightweight daemon thread
    heartbeats the run every *heartbeat_interval* seconds (S10-1). A long
    (e.g. 10M-row) validation can outlive the reaper's stale threshold; the
    fresh heartbeats keep the row out of the reaper's reach so another worker
    never falsely reclaims an actively-running job. The thread is always
    stopped and joined in a ``finally`` block.

    The engine body never lets an exception escape — a failing validation
    is folded into a ``failed`` record by ``_run_validate_synchronously``.

    Args:
        registry: The :class:`RunRegistry` to drain. The worker shares the
            same backend (database in INT/prod, in-memory fallback) the MCP
            server enqueues against.
        heartbeat_interval: Seconds between background heartbeats while the
            run executes. In the CLI this is the poll interval.

    Returns:
        ``1`` if a job was claimed and run, ``0`` if the queue was empty.
    """
    record = registry.claim_next()
    if record is None:
        return 0

    logger.info(
        "run-job-worker: claimed run_id=%s source=%s file=%s attempt=%d",
        record.run_id,
        record.source,
        record.file_path,
        record.attempt_count,
    )

    # Re-resolve mapping/rules from the source overlay — the queued row
    # stores only (source, file_path, file_type), not the resolved
    # artefacts, so we resolve them here exactly as the enqueue path did.
    artefacts = _resolve_artefacts(record.source, record.file_path, record.file_type)

    stop_heartbeat = threading.Event()

    def _heartbeat_loop() -> None:
        """Refresh the run's heartbeat until the job finishes."""
        while not stop_heartbeat.wait(heartbeat_interval):
            try:
                registry.heartbeat(record.run_id)
            except Exception:  # noqa: BLE001 — a heartbeat failure must not kill the run.
                logger.warning(
                    "run-job-worker: heartbeat failed for run_id=%s",
                    record.run_id,
                    exc_info=True,
                )

    hb_thread = threading.Thread(
        target=_heartbeat_loop,
        name=f"valdo-hb-{record.run_id[:8]}",
        daemon=True,
    )
    hb_thread.start()
    try:
        # ``_run_validate_synchronously`` flips the record to running
        # (already done by claim_next, harmlessly re-asserted), runs the
        # engine, and persists the terminal record via the shared registry.
        _run_validate_synchronously(record, artefacts)
    finally:
        stop_heartbeat.set()
        hb_thread.join(timeout=max(heartbeat_interval, 1.0))

    logger.info(
        "run-job-worker: finished run_id=%s status=%s",
        record.run_id,
        record.status,
    )
    return 1


def reap_stuck_running(
    registry: RunRegistry,
    stuck_after_seconds: float,
    now_fn: Callable[[], datetime] = _utcnow,
) -> int:
    """Reclaim runs stuck in ``running`` back to ``queued`` (S10-1, ADR 0021 §4).

    A row in ``running`` whose heartbeat (or, lacking one, its
    ``started_at``) is older than *stuck_after_seconds* was almost certainly
    claimed by a worker that died mid-flight. This computes the stale cutoff
    (``now - stuck_after_seconds``) and delegates the guarded, parameterised
    reclaim to :meth:`RunRegistry.reap_stuck`, which flips each stuck row
    back to ``queued`` and increments its ``attempt_count`` so the backlog
    drains and a live long validation (kept fresh by ``drain_once``'s
    heartbeat thread) is never reclaimed.

    Args:
        registry: The registry to scan.
        stuck_after_seconds: Age threshold in seconds; runs whose last
            heartbeat is older than this are considered stuck. Typically
            ``poll_interval * reap_multiple`` (≈2×+ the max expected
            validation time).
        now_fn: Clock injection point for deterministic testing.

    Returns:
        The number of rows reclaimed.
    """
    now = now_fn()
    stale_before = now - timedelta(seconds=stuck_after_seconds)
    reclaimed = registry.reap_stuck(stale_before=stale_before, now=now)
    if reclaimed:
        logger.info(
            "run-job-worker: reaper reclaimed %d stuck run(s) older than %.0fs",
            reclaimed,
            stuck_after_seconds,
        )
    return reclaimed


def run_worker_loop(
    registry: RunRegistry,
    poll_interval: float,
    max_runs: int,
    reap_multiple: int,
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
    shutdown: Optional[object] = None,
) -> int:
    """Run the continuous claim/run/reap poll loop (S10-1, ADR 0021).

    Each iteration:

    1. If *shutdown* is set, break (graceful stop — no new claim).
    2. ``drain_once`` — claim and run one job if the queue is non-empty.
       On work, reset the idle-backoff and continue immediately (drain the
       backlog as fast as the engine allows).
    3. On an empty queue, run the stuck-run reaper (threshold =
       ``poll_interval * reap_multiple``), then sleep with a simple capped
       backoff that grows on consecutive empties and resets on work.
    4. Stop after *max_runs* jobs (``0`` = unbounded).

    Signal handling is deliberately NOT installed here — the CLI entry point
    owns ``SIGTERM``/``SIGINT`` registration so this function stays a clean,
    importable, deterministically-testable unit (a fake clock + fake sleep
    + a stub shutdown flag fully exercise it).

    Args:
        registry: The shared :class:`RunRegistry`.
        poll_interval: Base seconds to sleep when the queue is empty; also
            the heartbeat cadence for in-flight jobs.
        max_runs: Stop after draining this many jobs; ``0`` is unbounded.
        reap_multiple: Reaper threshold multiplier
            (``stuck_after = poll_interval * reap_multiple``).
        sleep_fn: Sleep injection point (default :func:`time.sleep`).
        shutdown: Object exposing ``is_set() -> bool``. When omitted, a
            never-set flag is used (loop only stops via *max_runs*).

    Returns:
        Total number of jobs drained before the loop exited.
    """
    flag = shutdown if shutdown is not None else _ShutdownFlag()
    stuck_after = poll_interval * reap_multiple
    drained_total = 0
    consecutive_empties = 0

    while True:
        if flag.is_set():
            logger.info("run-job-worker: shutdown requested; stopping loop.")
            break

        if max_runs and drained_total >= max_runs:
            logger.info("run-job-worker: reached --max-runs=%d; stopping.", max_runs)
            break

        drained = drain_once(registry, heartbeat_interval=poll_interval)
        if drained:
            drained_total += drained
            consecutive_empties = 0
            continue

        # Empty queue: opportunistically reap stuck rows, then back off.
        try:
            reap_stuck_running(registry, stuck_after_seconds=stuck_after)
        except Exception:  # noqa: BLE001 — a reaper error must not kill the loop.
            logger.warning("run-job-worker: reaper raised; continuing.", exc_info=True)

        if flag.is_set():
            break

        consecutive_empties += 1
        backoff = poll_interval * min(consecutive_empties, _MAX_BACKOFF_MULTIPLIER)
        sleep_fn(backoff)

    return drained_total


@click.command("run-job-worker")
@click.option(
    "--once",
    "once",
    is_flag=True,
    default=False,
    help="Drain a single queued job and exit (cron / manual backlog drain / CI).",
)
@click.option(
    "--poll-interval",
    type=float,
    default=2.0,
    show_default=True,
    help="Seconds between polls when the queue is empty (also the in-flight heartbeat cadence).",
)
@click.option(
    "--max-runs",
    type=int,
    default=0,
    show_default=True,
    help="Max jobs to drain before exiting in continuous mode, 0 = unbounded.",
)
@click.option(
    "--reap-multiple",
    type=int,
    default=10,
    show_default=True,
    help="Stuck-run reaper threshold as a multiple of --poll-interval "
    "(stuck_after = poll-interval * reap-multiple).",
)
def run_job_worker(
    once: bool, poll_interval: float, max_runs: int, reap_multiple: int
) -> None:
    """Drain queued MCP validation jobs from the run registry (ADR 0021).

    Two modes:

    * ``--once`` — claim and run a single queued job, then exit (cron /
      manual backlog drain / CI). Exits 0 whether or not a job was found.
    * default (no ``--once``) — the production continuous poll loop:
      claim/run jobs, heartbeat in-flight runs, reap stuck rows on idle, and
      back off when the queue is empty. ``SIGTERM``/``SIGINT`` trigger a
      graceful shutdown — the in-flight job finishes, no new work is
      claimed, and the process exits 0.
    """
    registry = make_run_registry()

    if once:
        drained = drain_once(registry, heartbeat_interval=poll_interval)
        if drained:
            click.echo(click.style("Drained 1 queued validation job.", fg="green"))
        else:
            click.echo("No queued jobs to drain.")
        return

    # Continuous mode: install signal handlers HERE (not in the importable
    # loop fn) so a SIGTERM/SIGINT flips the shutdown flag, the in-flight job
    # finishes, and the process exits cleanly.
    flag = _ShutdownFlag()

    def _handle_signal(signum, _frame) -> None:
        logger.info("run-job-worker: received signal %s; finishing in-flight job.", signum)
        flag.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    click.echo(
        click.style(
            f"run-job-worker: continuous mode (poll={poll_interval}s, "
            f"max_runs={max_runs or 'unbounded'}, "
            f"reap_after={poll_interval * reap_multiple:.0f}s). "
            "Send SIGTERM/SIGINT to stop gracefully.",
            fg="green",
        )
    )
    drained = run_worker_loop(
        registry,
        poll_interval=poll_interval,
        max_runs=max_runs,
        reap_multiple=reap_multiple,
        shutdown=flag,
    )
    click.echo(f"run-job-worker: stopped after draining {drained} job(s).")
    sys.exit(0)
