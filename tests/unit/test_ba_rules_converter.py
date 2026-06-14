"""Unit tests for BA-friendly rules template conversion."""

import os
import tempfile
import json

from src.config.ba_rules_template_converter import BARulesTemplateConverter


def test_ba_converter_builds_allowed_values_rule():
    csv_content = """Rule ID,Rule Name,Field,Rule Type,Severity,Expected / Values,Condition (optional),Enabled,Notes
BR1,Status allowed,status,Allowed Values,Warning,ACTIVE, ,Y,
"""

    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv') as f:
        f.write(csv_content)
        path = f.name

    try:
        conv = BARulesTemplateConverter()
        out = conv.from_csv(path)
        assert len(out['rules']) == 1
        r = out['rules'][0]
        assert r['type'] == 'field_validation'
        assert r['operator'] == 'in'
        assert r['values'] == ['ACTIVE']
    finally:
        os.unlink(path)


def test_ba_converter_builds_compare_fields_rule():
    csv_content = """Rule ID,Rule Name,Field,Rule Type,Severity,Expected / Values,Condition (optional),Enabled,Notes
BR2,Compare totals,total_due_amt,Compare Fields,Error,>= CURRENT_DUE_AMT,,Y,
"""

    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv') as f:
        f.write(csv_content)
        path = f.name

    try:
        conv = BARulesTemplateConverter()
        out = conv.from_csv(path)
        r = out['rules'][0]
        assert r['type'] == 'cross_field'
        assert r['left_field'] == 'total_due_amt'
        assert r['right_field'] == 'CURRENT_DUE_AMT'
        assert r['operator'] == '>='
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Regression tests for ADR 0007 — preserve string literals on CSV/Excel read
# ---------------------------------------------------------------------------
#
# Defensive coverage for ``BARulesTemplateConverter``. The SHAW ATOCTRAN
# rules CSVs happened to escape the pandas-float-inference bug because
# their ``Expected / Values`` column is mixed with non-numeric tokens
# (``CCYYMMDD``) which forces ``object`` dtype. A tighter BA spec whose
# ``Expected / Values`` column is *purely* numeric would surface the
# same corruption that bit ``TemplateConverter``. These tests pin the
# fix in place for both halves.


def test_ba_converter_preserves_numeric_only_valid_values():
    """``valid_values`` numeric cells keep their string form."""
    # Purely-numeric Expected / Values column — without ``dtype=str`` pandas
    # would auto-infer ``float64`` and ``str(100030.0)`` would emit
    # ``'100030.0'`` into ``rule['values']``.
    csv_content = (
        "Rule ID,Rule Name,Field,Rule Type,Severity,Expected / Values,Enabled,Notes\n"
        "R001,LOC valid values,LOCATION-CODE,valid_values,error,100030,Y,\n"
        "R002,TXN valid values,TRANSACTION-CODE,valid_values,error,900,Y,\n"
    )
    with tempfile.NamedTemporaryFile(
        mode="w", delete=False, suffix=".csv", encoding="utf-8"
    ) as f:
        f.write(csv_content)
        path = f.name

    try:
        out = BARulesTemplateConverter().from_csv(path)
        loc_rule = next(r for r in out["rules"] if r["id"] == "R001")
        txn_rule = next(r for r in out["rules"] if r["id"] == "R002")
        assert loc_rule["values"] == ["100030"], (
            f"ADR 0007 regression: expected ['100030'] but got "
            f"{loc_rule['values']!r}"
        )
        assert txn_rule["values"] == ["900"]
        # Ensure no ``.0`` artifact anywhere in the serialized rules.
        assert ".0" not in json.dumps(out["rules"])
    finally:
        os.unlink(path)


def test_ba_converter_preserves_numeric_only_exact_length_values():
    """``exact_length`` rules' ``value`` field keeps integer semantics.

    Numeric-only ``Expected / Values`` column with ``exact_length`` /
    ``min_length`` / ``min_value`` / ``max_value`` rules.
    Without ``dtype=str`` the column would be ``float64``; the converter
    parses via ``int(expected)`` after ``str(...).strip()``, so a value
    of ``18`` (read as ``18.0``) would crash ``int('18.0')``.
    """
    csv_content = (
        "Rule ID,Rule Name,Field,Rule Type,Severity,Expected / Values,Enabled,Notes\n"
        "R001,ACCT length,ACCT-NUM,exact_length,error,18,Y,\n"
        "R002,TXN length,TRANSACTION-CODE,exact_length,error,3,Y,\n"
        "R003,AMT min,AMOUNT,min_value,error,0,Y,\n"
    )
    with tempfile.NamedTemporaryFile(
        mode="w", delete=False, suffix=".csv", encoding="utf-8"
    ) as f:
        f.write(csv_content)
        path = f.name

    try:
        out = BARulesTemplateConverter().from_csv(path)
        acct = next(r for r in out["rules"] if r["id"] == "R001")
        txn = next(r for r in out["rules"] if r["id"] == "R002")
        amt = next(r for r in out["rules"] if r["id"] == "R003")
        # Integers preserved as ints, not floats.
        assert acct["value"] == 18 and isinstance(acct["value"], int)
        assert txn["value"] == 3 and isinstance(txn["value"], int)
        assert amt["value"] == 0 and isinstance(amt["value"], int)
    finally:
        os.unlink(path)
