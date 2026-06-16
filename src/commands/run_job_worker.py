"""``valdo run-job-worker`` — background validation worker skeleton (S9-5, #391).

Implements the thin, reviewable skeleton defined by **ADR 0021**
(``docs/adr/0021-mcp-background-jobs.md``, Option A — the
``APP_MCP_RUN_REGISTRY`` table *is* the job queue; this process is the
consumer). It decouples long validations from the MCP request path:
``validate_file`` (when ``VALDO_MCP_ASYNC_VALIDATE`` is on) only writes a
``queued`` row and returns within 100ms; this worker claims that row out
of band and runs the engine.

What the SKELETON ships (this sprint)
-------------------------------------
* :func:`drain_once` — claim ONE queued job via the registry's atomic
  ``claim_next()`` primitive, run the existing validate body
  (``_run_validate_synchronously`` lifted verbatim from
  ``src/mcp/action_tools.py``), and write the terminal record. Returns the
  number of jobs drained (0 or 1).
* The ``valdo run-job-worker --once`` CLI command: a single drain-and-exit
  cycle, used by cron / manual backlog draining and by CI/tests.

What is DEFERRED to the fast-follow (M) — NOT built here
--------------------------------------------------------
* The production continuous poll loop with backoff, structured
  logging/metrics, and signal-driven graceful shutdown. ``--poll-interval``
  / ``--max-runs`` are accepted on the CLI surface (so the contract is
  stable) but only ``--once`` is wired this sprint.
* The stuck-``running`` reaper IMPLEMENTATION. :func:`reap_stuck_running`
  exists as a documented hook and raises ``NotImplementedError``.

Lifecycle semantics (defined by ADR 0021 §4; minimally wired here)
------------------------------------------------------------------
* **Graceful shutdown.** The production loop traps ``SIGTERM``/``SIGINT``,
  finishes the run it is currently executing, and exits without claiming
  new work. systemd ``TimeoutStopSec`` MUST exceed the worst-case single
  validation time so a clean stop is not ``SIGKILL``'d mid-run. (The
  ``--once`` skeleton has no loop to interrupt — it claims at most one job
  and returns.)
* **Restart-pickup.** ``queued`` rows are durable in the database backend.
  A worker, host, or deploy restart leaves the row ``queued``; the next
  ``drain_once`` (or the future poll loop) claims it. No work is lost on
  rotation — the durability property S6-1 built the registry to provide.
* **Stuck-``running`` reaper.** A run claimed by a worker that died
  mid-flight is left ``running`` forever. The contract (see
  :func:`reap_stuck_running`): a row in ``running`` whose ``started_at`` is
  older than a configurable threshold (≈2× the max expected validation
  time) is reset to ``queued`` (or ``failed`` after N attempts). Until the
  reaper lands (fast-follow), the runbook documents a manual reset.
"""

from __future__ import annotations

import logging
import sys
from typing import Optional

import click

from src.mcp.action_tools import _resolve_artefacts, _run_validate_synchronously
from src.mcp.run_registry import RunRegistry, make_run_registry

logger = logging.getLogger(__name__)

__all__ = ["run_job_worker", "drain_once", "reap_stuck_running"]


def drain_once(registry: RunRegistry) -> int:
    """Claim and run at most one queued validation job.

    The single-iteration core of the worker. It performs exactly the
    ADR 0021 cycle: ``claim_next`` (atomic ``queued`` → ``running``) →
    re-resolve the source artefacts → run the existing
    ``_run_validate_synchronously`` body (which writes the terminal
    ``completed`` / ``failed`` record) → return.

    The engine body never lets an exception escape — a failing validation
    is folded into a ``failed`` record by ``_run_validate_synchronously``.

    Args:
        registry: The :class:`RunRegistry` to drain. The worker shares the
            same backend (database in INT/prod, in-memory fallback) the MCP
            server enqueues against.

    Returns:
        ``1`` if a job was claimed and run, ``0`` if the queue was empty.
    """
    record = registry.claim_next()
    if record is None:
        return 0

    logger.info(
        "run-job-worker: claimed run_id=%s source=%s file=%s",
        record.run_id,
        record.source,
        record.file_path,
    )

    # Re-resolve mapping/rules from the source overlay — the queued row
    # stores only (source, file_path, file_type), not the resolved
    # artefacts, so we resolve them here exactly as the enqueue path did.
    artefacts = _resolve_artefacts(record.source, record.file_path, record.file_type)

    # ``_run_validate_synchronously`` flips the record to running (already
    # done by claim_next, harmlessly re-asserted), runs the engine, and
    # persists the terminal record via the shared registry singleton.
    _run_validate_synchronously(record, artefacts)

    logger.info(
        "run-job-worker: finished run_id=%s status=%s",
        record.run_id,
        record.status,
    )
    return 1


def reap_stuck_running(registry: RunRegistry, stuck_after_seconds: int) -> int:
    """Reset runs stuck in ``running`` back to ``queued`` (STUB — fast-follow).

    ADR 0021 §4 defines the contract: a row in ``running`` whose
    ``started_at`` is older than *stuck_after_seconds* (≈2× the max
    expected validation time) was almost certainly claimed by a worker that
    died mid-flight, and must be reset to ``queued`` (or moved to ``failed``
    after N attempts) so the backlog drains.

    **Not implemented this sprint.** The reaper is part of the follow-up M
    (``feat(mcp): valdo run-job-worker — continuous poll loop + claim +
    stuck-run reaper``). The hook exists so the seam is visible and a
    premature call fails loudly rather than silently no-op'ing. Until it
    lands, the runbook documents a manual ``UPDATE ... SET status='queued'``
    reset.

    Args:
        registry: The registry to scan.
        stuck_after_seconds: Age threshold; runs older than this in
            ``running`` are considered stuck.

    Returns:
        The number of rows reset (once implemented).

    Raises:
        NotImplementedError: Always, this sprint.
    """
    raise NotImplementedError(
        "stuck-running reaper is a fast-follow (ADR 0021 §4); not implemented "
        "in the S9-5 skeleton. Manually reset stuck rows per the runbook."
    )


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
    help="Seconds between polls in continuous mode (fast-follow; only --once is wired this sprint).",
)
@click.option(
    "--max-runs",
    type=int,
    default=0,
    show_default=True,
    help="Max jobs to drain before exiting in continuous mode, 0 = unbounded (fast-follow).",
)
def run_job_worker(once: bool, poll_interval: float, max_runs: int) -> None:
    """Drain queued MCP validation jobs from the run registry (ADR 0021).

    This sprint ships the ``--once`` single-drain cycle only. The continuous
    poll loop (``--poll-interval`` / ``--max-runs``), graceful-shutdown
    signal handling, and the stuck-``running`` reaper are the documented
    fast-follow. Invoking without ``--once`` reports that the continuous
    loop is not yet available and exits non-zero, so an operator is never
    silently left with a non-draining worker.
    """
    if not once:
        click.echo(
            click.style(
                "Continuous poll loop is a fast-follow (ADR 0021). "
                "Use --once for a single drain cycle this sprint.",
                fg="yellow",
            ),
            err=True,
        )
        sys.exit(2)

    registry = make_run_registry()
    drained = drain_once(registry)
    if drained:
        click.echo(click.style("Drained 1 queued validation job.", fg="green"))
    else:
        click.echo("No queued jobs to drain.")
