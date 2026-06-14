"""Shared SQLAlchemy engine factory for Valdo database access."""
from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine, text

from src.database.db_url import get_db_url, get_valdo_schema


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return a cached SQLAlchemy engine built from environment variables.

    Uses ``lru_cache`` so the same engine instance is reused across calls.
    Call :func:`reset_engine` in tests to clear the cache.

    Returns:
        SQLAlchemy Engine instance configured for the current ``DB_ADAPTER``.
    """
    url = get_db_url()
    return create_engine(url, pool_pre_ping=True)


def reset_engine() -> None:
    """Clear the cached engine — use in tests only.

    Clears the ``lru_cache`` on :func:`get_engine` so that the next call
    creates a fresh engine from the current environment variables.
    """
    get_engine.cache_clear()


def check_connection() -> bool:
    """Test that the engine can connect to the database.

    Executes a trivial ``SELECT 1`` statement to verify connectivity.
    Any exception is caught and treated as a connection failure.

    Returns:
        ``True`` if the connection succeeds, ``False`` otherwise.
    """
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def get_schema_prefix() -> str:
    """Return the table name prefix with dot separator, or empty string for SQLite.

    Delegates to :func:`~src.database.db_url.get_valdo_schema` and appends a
    trailing ``"."`` when the schema name is non-empty.

    Examples:
        Oracle  → ``'APP_INT.'``
        PostgreSQL → ``'public.'``
        SQLite  → ``''``

    Returns:
        Schema prefix string (e.g. ``'APP_INT.'``) or ``''`` when the adapter
        has no schema concept (SQLite) or ``VALDO_SCHEMA`` is empty.
    """
    schema = get_valdo_schema()
    return f"{schema}." if schema else ""
