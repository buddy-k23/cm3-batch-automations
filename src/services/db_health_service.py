"""Cheap, bounded database connectivity probe for readiness checks (S16-1, #425).

This service backs the readiness path (``/ready``) and the ``database_connected``
field of ``/info``.  It is intentionally:

- **Cheap** — runs a trivial ``SELECT 1`` against the configured adapter.
- **Bounded** — wrapped in a short, hard timeout so a hung DB cannot stall the
  probe (and, by extension, the load balancer).
- **Non-throwing** — returns a boolean; any failure (driver missing,
  connection refused, timeout) resolves to ``False`` rather than propagating.

Liveness vs readiness (per the Sprint 16 risk):
    The liveness probe (``/health``) must NOT call this — a transient DB blip
    must not take the process out of the load balancer.  Only the readiness
    signal (``/ready`` and the informational ``/info.database_connected``)
    reflect real DB connectivity here.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

logger = logging.getLogger(__name__)

# Default bound for the probe.  Kept short so readiness checks stay snappy and
# never hang the caller waiting on an unresponsive backend.
_DEFAULT_TIMEOUT_SECONDS = 2.0


def _probe() -> bool:
    """Open a connection via the configured adapter and run ``SELECT 1``.

    Returns:
        ``True`` if the trivial query succeeds, ``False`` on any failure.
    """
    from src.database.adapters.factory import get_database_adapter

    adapter = get_database_adapter()
    try:
        adapter.connect()
        try:
            adapter.execute_query("SELECT 1")
            return True
        finally:
            adapter.disconnect()
    except Exception as exc:  # noqa: BLE001 — probe must never raise
        logger.warning("DB connectivity probe failed: %s", exc)
        return False


def check_db_connectivity(timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS) -> bool:
    """Return whether the configured database is reachable (bounded, non-throwing).

    Runs a cheap ``SELECT 1`` against the adapter selected by ``DB_ADAPTER``,
    inside a hard timeout so a hung backend cannot stall the readiness probe.

    Args:
        timeout_seconds: Maximum time to wait for the probe before treating the
            database as unreachable.  Defaults to a short bound (2s).

    Returns:
        ``True`` if the database responded to a trivial query within the
        timeout, ``False`` otherwise (failure, missing driver, or timeout).
    """
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_probe)
            return future.result(timeout=timeout_seconds)
    except FutureTimeout:
        logger.warning(
            "DB connectivity probe timed out after %.1fs", timeout_seconds
        )
        return False
    except Exception as exc:  # noqa: BLE001 — never propagate from a probe
        logger.warning("DB connectivity probe error: %s", exc)
        return False
