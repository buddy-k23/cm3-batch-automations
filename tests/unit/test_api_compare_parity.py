"""Tests for comparison parity between CLI and API (issue #91).

Covers:
- output_format parameter (json/html) on POST /compare
- chunk_size parameter on POST /compare
- threshold_result in FileCompareResult
- async endpoint accepts same new params
- _run_compare_with_mapping delegates chunk_size and output_format correctly
"""
from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import UploadFile

from src.api.models.file import FileCompareRequest, FileCompareResult
from src.api.routers.files import (
    _run_compare_with_mapping,
    compare_files,
    compare_files_async,
    MAPPINGS_DIR,
    UPLOADS_DIR,
)

ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _upload(name: str, content: str) -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(content.encode("utf-8")))


def _write_mapping(mapping_id: str) -> Path:
    payload = {
        "mapping_name": mapping_id,
        "version": "1.0.0",
        "source": {"type": "file", "format": "pipe_delimited", "has_header": False},
        "fields": [
            {"name": "id", "data_type": "string"},
            {"name": "name", "data_type": "string"},
        ],
        "key_columns": ["id"],
    }
    path = MAPPINGS_DIR / f"{mapping_id}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Model tests: FileCompareRequest now has output_format and chunk_size
# ---------------------------------------------------------------------------

class TestFileCompareRequestModel:
    def test_default_output_format_is_html(self):
        req = FileCompareRequest(mapping_id="m", key_columns=[])
        assert req.output_format == "html"

    def test_output_format_json_accepted(self):
        req = FileCompareRequest(mapping_id="m", key_columns=[], output_format="json")
        assert req.output_format == "json"

    def test_output_format_html_accepted(self):
        req = FileCompareRequest(mapping_id="m", key_columns=[], output_format="html")
        assert req.output_format == "html"

    def test_default_chunk_size(self):
        req = FileCompareRequest(mapping_id="m", key_columns=[])
        assert req.chunk_size == 100_000

    def test_custom_chunk_size(self):
        req = FileCompareRequest(mapping_id="m", key_columns=[], chunk_size=50_000)
        assert req.chunk_size == 50_000


# ---------------------------------------------------------------------------
# Model tests: FileCompareResult now has threshold_result
# ---------------------------------------------------------------------------

class TestFileCompareResultModel:
    def test_threshold_result_defaults_to_none(self):
        result = FileCompareResult(
            total_rows_file1=10,
            total_rows_file2=10,
            matching_rows=10,
            only_in_file1=0,
            only_in_file2=0,
            differences=0,
        )
        assert result.threshold_result is None

    def test_threshold_result_can_be_set(self):
        result = FileCompareResult(
            total_rows_file1=10,
            total_rows_file2=10,
            matching_rows=10,
            only_in_file1=0,
            only_in_file2=0,
            differences=0,
            threshold_result={"passed": True, "overall_result": "pass"},
        )
        assert result.threshold_result["passed"] is True


# ---------------------------------------------------------------------------
# _run_compare_with_mapping: output_format=json produces download_url not report_url
# ---------------------------------------------------------------------------

class TestRunCompareWithMapping:
    def test_html_output_produces_report_url(self, tmp_path):
        mapping_id = "parity_test_html"
        mapping_path = _write_mapping(mapping_id)
        try:
            p1 = tmp_path / "f1.txt"
            p2 = tmp_path / "f2.txt"
            p1.write_text("1|Alice\n2|Bob\n", encoding="utf-8")
            p2.write_text("1|Alice\n2|Bob\n", encoding="utf-8")
            request = FileCompareRequest(
                mapping_id=mapping_id,
                key_columns=["id"],
                output_format="html",
            )
            result = _run_compare_with_mapping(p1, p2, request)
            assert result.report_url is not None
            assert result.report_url.endswith(".html")
        finally:
            mapping_path.unlink(missing_ok=True)

    def test_json_output_produces_download_url(self, tmp_path):
        mapping_id = "parity_test_json"
        mapping_path = _write_mapping(mapping_id)
        try:
            p1 = tmp_path / "f1.txt"
            p2 = tmp_path / "f2.txt"
            p1.write_text("1|Alice\n2|Bob\n", encoding="utf-8")
            p2.write_text("1|Alice\n2|Bob\n", encoding="utf-8")
            request = FileCompareRequest(
                mapping_id=mapping_id,
                key_columns=["id"],
                output_format="json",
            )
            result = _run_compare_with_mapping(p1, p2, request)
            assert result.download_url is not None
            assert result.download_url.endswith(".json")
        finally:
            mapping_path.unlink(missing_ok=True)

    def test_chunk_size_is_forwarded_to_service(self, tmp_path):
        """chunk_size from request is forwarded to run_compare_service."""
        mapping_id = "parity_test_chunk"
        mapping_path = _write_mapping(mapping_id)
        try:
            p1 = tmp_path / "f1.txt"
            p2 = tmp_path / "f2.txt"
            p1.write_text("1|Alice\n2|Bob\n", encoding="utf-8")
            p2.write_text("1|Alice\n2|Bob\n", encoding="utf-8")
            request = FileCompareRequest(
                mapping_id=mapping_id,
                key_columns=["id"],
                chunk_size=5_000,
            )
            with patch("src.api.routers.files.run_compare_service") as mock_svc:
                mock_svc.return_value = {
                    "total_rows_file1": 2,
                    "total_rows_file2": 2,
                    "matching_rows": 2,
                    "only_in_file1": [],
                    "only_in_file2": [],
                    "differences": [],
                    "rows_with_differences": 0,
                    "only_in_file1_count": 0,
                    "only_in_file2_count": 0,
                    "structure_compatible": True,
                }
                _run_compare_with_mapping(p1, p2, request)
            call_kwargs = mock_svc.call_args[1]
            assert call_kwargs["chunk_size"] == 5_000
        finally:
            mapping_path.unlink(missing_ok=True)

    def test_threshold_result_included_in_result(self, tmp_path):
        """Threshold evaluation is run and included in result."""
        mapping_id = "parity_test_thresh"
        mapping_path = _write_mapping(mapping_id)
        try:
            p1 = tmp_path / "f1.txt"
            p2 = tmp_path / "f2.txt"
            p1.write_text("1|Alice\n2|Bob\n", encoding="utf-8")
            p2.write_text("1|Alice\n2|Bob\n", encoding="utf-8")
            request = FileCompareRequest(
                mapping_id=mapping_id,
                key_columns=["id"],
            )
            result = _run_compare_with_mapping(p1, p2, request)
            assert result.threshold_result is not None
            assert "passed" in result.threshold_result
            assert "overall_result" in result.threshold_result
        finally:
            mapping_path.unlink(missing_ok=True)

    def test_missing_mapping_raises_404(self, tmp_path):
        from fastapi import HTTPException
        p1 = tmp_path / "f1.txt"
        p2 = tmp_path / "f2.txt"
        p1.write_text("1|Alice\n", encoding="utf-8")
        p2.write_text("1|Alice\n", encoding="utf-8")
        request = FileCompareRequest(mapping_id="nonexistent_xyz", key_columns=[])
        with pytest.raises(HTTPException) as exc_info:
            _run_compare_with_mapping(p1, p2, request)
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# compare_files endpoint: accepts output_format and chunk_size form params
# ---------------------------------------------------------------------------

class TestCompareFilesEndpoint:
    def test_compare_accepts_output_format_html(self):
        mapping_id = "parity_endpoint_html"
        mapping_path = _write_mapping(mapping_id)
        try:
            result = asyncio.run(
                compare_files(
                    file1=_upload("f1.txt", "1|Alice\n2|Bob\n"),
                    file2=_upload("f2.txt", "1|Alice\n2|Bob\n"),
                    mapping_id=mapping_id,
                    key_columns="id",
                    detailed=True,
                    output_format="html",
                    chunk_size=100_000,
                )
            )
            assert result.total_rows_file1 == 2
            assert result.report_url is not None
        finally:
            mapping_path.unlink(missing_ok=True)

    def test_compare_accepts_output_format_json(self):
        mapping_id = "parity_endpoint_json"
        mapping_path = _write_mapping(mapping_id)
        try:
            result = asyncio.run(
                compare_files(
                    file1=_upload("f1.txt", "1|Alice\n2|Bob\n"),
                    file2=_upload("f2.txt", "1|Alice\n2|Bob\n"),
                    mapping_id=mapping_id,
                    key_columns="id",
                    detailed=True,
                    output_format="json",
                    chunk_size=100_000,
                )
            )
            assert result.total_rows_file1 == 2
            assert result.download_url is not None
            assert result.download_url.endswith(".json")
        finally:
            mapping_path.unlink(missing_ok=True)

    def test_compare_accepts_custom_chunk_size(self):
        mapping_id = "parity_endpoint_chunksize"
        mapping_path = _write_mapping(mapping_id)
        try:
            result = asyncio.run(
                compare_files(
                    file1=_upload("f1.txt", "1|Alice\n2|Bob\n"),
                    file2=_upload("f2.txt", "1|Alice\n2|Bob\n"),
                    mapping_id=mapping_id,
                    key_columns="id",
                    detailed=True,
                    output_format="html",
                    chunk_size=500,
                )
            )
            assert result.total_rows_file1 == 2
        finally:
            mapping_path.unlink(missing_ok=True)

    def test_compare_threshold_result_in_response(self):
        mapping_id = "parity_endpoint_thresh"
        mapping_path = _write_mapping(mapping_id)
        try:
            result = asyncio.run(
                compare_files(
                    file1=_upload("f1.txt", "1|Alice\n2|Bob\n"),
                    file2=_upload("f2.txt", "1|Alice\n2|Bob\n"),
                    mapping_id=mapping_id,
                    key_columns="id",
                    detailed=True,
                    output_format="html",
                    chunk_size=100_000,
                )
            )
            assert result.threshold_result is not None
            assert "passed" in result.threshold_result
        finally:
            mapping_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# compare_files_async endpoint: accepts same new params
# ---------------------------------------------------------------------------

class TestCompareFilesAsyncEndpoint:
    def test_async_compare_accepts_output_format_and_chunk_size(self):
        mapping_id = "parity_async_endpoint"
        mapping_path = _write_mapping(mapping_id)
        try:
            response = asyncio.run(
                compare_files_async(
                    file1=_upload("f1.txt", "1|Alice\n2|Bob\n"),
                    file2=_upload("f2.txt", "1|Alice\n2|Bob\n"),
                    mapping_id=mapping_id,
                    key_columns="id",
                    detailed=True,
                    output_format="json",
                    chunk_size=10_000,
                )
            )
            assert response.job_id
            assert response.status in ("running", "queued")
        finally:
            mapping_path.unlink(missing_ok=True)
