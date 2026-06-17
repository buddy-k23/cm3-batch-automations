"""Oracle database connection management.

Uses :mod:`src.config.db_config` for centralised configuration so that
connection parameters (user, password, DSN) are read from environment
variables in exactly one place.
"""

import oracledb
from typing import Optional

from src.config.db_config import get_db_config, DbConfig

# Backward-compatible alias for older tests/code that patch cx_Oracle.
cx_Oracle = oracledb


class OracleConnection:
    """Manages Oracle database connections.

    Prefer :meth:`from_env` to create instances — it reads ``ORACLE_USER``,
    ``ORACLE_PASSWORD``, and ``ORACLE_DSN`` from the environment via
    :func:`~src.config.db_config.get_db_config`.

    .. deprecated::
        **Deprecated as a direct entry point for the DB-integration features.**
        Per ADR 0022 (S15-3, #406) the backend-agnostic features —
        ``reconcile`` / ``reconcile-all`` (Sprint 12), ``extract`` and
        ``db-compare`` (Sprint 15) — now obtain their connection through
        :func:`src.database.adapters.factory.get_database_adapter`, which honours
        ``DB_ADAPTER`` (oracle / postgresql / sqlite). For new code that needs a
        portable connection, use the adapter factory; ``OracleConnection`` is a
        functional subset of :class:`~src.database.adapters.oracle_adapter.OracleAdapter`
        (same ``ORACLE_*`` env, same ``oracledb`` connect) and is superseded by it.

        ``OracleConnection`` is **kept, not removed**, because it still has
        legitimate Oracle-only consumers that are intentionally not portable:

        * ``src/pipeline/oracle_expected_generator.py`` — the ``run-tests``
          Oracle-expected-rowset generator (Oracle-bound by design; see ADR 0010).
        * ``src/api/routers/system.py`` — the Oracle DB-profile ping endpoint.
        * ``src/database/query_executor.py`` — legacy type reference.

        None of the adapter-routed DB-integration features import it any more.
    """

    def __init__(
        self,
        username: str,
        password: str,
        dsn: str,
        encoding: str = "UTF-8",
    ):
        """Initialize Oracle connection parameters.

        Args:
            username: Database username.
            password: Database password.
            dsn: Data Source Name (TNS or Easy Connect).
            encoding: Character encoding (default: UTF-8).
        """
        self.username = username
        self.password = password
        self.dsn = dsn
        self.encoding = encoding
        self.connection: Optional[oracledb.Connection] = None

    def connect(self) -> oracledb.Connection:
        """Establish database connection.

        Returns:
            Oracle connection object.

        Raises:
            RuntimeError: If password is empty (fail-fast before network call).
            ConnectionError: If oracledb cannot connect.
        """
        if not self.password:
            raise RuntimeError(
                "ORACLE_PASSWORD is not set.  "
                "Set it in your .env file or export it as an environment "
                "variable before attempting a database connection."
            )
        try:
            self.connection = cx_Oracle.connect(
                user=self.username,
                password=self.password,
                dsn=self.dsn,
            )
            return self.connection
        except oracledb.Error as e:
            raise ConnectionError(f"Failed to connect to Oracle database: {e}")

    def disconnect(self) -> None:
        """Close database connection."""
        if self.connection:
            try:
                self.connection.close()
            except oracledb.Error as e:
                raise ConnectionError(f"Failed to close connection: {e}")
            finally:
                self.connection = None

    def __enter__(self):
        """Context manager entry."""
        return self.connect()

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect()

    @staticmethod
    def from_env() -> "OracleConnection":
        """Create connection from environment variables.

        Reads ``ORACLE_USER``, ``ORACLE_PASSWORD``, and ``ORACLE_DSN`` via
        :func:`~src.config.db_config.get_db_config`.  Defaults are applied
        there (``APP_INT`` / ``localhost:1521/FREEPDB1``).

        Returns:
            OracleConnection instance configured from the environment.
        """
        cfg = get_db_config()
        return OracleConnection(
            username=cfg.user,
            password=cfg.password,
            dsn=cfg.dsn,
        )

