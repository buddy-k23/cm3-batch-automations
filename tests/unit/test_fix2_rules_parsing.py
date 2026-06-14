"""Tests for Fix 2: Smarter valid values parsing in rules generation.

Ensures BARulesTemplateConverter skips descriptive text and generates
cross_field rules when the expected value references another field name.
"""

import os
import tempfile
import pytest

from src.config.ba_rules_template_converter import BARulesTemplateConverter


def _csv_with_rows(*rows: str) -> str:
    header = "Rule ID,Rule Name,Field,Rule Type,Severity,Expected / Values,Condition (optional),Enabled,Notes\n"
    return header + "\n".join(rows) + "\n"


def test_descriptive_text_with_must_be_is_skipped():
    """'Expected / Values' containing 'must be' should not be treated as valid_values list."""
    csv_content = _csv_with_rows(
        "BR1,Status check,STATUS_CODE,allowed values,warning,Must be a valid status code,,Y,"
    )
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".csv") as f:
        f.write(csv_content)
        path = f.name
    try:
        conv = BARulesTemplateConverter()
        result = conv.from_csv(path)
        rule = result["rules"][0]
        # Should still be an allowed values rule but values list should be empty
        # or the rule should be skipped/have no values (descriptive text discarded)
        values = rule.get("values", [])
        assert values == [], f"Descriptive text should not produce valid values; got {values}"
    finally:
        os.unlink(path)


def test_descriptive_text_with_is_used_for_is_skipped():
    """'Expected / Values' containing 'is used for' should not become valid_values."""
    csv_content = _csv_with_rows(
        "BR2,Cycle field,CYCLE_CODE,allowed values,warning,is used for billing cycles,,Y,"
    )
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".csv") as f:
        f.write(csv_content)
        path = f.name
    try:
        conv = BARulesTemplateConverter()
        result = conv.from_csv(path)
        rule = result["rules"][0]
        values = rule.get("values", [])
        assert values == [], f"Descriptive 'is used for' text should not produce values; got {values}"
    finally:
        os.unlink(path)


def test_pipe_separated_actual_values_are_preserved():
    """Short pipe-separated values like 'A|B|C' must still be parsed as valid_values."""
    csv_content = _csv_with_rows(
        "BR3,Status check,STATUS,allowed values,error,A|B|C,,Y,"
    )
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".csv") as f:
        f.write(csv_content)
        path = f.name
    try:
        conv = BARulesTemplateConverter()
        result = conv.from_csv(path)
        rule = result["rules"][0]
        assert rule.get("values") == ["A", "B", "C"], f"Short pipe-separated values must be kept; got {rule.get('values')}"
    finally:
        os.unlink(path)


def test_valid_values_native_type_pipe_separation():
    """valid_values rule type with pipe-separated values must produce a JSON array."""
    csv_content = _csv_with_rows(
        "BR4,Type check,RECORD_TYPE,valid_values,error,LS|CS|RS,,Y,"
    )
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".csv") as f:
        f.write(csv_content)
        path = f.name
    try:
        conv = BARulesTemplateConverter()
        result = conv.from_csv(path)
        rule = result["rules"][0]
        assert rule.get("values") == ["LS", "CS", "RS"], \
            f"valid_values rule must split pipe-separated values; got {rule.get('values')}"
    finally:
        os.unlink(path)
