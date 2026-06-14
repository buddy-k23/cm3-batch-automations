"""Unit tests for ``scripts.lib.secret_resolver``."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.e2e_lib.secret_resolver import (  # noqa: E402
    EnvSecretProvider,
    SecretResolver,
    SecretResolverError,
    VaultSecretProvider,
)


class TestEnvProvider:
    def test_fetch_returns_set_value(self, monkeypatch):
        monkeypatch.setenv("MY_SECRET", "abc123")
        provider = EnvSecretProvider()
        assert provider.fetch("MY_SECRET") == "abc123"

    def test_fetch_returns_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("MY_SECRET", raising=False)
        assert EnvSecretProvider().fetch("MY_SECRET") is None


class TestSecretResolverGet:
    def test_get_returns_value_from_env(self, monkeypatch):
        monkeypatch.setenv("ORACLE_DSN_SIT", "host:1521/svc")
        resolver = SecretResolver(provider=EnvSecretProvider())
        assert resolver.get("ORACLE_DSN_SIT") == "host:1521/svc"

    def test_get_raises_when_missing(self, monkeypatch):
        monkeypatch.delenv("ORACLE_DSN_SIT", raising=False)
        resolver = SecretResolver(provider=EnvSecretProvider())
        with pytest.raises(SecretResolverError, match="ORACLE_DSN_SIT"):
            resolver.get("ORACLE_DSN_SIT")

    def test_get_raises_when_empty_string(self, monkeypatch):
        monkeypatch.setenv("EMPTY_SECRET", "")
        resolver = SecretResolver(provider=EnvSecretProvider())
        with pytest.raises(SecretResolverError, match="EMPTY_SECRET"):
            resolver.get("EMPTY_SECRET")

    def test_get_rejects_empty_name(self):
        resolver = SecretResolver(provider=EnvSecretProvider())
        with pytest.raises(SecretResolverError, match="non-empty"):
            resolver.get("")

    def test_get_error_message_does_not_leak_value(self, monkeypatch):
        monkeypatch.setenv("SOME_SECRET", "supersecret-value")
        resolver = SecretResolver(provider=EnvSecretProvider())
        # The value IS present, so .get succeeds; verify the value is not
        # accidentally echoed by any other path. (Defensive — protects against
        # future log statements.)
        assert "supersecret-value" not in repr(resolver)


class TestSecretResolverOptional:
    def test_get_optional_returns_default_when_missing(self, monkeypatch):
        monkeypatch.delenv("NOPE", raising=False)
        resolver = SecretResolver(provider=EnvSecretProvider())
        assert resolver.get_optional("NOPE", default="fallback") == "fallback"

    def test_get_optional_returns_value_when_set(self, monkeypatch):
        monkeypatch.setenv("YES", "real")
        resolver = SecretResolver(provider=EnvSecretProvider())
        assert resolver.get_optional("YES", default="fallback") == "real"


class TestSecretResolverDefaultFactory:
    def test_default_uses_env_when_unset(self, monkeypatch):
        monkeypatch.delenv("SECRETS_PROVIDER", raising=False)
        resolver = SecretResolver.default()
        assert isinstance(resolver.provider, EnvSecretProvider)

    def test_default_uses_env_explicitly(self, monkeypatch):
        monkeypatch.setenv("SECRETS_PROVIDER", "env")
        resolver = SecretResolver.default()
        assert isinstance(resolver.provider, EnvSecretProvider)

    def test_default_uses_vault_stub(self, monkeypatch):
        monkeypatch.setenv("SECRETS_PROVIDER", "vault")
        resolver = SecretResolver.default()
        assert isinstance(resolver.provider, VaultSecretProvider)

    def test_default_rejects_unknown_provider(self, monkeypatch):
        monkeypatch.setenv("SECRETS_PROVIDER", "wat")
        with pytest.raises(SecretResolverError, match="unsupported"):
            SecretResolver.default()

    def test_vault_provider_raises_until_implemented(self):
        provider = VaultSecretProvider()
        with pytest.raises(SecretResolverError, match="not yet implemented"):
            provider.fetch("ANYTHING")
