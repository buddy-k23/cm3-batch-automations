"""S16-4 (#424) — dead config layer removal + unified DB defaults + secrets threading.

These tests pin the three acceptance criteria:

1. The dead ``config/<env>.json`` loader (operator trap) is gone — neither the
   ``ConfigLoader.load`` env-config method nor the committed ``config/*.json``
   environment files exist, so editing them can no longer silently no-op.
2. The DB adapters and the central :func:`~src.config.db_config.get_db_config`
   agree on defaults — in particular there is exactly ONE PostgreSQL ``DB_NAME``
   default (``valdo``), not the old ``postgres`` vs ``valdo`` divergence.
3. A factory-built adapter resolves its password through the configured
   ``SECRETS_PROVIDER`` (e.g. Vault/Azure), not via a raw ``os.environ`` read.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# 1. Dead config layer removed (no silent no-op env-config layer)
# ---------------------------------------------------------------------------


class TestDeadConfigLayerRemoved:
    """The env-config operator trap must be deleted."""

    def test_config_loader_has_no_env_load_method(self) -> None:
        """ConfigLoader.load(environment) — the dead trap — is gone."""
        from src.config.loader import ConfigLoader

        assert not hasattr(ConfigLoader, "load"), (
            "ConfigLoader.load(environment) read config/<env>.json but was only "
            "ever called in tests; production reads env vars. It must be deleted "
            "so editing config/<env>.json can no longer silently no-op."
        )

    def test_config_loader_has_no_merge_with_env(self) -> None:
        """merge_with_env was only exercised by the now-removed loader test."""
        from src.config.loader import ConfigLoader

        assert not hasattr(ConfigLoader, "merge_with_env")

    def test_load_mapping_still_present(self) -> None:
        """The LIVE method (used by reconcile/ETL) must survive the deletion."""
        from src.config.loader import ConfigLoader

        assert hasattr(ConfigLoader, "load_mapping")

    def test_env_config_json_files_deleted(self) -> None:
        """config/dev.json, staging.json, int.json (the trap) must not exist."""
        for name in ("dev.json", "staging.json", "int.json"):
            assert not (REPO_ROOT / "config" / name).exists(), (
                f"config/{name} was a dead env-config file with no runtime "
                f"consumer (operator trap); it must be removed."
            )


# ---------------------------------------------------------------------------
# 2. Unified DB-config defaults (no 'postgres' vs 'valdo' divergence)
# ---------------------------------------------------------------------------


class TestUnifiedDefaults:
    """Adapter defaults and the central config must agree."""

    def test_postgres_dbname_default_is_valdo_everywhere(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The PostgreSQL DB_NAME default is 'valdo' in BOTH config sources."""
        for var in ("DB_NAME", "DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("DB_ADAPTER", "postgresql")

        from src.config.db_config import get_db_config
        from src.database.db_url import get_db_url
        from src.database.adapters.postgresql_adapter import PostgreSQLAdapter

        cfg = get_db_config()
        adapter = PostgreSQLAdapter()
        url = get_db_url()

        # One source of truth: all three resolve the same default name.
        assert cfg.db_name == "valdo"
        assert adapter.database == "valdo"
        assert url.endswith("/valdo")

    def test_adapter_builds_from_central_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Env values flow through get_db_config into the adapter, not via a
        second independent env read with divergent defaults."""
        monkeypatch.setenv("DB_ADAPTER", "postgresql")
        monkeypatch.setenv("DB_HOST", "pghost")
        monkeypatch.setenv("DB_PORT", "6543")
        monkeypatch.setenv("DB_NAME", "mydb")
        monkeypatch.setenv("DB_USER", "pguser")
        monkeypatch.setenv("DB_PASSWORD", "pgpass")

        from src.database.adapters.postgresql_adapter import PostgreSQLAdapter

        adapter = PostgreSQLAdapter()
        assert adapter.host == "pghost"
        assert adapter.port == 6543
        assert adapter.database == "mydb"
        assert adapter.username == "pguser"
        assert adapter.password == "pgpass"

    def test_oracle_adapter_default_matches_central_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Oracle adapter user/dsn defaults equal get_db_config()'s."""
        for var in ("ORACLE_USER", "ORACLE_PASSWORD", "ORACLE_DSN", "ORACLE_SCHEMA"):
            monkeypatch.delenv(var, raising=False)

        from src.config.db_config import get_db_config
        from src.database.adapters.oracle_adapter import OracleAdapter

        cfg = get_db_config()
        adapter = OracleAdapter()
        assert adapter.username == cfg.user
        assert adapter.dsn == cfg.dsn


# ---------------------------------------------------------------------------
# 3. SECRETS_PROVIDER threads through factory-built adapters
# ---------------------------------------------------------------------------


class TestSecretsProviderThreading:
    """Factory-built adapters resolve credentials via the provider."""

    def test_oracle_adapter_password_via_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A factory-created Oracle adapter pulls ORACLE_PASSWORD from the
        configured secrets provider, not a raw env read."""
        monkeypatch.delenv("ORACLE_PASSWORD", raising=False)
        monkeypatch.setenv("DB_ADAPTER", "oracle")

        mock_provider = MagicMock()
        # Provider returns a value that is NOT in os.environ — proving the
        # adapter went through the provider rather than os.getenv.
        mock_provider.get_secret.side_effect = lambda key, default="": {
            "ORACLE_USER": "VAULTUSER",
            "ORACLE_PASSWORD": "vault-secret",
            "ORACLE_DSN": "vaulthost:1521/VDB",
            "ORACLE_SCHEMA": "VAULTUSER",
        }.get(key, default)

        with patch(
            "src.config.db_config.get_secrets_provider", return_value=mock_provider
        ):
            from src.database.adapters.factory import get_database_adapter

            adapter = get_database_adapter()

        assert adapter.password == "vault-secret"
        assert adapter.username == "VAULTUSER"
        # The provider was actually consulted for the password.
        called_keys = {c.args[0] for c in mock_provider.get_secret.call_args_list}
        assert "ORACLE_PASSWORD" in called_keys

    def test_postgres_adapter_password_via_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A factory-created PostgreSQL adapter pulls DB_PASSWORD from the
        configured secrets provider."""
        for var in ("DB_PASSWORD", "DB_USER", "DB_NAME", "DB_HOST", "DB_PORT"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("DB_ADAPTER", "postgresql")

        mock_provider = MagicMock()
        mock_provider.get_secret.side_effect = lambda key, default="": {
            "DB_USER": "pgvaultuser",
            "DB_PASSWORD": "pg-vault-secret",
        }.get(key, default)

        with patch(
            "src.config.db_config.get_secrets_provider", return_value=mock_provider
        ):
            from src.database.adapters.factory import get_database_adapter

            adapter = get_database_adapter()

        assert adapter.password == "pg-vault-secret"
        assert adapter.username == "pgvaultuser"
        called_keys = {c.args[0] for c in mock_provider.get_secret.call_args_list}
        assert "DB_PASSWORD" in called_keys
