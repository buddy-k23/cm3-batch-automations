"""Alembic environment configuration for Valdo.

Supports Oracle (oracledb), PostgreSQL (psycopg2), and SQLite adapters.
The active adapter is selected via the ``DB_ADAPTER`` environment variable
(default: ``oracle``).

Schema filtering: only tables whose names start with ``APP_`` or ``VALDO_``
(case-insensitive) are managed by Alembic migrations.  All other database
objects (tables owned by other applications, legacy tables, etc.) are ignored.

The version table is placed in the schema returned by
:func:`~src.database.db_url.get_valdo_schema` so that migration state is
co-located with the managed tables.
"""

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Add project root to path so ``src`` package is importable when Alembic is
# invoked from the ``alembic/`` subdirectory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env file for database credentials
from dotenv import load_dotenv
load_dotenv()

from src.database.db_url import get_db_url, get_valdo_schema, include_object  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Set to a SQLAlchemy MetaData object for autogenerate support.
# Populated once ORM models are defined (future work).
target_metadata = None

# include_object is imported from src.database.db_url so that it can be unit
# tested without triggering the alembic package-shadowing issue that arises
# when importing alembic/env.py directly from within the project root.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (no live DB connection required).

    In offline mode Alembic emits SQL to stdout or a file rather than
    executing it.  The URL is read from :func:`~src.database.db_url.get_db_url`
    so the ``sqlalchemy.url`` key in ``alembic.ini`` is intentionally left
    unset.
    """
    url = get_db_url()
    schema = get_valdo_schema()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        version_table_schema=schema if schema else None,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode with a live database connection.

    The SQLAlchemy URL is injected into the config section at runtime via
    :func:`~src.database.db_url.get_db_url`, overriding any ``sqlalchemy.url``
    value that may be present in ``alembic.ini``.
    """
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = get_db_url()
    schema = get_valdo_schema()
    connectable = engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            version_table_schema=schema if schema else None,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
