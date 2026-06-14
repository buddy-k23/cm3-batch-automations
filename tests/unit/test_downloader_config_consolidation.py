# tests/unit/test_downloader_config_consolidation.py
"""Unit tests for Issue #365 — env-based config consolidation into ui.yml."""

import io
import logging
import logging.handlers
from pathlib import Path
from unittest.mock import patch


def test_log_path_from_constructor(tmp_path):
    """DownloaderLogger accepts log_path as a constructor parameter."""
    from src.services.downloader_logger import DownloaderLogger

    log_file = tmp_path / "custom.log"
    dl = DownloaderLogger(log_path=str(log_file))
    dl.log_activity(
        operation="download",
        client_ip="1.2.3.4",
        client_host="host",
        path="/data",
        filename="f.txt",
        archive=None,
        status="success",
    )
    assert log_file.exists()
    import json
    entry = json.loads(log_file.read_text().strip())
    assert entry["operation"] == "download"


def test_log_path_default(tmp_path, monkeypatch):
    """DownloaderLogger uses default log path when none supplied."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "logs").mkdir()
    from src.services.downloader_logger import DownloaderLogger

    dl = DownloaderLogger()
    assert "file-downloads.log" in dl._log_path


def test_deprecated_file_downloader_yml_comment():
    """config/file-downloader.yml contains the DEPRECATED comment."""
    config_path = Path(__file__).parent.parent.parent / "config" / "file-downloader.yml"
    assert config_path.exists(), "config/file-downloader.yml must still exist"
    content = config_path.read_text()
    assert "DEPRECATED" in content


def test_enable_flag_from_ui_yml(tmp_path):
    """Router registration reads downloader.enabled from ui.yml, not env var."""
    # Confirm the env var ENABLE_FILE_DOWNLOADER is no longer referenced
    # in src/api/main.py for router registration.
    main_path = Path(__file__).parent.parent.parent / "src" / "api" / "main.py"
    source = main_path.read_text()
    # The old env-var gate must be gone
    assert "ENABLE_FILE_DOWNLOADER" not in source, (
        "src/api/main.py must not reference ENABLE_FILE_DOWNLOADER for registration"
    )
    # The new gate must read from ui_config / ui.yml
    assert "downloader" in source


def test_system_router_no_enable_file_downloader():
    """src/api/routers/system.py no longer references ENABLE_FILE_DOWNLOADER."""
    system_path = Path(__file__).parent.parent.parent / "src" / "api" / "routers" / "system.py"
    source = system_path.read_text()
    assert "ENABLE_FILE_DOWNLOADER" not in source, (
        "system.py must not reference ENABLE_FILE_DOWNLOADER"
    )


def test_downloader_router_uses_ui_config_not_fd_config():
    """downloader.py router reads from ui_config, not fd_config."""
    router_path = Path(__file__).parent.parent.parent / "src" / "api" / "routers" / "downloader.py"
    source = router_path.read_text()
    # Must NOT reference fd_config
    assert "fd_config" not in source, (
        "downloader.py must not reference fd_config — use ui_config instead"
    )
    # Must reference ui_config
    assert "ui_config" in source
