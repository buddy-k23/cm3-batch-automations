"""Centralised database configuration.

Reads connection parameters from environment variables (or a pluggable secrets
provider) with sensible defaults for local development.  All database code
should use :func:`get_db_config` or :func:`get_connection` instead of reading
env vars directly.

The password is resolved via :func:`~src.utils.secrets.get_secrets_provider`,
which honours the ``SECRETS_PROVIDER`` env var (default ``env``).  See
:mod:`src.utils.secrets` for supported backends (env, vault, azure).

Environment variables
---------------------
``DB_ADAPTER``
    Database adapter to use: ``oracle`` (default), ``postgresql``, or
    ``sqlite``.
``ORACLE_USER``
    Oracle database username.  Default: ``APP_INT``.
``ORACLE_PASSWORD``
    Oracle database password.  **No default** -- must be set before connecting.
    Resolved through the active secrets provider.
``ORACLE_DSN``
    Oracle Easy Connect string.  Default: ``localhost:1521/FREEPDB1``.
``ORACLE_SCHEMA``
    Schema prefix used in SQL statements (e.g. ``APP_INT.TABLE``).
    Default: value of ``ORACLE_USER`` (or ``APP_INT`` if unset).
``DB_HOST``
    Generic database host for non-Oracle adapters (e.g. PostgreSQL).
``DB_PORT``
    Generic database port for non-Oracle adapters.
``DB_NAME``
    Generic database name / catalog for non-Oracle adapters.
``SECRETS_PROVIDER``
    Secrets backend: ``env`` (default), ``vault``, or ``azure``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

import oracledb

from src.utils.secrets import get_secrets_provider


_DEFAULT_USER = "APP_INT"
_DEFAULT_DSN = "localhost:1521/FREEPDB1"
_DEFAULT_ADAPTER = "oracle"

# Generic (non-Oracle) adapter defaults — single source of truth shared by the
# PostgreSQL adapter and :mod:`src.database.db_url`.  ``DB_NAME`` is the Valdo
# application database (``valdo``), NOT the Postgres admin catalog ``postgres``
# (the prior divergence reconciled in S16-4, #424).
_DEFAULT_DB_HOST = "localhost"
_DEFAULT_DB_PORT = "5432"
_DEFAULT_DB_NAME = "valdo"
_DEFAULT_DB_USER = "postgres"


@dataclass(frozen=True)
class DbConfig:
    """Immutable container for database connection parameters.

    Attributes:
        user: Oracle database username.
        password: Oracle database password (may be empty when only reading
            config).
        dsn: Oracle Easy Connect string (``host:port/service``).
        schema: Schema qualifier for SQL table references.
        db_adapter: Adapter type: ``"oracle"`` (default), ``"postgresql"``,
            or ``"sqlite"``.
        db_host: Generic host for non-Oracle adapters (default ``localhost``).
        db_port: Generic port for non-Oracle adapters (default ``5432``).
        db_name: Generic database name for non-Oracle adapters (default
            ``valdo`` — the Valdo application database).
        db_user: Generic username for non-Oracle adapters (default
            ``postgres``).  Resolved via the secrets provider.
        db_password: Generic password for non-Oracle adapters.  Resolved via
            the secrets provider; empty string when unset.
    """

    user: str
    password: str
    dsn: str
    schema: str
    db_adapter: str = _DEFAULT_ADAPTER
    db_host: Optional[str] = None
    db_port: Optional[str] = None
    db_name: Optional[str] = None
    db_user: str = _DEFAULT_DB_USER
    db_password: str = ""


def get_db_config() -> DbConfig:
    """Build a :class:`DbConfig` from environment variables.

    Falls back to sensible defaults for local development when variables are
    not set.  ``ORACLE_SCHEMA`` defaults to the resolved ``ORACLE_USER``.
    Generic ``DB_*`` variables are included for non-Oracle adapters and
    default to ``None`` when not set.

    Returns:
        Populated :class:`DbConfig` instance.

    Example::

        cfg = get_db_config()
        print(cfg.user, cfg.dsn, cfg.db_adapter)
    """
    secrets = get_secrets_provider()
    user = secrets.get_secret("ORACLE_USER", default=_DEFAULT_USER)
    password = secrets.get_secret("ORACLE_PASSWORD", default="")
    dsn = secrets.get_secret("ORACLE_DSN", default=_DEFAULT_DSN)
    schema = secrets.get_secret("ORACLE_SCHEMA", default=user)
    db_adapter = os.environ.get("DB_ADAPTER", _DEFAULT_ADAPTER)
    # Generic (non-Oracle) adapter parameters.  Hosts/ports/names are not
    # secrets, but DB_USER/DB_PASSWORD are resolved through the same provider so
    # Vault/Azure applies to factory-created PostgreSQL connections (S16-4).
    db_host = os.environ.get("DB_HOST") or _DEFAULT_DB_HOST
    db_port = os.environ.get("DB_PORT") or _DEFAULT_DB_PORT
    db_name = os.environ.get("DB_NAME") or _DEFAULT_DB_NAME
    db_user = secrets.get_secret("DB_USER", default=_DEFAULT_DB_USER)
    db_password = secrets.get_secret("DB_PASSWORD", default="")
    return DbConfig(
        user=user,
        password=password,
        dsn=dsn,
        schema=schema,
        db_adapter=db_adapter,
        db_host=db_host,
        db_port=db_port,
        db_name=db_name,
        db_user=db_user,
        db_password=db_password,
    )


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
