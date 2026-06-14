"""Service for loading and resolving named database connection profiles.

Named profiles are defined in ``config/db_connections.yaml``.  Passwords are
**never** stored in the file — each profile names an environment variable
(``password_env``) that holds the secret at runtime.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

from src.api.models.db_profile import DbProfile
from src.config.db_config import DbConfig

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "db_connections.yaml"


def load_profiles(path: Path | None = None) -> list[DbProfile]:
    """Load named DB profiles from a YAML config file.

    Returns an empty list — without raising — when the file is absent,
    empty, or malformed.

    Args:
        path: Path to the YAML config file. Defaults to
            ``config/db_connections.yaml`` relative to the project root.

    Returns:
        List of :class:`DbProfile` instances. Empty list on any error.
    """
    if path is None:
        path = _DEFAULT_CONFIG_PATH

    if not path.exists():
        return []

    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        logger.warning("Failed to parse db_connections.yaml: %s", exc)
        return []

    if not isinstance(raw, dict):
        return []

    connections = raw.get("connections") or []
    profiles: list[DbProfile] = []
    for entry in connections:
        try:
            profiles.append(DbProfile(**entry))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping invalid db_connections entry %r: %s", entry, exc)
    return profiles


def resolve_profile(name: str, path: Path | None = None) -> DbConfig:
    """Resolve a named profile to a full :class:`DbConfig` with password.

    Args:
        name: Profile name as it appears in ``db_connections.yaml``.
        path: Path to the YAML config file. Defaults to
            ``config/db_connections.yaml`` relative to the project root.

    Returns:
        A fully populated :class:`DbConfig` ready for use.

    Raises:
        KeyError: If no profile with ``name`` exists in the config.
        RuntimeError: If the profile's ``password_env`` is not set in the
            environment.
    """
    profiles = load_profiles(path)
    profile = next((p for p in profiles if p.name == name), None)
    if profile is None:
        raise KeyError(f"Profile not found: {name!r}")

    password = os.environ.get(profile.password_env)
    if not password:
        raise RuntimeError(
            f"Password env var {profile.password_env!r} is not set on the server "
            f"(required by profile {name!r})"
        )

    return DbConfig(
        user=profile.user,
        password=password,
        dsn=profile.host,
        schema=profile.db_schema,
        db_adapter=profile.adapter,
    )
