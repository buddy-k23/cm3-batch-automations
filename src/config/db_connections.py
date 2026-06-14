"""Named database connection profiles parsed from the DB_CONNECTIONS env var.

Allows operators to pre-configure multiple database connections (e.g. STAGING,
DEV-1, PROD) that the DB Compare UI can select from without requiring users to
enter credentials manually.

Environment variables
---------------------
``DB_CONNECTIONS``
    JSON dict of named connection profiles.  Each key is a display name and
    each value is an object with ``host``, ``user``, ``password``, ``schema``,
    and ``adapter`` fields.  Example::

        DB_CONNECTIONS={"STAGING": {"host": "stg:1522/DB", "user": "Valdo",
            "password": "secret", "schema": "APP_INT", "adapter": "oracle"}}

    Returns an empty dict when unset or empty.  Malformed JSON or invalid
    adapter values are skipped with a warning log.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Dict

from pydantic import BaseModel, ConfigDict, field_validator

logger = logging.getLogger(__name__)

_VALID_ADAPTERS = {"oracle", "postgresql", "sqlite"}


class NamedDbConnection(BaseModel):
    """A single named database connection profile.

    Attributes:
        name: Display label for the connection (e.g. ``"STAGING"``, ``"DEV-1"``).
        host: Database host / DSN string (e.g. ``"stg:1522/DB"`` for Oracle or
            ``"localhost:5432"`` for PostgreSQL).
        user: Database username.
        password: Database password.
        schema: Schema prefix used in SQL table references
            (e.g. ``"APP_INT"``).
        adapter: Database adapter type.  Must be one of ``"oracle"``,
            ``"postgresql"``, or ``"sqlite"``.
    """

    # ``schema`` shadows a Pydantic BaseModel internal attribute in v2; the
    # warning is suppressed here because the field name is required by the
    # external JSON contract and works correctly at runtime.
    model_config = ConfigDict(extra="ignore")

    name: str
    host: str
    user: str
    password: str
    schema: str
    adapter: str

    @field_validator("adapter")
    @classmethod
    def adapter_must_be_valid(cls, v: str) -> str:
        """Validate that adapter is one of the supported values.

        Args:
            v: The adapter value to validate.

        Returns:
            The adapter string unchanged if valid.

        Raises:
            ValueError: If the adapter is not ``oracle``, ``postgresql``, or
                ``sqlite``.
        """
        if v not in _VALID_ADAPTERS:
            raise ValueError(
                f"adapter must be one of {sorted(_VALID_ADAPTERS)!r}, got {v!r}"
            )
        return v


def get_named_connections() -> Dict[str, NamedDbConnection]:
    """Parse named DB connection profiles from the ``DB_CONNECTIONS`` env var.

    Reads the ``DB_CONNECTIONS`` environment variable, expects a JSON object
    mapping connection names to connection parameter dicts, and returns a typed
    dict of :class:`NamedDbConnection` instances.

    Behaviour for edge cases:

    - Env var unset or empty string → returns ``{}``.
    - Malformed JSON → logs a warning and returns ``{}``.
    - Entry with an invalid ``adapter`` value → logs a warning, skips that
      entry, and continues parsing the rest.
    - Unknown extra fields in an entry → silently ignored (Pydantic
      ``extra="ignore"``).

    Returns:
        Dict mapping each connection name (str) to its
        :class:`NamedDbConnection` instance.  Empty dict when no valid
        connections are found.

    Example::

        import os
        os.environ["DB_CONNECTIONS"] = json.dumps({
            "STAGING": {
                "host": "stg:1522/DB",
                "user": "Valdo",
                "password": "secret",
                "schema": "APP_INT",
                "adapter": "oracle",
            }
        })
        conns = get_named_connections()
        print(conns["STAGING"].host)  # "stg:1522/DB"
    """
    raw = os.environ.get("DB_CONNECTIONS", "").strip()
    if not raw:
        return {}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("DB_CONNECTIONS is not valid JSON — skipping: %s", exc)
        return {}

    if not isinstance(data, dict):
        logger.warning(
            "DB_CONNECTIONS must be a JSON object (dict), got %s — skipping",
            type(data).__name__,
        )
        return {}

    connections: Dict[str, NamedDbConnection] = {}
    for name, entry in data.items():
        if not isinstance(entry, dict):
            logger.warning(
                "DB_CONNECTIONS[%r] is not an object — skipping", name
            )
            continue
        try:
            conn = NamedDbConnection(name=name, **entry)
            connections[name] = conn
        except Exception as exc:  # pydantic.ValidationError or TypeError
            logger.warning(
                "DB_CONNECTIONS[%r] is invalid and will be skipped: %s",
                name,
                exc,
            )

    return connections
