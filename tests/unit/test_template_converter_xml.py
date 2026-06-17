"""Unit tests for the XML XPath template-converter branch (ADR 0019, S19-3, #396).

Mirrors ``test_template_converter`` JSON coverage: a CSV template carrying an
``XML XPath`` column round-trips to a mapping whose per-field entries carry
``xml_xpath`` and whose ``source.format`` is ``xml``; and backward-compat —
templates without the column still emit fixed-width / pipe mappings unchanged.
"""

import os
import tempfile

from src.config.template_converter import TemplateConverter


def _write_csv(content: str) -> str:
    """Write a .csv temp template from a raw string.

    Args:
        content: Raw CSV text.

    Returns:
        Absolute path to the temp file.
    """
    f = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".csv", encoding="utf-8")
    f.write(content)
    f.close()
    return f.name


class TestXmlTemplateConversion:
    """An XML XPath column drives an XML mapping with xml_xpath per field."""

    def test_from_xml_template_emits_xml_xpath(self):
        csv = (
            "Field Name,Data Type,XML XPath,Required\n"
            "customer_id,string,customer/id,Y\n"
            "account_id,string,account/@id,N\n"
            "transactions,integer,transactions/transaction,N\n"
        )
        path = _write_csv(csv)
        try:
            mapping = TemplateConverter().from_xml_template(path)
            assert mapping["source"]["format"] == "xml"
            by_name = {f["name"]: f for f in mapping["fields"]}
            assert by_name["customer_id"]["xml_xpath"] == "customer/id"
            # Attribute locator preserved verbatim.
            assert by_name["account_id"]["xml_xpath"] == "account/@id"
            assert by_name["transactions"]["xml_xpath"] == "transactions/transaction"
        finally:
            os.unlink(path)

    def test_xml_path_column_autodetected_as_xml_format(self):
        csv = (
            "Field Name,Data Type,XML XPath\n"
            "customer_id,string,customer/id\n"
        )
        path = _write_csv(csv)
        try:
            # No explicit file_format — a populated XML XPath column wins.
            mapping = TemplateConverter().from_csv(path)
            assert mapping["source"]["format"] == "xml"
            assert mapping["fields"][0]["xml_xpath"] == "customer/id"
        finally:
            os.unlink(path)

    def test_blank_xml_xpath_cell_emits_no_key(self):
        csv = (
            "Field Name,Data Type,XML XPath\n"
            "a,string,customer/id\n"
            "b,string,\n"
        )
        path = _write_csv(csv)
        try:
            mapping = TemplateConverter().from_xml_template(path)
            by_name = {f["name"]: f for f in mapping["fields"]}
            assert by_name["a"]["xml_xpath"] == "customer/id"
            # A blank cell must NOT leak an empty xml_xpath key.
            assert "xml_xpath" not in by_name["b"]
        finally:
            os.unlink(path)


class TestBackwardCompat:
    """Templates without an XML XPath column behave exactly as before."""

    def test_fixed_width_template_unaffected(self):
        csv = (
            "Field Name,Data Type,Position,Length\n"
            "id,string,1,5\n"
            "name,string,6,20\n"
        )
        path = _write_csv(csv)
        try:
            mapping = TemplateConverter().from_csv(path)
            assert mapping["source"]["format"] == "fixed_width"
            assert all("xml_xpath" not in f for f in mapping["fields"])
        finally:
            os.unlink(path)
