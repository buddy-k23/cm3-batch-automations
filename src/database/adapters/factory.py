"""Factory function for creating database adapter instances.

The active adapter type is resolved in this priority order:

1. Explicit ``adapter_type`` argument passed to :func:`get_database_adapter`.
2. ``DB_ADAPTER`` environment variable.
3. Default value: ``"oracle"`` (for backward compatibility).

Example::

    # .env
    # DB_ADAPTER=sqlite

    adapter = get_database_adapter()        # → SQLiteAdapter
    adapter = get_database_adapter("oracle")  # always OracleAdapter
"""

from __future__ import annotations

import os
from typing import Optional

from src.database.adapters.base import DatabaseAdapter

# Lazily imported to avoid import errors when drivers are not installed.
# The adapters themselves handle graceful ImportError on connect().
_ADAPTER_MAP = {
    "oracle": "src.database.adapters.oracle_adapter.OracleAdapter",
    "postgresql": "src.database.adapters.postgresql_adapter.PostgreSQLAdapter",
    "sqlite": "src.database.adapters.sqlite_adapter.SQLiteAdapter",
}


def get_database_adapter(adapter_type: Optional[str] = None) -> DatabaseAdapter:
    """Return a database adapter instance for the requested backend.

    The adapter is instantiated but **not** connected.  Callers should use it
    as a context manager (``with get_database_adapter() as adapter:`` …) or
    call :meth:`~src.database.adapters.base.DatabaseAdapter.connect` /
    :meth:`~src.database.adapters.base.DatabaseAdapter.disconnect` explicitly.

    Args:
        adapter_type: One of ``"oracle"``, ``"postgresql"``, or ``"sqlite"``.
            When *None*, the ``DB_ADAPTER`` environment variable is read;
            defaults to ``"oracle"`` if neither is set.

    Returns:
        A concrete :class:`~src.database.adapters.base.DatabaseAdapter`
        instance (not yet connected).

    Raises:
        ValueError: If *adapter_type* is not a recognised adapter name.

    Example::

        with get_database_adapter("sqlite") as adapter:
            df = adapter.execute_query("SELECT 1 AS n")
    """
    resolved = adapter_type or os.getenv("DB_ADAPTER", "oracle")

    if resolved not in _ADAPTER_MAP:
        raise ValueError(
            f"Unknown adapter: {resolved!r}.  "
            f"Valid options are: {', '.join(sorted(_ADAPTER_MAP))}"
        )

    # Dynamic import keeps optional driver failures local to connect().
    import importlib

    module_path, class_name = _ADAPTER_MAP[resolved].rsplit(".", 1)
    module = importlib.import_module(module_path)
    adapter_class = getattr(module, class_name)
    return adapter_class()
