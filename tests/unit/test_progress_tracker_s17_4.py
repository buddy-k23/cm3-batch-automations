"""Unit tests for ProgressTracker / SimpleProgressCallback (S17-4, #432)."""

import pytest

from src.utils.progress import ProgressTracker, SimpleProgressCallback


def test_update_and_increment_track_current():
    t = ProgressTracker(total=100, show_bar=False)
    t.update(10)
    assert t.current == 10
    t.increment(5)
    assert t.current == 15
    t.increment()
    assert t.current == 16


def test_get_stats_reports_percent_and_completion():
    t = ProgressTracker(total=200, show_bar=False)
    t.update(50)
    stats = t.get_stats()
    assert stats["current"] == 50
    assert stats["total"] == 200
    assert stats["percent"] == pytest.approx(25.0)
    assert stats["is_complete"] is False
    assert stats["rate_per_second"] >= 0


def test_get_stats_zero_total_no_divide_error():
    t = ProgressTracker(total=0, show_bar=False)
    stats = t.get_stats()
    assert stats["percent"] == 0
    # current(0) >= total(0) => complete
    assert stats["is_complete"] is True


def test_format_time_buckets():
    t = ProgressTracker(total=1, show_bar=False)
    assert t._format_time(30).endswith("s")
    assert t._format_time(120).endswith("m")
    assert t._format_time(7200).endswith("h")


def test_finish_marks_complete():
    t = ProgressTracker(total=5, show_bar=False)
    t.finish()
    assert t.current == 5
    assert t.get_stats()["is_complete"] is True


def test_display_progress_bar_writes_to_stdout(capsys):
    t = ProgressTracker(total=10, description="Work", show_bar=True, bar_width=10)
    t.update(5)
    captured = capsys.readouterr()
    assert "Work" in captured.out
    assert "50.0%" in captured.out


def test_display_progress_bar_newline_on_complete(capsys):
    t = ProgressTracker(total=2, show_bar=True, bar_width=4)
    t.update(2)
    captured = capsys.readouterr()
    assert captured.out.endswith("\n")


def test_set_description_updates_label_on_next_redraw(capsys):
    t = ProgressTracker(total=10, description="Validating", show_bar=True, bar_width=10)
    t.set_description("Validating chunk 3")
    t.update(5)
    captured = capsys.readouterr()
    assert "Validating chunk 3" in captured.out
    assert "50.0%" in captured.out


def test_simple_callback_lazily_creates_tracker(capsys):
    cb = SimpleProgressCallback("Loading")
    assert cb.tracker is None
    cb(1, 4)
    assert cb.tracker is not None
    assert cb.tracker.total == 4
    # second call reuses the same tracker
    first = cb.tracker
    cb(2, 4)
    assert cb.tracker is first
