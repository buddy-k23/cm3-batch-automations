"""Unit tests for src.utils.secrets — pluggable secrets provider."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch, Mock

import pytest

from src.utils.secrets import (
    SecretsProvider,
    EnvSecretsProvider,
    HashiCorpVaultSecretsProvider,
    AzureKeyVaultSecretsProvider,
    get_secrets_provider,
)


# ---------------------------------------------------------------------------
# EnvSecretsProvider
# ---------------------------------------------------------------------------


class TestEnvSecretsProvider:
    """Tests for EnvSecretsProvider."""

    def test_reads_from_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """EnvSecretsProvider returns the value of the named env var."""
        monkeypatch.setenv("MY_SECRET", "hunter2")
        provider = EnvSecretsProvider()
        assert provider.get_secret("MY_SECRET") == "hunter2"

    def test_returns_empty_string_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing env var returns empty string (matching os.getenv default)."""
        monkeypatch.delenv("NONEXISTENT_VAR", raising=False)
        provider = EnvSecretsProvider()
        assert provider.get_secret("NONEXISTENT_VAR") == ""

    def test_returns_default_when_provided(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default value is returned when env var is not set."""
        monkeypatch.delenv("MISSING_KEY", raising=False)
        provider = EnvSecretsProvider()
        assert provider.get_secret("MISSING_KEY", default="fallback") == "fallback"

    def test_env_value_takes_precedence_over_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When env var is set, default is ignored."""
        monkeypatch.setenv("PRESENT_KEY", "real_value")
        provider = EnvSecretsProvider()
        assert provider.get_secret("PRESENT_KEY", default="fallback") == "real_value"


# ---------------------------------------------------------------------------
# get_secrets_provider factory
# ---------------------------------------------------------------------------


class TestGetSecretsProvider:
    """Tests for the get_secrets_provider factory function."""

    def test_default_returns_env_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When SECRETS_PROVIDER is unset, factory returns EnvSecretsProvider."""
        monkeypatch.delenv("SECRETS_PROVIDER", raising=False)
        provider = get_secrets_provider()
        assert isinstance(provider, EnvSecretsProvider)

    def test_env_value_returns_env_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SECRETS_PROVIDER=env returns EnvSecretsProvider."""
        monkeypatch.setenv("SECRETS_PROVIDER", "env")
        provider = get_secrets_provider()
        assert isinstance(provider, EnvSecretsProvider)

    def test_vault_value_returns_vault_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SECRETS_PROVIDER=vault returns HashiCorpVaultSecretsProvider."""
        monkeypatch.setenv("SECRETS_PROVIDER", "vault")
        monkeypatch.setenv("VAULT_ADDR", "https://vault.example.com")
        monkeypatch.setenv("VAULT_ROLE_ID", "role-123")
        monkeypatch.setenv("VAULT_SECRET_ID", "secret-456")
        provider = get_secrets_provider()
        assert isinstance(provider, HashiCorpVaultSecretsProvider)

    def test_azure_value_returns_azure_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SECRETS_PROVIDER=azure returns AzureKeyVaultSecretsProvider."""
        monkeypatch.setenv("SECRETS_PROVIDER", "azure")
        monkeypatch.setenv("AZURE_VAULT_URL", "https://myvault.vault.azure.net")
        provider = get_secrets_provider()
        assert isinstance(provider, AzureKeyVaultSecretsProvider)

    def test_unknown_provider_raises_value_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unknown SECRETS_PROVIDER value raises ValueError."""
        monkeypatch.setenv("SECRETS_PROVIDER", "unknown_backend")
        with pytest.raises(ValueError, match="Unknown secrets provider.*unknown_backend"):
            get_secrets_provider()

    def test_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Provider name matching is case-insensitive."""
        monkeypatch.setenv("SECRETS_PROVIDER", "ENV")
        provider = get_secrets_provider()
        assert isinstance(provider, EnvSecretsProvider)


# ---------------------------------------------------------------------------
# HashiCorpVaultSecretsProvider
# ---------------------------------------------------------------------------


class TestHashiCorpVaultSecretsProvider:
    """Tests for HashiCorpVaultSecretsProvider."""

    def _make_provider(self) -> HashiCorpVaultSecretsProvider:
        """Create a provider with test configuration."""
        return HashiCorpVaultSecretsProvider(
            vault_addr="https://vault.example.com",
            role_id="test-role-id",
            secret_id="test-secret-id",
            secret_path="secret/data/cm3",
        )

    @patch("src.utils.secrets.urllib.request.urlopen")
    def test_authenticates_with_approle(self, mock_urlopen: MagicMock) -> None:
        """Vault provider sends AppRole auth request with correct payload."""
        # First call: auth login -> returns token
        auth_response = MagicMock()
        auth_response.read.return_value = json.dumps(
            {"auth": {"client_token": "s.test-token"}}
        ).encode()
        auth_response.__enter__ = lambda s: s
        auth_response.__exit__ = MagicMock(return_value=False)

        # Second call: read secret -> returns data
        secret_response = MagicMock()
        secret_response.read.return_value = json.dumps(
            {"data": {"data": {"ORACLE_PASSWORD": "vault-password"}}}
        ).encode()
        secret_response.__enter__ = lambda s: s
        secret_response.__exit__ = MagicMock(return_value=False)

        mock_urlopen.side_effect = [auth_response, secret_response]

        provider = self._make_provider()
        result = provider.get_secret("ORACLE_PASSWORD")

        assert result == "vault-password"

        # Verify auth request
        auth_call = mock_urlopen.call_args_list[0]
        auth_req = auth_call[0][0]
        assert auth_req.full_url == "https://vault.example.com/v1/auth/approle/login"
        auth_body = json.loads(auth_req.data)
        assert auth_body["role_id"] == "test-role-id"
        assert auth_body["secret_id"] == "test-secret-id"

    @patch("src.utils.secrets.urllib.request.urlopen")
    def test_reads_secret_with_token(self, mock_urlopen: MagicMock) -> None:
        """Vault provider sends X-Vault-Token header when reading secrets."""
        auth_response = MagicMock()
        auth_response.read.return_value = json.dumps(
            {"auth": {"client_token": "s.my-token"}}
        ).encode()
        auth_response.__enter__ = lambda s: s
        auth_response.__exit__ = MagicMock(return_value=False)

        secret_response = MagicMock()
        secret_response.read.return_value = json.dumps(
            {"data": {"data": {"DB_PASS": "secret123"}}}
        ).encode()
        secret_response.__enter__ = lambda s: s
        secret_response.__exit__ = MagicMock(return_value=False)

        mock_urlopen.side_effect = [auth_response, secret_response]

        provider = self._make_provider()
        provider.get_secret("DB_PASS")

        # Verify secret read request has token header
        secret_call = mock_urlopen.call_args_list[1]
        secret_req = secret_call[0][0]
        assert secret_req.full_url == "https://vault.example.com/v1/secret/data/cm3"
        assert secret_req.get_header("X-vault-token") == "s.my-token"

    @patch("src.utils.secrets.urllib.request.urlopen")
    def test_returns_default_when_key_missing(self, mock_urlopen: MagicMock) -> None:
        """Returns default when requested key is not in Vault response."""
        auth_response = MagicMock()
        auth_response.read.return_value = json.dumps(
            {"auth": {"client_token": "s.tok"}}
        ).encode()
        auth_response.__enter__ = lambda s: s
        auth_response.__exit__ = MagicMock(return_value=False)

        secret_response = MagicMock()
        secret_response.read.return_value = json.dumps(
            {"data": {"data": {"OTHER_KEY": "val"}}}
        ).encode()
        secret_response.__enter__ = lambda s: s
        secret_response.__exit__ = MagicMock(return_value=False)

        mock_urlopen.side_effect = [auth_response, secret_response]

        provider = self._make_provider()
        assert provider.get_secret("MISSING_KEY", default="fb") == "fb"

    @patch("src.utils.secrets.urllib.request.urlopen")
    def test_caches_token_across_calls(self, mock_urlopen: MagicMock) -> None:
        """Auth token is cached — second get_secret reuses the token."""
        auth_response = MagicMock()
        auth_response.read.return_value = json.dumps(
            {"auth": {"client_token": "s.cached"}}
        ).encode()
        auth_response.__enter__ = lambda s: s
        auth_response.__exit__ = MagicMock(return_value=False)

        def make_secret_response(data: dict) -> MagicMock:
            resp = MagicMock()
            resp.read.return_value = json.dumps({"data": {"data": data}}).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        mock_urlopen.side_effect = [
            auth_response,
            make_secret_response({"K1": "v1"}),
            make_secret_response({"K2": "v2"}),
        ]

        provider = self._make_provider()
        provider.get_secret("K1")
        provider.get_secret("K2")

        # Auth called once, secret read called twice = 3 total
        assert mock_urlopen.call_count == 3


# ---------------------------------------------------------------------------
# AzureKeyVaultSecretsProvider
# ---------------------------------------------------------------------------


class TestAzureKeyVaultSecretsProvider:
    """Tests for AzureKeyVaultSecretsProvider."""

    def test_constructs_correct_url(self) -> None:
        """Azure provider builds the correct secret URL from vault URL and key."""
        provider = AzureKeyVaultSecretsProvider(
            vault_url="https://myvault.vault.azure.net"
        )
        url = provider._build_secret_url("ORACLE-PASSWORD")
        assert url == (
            "https://myvault.vault.azure.net/secrets/ORACLE-PASSWORD"
            "?api-version=7.4"
        )

    def test_strips_trailing_slash_from_vault_url(self) -> None:
        """Trailing slash in vault URL does not cause double-slash in URL."""
        provider = AzureKeyVaultSecretsProvider(
            vault_url="https://myvault.vault.azure.net/"
        )
        url = provider._build_secret_url("MY-SECRET")
        assert "//secrets" not in url

    @patch("src.utils.secrets.urllib.request.urlopen")
    def test_reads_secret_via_http(self, mock_urlopen: MagicMock) -> None:
        """When azure-identity is not available, uses HTTP with managed identity."""
        response = MagicMock()
        response.read.return_value = json.dumps({"value": "az-secret-value"}).encode()
        response.__enter__ = lambda s: s
        response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = response

        provider = AzureKeyVaultSecretsProvider(
            vault_url="https://myvault.vault.azure.net"
        )
        # Force HTTP fallback by ensuring _use_sdk is False
        provider._use_sdk = False

        result = provider.get_secret("MY_SECRET")
        assert result == "az-secret-value"

    def test_returns_default_on_error(self) -> None:
        """Returns default when secret retrieval fails."""
        provider = AzureKeyVaultSecretsProvider(
            vault_url="https://myvault.vault.azure.net"
        )
        provider._use_sdk = False

        with patch("src.utils.secrets.urllib.request.urlopen", side_effect=Exception("fail")):
            result = provider.get_secret("BAD_KEY", default="safe")

        assert result == "safe"


# ---------------------------------------------------------------------------
# Security — secrets never exposed in repr/str
# ---------------------------------------------------------------------------


class TestSecretsNotExposed:
    """Verify secrets are never leaked through string representations."""

    def test_vault_provider_repr_hides_secret_id(self) -> None:
        """HashiCorpVaultSecretsProvider repr/str must not contain secret_id."""
        provider = HashiCorpVaultSecretsProvider(
            vault_addr="https://vault.example.com",
            role_id="my-role",
            secret_id="super-secret-value-123",
            secret_path="secret/data/cm3",
        )
        text = repr(provider) + str(provider)
        assert "super-secret-value-123" not in text

    def test_vault_provider_repr_hides_token(self) -> None:
        """Cached Vault token must not appear in repr/str."""
        provider = HashiCorpVaultSecretsProvider(
            vault_addr="https://vault.example.com",
            role_id="my-role",
            secret_id="sec",
            secret_path="secret/data/cm3",
        )
        provider._token = "s.sensitive-token-value"
        text = repr(provider) + str(provider)
        assert "s.sensitive-token-value" not in text

    def test_azure_provider_repr_is_safe(self) -> None:
        """AzureKeyVaultSecretsProvider repr does not leak credentials."""
        provider = AzureKeyVaultSecretsProvider(
            vault_url="https://myvault.vault.azure.net"
        )
        # Should not raise and should be a safe string
        text = repr(provider)
        assert "AzureKeyVaultSecretsProvider" in text
