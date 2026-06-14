"""Centralized secret resolver for the E2E batch testing harness.

Goals
-----
- One chokepoint for every secret lookup so a future migration from
  environment variables to HashiCorp Vault is a config + adapter change,
  not a refactor across dozens of call sites.
- Zero secret values logged. Errors mention only the *name* of the missing
  secret, never its value.
- Pluggable providers via the :class:`SecretProvider` protocol so a Vault
  adapter can drop in without touching callers.

Usage
-----
::

    from scripts.e2e_lib.secret_resolver import SecretResolver

    resolver = SecretResolver.default()
    dsn = resolver.get("ORACLE_DSN_SIT")

The ``default()`` factory honors the ``SECRETS_PROVIDER`` env var:
``env`` (default) or ``vault``. The ``vault`` adapter is a stub — see
:class:`VaultSecretProvider`.

This module is the ONLY place in ``scripts/`` allowed to call ``os.environ``
for secret values. A repo-wide grep enforces this:

    git grep -n "os.environ" scripts/

should match only this file (plus any pre-existing legacy callers, which are
out of scope for the E2E harness).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

# Auto-load the repo-root .env so the E2E harness sees the same credentials
# as the rest of Valdo (which calls ``load_dotenv()`` in ``src/api/main.py``).
# Without this, wrapper scripts would silently miss ORACLE_USER /
# ORACLE_PASSWORD / ORACLE_DSN that the rest of the app can see. Real exported
# environment variables always win over .env (``override=False``).
try:
    from dotenv import load_dotenv  # type: ignore

    _DOTENV_PATH = Path(__file__).resolve().parents[2] / ".env"
    if _DOTENV_PATH.is_file():
        load_dotenv(_DOTENV_PATH, override=False)
except ImportError:  # pragma: no cover — dotenv is a base Valdo dependency
    pass


class SecretResolverError(RuntimeError):
    """Raised when a secret is missing or a provider misbehaves."""


class SecretProvider(Protocol):
    """Pluggable secret backend."""

    name: str

    def fetch(self, name: str) -> Optional[str]:
        """Return the secret value, or ``None`` if not found."""
        ...


# --------------------------------------------------------------------------- #
# Concrete providers
# --------------------------------------------------------------------------- #


@dataclass
class EnvSecretProvider:
    """Reads secrets from process environment variables."""

    name: str = "env"

    def fetch(self, secret_name: str) -> Optional[str]:
        # The sole sanctioned os.environ.get for the E2E harness.
        return os.environ.get(secret_name)


@dataclass
class VaultSecretProvider:
    """Stub adapter for HashiCorp Vault — to be implemented in a later milestone.

    The interface is fixed now so callers do not need to change when the
    real implementation lands. Until then it raises a clear error.
    """

    name: str = "vault"

    def fetch(self, secret_name: str) -> Optional[str]:
        raise SecretResolverError(
            "VaultSecretProvider is not yet implemented. "
            "Set SECRETS_PROVIDER=env or implement the adapter."
        )


# --------------------------------------------------------------------------- #
# Resolver
# --------------------------------------------------------------------------- #


@dataclass
class SecretResolver:
    """High-level secret accessor used by every wrapper script."""

    provider: SecretProvider

    @classmethod
    def default(cls) -> "SecretResolver":
        """Pick a provider based on ``SECRETS_PROVIDER`` (default: ``env``)."""
        choice = os.environ.get("SECRETS_PROVIDER", "env").strip().lower()
        if choice == "env":
            return cls(provider=EnvSecretProvider())
        if choice == "vault":
            return cls(provider=VaultSecretProvider())
        raise SecretResolverError(
            f"unsupported SECRETS_PROVIDER={choice!r}. "
            "Expected 'env' or 'vault'."
        )

    def get(self, name: str) -> str:
        """Return the secret value or raise if missing.

        Args:
            name: Name of the secret (env var name for the ``env`` provider,
                Vault key for the ``vault`` provider).

        Returns:
            The secret value as a string.

        Raises:
            SecretResolverError: If the secret is not set. The value itself
                is never included in the error message.
        """
        if not name:
            raise SecretResolverError("secret name must be non-empty")
        value = self.provider.fetch(name)
        if value is None or value == "":
            raise SecretResolverError(
                f"secret '{name}' is not set via provider '{self.provider.name}'"
            )
        return value

    def get_optional(self, name: str, default: Optional[str] = None) -> Optional[str]:
        """Return the secret value or ``default`` if not set.

        Use sparingly — most secrets should be mandatory.
        """
        try:
            return self.get(name)
        except SecretResolverError:
            return default
