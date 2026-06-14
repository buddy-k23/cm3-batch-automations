import json
import os
from unittest.mock import patch

from src.services.downloader_logger import DownloaderLogger


def test_log_writes_json_line(tmp_path):
    log_path = tmp_path / "dl.log"
    with patch.dict(os.environ, {"APP_ENVIRONMENT": "SIT"}):
        dl = DownloaderLogger(log_path=str(log_path))
        dl.log_activity(operation="download", client_ip="10.0.0.1", client_host="host1",
                        path="/data/batch", filename="r.csv", archive="b.tar.gz", status="success")
    entry = json.loads(log_path.read_text().strip())
    assert entry["operation"] == "download"
    assert entry["client_ip"] == "10.0.0.1"
    assert entry["file"] == "r.csv"
    assert entry["archive"] == "b.tar.gz"
    assert entry["environment"] == "SIT"
    assert "timestamp" in entry


def test_log_no_archive(tmp_path):
    log_path = tmp_path / "dl.log"
    with patch.dict(os.environ, {"APP_ENVIRONMENT": "DEV"}):
        dl = DownloaderLogger(log_path=str(log_path))
        dl.log_activity(operation="search_files", client_ip="1.2.3.4", client_host="h",
                        path="/opt/logs", filename="e.log", archive=None, status="success")
    entry = json.loads(log_path.read_text().strip())
    assert entry["archive"] is None
