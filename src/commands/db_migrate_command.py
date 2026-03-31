"""CLI handler for valdo db-migrate — runs Alembic migrations."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import click

# Absolute path to alembic.ini at the project root.
_ALEMBIC_INI = str(
    Path(__file__).resolve().parent.parent.parent / "alembic.ini"
)


def _get_alembic_config(ini_path: str) -> Any:
    """Load and return an Alembic :class:`~alembic.config.Config` object.

    The installed ``alembic`` package is accessed via an explicit path
    injection into ``sys.path`` so that the local ``alembic/`` migration
    directory does not shadow the installed package.

    Args:
        ini_path: Absolute path to ``alembic.ini``.

    Returns:
        An ``alembic.config.Config`` instance.
    """
    # Temporarily remove the project root from sys.path so that the local
    # alembic/ directory is skipped and the installed alembic package is used.
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    original_path = sys.path[:]
    sys.path = [p for p in sys.path if os.path.realpath(p) != os.path.realpath(project_root)]
    try:
        from alembic.config import Config  # noqa: PLC0415
        return Config(ini_path)
    finally:
        sys.path = original_path


def _get_alembic_command() -> Any:
    """Return the ``alembic.command`` module, bypassing the local alembic/ dir.

    Returns:
        The ``alembic.command`` module object.
    """
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    original_path = sys.path[:]
    sys.path = [p for p in sys.path if os.path.realpath(p) != os.path.realpath(project_root)]
    try:
        import importlib  # noqa: PLC0415
        return importlib.import_module("alembic.command")
    finally:
        sys.path = original_path


def run_db_migrate(
    revision: str = "head",
    downgrade: bool = False,
    dry_run: bool = False,
    *,
    _alembic_cfg: Any = None,
    _alembic_cmd: Any = None,
) -> None:
    """Run Alembic database migrations.

    Locates ``alembic.ini`` at the project root and delegates to
    :mod:`alembic.command` for the actual migration work.

    Args:
        revision: Target revision label (default ``'head'``).  Pass
            ``'base'`` when downgrading all migrations.
        downgrade: When ``True``, run :func:`alembic.command.downgrade`
            instead of :func:`alembic.command.upgrade`.
        dry_run: When ``True``, emit SQL without executing it (Alembic
            offline / ``--sql`` mode).  The database URL is cleared so
            that no live connection is attempted.
        _alembic_cfg: Injectable Alembic Config object (used by tests).
            Defaults to loading ``alembic.ini`` from the project root.
        _alembic_cmd: Injectable alembic.command module (used by tests).
            Defaults to the installed alembic package's command module.

    Raises:
        SystemExit: Propagated from Alembic when the migration fails.
    """
    alembic_cfg = _alembic_cfg if _alembic_cfg is not None else _get_alembic_config(_ALEMBIC_INI)
    alembic_cmd = _alembic_cmd if _alembic_cmd is not None else _get_alembic_command()

    if dry_run:
        # Offline mode: clear the URL so Alembic never connects and emit SQL.
        alembic_cfg.set_main_option("sqlalchemy.url", "")
        alembic_cmd.upgrade(alembic_cfg, revision, sql=True)
        return

    if downgrade:
        alembic_cmd.downgrade(alembic_cfg, revision)
    else:
        alembic_cmd.upgrade(alembic_cfg, revision)
