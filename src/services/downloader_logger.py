"""Download activity logger with daily log rotation."""

import json
import logging
import logging.handlers
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

_DEFAULT_LOG_PATH = "logs/file-downloads.log"


class DownloaderLogger:
    """Stateful download activity logger backed by a rotating file handler.

    Each instance writes JSON-line entries to its own log file, so the router
    can supply the path from ``request.app.state.ui_config`` at construction
    time rather than relying on an environment variable.

    Args:
        log_path: Path to the log file. Defaults to
            ``"logs/file-downloads.log"``. Parent directories are created
            automatically.
    """

    def __init__(self, log_path: str = _DEFAULT_LOG_PATH) -> None:
        self._log_path = log_path
        self._logger = self._build_logger(log_path)

    @staticmethod
    def _build_logger(log_path: str) -> logging.Logger:
        """Create and configure a rotating file logger for *log_path*.

        Args:
            log_path: Destination file path.

        Returns:
            Configured ``logging.Logger`` instance.
        """
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Use a unique logger name per path so parallel instances don't clash.
        logger = logging.getLogger(f"valdo.downloader.{log_path}")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.handlers.clear()

        handler = logging.handlers.TimedRotatingFileHandler(
            filename=str(path), when="midnight", backupCount=30, utc=True
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        return logger

    def log_activity(
        self,
        *,
        operation: str,
        client_ip: str,
        client_host: str,
        path: str,
        filename: str,
        archive: Optional[str],
        status: str,
    ) -> None:
        """Write a JSON-line entry to the download activity log.

        Args:
            operation: ``"download"``, ``"search_files"``, or
                ``"search_archive"``.
            client_ip: Client IP from ``X-Forwarded-For`` or
                ``request.client.host``.
            client_host: Best-effort reverse-DNS hostname (falls back to IP).
            path: Configured server path that was accessed.
            filename: File that was downloaded or searched.
            archive: Archive filename if applicable, else ``None``.
            status: ``"success"`` or ``"error"``.
        """
        entry = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "client_ip": client_ip,
            "client_host": client_host,
            "operation": operation,
            "path": path,
            "archive": archive,
            "file": filename,
            "environment": os.getenv("APP_ENVIRONMENT", "unknown"),
            "status": status,
        }
        self._logger.info(json.dumps(entry))


# ---------------------------------------------------------------------------
# Module-level convenience shims for backwards compatibility.
# The downloader router now constructs a DownloaderLogger directly; the
# functions below remain so existing callers (e.g. old tests that import
# log_activity and resolve_client_info at module level) continue to work.
# ---------------------------------------------------------------------------

_module_logger: Optional[DownloaderLogger] = None


def _get_module_logger() -> DownloaderLogger:
    """Return the module-level singleton DownloaderLogger.

    Returns:
        Singleton :class:`DownloaderLogger` using the default log path.
    """
    global _module_logger
    if _module_logger is None:
        _module_logger = DownloaderLogger()
    return _module_logger


def log_activity(
    *,
    operation: str,
    client_ip: str,
    client_host: str,
    path: str,
    filename: str,
    archive: Optional[str],
    status: str,
) -> None:
    """Write a JSON-line entry to the download activity log.

    Convenience wrapper around the module-level :class:`DownloaderLogger`
    singleton. Prefer constructing a :class:`DownloaderLogger` directly
    when a custom log path is required.

    Args:
        operation: ``"download"``, ``"search_files"``, or ``"search_archive"``.
        client_ip: Client IP from ``X-Forwarded-For`` or
            ``request.client.host``.
        client_host: Best-effort reverse-DNS hostname (falls back to IP).
        path: Configured server path that was accessed.
        filename: File that was downloaded or searched.
        archive: Archive filename if applicable, else ``None``.
        status: ``"success"`` or ``"error"``.
    """
    _get_module_logger().log_activity(
        operation=operation,
        client_ip=client_ip,
        client_host=client_host,
        path=path,
        filename=filename,
        archive=archive,
        status=status,
    )


def resolve_client_info(request) -> Tuple[str, str]:
    """Extract client IP and attempt reverse-DNS lookup.

    Args:
        request: FastAPI ``Request`` object.

    Returns:
        Tuple of ``(ip_address, hostname)``. Hostname falls back to IP on
        failure.
    """
    forwarded = request.headers.get("X-Forwarded-For")
    ip = (
        forwarded.split(",")[0].strip()
        if forwarded
        else (request.client.host if request.client else "unknown")
    )
    try:
        host = socket.gethostbyaddr(ip)[0]
    except (socket.herror, socket.gaierror, OSError):
        host = ip
    return ip, host
