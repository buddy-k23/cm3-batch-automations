"""Database URL and schema helpers shared by Alembic env.py and SQLAlchemy engine.

This module centralises the logic for constructing SQLAlchemy connection URLs
and resolving the Valdo-managed schema name for each supported database adapter
(Oracle, PostgreSQL, SQLite).

Adapter selection is driven by the ``DB_ADAPTER`` environment variable
(default: ``oracle``).
"""

import os


def get_db_url() -> str:
    """Return SQLAlchemy connection URL based on DB_ADAPTER env var.

    Reads connection parameters from environment variables.  The adapter is
    selected via ``DB_ADAPTER`` (default: ``oracle``).

    Oracle env vars:
        - ``ORACLE_USER`` (default: ``APP_INT``)
        - ``ORACLE_PASSWORD`` (default: ``""``)
        - ``ORACLE_DSN`` (default: ``localhost:1521/FREEPDB1``)

    PostgreSQL env vars:
        - ``DB_USER`` (default: ``""``)
        - ``DB_PASSWORD`` (default: ``""``)
        - ``DB_HOST`` (default: ``localhost``)
        - ``DB_PORT`` (default: ``5432``)
        - ``DB_NAME`` (default: ``valdo``)

    SQLite env vars:
        - ``DB_PATH`` (default: ``valdo.db``)

    Returns:
        A fully-formed SQLAlchemy connection URL string.
    """
    adapter = os.getenv("DB_ADAPTER", "oracle").lower()

    if adapter == "postgresql":
        user = os.getenv("DB_USER", "")
        password = os.getenv("DB_PASSWORD", "")
        host = os.getenv("DB_HOST", "localhost")
        port = os.getenv("DB_PORT", "5432")
        dbname = os.getenv("DB_NAME", "valdo")
        return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{dbname}"

    elif adapter == "sqlite":
        path = os.getenv("DB_PATH", "valdo.db")
        return f"sqlite:///{path}"

    else:  # oracle (default)
        user = os.getenv("ORACLE_USER", "APP_INT")
        password = os.getenv("ORACLE_PASSWORD", "")
        dsn = os.getenv("ORACLE_DSN", "localhost:1521/FREEPDB1")

        # Parse DSN: "host:port/service" or "host/service" or "host:port"
        if "/" in dsn:
            hostport, service = dsn.rsplit("/", 1)
        else:
            hostport, service = dsn, "FREEPDB1"

        if ":" in hostport:
            host, port = hostport.rsplit(":", 1)
        else:
            host, port = hostport, "1521"

        return f"oracle+oracledb://{user}:{password}@{host}:{port}/{service}"  # gitleaks:allow


def include_object(object, name: str, type_: str, reflected: bool, compare_to) -> bool:
    """Decide whether Alembic should manage a given database object.

    Only tables whose names begin with ``APP_`` or ``VALDO_`` (checked
    case-insensitively) are included.  All non-table objects (indexes,
    sequences, views, etc.) pass through unconditionally so that Alembic can
    manage their dependencies.

    Args:
        object: The SQLAlchemy schema object being evaluated.
        name: The name of the object.
        type_: The object type string (e.g. ``"table"``, ``"index"``).
        reflected: ``True`` when the object was reflected from the database.
        compare_to: The object being compared against (may be ``None``).

    Returns:
        ``True`` if the object should be included in migrations, ``False``
        otherwise.
    """
    if type_ == "table":
        return name.upper().startswith(("APP_", "VALDO_"))
    return True


def get_valdo_schema() -> str:
    """Return the schema name where Valdo-managed tables live.

    The schema defaults depend on the active adapter:

    - **Oracle**: ``VALDO_SCHEMA`` → ``ORACLE_SCHEMA`` → ``ORACLE_USER`` →
      ``APP_INT``
    - **PostgreSQL**: ``VALDO_SCHEMA`` → ``public``
    - **SQLite**: ``VALDO_SCHEMA`` → ``""`` (empty string — SQLite has no schema
      concept)

    Returns:
        The schema name string, which may be empty for SQLite.
    """
    adapter = os.getenv("DB_ADAPTER", "oracle").lower()

    if adapter == "postgresql":
        return os.getenv("VALDO_SCHEMA", "public")

    elif adapter == "sqlite":
        return os.getenv("VALDO_SCHEMA", "")

    else:  # oracle (default)
        default = os.getenv(
            "ORACLE_SCHEMA",
            os.getenv("ORACLE_USER", "APP_INT"),
        )
        return os.getenv("VALDO_SCHEMA", default)
