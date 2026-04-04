import json
import os
from unittest.mock import patch


def test_log_writes_json_line(tmp_path):
    log_path = tmp_path / "dl.log"
    with patch.dict(os.environ, {"DOWNLOADER_LOG_PATH": str(log_path), "CM3_ENVIRONMENT": "SIT"}):
        from importlib import reload
        import src.services.downloader_logger as mod
        reload(mod)
        mod.log_activity(operation="download", client_ip="10.0.0.1", client_host="host1",
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
    with patch.dict(os.environ, {"DOWNLOADER_LOG_PATH": str(log_path), "CM3_ENVIRONMENT": "DEV"}):
        from importlib import reload
        import src.services.downloader_logger as mod
        reload(mod)
        mod.log_activity(operation="search_files", client_ip="1.2.3.4", client_host="h",
                         path="/opt/logs", filename="e.log", archive=None, status="success")
    entry = json.loads(log_path.read_text().strip())
    assert entry["archive"] is None
