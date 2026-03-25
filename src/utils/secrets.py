"""Pluggable secrets provider for vault integration.

Abstracts secret retrieval behind a :class:`SecretsProvider` interface so that
application code (e.g. database config) can read credentials from environment
variables, HashiCorp Vault, Azure Key Vault, or any future backend without
code changes.

Usage::

    from src.utils.secrets import get_secrets_provider

    provider = get_secrets_provider()          # reads SECRETS_PROVIDER env var
    password = provider.get_secret("ORACLE_PASSWORD")

Environment variables
---------------------
``SECRETS_PROVIDER``
    Which backend to use.  One of ``env`` (default), ``vault``, ``azure``.

For HashiCorp Vault (``vault``):
    ``VAULT_ADDR``, ``VAULT_ROLE_ID``, ``VAULT_SECRET_ID``,
    ``VAULT_SECRET_PATH`` (default: ``secret/data/cm3``).

For Azure Key Vault (``azure``):
    ``AZURE_VAULT_URL``.  Uses ``azure-identity`` SDK if installed,
    otherwise falls back to managed-identity HTTP endpoint.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class SecretsProvider(ABC):
    """Abstract base class for secrets providers.

    All implementations must override :meth:`get_secret`.  Secrets must
    **never** appear in log output — only key names may be logged.
    """

    @abstractmethod
    def get_secret(self, key: str, *, default: str = "") -> str:
        """Retrieve a secret value by key.

        Args:
            key: The name / identifier of the secret.
            default: Value to return when the secret is not found.

        Returns:
            The secret value, or *default* if not found.
        """


# ---------------------------------------------------------------------------
# Environment-variable provider (default)
# ---------------------------------------------------------------------------


class EnvSecretsProvider(SecretsProvider):
    """Reads secrets from ``os.environ``.

    This is the default provider and preserves the pre-existing behaviour of
    reading credentials directly from environment variables.
    """

    def get_secret(self, key: str, *, default: str = "") -> str:
        """Return the value of environment variable *key*.

        Args:
            key: Environment variable name.
            default: Fallback when the variable is not set.

        Returns:
            Environment variable value, or *default*.
        """
        logger.debug("EnvSecretsProvider: reading key=%s", key)
        return os.getenv(key, default)

    def __repr__(self) -> str:
        return "EnvSecretsProvider()"


# ---------------------------------------------------------------------------
# HashiCorp Vault provider (AppRole auth)
# ---------------------------------------------------------------------------


class HashiCorpVaultSecretsProvider(SecretsProvider):
    """Reads secrets from HashiCorp Vault using AppRole authentication.

    Authenticates via the ``/v1/auth/approle/login`` endpoint and then reads
    secrets from the configured *secret_path*.  The Vault token is cached for
    the lifetime of the provider instance.

    Uses only :mod:`urllib.request` — no third-party dependencies required.

    Args:
        vault_addr: Base URL of the Vault server (e.g. ``https://vault.example.com``).
        role_id: AppRole role ID.
        secret_id: AppRole secret ID.
        secret_path: KV v2 secret path (default: ``secret/data/cm3``).
    """

    def __init__(
        self,
        vault_addr: str,
        role_id: str,
        secret_id: str,
        secret_path: str = "secret/data/cm3",
    ) -> None:
        self._vault_addr = vault_addr.rstrip("/")
        self._role_id = role_id
        self._secret_id = secret_id
        self._secret_path = secret_path
        self._token: Optional[str] = None

    # -- public API --------------------------------------------------------

    def get_secret(self, key: str, *, default: str = "") -> str:
        """Fetch a secret from Vault.

        Authenticates on first call, then reads the secret path and returns
        the value stored under *key*.

        Args:
            key: Key within the Vault secret data.
            default: Fallback if the key is not present.

        Returns:
            Secret value, or *default*.

        Raises:
            RuntimeError: If Vault authentication or secret read fails.
        """
        logger.debug("HashiCorpVaultSecretsProvider: reading key=%s", key)
        if self._token is None:
            self._authenticate()

        data = self._read_secret()
        return data.get(key, default)

    # -- internal ----------------------------------------------------------

    def _authenticate(self) -> None:
        """Authenticate to Vault via AppRole and cache the client token."""
        url = f"{self._vault_addr}/v1/auth/approle/login"
        payload = json.dumps(
            {"role_id": self._role_id, "secret_id": self._secret_id}
        ).encode()
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req) as resp:
                body = json.loads(resp.read())
            self._token = body["auth"]["client_token"]
            logger.info("HashiCorpVaultSecretsProvider: authenticated successfully")
        except Exception as exc:
            raise RuntimeError(
                f"Vault AppRole authentication failed (addr={self._vault_addr!r})"
            ) from exc

    def _read_secret(self) -> dict:
        """Read the full secret data dict from the configured path."""
        url = f"{self._vault_addr}/v1/{self._secret_path}"
        req = urllib.request.Request(
            url, headers={"X-Vault-Token": self._token}
        )
        try:
            with urllib.request.urlopen(req) as resp:
                body = json.loads(resp.read())
            return body.get("data", {}).get("data", {})
        except Exception as exc:
            raise RuntimeError(
                f"Failed to read Vault secret path={self._secret_path!r}"
            ) from exc

    # -- safety: never expose secret_id or token in repr -------------------

    def __repr__(self) -> str:
        return (
            f"HashiCorpVaultSecretsProvider("
            f"vault_addr={self._vault_addr!r}, "
            f"secret_path={self._secret_path!r})"
        )

    def __str__(self) -> str:
        return self.__repr__()


# ---------------------------------------------------------------------------
# Azure Key Vault provider
# ---------------------------------------------------------------------------


class AzureKeyVaultSecretsProvider(SecretsProvider):
    """Reads secrets from Azure Key Vault.

    If the ``azure-identity`` and ``azure-keyvault-secrets`` packages are
    installed, uses :class:`DefaultAzureCredential` for authentication.
    Otherwise, falls back to the managed-identity HTTP endpoint via
    :mod:`urllib.request`.

    Args:
        vault_url: Azure Key Vault URL
            (e.g. ``https://myvault.vault.azure.net``).
    """

    _API_VERSION = "7.4"

    def __init__(self, vault_url: str) -> None:
        self._vault_url = vault_url.rstrip("/")
        self._use_sdk = self._try_init_sdk()

    # -- public API --------------------------------------------------------

    def get_secret(self, key: str, *, default: str = "") -> str:
        """Retrieve a secret from Azure Key Vault.

        Args:
            key: Secret name in the vault.
            default: Fallback if retrieval fails.

        Returns:
            Secret value, or *default*.
        """
        logger.debug("AzureKeyVaultSecretsProvider: reading key=%s", key)
        try:
            if self._use_sdk:
                return self._get_via_sdk(key)
            return self._get_via_http(key)
        except Exception:
            logger.warning(
                "AzureKeyVaultSecretsProvider: failed to read key=%s, "
                "returning default",
                key,
            )
            return default

    # -- internal ----------------------------------------------------------

    def _try_init_sdk(self) -> bool:
        """Attempt to import Azure SDK.  Returns True if available."""
        try:
            from azure.identity import DefaultAzureCredential  # type: ignore[import-untyped]
            from azure.keyvault.secrets import SecretClient  # type: ignore[import-untyped]

            self._credential = DefaultAzureCredential()
            self._client = SecretClient(
                vault_url=self._vault_url, credential=self._credential
            )
            logger.info("AzureKeyVaultSecretsProvider: using azure-identity SDK")
            return True
        except ImportError:
            logger.info(
                "AzureKeyVaultSecretsProvider: azure-identity not installed, "
                "falling back to HTTP"
            )
            return False

    def _get_via_sdk(self, key: str) -> str:
        """Read secret using the Azure SDK."""
        secret = self._client.get_secret(key)
        return secret.value or ""

    def _get_via_http(self, key: str) -> str:
        """Read secret using managed-identity HTTP endpoint."""
        url = self._build_secret_url(key)
        req = urllib.request.Request(url)
        # Managed identity token header (for Azure App Service / VM)
        identity_endpoint = os.getenv("IDENTITY_ENDPOINT", "")
        identity_header = os.getenv("IDENTITY_HEADER", "")
        if identity_endpoint and identity_header:
            req.add_header("X-IDENTITY-HEADER", identity_header)

        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read())
        return body.get("value", "")

    def _build_secret_url(self, key: str) -> str:
        """Build the REST API URL for a single secret.

        Args:
            key: Secret name.

        Returns:
            Full URL including api-version query parameter.
        """
        return (
            f"{self._vault_url}/secrets/{key}"
            f"?api-version={self._API_VERSION}"
        )

    def __repr__(self) -> str:
        return f"AzureKeyVaultSecretsProvider(vault_url={self._vault_url!r})"

    def __str__(self) -> str:
        return self.__repr__()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_secrets_provider() -> SecretsProvider:
    """Create a secrets provider based on the ``SECRETS_PROVIDER`` env var.

    Supported values (case-insensitive):

    - ``env`` (default): :class:`EnvSecretsProvider`
    - ``vault``: :class:`HashiCorpVaultSecretsProvider`
    - ``azure``: :class:`AzureKeyVaultSecretsProvider`

    Returns:
        A :class:`SecretsProvider` instance.

    Raises:
        ValueError: If ``SECRETS_PROVIDER`` is set to an unrecognised value.
    """
    provider_name = os.getenv("SECRETS_PROVIDER", "env").lower().strip()

    if provider_name == "env":
        logger.info("Using EnvSecretsProvider")
        return EnvSecretsProvider()

    if provider_name == "vault":
        logger.info("Using HashiCorpVaultSecretsProvider")
        return HashiCorpVaultSecretsProvider(
            vault_addr=os.getenv("VAULT_ADDR", ""),
            role_id=os.getenv("VAULT_ROLE_ID", ""),
            secret_id=os.getenv("VAULT_SECRET_ID", ""),
            secret_path=os.getenv("VAULT_SECRET_PATH", "secret/data/cm3"),
        )

    if provider_name == "azure":
        logger.info("Using AzureKeyVaultSecretsProvider")
        return AzureKeyVaultSecretsProvider(
            vault_url=os.getenv("AZURE_VAULT_URL", ""),
        )

    raise ValueError(
        f"Unknown secrets provider: {provider_name!r}.  "
        f"Supported values: env, vault, azure"
    )
