"""Pydantic model for a named database connection profile."""
from __future__ import annotations

import os

from pydantic import BaseModel, ConfigDict, Field, computed_field


class DbProfile(BaseModel):
    """A named database connection profile (no password).

    Attributes:
        name: Human-readable display name shown in the UI dropdown.
        adapter: Database adapter: ``"oracle"``, ``"postgresql"``, or
            ``"sqlite"``.
        host: Host/DSN string (e.g. ``"localhost:1521/FREEPDB1"``).
        user: Database username.
        db_schema: Schema qualifier for SQL table references.
            Serialised as ``"schema"`` in YAML and JSON.
        password_env: Name of the environment variable that holds the password.
        password_env_set: Computed — ``True`` when the env var is non-empty.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str
    adapter: str
    host: str
    user: str
    db_schema: str = Field(alias="schema")
    password_env: str

    @computed_field  # type: ignore[misc]
    @property
    def password_env_set(self) -> bool:
        """Return True when the password environment variable is non-empty."""
        return bool(os.environ.get(self.password_env))
