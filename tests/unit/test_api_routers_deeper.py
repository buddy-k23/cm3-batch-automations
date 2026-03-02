import asyncio
import io
import json
from pathlib import Path

from fastapi import UploadFile, HTTPException

from src.api.models.file import FileParseRequest, FileCompareRequest
from src.api.models.mapping import MappingCreate, SourceConfig, FieldSpec
from src.api.routers.files import detect_format, parse_file, compare_files
from src.api.routers.mappings import (
    list_mappings,
    get_mapping,
    validate_mapping,
    delete_mapping,
)


ROOT = Path(__file__).resolve().parents[2]
MAPPINGS_DIR = ROOT / "config" / "mappings"


def _write_test_mapping(mapping_id: str):
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


def _upload_file(name: str, content: str) -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(content.encode("utf-8")))


def test_files_router_detect_parse_compare_direct():
    mapping_id = "test_api_pipe_mapping"
    mapping_path = _write_test_mapping(mapping_id)
    try:
        det = asyncio.run(detect_format(file=_upload_file("sample.txt", "1|Alice\n2|Bob\n")))
        assert det.format

        # parse_service normalizes column names to strings, so parse succeeds.
        parse_res = asyncio.run(
            parse_file(
                file=_upload_file("sample_parse.txt", "1|Alice\n2|Bob\n"),
                request=FileParseRequest(mapping_id=mapping_id, output_format="csv"),
            )
        )
        assert parse_res.rows_parsed == 2

        cmp_res = asyncio.run(
            compare_files(
                file1=_upload_file("f1.txt", "1|Alice\n2|Bob\n"),
                file2=_upload_file("f2.txt", "1|Alice\n3|Charlie\n"),
                request=FileCompareRequest(mapping_id=mapping_id, key_columns=["id"], detailed=True),
            )
        )
        assert cmp_res.total_rows_file1 == 2
        assert cmp_res.total_rows_file2 == 2
    finally:
        if mapping_path.exists():
            mapping_path.unlink()


def test_mappings_router_list_get_validate_delete_direct():
    mapping_id = "test_api_mapping_ops"
    mapping_path = _write_test_mapping(mapping_id)
    try:
        items = asyncio.run(list_mappings())
        assert any(i.id == mapping_id for i in items)

        fetched = asyncio.run(get_mapping(mapping_id))
        assert fetched.id == mapping_id
        assert fetched.total_fields == 2

        validation = asyncio.run(
            validate_mapping(
                MappingCreate(
                    mapping_name="validate_me",
                    source=SourceConfig(type="file", format="pipe_delimited", has_header=False),
                    fields=[FieldSpec(name="id", data_type="string")],
                )
            )
        )
        assert validation.valid is True

        deleted = asyncio.run(delete_mapping(mapping_id))
        assert deleted["success"] is True
    finally:
        if mapping_path.exists():
            mapping_path.unlink()
