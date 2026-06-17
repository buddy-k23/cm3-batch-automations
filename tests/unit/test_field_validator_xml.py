"""Unit tests for XML field validators (ADR 0019, S19-3, #396).

Covers ``validate_xml_array_length`` (the repeated-child count column, the XML
twin of ``validate_json_array_length``) and confirms ``validate_nested_required``
is reused unchanged on XML-sourced absent-vs-present-empty columns.
"""

import pandas as pd

from src.validators.field_validator import FieldValidator


class TestValidateXmlArrayLength:
    """min/max bounds on the repeated-child count column."""

    def test_min_len_flags_below(self):
        df = pd.DataFrame({"transactions_count": [0, 1, 3]})
        mask = FieldValidator().validate_xml_array_length(df, "transactions_count", min_len=1)
        # 0 violates the minimum of 1; 1 and 3 pass.
        assert list(mask) == [True, False, False]

    def test_max_len_flags_above(self):
        df = pd.DataFrame({"transactions_count": [1, 500, 501]})
        mask = FieldValidator().validate_xml_array_length(
            df, "transactions_count", min_len=1, max_len=500
        )
        assert list(mask) == [False, False, True]

    def test_absent_count_flagged(self):
        # A non-numeric / NA count cannot satisfy a minimum.
        df = pd.DataFrame({"transactions_count": [pd.NA, 2]})
        mask = FieldValidator().validate_xml_array_length(df, "transactions_count", min_len=1)
        assert list(mask) == [True, False]


class TestValidateNestedRequiredOnXml:
    """nested_required reused verbatim on XML absent-vs-present-empty columns."""

    def test_absent_flagged_present_and_empty_not(self):
        # Mirrors the XmlParser sentinels: pd.NA for absent, None for
        # present-but-empty, a value for present. The object dtype matches the
        # mixed (str / None / pd.NA) column XmlParser.parse emits, which
        # preserves the present-empty (None) vs absent (pd.NA) distinction.
        df = pd.DataFrame({"zip": pd.Series(["10001", None, pd.NA], dtype=object)})
        mask = FieldValidator().validate_nested_required(df, "zip")
        # Only the absent (pd.NA) row is flagged; present-empty (None) is not.
        assert list(mask) == [False, False, True]
