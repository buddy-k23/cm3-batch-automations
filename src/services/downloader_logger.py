"""Download activity logger with daily log rotation."""

import json
import logging
import logging.handlers
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

_logger: Optional[logging.Logger] = None


def _get_logger() -> logging.Logger:
    """Return the singleton download activity logger, creating it if needed.

    The logger writes JSON-line entries to a rotating log file. The file path
    is read from the ``DOWNLOADER_LOG_PATH`` environment variable (default:
    ``logs/file-downloads.log``). Existing handlers are cleared on each
    (re)initialisation so that test reloads pick up the correct file path.

    Returns:
        Configured ``logging.Logger`` instance for download activity.
    """
    global _logger
    if _logger is not None:
        return _logger

    log_path = Path(os.getenv("DOWNLOADER_LOG_PATH", "logs/file-downloads.log"))
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("valdo.downloader")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    # Clear any handlers from previous (test) instantiations so that a module
    # reload always writes to the path given by the current environment.
    logger.handlers.clear()

    handler = logging.handlers.TimedRotatingFileHandler(
        filename=str(log_path), when="midnight", backupCount=30, utc=True
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)

    _logger = logger
    return logger


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

    Args:
        operation: ``"download"``, ``"search_files"``, or ``"search_archive"``.
        client_ip: Client IP from ``X-Forwarded-For`` or ``request.client.host``.
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
        "environment": os.getenv("CM3_ENVIRONMENT", "unknown"),
        "status": status,
    }
    _get_logger().info(json.dumps(entry))


def resolve_client_info(request) -> Tuple[str, str]:
    """Extract client IP and attempt reverse-DNS lookup.

    Args:
        request: FastAPI ``Request`` object.

    Returns:
        Tuple of ``(ip_address, hostname)``. Hostname falls back to IP on failure.
    """
    forwarded = request.headers.get("X-Forwarded-For")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
    try:
        host = socket.gethostbyaddr(ip)[0]
    except (socket.herror, socket.gaierror, OSError):
        host = ip
    return ip, host
