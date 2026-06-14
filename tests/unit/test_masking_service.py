"""Tests for the MaskingService."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from src.services.masking_service import MaskingService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fixed_width_mapping(fields: list[tuple[str, int]]) -> dict:
    """Build a minimal fixed-width mapping dict.

    Args:
        fields: list of (name, length) tuples.
    """
    return {
        "mapping_name": "test",
        "source": {"format": "fixed_width"},
        "fields": [
            {"name": name, "length": length, "data_type": "string"}
            for name, length in fields
        ],
    }


def _pipe_mapping(fields: list[str]) -> dict:
    """Build a minimal pipe-delimited mapping dict."""
    return {
        "mapping_name": "test",
        "source": {"format": "pipe_delimited"},
        "fields": [
            {"name": name, "data_type": "string"}
            for name in fields
        ],
    }


def _write_tmp(content: str, suffix: str = ".dat") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.write(fd, content.encode("utf-8"))
    os.close(fd)
    return path


# ---------------------------------------------------------------------------
# Strategy: preserve
# ---------------------------------------------------------------------------

class TestPreserveStrategy:
    def test_preserve_returns_original_value(self):
        svc = MaskingService()
        assert svc.apply_strategy("preserve", "hello", {}) == "hello"

    def test_preserve_returns_original_numeric(self):
        svc = MaskingService()
        assert svc.apply_strategy("preserve", "12345", {}) == "12345"


# ---------------------------------------------------------------------------
# Strategy: preserve_format
# ---------------------------------------------------------------------------

class TestPreserveFormatStrategy:
    def test_preserves_length(self):
        svc = MaskingService()
        original = "ABC123"
        result = svc.apply_strategy("preserve_format", original, {})
        assert len(result) == len(original)

    def test_alpha_positions_stay_alpha(self):
        svc = MaskingService()
        original = "ABC"
        result = svc.apply_strategy("preserve_format", original, {})
        assert result.isalpha()

    def test_digit_positions_stay_digit(self):
        svc = MaskingService()
        original = "123"
        result = svc.apply_strategy("preserve_format", original, {})
        assert result.isdigit()

    def test_spaces_preserved(self):
        svc = MaskingService()
        original = "AB 12"
        result = svc.apply_strategy("preserve_format", original, {})
        assert result[2] == " "
        assert len(result) == 5


# ---------------------------------------------------------------------------
# Strategy: deterministic_hash
# ---------------------------------------------------------------------------

class TestDeterministicHashStrategy:
    def test_same_input_same_output(self):
        svc = MaskingService()
        val = "SMITH"
        r1 = svc.apply_strategy("deterministic_hash", val, {"length": 10})
        r2 = svc.apply_strategy("deterministic_hash", val, {"length": 10})
        assert r1 == r2

    def test_different_input_different_output(self):
        svc = MaskingService()
        r1 = svc.apply_strategy("deterministic_hash", "SMITH", {"length": 10})
        r2 = svc.apply_strategy("deterministic_hash", "JONES", {"length": 10})
        assert r1 != r2

    def test_respects_length(self):
        svc = MaskingService()
        result = svc.apply_strategy("deterministic_hash", "SMITH", {"length": 8})
        assert len(result) == 8


# ---------------------------------------------------------------------------
# Strategy: random_range
# ---------------------------------------------------------------------------

class TestRandomRangeStrategy:
    def test_within_range(self):
        svc = MaskingService()
        for _ in range(50):
            result = svc.apply_strategy("random_range", "500", {"min": 100, "max": 999})
            assert 100 <= int(result) <= 999

    def test_string_output(self):
        svc = MaskingService()
        result = svc.apply_strategy("random_range", "42", {"min": 1, "max": 100})
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Strategy: redact
# ---------------------------------------------------------------------------

class TestRedactStrategy:
    def test_redact_fixed_width(self):
        svc = MaskingService()
        result = svc.apply_strategy("redact", "SECRET", {"length": 6, "format_type": "fixed_width"})
        assert result == "      "
        assert len(result) == 6

    def test_redact_delimited(self):
        svc = MaskingService()
        result = svc.apply_strategy("redact", "SECRET", {"format_type": "pipe_delimited"})
        assert result == ""


# ---------------------------------------------------------------------------
# Strategy: fake_name
# ---------------------------------------------------------------------------

class TestFakeNameStrategy:
    def test_returns_string(self):
        svc = MaskingService()
        result = svc.apply_strategy("fake_name", "JOHN", {})
        assert isinstance(result, str)
        assert len(result) > 0

    def test_returns_from_builtin_list(self):
        svc = MaskingService()
        result = svc.apply_strategy("fake_name", "JOHN", {})
        assert result in svc.FAKE_NAMES


# ---------------------------------------------------------------------------
# Fixed-width masking: record length preservation
# ---------------------------------------------------------------------------

class TestFixedWidthMasking:
    def test_preserves_record_length(self):
        mapping = _fixed_width_mapping([("NAME", 10), ("ACCT", 8), ("AMT", 6)])
        record_len = 10 + 8 + 6  # 24
        lines = [
            "JOHN SMITH00012345000100",
            "JANE DOE  00067890000200",
        ]
        content = "\n".join(lines)
        in_path = _write_tmp(content)
        out_path = in_path + ".masked"

        masking_rules = {
            "fields": {
                "NAME": {"strategy": "preserve_format"},
                "ACCT": {"strategy": "deterministic_hash", "length": 8},
                "AMT": {"strategy": "preserve"},
            }
        }

        try:
            svc = MaskingService()
            svc.mask_file(in_path, out_path, mapping, masking_rules)

            with open(out_path, "r") as f:
                masked_lines = f.read().splitlines()

            assert len(masked_lines) == 2
            for line in masked_lines:
                assert len(line) == record_len
        finally:
            os.unlink(in_path)
            if os.path.exists(out_path):
                os.unlink(out_path)

    def test_original_file_unmodified(self):
        mapping = _fixed_width_mapping([("NAME", 10), ("CODE", 4)])
        original_content = "JOHN SMITHABCD\nJANE DOE  EFGH\n"
        in_path = _write_tmp(original_content)
        out_path = in_path + ".masked"

        masking_rules = {
            "fields": {
                "NAME": {"strategy": "redact"},
                "CODE": {"strategy": "preserve_format"},
            }
        }

        try:
            svc = MaskingService()
            svc.mask_file(in_path, out_path, mapping, masking_rules)

            with open(in_path, "r") as f:
                after = f.read()
            assert after == original_content
        finally:
            os.unlink(in_path)
            if os.path.exists(out_path):
                os.unlink(out_path)

    def test_default_strategy_is_preserve(self):
        """Fields without an explicit masking rule should be preserved."""
        mapping = _fixed_width_mapping([("NAME", 10), ("CODE", 4)])
        content = "JOHN SMITHABCD"
        in_path = _write_tmp(content)
        out_path = in_path + ".masked"

        masking_rules = {
            "fields": {
                "NAME": {"strategy": "redact"},
                # CODE not listed → should be preserved
            }
        }

        try:
            svc = MaskingService()
            svc.mask_file(in_path, out_path, mapping, masking_rules)

            with open(out_path, "r") as f:
                masked = f.read().splitlines()[0]

            # CODE portion (positions 10-14) should be unchanged
            assert masked[10:14] == "ABCD"
        finally:
            os.unlink(in_path)
            if os.path.exists(out_path):
                os.unlink(out_path)


# ---------------------------------------------------------------------------
# Pipe-delimited masking: structure preservation
# ---------------------------------------------------------------------------

class TestPipeDelimitedMasking:
    def test_preserves_column_count(self):
        mapping = _pipe_mapping(["NAME", "ACCT", "AMT"])
        content = "JOHN|00012345|000100\nJANE|00067890|000200\n"
        in_path = _write_tmp(content, suffix=".txt")
        out_path = in_path + ".masked"

        masking_rules = {
            "fields": {
                "NAME": {"strategy": "fake_name"},
                "ACCT": {"strategy": "deterministic_hash", "length": 8},
                "AMT": {"strategy": "random_range", "min": 100, "max": 999},
            }
        }

        try:
            svc = MaskingService()
            svc.mask_file(in_path, out_path, mapping, masking_rules)

            with open(out_path, "r") as f:
                masked_lines = [l for l in f.read().splitlines() if l.strip()]

            assert len(masked_lines) == 2
            for line in masked_lines:
                assert line.count("|") == 2
        finally:
            os.unlink(in_path)
            if os.path.exists(out_path):
                os.unlink(out_path)

    def test_preserves_delimiter(self):
        mapping = _pipe_mapping(["A", "B"])
        content = "hello|world\n"
        in_path = _write_tmp(content, suffix=".txt")
        out_path = in_path + ".masked"

        masking_rules = {"fields": {"A": {"strategy": "preserve"}, "B": {"strategy": "preserve"}}}

        try:
            svc = MaskingService()
            svc.mask_file(in_path, out_path, mapping, masking_rules)

            with open(out_path, "r") as f:
                line = f.read().splitlines()[0]
            assert "|" in line
        finally:
            os.unlink(in_path)
            if os.path.exists(out_path):
                os.unlink(out_path)

    def test_original_file_unmodified_pipe(self):
        mapping = _pipe_mapping(["NAME", "CODE"])
        original_content = "JOHN|ABCD\nJANE|EFGH\n"
        in_path = _write_tmp(original_content, suffix=".txt")
        out_path = in_path + ".masked"

        masking_rules = {
            "fields": {
                "NAME": {"strategy": "redact"},
                "CODE": {"strategy": "preserve_format"},
            }
        }

        try:
            svc = MaskingService()
            svc.mask_file(in_path, out_path, mapping, masking_rules)

            with open(in_path, "r") as f:
                after = f.read()
            assert after == original_content
        finally:
            os.unlink(in_path)
            if os.path.exists(out_path):
                os.unlink(out_path)


# ---------------------------------------------------------------------------
# Deterministic masking consistency across file
# ---------------------------------------------------------------------------

class TestDeterministicConsistency:
    def test_same_value_masked_consistently_in_file(self):
        mapping = _pipe_mapping(["NAME", "CODE"])
        content = "JOHN|A1\nJANE|B2\nJOHN|C3\n"
        in_path = _write_tmp(content, suffix=".txt")
        out_path = in_path + ".masked"

        masking_rules = {
            "fields": {
                "NAME": {"strategy": "deterministic_hash", "length": 10},
                "CODE": {"strategy": "preserve"},
            }
        }

        try:
            svc = MaskingService()
            svc.mask_file(in_path, out_path, mapping, masking_rules)

            with open(out_path, "r") as f:
                lines = f.read().splitlines()

            name1 = lines[0].split("|")[0]
            name3 = lines[2].split("|")[0]
            name2 = lines[1].split("|")[0]
            # JOHN appears twice → same masked output
            assert name1 == name3
            # JANE is different
            assert name1 != name2
        finally:
            os.unlink(in_path)
            if os.path.exists(out_path):
                os.unlink(out_path)


# ---------------------------------------------------------------------------
# Unknown strategy raises ValueError
# ---------------------------------------------------------------------------

class TestUnknownStrategy:
    def test_unknown_strategy_raises(self):
        svc = MaskingService()
        with pytest.raises(ValueError, match="Unknown masking strategy"):
            svc.apply_strategy("nonexistent_strategy", "val", {})
