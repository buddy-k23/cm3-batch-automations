"""Tests for the _should_use_chunked threshold helper in files router."""
from pathlib import Path

import pytest

from src.api.routers.files import _should_use_chunked, _CHUNK_THRESHOLD_BYTES


def test_small_file_returns_false(tmp_path):
    """Files below threshold should not trigger chunked processing."""
    f = tmp_path / "small.txt"
    f.write_bytes(b"x" * 100)
    assert _should_use_chunked(f) is False


def test_large_file_returns_true(tmp_path):
    """Files at or above threshold should trigger chunked processing."""
    f = tmp_path / "large.txt"
    f.write_bytes(b"x" * _CHUNK_THRESHOLD_BYTES)
    assert _should_use_chunked(f) is True


def test_threshold_constant_is_50mb():
    """Threshold should be exactly 50 MB."""
    assert _CHUNK_THRESHOLD_BYTES == 50 * 1024 * 1024


def test_missing_file_returns_false(tmp_path):
    """Non-existent files should return False (safe default)."""
    assert _should_use_chunked(tmp_path / "ghost.txt") is False


def test_file_one_byte_below_threshold_returns_false(tmp_path):
    """Files strictly below threshold should not trigger chunked processing."""
    f = tmp_path / "just_under.txt"
    f.write_bytes(b"x" * (_CHUNK_THRESHOLD_BYTES - 1))
    assert _should_use_chunked(f) is False
