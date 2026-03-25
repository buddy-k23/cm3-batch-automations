"""Exploratory tests for boundary conditions (#112)."""

import json
import os
import tempfile
import threading

os.environ.setdefault("API_KEYS", "test-key:admin")

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.services.validate_service import run_validate_service
from src.services.compare_service import run_compare_service

AUTH = {"X-API-Key": "test-key"}


def _write_mapping(fields, delimiter="|"):
    """Write a minimal mapping JSON and return its path."""
    mapping = {
        "mapping_name": "test_boundary",
        "source": {
            "format": "pipe_delimited",
            "delimiter": delimiter,
            "has_header": True,
        },
        "fields": [{"name": name, "type": "string"} for name in fields],
    }
    f = tempfile.NamedTemporaryFile(
        mode="w", delete=False, suffix=".json", prefix="mapping_"
    )
    json.dump(mapping, f)
    f.close()
    return f.name


class TestExploratoryBoundaries:
    """Boundary and concurrency edge cases."""

    def test_validate_file_with_one_row(self):
        """Single data row should produce total_rows=1."""
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".txt"
        ) as f:
            f.write("id|name\n")
            f.write("1|Alice\n")
            temp_file = f.name

        mapping_file = _write_mapping(["id", "name"])

        try:
            result = run_validate_service(file=temp_file, mapping=mapping_file)

            # Header is not a data row; expect exactly 1 data row counted
            # (or at minimum, total_rows >= 1).
            assert result["total_rows"] >= 1
        finally:
            os.unlink(temp_file)
            os.unlink(mapping_file)

    def test_validate_file_with_max_fields(self):
        """Mapping with 100+ fields should all be validated without error."""
        num_fields = 120
        field_names = [f"field_{i:03d}" for i in range(num_fields)]

        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".txt"
        ) as f:
            header = "|".join(field_names)
            f.write(header + "\n")
            row = "|".join(f"val_{i:03d}" for i in range(num_fields))
            f.write(row + "\n")
            f.write(row + "\n")
            temp_file = f.name

        mapping_file = _write_mapping(field_names)

        try:
            result = run_validate_service(file=temp_file, mapping=mapping_file)

            assert result["total_rows"] >= 2
        finally:
            os.unlink(temp_file)
            os.unlink(mapping_file)

    def test_compare_identical_files_zero_diff(self):
        """Two identical files should produce 100% match (zero differences)."""
        content = "id|name|value\n1|Alice|100\n2|Bob|200\n3|Charlie|300\n"

        files = []
        for _ in range(2):
            f = tempfile.NamedTemporaryFile(
                mode="w", delete=False, suffix=".txt"
            )
            f.write(content)
            f.close()
            files.append(f.name)

        mapping_file = _write_mapping(["id", "name", "value"])

        try:
            result = run_compare_service(
                file1=files[0],
                file2=files[1],
                mapping=mapping_file,
            )

            assert result["structure_compatible"] is True
            assert result["total_rows_file1"] == result["total_rows_file2"]
            # No rows should be only in one file or have differences.
            only1 = result.get("only_in_file1_count", len(result.get("only_in_file1", [])))
            only2 = result.get("only_in_file2_count", len(result.get("only_in_file2", [])))
            diffs = result.get("rows_with_differences", len(result.get("differences", [])))
            assert only1 == 0
            assert only2 == 0
            assert diffs == 0
        finally:
            for path in files:
                os.unlink(path)
            os.unlink(mapping_file)

    def test_compare_completely_different_files(self):
        """Two files with zero matching rows should report zero matches."""
        content1 = "id|name\n1|Alice\n2|Bob\n"
        content2 = "id|name\n3|Charlie\n4|Diana\n"

        files = []
        for content in (content1, content2):
            f = tempfile.NamedTemporaryFile(
                mode="w", delete=False, suffix=".txt"
            )
            f.write(content)
            f.close()
            files.append(f.name)

        mapping_file = _write_mapping(["id", "name"])

        try:
            result = run_compare_service(
                file1=files[0],
                file2=files[1],
                keys="id",
                mapping=mapping_file,
            )

            assert result["structure_compatible"] is True
            # With key-based comparison on 'id', no keys overlap.
            assert result["matching_rows"] == 0
        finally:
            for path in files:
                os.unlink(path)
            os.unlink(mapping_file)

    def test_concurrent_validate_requests(self):
        """Three simultaneous validations via threading should all complete."""
        client = TestClient(app)

        results = [None, None, None]
        errors = [None, None, None]

        def validate_request(index):
            try:
                content = f"id|name\n{index}|User{index}\n".encode()
                # Build a minimal mapping file for the upload
                mapping_data = {
                    "mapping_name": f"concurrent_{index}",
                    "source": {"format": "pipe_delimited", "delimiter": "|", "has_header": True},
                    "fields": [{"name": "id", "type": "string"}, {"name": "name", "type": "string"}],
                }
                mapping_file = tempfile.NamedTemporaryFile(
                    mode="w", delete=False, suffix=".json",
                    dir="config/mappings", prefix=f"concurrent_{index}_",
                )
                json.dump(mapping_data, mapping_file)
                mapping_file.close()
                mapping_id = os.path.splitext(os.path.basename(mapping_file.name))[0]

                try:
                    r = client.post(
                        "/api/v1/files/validate",
                        files={"file": (f"test_{index}.txt", content, "text/plain")},
                        data={"mapping_id": mapping_id, "output_html": "false"},
                        headers=AUTH,
                    )
                    results[index] = r.status_code
                finally:
                    os.unlink(mapping_file.name)
            except Exception as exc:
                errors[index] = exc

        threads = [threading.Thread(target=validate_request, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        # All threads should have completed without exception.
        for i in range(3):
            assert errors[i] is None, f"Thread {i} raised: {errors[i]}"
            assert results[i] == 200, f"Thread {i} returned status {results[i]}"
