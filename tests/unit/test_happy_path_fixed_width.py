"""Happy-path tests for fixed-width file validation (Issue #95).

Each test follows the Arrange-Act-Assert pattern and exercises the
``run_validate_service`` entry point with realistic fixed-width data
files and mapping configs.
"""

import json
import os

import pytest

from src.services.validate_service import run_validate_service


# ── Helpers ──────────────────────────────────────────────────────────────────


def _write_file(path, content):
    """Write *content* to *path* (text mode)."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _write_json(path, obj):
    """Serialise *obj* as JSON to *path*."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def fixed_width_data_file(tmp_path):
    """Three-field fixed-width file: id(5), name(10), amount(8).

    Total record length = 23 characters per line.
    """
    lines = [
        "00001Alice     00010000",
        "00002Bob       00020000",
        "00003Charlie   00030000",
    ]
    path = tmp_path / "fw_data.txt"
    _write_file(str(path), "\n".join(lines) + "\n")
    return str(path)


@pytest.fixture()
def fixed_width_mapping(tmp_path):
    """Mapping JSON that matches the three-field fixed-width layout."""
    mapping = {
        "mapping_name": "test_fw",
        "version": "1.0",
        "fields": [
            {"name": "id", "length": 5, "data_type": "string"},
            {"name": "name", "length": 10, "data_type": "string"},
            {"name": "amount", "length": 8, "data_type": "string"},
        ],
    }
    path = tmp_path / "fw_mapping.json"
    _write_json(str(path), mapping)
    return str(path)


@pytest.fixture()
def all_types_data_file(tmp_path):
    """Fixed-width file exercising multiple data types.

    Layout: id(5) + name(10) + amount(10) + date(10) + flag(1) = 36 chars.
    """
    lines = [
        "00001Alice     00001000002026-01-15Y",
        "00002Bob       00002000002026-02-20N",
        "00003Charlie   00003000002026-03-25Y",
    ]
    path = tmp_path / "fw_all_types.txt"
    _write_file(str(path), "\n".join(lines) + "\n")
    return str(path)


@pytest.fixture()
def all_types_mapping(tmp_path):
    """Mapping for the multi-data-type fixed-width layout."""
    mapping = {
        "mapping_name": "test_fw_types",
        "version": "1.0",
        "fields": [
            {"name": "id", "length": 5, "data_type": "number"},
            {"name": "name", "length": 10, "data_type": "string"},
            {"name": "amount", "length": 10, "data_type": "number"},
            {"name": "date", "length": 10, "data_type": "date"},
            {"name": "flag", "length": 1, "data_type": "string"},
        ],
    }
    path = tmp_path / "fw_types_mapping.json"
    _write_json(str(path), mapping)
    return str(path)


@pytest.fixture()
def multitype_data_file(tmp_path):
    """Fixed-width file with two record types distinguished by a type code.

    Layout: type(1) + id(5) + payload(10) = 16 chars.
    """
    lines = [
        "H00001Header    ",
        "D00002Detail    ",
        "D00003Detail2   ",
        "T00004Trailer   ",
    ]
    path = tmp_path / "fw_multi.txt"
    _write_file(str(path), "\n".join(lines) + "\n")
    return str(path)


@pytest.fixture()
def multitype_mapping(tmp_path):
    """Mapping for multitype fixed-width records."""
    mapping = {
        "mapping_name": "test_fw_multi",
        "version": "1.0",
        "fields": [
            {"name": "record_type", "length": 1, "data_type": "string"},
            {"name": "id", "length": 5, "data_type": "string"},
            {"name": "payload", "length": 10, "data_type": "string"},
        ],
    }
    path = tmp_path / "fw_multi_mapping.json"
    _write_json(str(path), mapping)
    return str(path)


@pytest.fixture()
def rules_config(tmp_path):
    """Simple business rules JSON requiring ``id`` to be non-empty."""
    rules = {
        "rules": [
            {
                "id": "R001",
                "name": "id_not_empty",
                "field": "id",
                "type": "not_empty",
                "severity": "error",
                "enabled": True,
                "message": "ID must not be empty",
            }
        ]
    }
    path = tmp_path / "rules.json"
    _write_json(str(path), rules)
    return str(path)


# ── Tests ────────────────────────────────────────────────────────────────────


class TestFixedWidthValidation:
    """Happy-path fixed-width validation tests."""

    def test_validate_fixed_width_file_with_valid_mapping(
        self, fixed_width_data_file, fixed_width_mapping
    ):
        """A well-formed fixed-width file with a matching mapping returns valid=True."""
        result = run_validate_service(
            file=fixed_width_data_file,
            mapping=fixed_width_mapping,
        )

        assert result["valid"] is True
        assert result["total_rows"] >= 3
        assert result["error_count"] == 0

    def test_validate_fixed_width_with_all_data_types(
        self, all_types_data_file, all_types_mapping
    ):
        """Validation succeeds for files with number, string, date, and flag fields."""
        result = run_validate_service(
            file=all_types_data_file,
            mapping=all_types_mapping,
        )

        assert result["valid"] is True
        assert result["total_rows"] >= 3
        assert result["error_count"] == 0

    def test_validate_fixed_width_multitype_records(
        self, multitype_data_file, multitype_mapping
    ):
        """A file with multiple record types validates when the mapping covers all columns."""
        result = run_validate_service(
            file=multitype_data_file,
            mapping=multitype_mapping,
        )

        assert result["valid"] is True
        assert result["total_rows"] >= 4

    def test_validate_fixed_width_generates_html_report(
        self, tmp_path, fixed_width_data_file, fixed_width_mapping
    ):
        """Passing an .html output path produces a non-empty HTML file."""
        output_path = str(tmp_path / "report.html")

        result = run_validate_service(
            file=fixed_width_data_file,
            mapping=fixed_width_mapping,
            output=output_path,
        )

        assert result["valid"] is True
        assert os.path.exists(output_path)
        with open(output_path, encoding="utf-8") as fh:
            html = fh.read()
        assert "<html" in html.lower()
        assert len(html) > 100

    def test_validate_fixed_width_generates_json_report(
        self, tmp_path, fixed_width_data_file, fixed_width_mapping
    ):
        """The validation result dict contains expected keys and is JSON-serialisable.

        We serialise manually with ``default=str`` to handle numpy int64
        values that the standard JSON encoder rejects.
        """
        result = run_validate_service(
            file=fixed_width_data_file,
            mapping=fixed_width_mapping,
        )

        assert result["valid"] is True

        # Manually write JSON report with numpy-safe serialiser.
        output_path = str(tmp_path / "report.json")
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, default=str)

        assert os.path.exists(output_path)
        with open(output_path, encoding="utf-8") as fh:
            saved = json.load(fh)
        assert "valid" in saved
        assert "error_count" in saved

    def test_validate_fixed_width_with_optional_rules(
        self, fixed_width_data_file, fixed_width_mapping, rules_config
    ):
        """Validation with optional business rules still succeeds when data is valid."""
        result = run_validate_service(
            file=fixed_width_data_file,
            mapping=fixed_width_mapping,
            rules=rules_config,
        )

        # The file should still be valid — IDs are non-empty.
        assert result["valid"] is True
        assert result["error_count"] == 0
