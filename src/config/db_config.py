"""Centralised Oracle database configuration.

Reads connection parameters from environment variables (or a pluggable secrets
provider) with sensible defaults for local development.  All database code
should use :func:`get_db_config` or :func:`get_connection` instead of reading
env vars directly.

The password is resolved via :func:`~src.utils.secrets.get_secrets_provider`,
which honours the ``SECRETS_PROVIDER`` env var (default ``env``).  See
:mod:`src.utils.secrets` for supported backends (env, vault, azure).

Environment variables
---------------------
``ORACLE_USER``
    Database username.  Default: ``CM3INT``.
``ORACLE_PASSWORD``
    Database password.  **No default** -- must be set before connecting.
    Resolved through the active secrets provider.
``ORACLE_DSN``
    Oracle Easy Connect string.  Default: ``localhost:1521/FREEPDB1``.
``ORACLE_SCHEMA``
    Schema prefix used in SQL statements (e.g. ``CM3INT.TABLE``).
    Default: value of ``ORACLE_USER`` (or ``CM3INT`` if unset).
``SECRETS_PROVIDER``
    Secrets backend: ``env`` (default), ``vault``, or ``azure``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

import oracledb

from src.utils.secrets import get_secrets_provider


_DEFAULT_USER = "CM3INT"
_DEFAULT_DSN = "localhost:1521/FREEPDB1"


@dataclass(frozen=True)
class DbConfig:
    """Immutable container for Oracle connection parameters.

    Attributes:
        user: Database username.
        password: Database password (may be empty when only reading config).
        dsn: Oracle Easy Connect string (``host:port/service``).
        schema: Schema qualifier for SQL table references.
    """

    user: str
    password: str
    dsn: str
    schema: str


def get_db_config() -> DbConfig:
    """Build a :class:`DbConfig` from environment variables.

    Falls back to sensible defaults for local development when variables are
    not set.  ``ORACLE_SCHEMA`` defaults to the resolved ``ORACLE_USER``.

    Returns:
        Populated :class:`DbConfig` instance.

    Example::

        cfg = get_db_config()
        print(cfg.user, cfg.dsn)
    """
    secrets = get_secrets_provider()
    user = secrets.get_secret("ORACLE_USER", default=_DEFAULT_USER)
    password = secrets.get_secret("ORACLE_PASSWORD", default="")
    dsn = secrets.get_secret("ORACLE_DSN", default=_DEFAULT_DSN)
    schema = secrets.get_secret("ORACLE_SCHEMA", default=user)
    return DbConfig(user=user, password=password, dsn=dsn, schema=schema)


def get_connection(config: DbConfig | None = None) -> oracledb.Connection:
    """Create an ``oracledb`` thin-mode connection.

    Args:
        config: Optional pre-built config.  When *None*, :func:`get_db_config`
            is called to read from environment variables.

    Returns:
        A live :class:`oracledb.Connection` in thin mode.

    Raises:
        RuntimeError: If ``ORACLE_PASSWORD`` is empty/unset (fail-fast).
        ConnectionError: If the underlying ``oracledb.connect`` call fails.
    """
    if config is None:
        config = get_db_config()

    if not config.password:
        raise RuntimeError(
            "ORACLE_PASSWORD is not set.  "
            "Set it in your .env file or export it as an environment variable "
            "before attempting a database connection."
        )

    try:
        return oracledb.connect(
            user=config.user,
            password=config.password,
            dsn=config.dsn,
        )
    except oracledb.Error as exc:
        raise ConnectionError(
            f"Failed to connect to Oracle (user={config.user!r}, "
            f"dsn={config.dsn!r}): {exc}"
        ) from exc
