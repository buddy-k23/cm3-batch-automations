"""Unit tests for db_profiles_service — written before implementation (TDD)."""
from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest


class TestLoadProfiles:
    def test_returns_empty_list_when_file_absent(self, tmp_path: Path) -> None:
        from src.services.db_profiles_service import load_profiles
        result = load_profiles(tmp_path / "nonexistent.yaml")
        assert result == []

    def test_returns_empty_list_on_yaml_parse_error(self, tmp_path: Path) -> None:
        from src.services.db_profiles_service import load_profiles
        bad = tmp_path / "bad.yaml"
        bad.write_text(": : invalid yaml :::", encoding="utf-8")
        result = load_profiles(bad)
        assert result == []

    def test_returns_empty_list_when_connections_key_missing(self, tmp_path: Path) -> None:
        from src.services.db_profiles_service import load_profiles
        f = tmp_path / "cfg.yaml"
        f.write_text("other_key: []\n", encoding="utf-8")
        result = load_profiles(f)
        assert result == []

    def test_loads_single_profile(self, tmp_path: Path) -> None:
        from src.services.db_profiles_service import load_profiles
        f = tmp_path / "cfg.yaml"
        f.write_text(textwrap.dedent("""\
            connections:
              - name: "Local Dev"
                adapter: oracle
                host: "localhost:1521/FREEPDB1"
                user: "APP_INT"
                schema: "APP_INT"
                password_env: "ORACLE_PASSWORD"
        """), encoding="utf-8")
        profiles = load_profiles(f)
        assert len(profiles) == 1
        p = profiles[0]
        assert p.name == "Local Dev"
        assert p.adapter == "oracle"
        assert p.host == "localhost:1521/FREEPDB1"
        assert p.user == "APP_INT"
        assert p.db_schema == "APP_INT"
        assert p.password_env == "ORACLE_PASSWORD"

    def test_password_env_set_true_when_env_var_present(self, tmp_path: Path, monkeypatch) -> None:
        from src.services.db_profiles_service import load_profiles
        monkeypatch.setenv("ORACLE_PASSWORD", "secret")
        f = tmp_path / "cfg.yaml"
        f.write_text(textwrap.dedent("""\
            connections:
              - name: "Local Dev"
                adapter: oracle
                host: "localhost:1521/FREEPDB1"
                user: "APP_INT"
                schema: "APP_INT"
                password_env: "ORACLE_PASSWORD"
        """), encoding="utf-8")
        profiles = load_profiles(f)
        assert profiles[0].password_env_set is True

    def test_password_env_set_false_when_env_var_absent(self, tmp_path: Path, monkeypatch) -> None:
        from src.services.db_profiles_service import load_profiles
        monkeypatch.delenv("DB_MISSING_PASSWORD", raising=False)
        f = tmp_path / "cfg.yaml"
        f.write_text(textwrap.dedent("""\
            connections:
              - name: "Prod"
                adapter: oracle
                host: "prod:1521/PROD"
                user: "APP_INT"
                schema: "APP_INT"
                password_env: "DB_MISSING_PASSWORD"
        """), encoding="utf-8")
        profiles = load_profiles(f)
        assert profiles[0].password_env_set is False

    def test_loads_multiple_profiles(self, tmp_path: Path) -> None:
        from src.services.db_profiles_service import load_profiles
        f = tmp_path / "cfg.yaml"
        f.write_text(textwrap.dedent("""\
            connections:
              - name: "Dev"
                adapter: oracle
                host: "dev:1521/DEV"
                user: "U1"
                schema: "S1"
                password_env: "PW1"
              - name: "Prod"
                adapter: oracle
                host: "prod:1521/PROD"
                user: "U2"
                schema: "S2"
                password_env: "PW2"
        """), encoding="utf-8")
        profiles = load_profiles(f)
        assert len(profiles) == 2
        assert profiles[0].name == "Dev"
        assert profiles[1].name == "Prod"


class TestResolveProfile:
    def _write_cfg(self, path: Path, name: str, password_env: str) -> None:
        path.write_text(textwrap.dedent(f"""\
            connections:
              - name: "{name}"
                adapter: oracle
                host: "host:1521/SVC"
                user: "USR"
                schema: "SCH"
                password_env: "{password_env}"
        """), encoding="utf-8")

    def test_resolves_profile_returns_db_config(self, tmp_path: Path, monkeypatch) -> None:
        from src.services.db_profiles_service import resolve_profile
        monkeypatch.setenv("MY_PW", "hunter2")
        f = tmp_path / "cfg.yaml"
        self._write_cfg(f, "Dev", "MY_PW")
        cfg = resolve_profile("Dev", f)
        assert type(cfg).__name__ == "DbConfig"
        assert cfg.user == "USR"
        assert cfg.password == "hunter2"
        assert cfg.dsn == "host:1521/SVC"
        assert cfg.schema == "SCH"
        assert cfg.db_adapter == "oracle"

    def test_raises_key_error_for_unknown_profile(self, tmp_path: Path) -> None:
        from src.services.db_profiles_service import resolve_profile
        f = tmp_path / "cfg.yaml"
        self._write_cfg(f, "Dev", "MY_PW")
        with pytest.raises(KeyError, match="Profile not found"):
            resolve_profile("Nonexistent", f)

    def test_raises_runtime_error_when_env_var_missing(self, tmp_path: Path, monkeypatch) -> None:
        from src.services.db_profiles_service import resolve_profile
        monkeypatch.delenv("MISSING_PW", raising=False)
        f = tmp_path / "cfg.yaml"
        self._write_cfg(f, "Dev", "MISSING_PW")
        with pytest.raises(RuntimeError, match="MISSING_PW"):
            resolve_profile("Dev", f)
