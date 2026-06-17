"""Round-trip tests for the JSON Path template column (ADR 0018, S19-1, #395).

Asserts that a CSV template carrying a ``JSON Path`` column produces a
mapping whose per-field entries carry ``json_path``, and that templates
*without* the column remain backward-compatible (fixed-width mapping).
"""

import os
import tempfile

import pandas as pd

from src.config.template_converter import TemplateConverter


def _write_csv(rows, columns):
    """Write a CSV temp file from a list of row dicts."""
    df = pd.DataFrame(rows, columns=columns)
    f = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".csv")
    df.to_csv(f.name, index=False)
    f.close()
    return f.name


class TestJsonTemplateRoundTrip:
    """from_json_template emits json_path per field."""

    def test_json_path_column_emits_json_path(self):
        rows = [
            {"Field Name": "customer_id", "Data Type": "string",
             "JSON Path": "$.customer.id", "Required": "Y"},
            {"Field Name": "zip", "Data Type": "string",
             "JSON Path": "$.customer.address.zip", "Required": "N"},
            {"Field Name": "transactions", "Data Type": "integer",
             "JSON Path": "$.transactions[*]", "Required": "N"},
        ]
        path = _write_csv(rows, ["Field Name", "Data Type", "JSON Path", "Required"])
        try:
            mapping = TemplateConverter().from_json_template(path)
            assert mapping["source"]["format"] == "json"
            fields = {f["name"]: f for f in mapping["fields"]}
            assert fields["customer_id"]["json_path"] == "$.customer.id"
            assert fields["zip"]["json_path"] == "$.customer.address.zip"
            assert fields["transactions"]["json_path"] == "$.transactions[*]"
            # No positional anchors for JSON mappings.
            assert "position" not in fields["customer_id"]
            assert "length" not in fields["customer_id"]
        finally:
            os.unlink(path)

    def test_from_csv_autodetects_json_when_json_path_present(self):
        rows = [
            {"Field Name": "id", "Data Type": "string", "JSON Path": "$.id"},
        ]
        path = _write_csv(rows, ["Field Name", "Data Type", "JSON Path"])
        try:
            # The generic from_csv entrypoint should auto-detect the JSON
            # format from the presence of a populated JSON Path column.
            mapping = TemplateConverter().from_csv(path)
            assert mapping["source"]["format"] == "json"
            assert mapping["fields"][0]["json_path"] == "$.id"
        finally:
            os.unlink(path)

    def test_blank_json_path_cells_omit_json_path(self):
        rows = [
            {"Field Name": "id", "Data Type": "string", "JSON Path": "$.id"},
            {"Field Name": "note", "Data Type": "string", "JSON Path": ""},
        ]
        path = _write_csv(rows, ["Field Name", "Data Type", "JSON Path"])
        try:
            mapping = TemplateConverter().from_json_template(path)
            fields = {f["name"]: f for f in mapping["fields"]}
            assert fields["id"]["json_path"] == "$.id"
            # A blank JSON Path cell must not emit an empty json_path key.
            assert "json_path" not in fields["note"]
        finally:
            os.unlink(path)


class TestBackwardCompatibility:
    """Templates without a JSON Path column keep emitting fixed-width."""

    def test_fixed_width_template_unaffected(self):
        rows = [
            {"Field Name": "acct", "Data Type": "string",
             "Position": "1", "Length": "10"},
            {"Field Name": "bal", "Data Type": "decimal",
             "Position": "11", "Length": "12"},
        ]
        path = _write_csv(rows, ["Field Name", "Data Type", "Position", "Length"])
        try:
            mapping = TemplateConverter().from_csv(path)
            assert mapping["source"]["format"] == "fixed_width"
            fields = {f["name"]: f for f in mapping["fields"]}
            assert fields["acct"]["position"] == 1
            assert fields["acct"]["length"] == 10
            # No json_path leaks into a fixed-width mapping.
            assert "json_path" not in fields["acct"]
        finally:
            os.unlink(path)
