"""Unit tests for the valdo db-migrate CLI command."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_alembic():
    """Return a fresh MagicMock standing in for alembic.command."""
    return MagicMock()


def _mock_cfg():
    """Return a fresh MagicMock standing in for an Alembic Config object."""
    return MagicMock()


# ---------------------------------------------------------------------------
# run_db_migrate() function tests
# ---------------------------------------------------------------------------

class TestRunDbMigrate:
    """Tests for the run_db_migrate service function."""

    def test_upgrade_head_by_default(self):
        """run_db_migrate() calls alembic upgrade with 'head' when no args given."""
        from src.commands.db_migrate_command import run_db_migrate

        cmd = _mock_alembic()
        cfg = _mock_cfg()
        run_db_migrate(_alembic_cfg=cfg, _alembic_cmd=cmd)

        cmd.upgrade.assert_called_once_with(cfg, "head")
        cmd.downgrade.assert_not_called()

    def test_upgrade_specific_revision(self):
        """run_db_migrate(revision='0001') calls upgrade with '0001'."""
        from src.commands.db_migrate_command import run_db_migrate

        cmd = _mock_alembic()
        cfg = _mock_cfg()
        run_db_migrate(revision="0001", _alembic_cfg=cfg, _alembic_cmd=cmd)

        cmd.upgrade.assert_called_once_with(cfg, "0001")

    def test_downgrade_calls_downgrade(self):
        """run_db_migrate(downgrade=True) calls alembic downgrade instead of upgrade."""
        from src.commands.db_migrate_command import run_db_migrate

        cmd = _mock_alembic()
        cfg = _mock_cfg()
        run_db_migrate(downgrade=True, _alembic_cfg=cfg, _alembic_cmd=cmd)

        cmd.downgrade.assert_called_once_with(cfg, "head")
        cmd.upgrade.assert_not_called()

    def test_downgrade_specific_revision(self):
        """run_db_migrate(revision='base', downgrade=True) passes 'base' to downgrade."""
        from src.commands.db_migrate_command import run_db_migrate

        cmd = _mock_alembic()
        cfg = _mock_cfg()
        run_db_migrate(revision="base", downgrade=True, _alembic_cfg=cfg, _alembic_cmd=cmd)

        cmd.downgrade.assert_called_once_with(cfg, "base")

    def test_dry_run_uses_sql_mode(self):
        """run_db_migrate(dry_run=True) calls upgrade in offline (sql=True) mode."""
        from src.commands.db_migrate_command import run_db_migrate

        cmd = _mock_alembic()
        cfg = _mock_cfg()
        run_db_migrate(dry_run=True, _alembic_cfg=cfg, _alembic_cmd=cmd)

        cfg.set_main_option.assert_called_with("sqlalchemy.url", "")
        cmd.upgrade.assert_called_once_with(cfg, "head", sql=True)

    def test_dry_run_does_not_call_downgrade(self):
        """run_db_migrate(dry_run=True) never calls downgrade even if downgrade=True."""
        from src.commands.db_migrate_command import run_db_migrate

        cmd = _mock_alembic()
        cfg = _mock_cfg()
        run_db_migrate(dry_run=True, downgrade=True, _alembic_cfg=cfg, _alembic_cmd=cmd)

        cmd.downgrade.assert_not_called()
        cmd.upgrade.assert_called_once()

    def test_config_path_uses_alembic_ini(self):
        """_ALEMBIC_INI constant ends with 'alembic.ini'."""
        from src.commands.db_migrate_command import _ALEMBIC_INI

        assert _ALEMBIC_INI.endswith("alembic.ini"), (
            f"Expected _ALEMBIC_INI to end with 'alembic.ini', got: {_ALEMBIC_INI}"
        )


# ---------------------------------------------------------------------------
# CLI integration tests via Click TestRunner
# ---------------------------------------------------------------------------

class TestDbMigrateCliCommand:
    """Tests for the db-migrate Click command registered in src/main.py."""

    def _get_cli(self):
        """Return the CLI group from src/main.py."""
        from src.main import cli
        return cli

    def test_db_migrate_command_exists(self):
        """valdo db-migrate --help exits 0 and lists expected options."""
        runner = CliRunner()
        cli = self._get_cli()
        result = runner.invoke(cli, ["db-migrate", "--help"])
        assert result.exit_code == 0, f"Help failed:\n{result.output}"
        assert "--revision" in result.output
        assert "--downgrade" in result.output
        assert "--dry-run" in result.output

    def test_db_migrate_invokes_run_function_with_defaults(self):
        """valdo db-migrate calls run_db_migrate with default arguments."""
        runner = CliRunner()
        cli = self._get_cli()

        with patch("src.commands.db_migrate_command.run_db_migrate") as mock_run:
            result = runner.invoke(cli, ["db-migrate"])
            assert result.exit_code == 0, f"Command failed:\n{result.output}"
            mock_run.assert_called_once_with(
                revision="head", downgrade=False, dry_run=False
            )

    def test_db_migrate_revision_option(self):
        """valdo db-migrate --revision 0001 passes revision correctly."""
        runner = CliRunner()
        cli = self._get_cli()

        with patch("src.commands.db_migrate_command.run_db_migrate") as mock_run:
            result = runner.invoke(cli, ["db-migrate", "--revision", "0001"])
            assert result.exit_code == 0, f"Command failed:\n{result.output}"
            mock_run.assert_called_once_with(
                revision="0001", downgrade=False, dry_run=False
            )

    def test_db_migrate_downgrade_flag(self):
        """valdo db-migrate --downgrade passes downgrade=True."""
        runner = CliRunner()
        cli = self._get_cli()

        with patch("src.commands.db_migrate_command.run_db_migrate") as mock_run:
            result = runner.invoke(cli, ["db-migrate", "--downgrade"])
            assert result.exit_code == 0, f"Command failed:\n{result.output}"
            mock_run.assert_called_once_with(
                revision="head", downgrade=True, dry_run=False
            )

    def test_db_migrate_dry_run_flag(self):
        """valdo db-migrate --dry-run passes dry_run=True."""
        runner = CliRunner()
        cli = self._get_cli()

        with patch("src.commands.db_migrate_command.run_db_migrate") as mock_run:
            result = runner.invoke(cli, ["db-migrate", "--dry-run"])
            assert result.exit_code == 0, f"Command failed:\n{result.output}"
            mock_run.assert_called_once_with(
                revision="head", downgrade=False, dry_run=True
            )
