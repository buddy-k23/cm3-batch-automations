"""Pluggable database adapter package.

Exports the abstract base class and the factory function so that call sites
need only import from this package:

    from src.database.adapters import DatabaseAdapter, get_database_adapter

Available adapters
------------------
- ``"oracle"`` — :class:`~src.database.adapters.oracle_adapter.OracleAdapter`
  (default, requires ``oracledb``)
- ``"postgresql"`` — :class:`~src.database.adapters.postgresql_adapter.PostgreSQLAdapter`
  (optional, requires ``psycopg2``)
- ``"sqlite"`` — :class:`~src.database.adapters.sqlite_adapter.SQLiteAdapter`
  (built-in, no extra packages)

The active adapter is selected via the ``DB_ADAPTER`` environment variable or
explicitly by passing ``adapter_type`` to :func:`get_database_adapter`.
"""

from src.database.adapters.base import DatabaseAdapter
from src.database.adapters.factory import get_database_adapter

__all__ = [
    "DatabaseAdapter",
    "get_database_adapter",
]
