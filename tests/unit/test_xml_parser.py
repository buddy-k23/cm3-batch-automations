"""Unit tests for the hardened lxml XmlParser (ADR 0019, S19-3, #396).

Covers: streaming ``iterparse`` parse of a repeated-record XML, XPath
resolution (element text / ``@attr`` / nested), namespace strip by default
and the opt-in namespace-aware mode, ``__source_row__`` from ``sourceline``,
repeated-child count columns, the absent-vs-present-empty distinction, and —
the load-bearing test for #396 — that the parser REJECTS untrusted XML attack
payloads (XXE external-entity file read, billion-laughs entity expansion,
external-DTD retrieval, and any DOCTYPE) rather than expanding/exfiltrating.
"""

import os
import tempfile

import pandas as pd
import pytest

from src.parsers.xml_parser import XmlParser


def _write_xml(content: str) -> str:
    """Write an .xml temp file from a raw string.

    Args:
        content: Raw XML document text.

    Returns:
        Absolute path to the temp file.
    """
    f = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".xml", encoding="utf-8")
    f.write(content)
    f.close()
    return f.name


# Field mapping mirrors what TemplateConverter.from_xml_template emits:
# a flat list of fields each carrying an ``xml_xpath`` locator.
# ``transactions`` is the repeated-child field: an ``integer``-typed field
# pointed at the repeated child element collapses to a ``<field>_count`` column
# (the XML analogue of JSON's ``[*]``), exactly what TemplateConverter emits.
_FIELDS = [
    {"name": "customer_id", "data_type": "string", "xml_xpath": "customer/id"},
    {"name": "zip", "data_type": "string", "xml_xpath": "customer/address/zip"},
    {"name": "account_id", "data_type": "string", "xml_xpath": "account/@id"},
    {"name": "transactions", "data_type": "integer", "xml_xpath": "transactions/transaction"},
]


class TestXmlParserHappyPath:
    """Streaming iterparse + XPath resolution (element text, attribute, nested)."""

    def test_parse_element_text_and_nested_and_attribute(self):
        xml = """<?xml version="1.0"?>
<records>
  <record>
    <customer><id>C1</id><address><zip>10001</zip></address></customer>
    <account id="A1"/>
    <transactions><transaction>t</transaction><transaction>t</transaction></transactions>
  </record>
  <record>
    <customer><id>C2</id><address><zip>94105</zip></address></customer>
    <account id="A2"/>
    <transactions><transaction>t</transaction></transactions>
  </record>
</records>
"""
        path = _write_xml(xml)
        try:
            df = XmlParser(path, _FIELDS).parse()
            assert len(df) == 2
            # Element text + nested element text resolution.
            assert list(df["customer_id"]) == ["C1", "C2"]
            assert list(df["zip"]) == ["10001", "94105"]
            # Attribute resolution via the trailing ``@id`` step.
            assert list(df["account_id"]) == ["A1", "A2"]
        finally:
            os.unlink(path)

    def test_repeated_child_resolves_to_count_column(self):
        xml = """<records>
  <record><transactions><transaction>a</transaction><transaction>b</transaction><transaction>c</transaction></transactions></record>
  <record><transactions/></record>
</records>"""
        path = _write_xml(xml)
        try:
            df = XmlParser(path, _FIELDS).parse()
            # A repeated-child path collapses to a ``<field>_count`` column
            # (mirrors JSON's [*]) per ADR 0019 §5.
            assert "transactions_count" in df.columns
            assert list(df["transactions_count"]) == [3, 0]
        finally:
            os.unlink(path)

    def test_source_row_from_sourceline(self):
        xml = "<records>\n  <record><customer><id>C1</id></customer></record>\n  <record><customer><id>C2</id></customer></record>\n</records>\n"
        path = _write_xml(xml)
        try:
            df = XmlParser(path, [{"name": "customer_id", "xml_xpath": "customer/id"}]).parse()
            assert df.columns[0] == "__source_row__"
            # record elements open on physical lines 2 and 3.
            assert list(df["__source_row__"]) == [2, 3]
        finally:
            os.unlink(path)

    def test_custom_record_tag(self):
        xml = """<Document>
  <CdtTrfTxInf><customer><id>C1</id></customer></CdtTrfTxInf>
  <CdtTrfTxInf><customer><id>C2</id></customer></CdtTrfTxInf>
</Document>"""
        path = _write_xml(xml)
        try:
            df = XmlParser(
                path,
                [{"name": "customer_id", "xml_xpath": "customer/id"}],
                record_tag="CdtTrfTxInf",
            ).parse()
            assert list(df["customer_id"]) == ["C1", "C2"]
        finally:
            os.unlink(path)


class TestXmlParserNamespaces:
    """Namespace strip by default; opt-in namespace-aware honouring."""

    def test_namespace_stripped_by_default(self):
        # The BA writes plain local names; the parser strips the namespace.
        xml = """<doc xmlns="urn:iso:std:iso:20022:tech:xsd:pain.001">
  <record><customer><id>C1</id></customer></record>
  <record><customer><id>C2</id></customer></record>
</doc>"""
        path = _write_xml(xml)
        try:
            df = XmlParser(path, [{"name": "customer_id", "xml_xpath": "customer/id"}]).parse()
            assert list(df["customer_id"]) == ["C1", "C2"]
        finally:
            os.unlink(path)

    def test_namespace_aware_opt_in(self):
        xml = """<doc xmlns:pmt="urn:pmt">
  <record><pmt:Amt>100</pmt:Amt></record>
  <record><pmt:Amt>200</pmt:Amt></record>
</doc>"""
        path = _write_xml(xml)
        try:
            df = XmlParser(
                path,
                [{"name": "amount", "xml_xpath": "pmt:Amt"}],
                namespace_aware=True,
                namespaces={"pmt": "urn:pmt"},
            ).parse()
            assert list(df["amount"]) == ["100", "200"]
        finally:
            os.unlink(path)


class TestXmlParserEdgeCases:
    """Absent-vs-present-empty, empty file, missing xpath fields skipped."""

    def test_absent_vs_present_empty_distinction(self):
        xml = """<records>
  <record><customer><zip></zip></customer></record>
  <record><customer></customer></record>
</records>"""
        path = _write_xml(xml)
        try:
            df = XmlParser(path, [{"name": "zip", "xml_xpath": "customer/zip"}]).parse()
            zip_col = df["zip"]
            # present-but-empty -> None (the element exists with no text)
            assert zip_col.iloc[0] is None
            # absent -> pd.NA (the element is missing entirely)
            assert zip_col.iloc[1] is pd.NA
        finally:
            os.unlink(path)

    def test_empty_file_yields_empty_dataframe_with_columns(self):
        path = _write_xml("<records></records>")
        try:
            df = XmlParser(path, _FIELDS).parse()
            assert len(df) == 0
            assert "__source_row__" in df.columns
            assert "customer_id" in df.columns
        finally:
            os.unlink(path)

    def test_fields_without_xpath_are_skipped(self):
        xml = "<records><record><a>x</a></record></records>"
        path = _write_xml(xml)
        try:
            df = XmlParser(
                path,
                [{"name": "a", "xml_xpath": "a"}, {"name": "ignored"}],
            ).parse()
            assert "a" in df.columns
            assert "ignored" not in df.columns
        finally:
            os.unlink(path)

    def test_validate_format_true_for_xml(self):
        path = _write_xml("<records><record/></records>")
        try:
            assert XmlParser(path, _FIELDS).validate_format() is True
        finally:
            os.unlink(path)

    def test_validate_format_false_for_non_xml(self):
        path = _write_xml("this is not xml at all")
        try:
            assert XmlParser(path, _FIELDS).validate_format() is False
        finally:
            os.unlink(path)


class TestXmlParserSecurity:
    """Load-bearing security test (#396): untrusted-XML attacks REJECTED.

    A fintech batch file is untrusted. The hardened ``_SAFE_PARSER``
    (resolve_entities=False, no_network=True, load_dtd=False) plus the
    explicit DOCTYPE rejection must make every one of these payloads FAIL
    LOUDLY rather than read a local file, hit the network, or expand to
    gigabytes of memory.
    """

    def test_xxe_external_entity_file_read_rejected(self):
        # Classic XXE: declares an external entity pointing at a local file
        # and references it inside a record. Must be rejected (DOCTYPE), and
        # the secret file contents must NEVER appear in the parsed output.
        secret = tempfile.NamedTemporaryFile(mode="w", delete=False)
        secret.write("TOP-SECRET-PASSWD-CONTENTS")
        secret.close()
        xml = f"""<?xml version="1.0"?>
<!DOCTYPE records [
  <!ENTITY xxe SYSTEM "file://{secret.name}">
]>
<records>
  <record><customer><id>&xxe;</id></customer></record>
</records>"""
        path = _write_xml(xml)
        try:
            with pytest.raises(ValueError) as exc:
                XmlParser(path, [{"name": "customer_id", "xml_xpath": "customer/id"}]).parse()
            msg = str(exc.value)
            # Rejected for the DOCTYPE/entity reason, and the file content
            # was never exfiltrated into the error.
            assert "DOCTYPE" in msg or "entit" in msg.lower()
            assert "TOP-SECRET-PASSWD-CONTENTS" not in msg
        finally:
            os.unlink(path)
            os.unlink(secret.name)

    def test_billion_laughs_entity_expansion_rejected(self):
        # Quadratic / exponential entity-expansion DoS. Must be rejected at
        # the DOCTYPE rather than expanded into memory.
        xml = """<?xml version="1.0"?>
<!DOCTYPE lolz [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
  <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
  <!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">
]>
<records>
  <record><customer><id>&lol4;</id></customer></record>
</records>"""
        path = _write_xml(xml)
        try:
            with pytest.raises(ValueError) as exc:
                XmlParser(path, [{"name": "customer_id", "xml_xpath": "customer/id"}]).parse()
            assert "DOCTYPE" in str(exc.value) or "entit" in str(exc.value).lower()
        finally:
            os.unlink(path)

    def test_external_dtd_retrieval_rejected(self):
        # External DTD reference (SSRF / processing-leak). Rejected at DOCTYPE.
        xml = """<?xml version="1.0"?>
<!DOCTYPE records SYSTEM "http://attacker.example.com/evil.dtd">
<records>
  <record><customer><id>C1</id></customer></record>
</records>"""
        path = _write_xml(xml)
        try:
            with pytest.raises(ValueError):
                XmlParser(path, [{"name": "customer_id", "xml_xpath": "customer/id"}]).parse()
        finally:
            os.unlink(path)

    def test_any_doctype_rejected(self):
        # Even a benign internal-subset DOCTYPE is rejected fail-closed.
        xml = """<?xml version="1.0"?>
<!DOCTYPE records>
<records><record><customer><id>C1</id></customer></record></records>"""
        path = _write_xml(xml)
        try:
            with pytest.raises(ValueError) as exc:
                XmlParser(path, [{"name": "customer_id", "xml_xpath": "customer/id"}]).parse()
            assert "DOCTYPE" in str(exc.value)
        finally:
            os.unlink(path)

    def test_malformed_xml_raises_value_error(self):
        path = _write_xml("<records><record><unclosed></records>")
        try:
            with pytest.raises(ValueError):
                XmlParser(path, [{"name": "a", "xml_xpath": "a"}]).parse()
        finally:
            os.unlink(path)
