"""Unit tests for MemoryMonitor and format_bytes (S17-4, #432)."""

from src.utils.memory_monitor import MemoryMonitor, format_bytes


def test_get_current_memory_tracks_peak():
    m = MemoryMonitor()
    val = m.get_current_memory_mb()
    assert val > 0
    assert m.peak_memory_mb >= val


def test_system_memory_info_keys():
    m = MemoryMonitor()
    info = m.get_system_memory_info()
    for key in (
        "total_mb",
        "available_mb",
        "used_mb",
        "percent_used",
        "process_memory_mb",
        "peak_memory_mb",
    ):
        assert key in info
    assert info["total_mb"] > 0


def test_check_threshold_none_returns_false():
    m = MemoryMonitor(threshold_mb=None)
    assert m.check_threshold() is False


def test_check_threshold_exceeded_fires_callback():
    fired = {}

    def cb(mb):
        fired["mb"] = mb

    # threshold 0 MB guarantees exceeded
    m = MemoryMonitor(threshold_mb=0.0, alert_callback=cb)
    assert m.check_threshold() is True
    assert fired["mb"] > 0


def test_check_threshold_not_exceeded():
    # huge threshold => never exceeded
    m = MemoryMonitor(threshold_mb=1_000_000.0)
    assert m.check_threshold() is False


def test_force_garbage_collection_runs():
    m = MemoryMonitor()
    # should not raise; exercises before/after measurement + log
    m.force_garbage_collection()


def test_log_memory_usage_with_and_without_context():
    m = MemoryMonitor()
    m.log_memory_usage()
    m.log_memory_usage("phase-1")


def test_get_memory_stats_shape():
    m = MemoryMonitor(threshold_mb=1_000_000.0)
    stats = m.get_memory_stats()
    assert stats["current_mb"] > 0
    assert stats["threshold_exceeded"] is False
    assert stats["system_total_mb"] > 0


def test_format_bytes_units():
    assert format_bytes(512) == "512.0 B"
    assert format_bytes(1536) == "1.5 KB"
    assert format_bytes(1024 * 1024) == "1.0 MB"
    assert format_bytes(1024 ** 3) == "1.0 GB"
    assert format_bytes(1024 ** 5) == "1.0 PB"
