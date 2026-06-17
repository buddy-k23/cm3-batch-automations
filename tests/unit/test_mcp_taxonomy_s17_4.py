"""Unit tests for the MCP taxonomy introspection helpers (S17-4, #432)."""

from src.mcp import taxonomy
from src.mcp.taxonomy import (
    _describe,
    _introspect_cross_row_checks,
    _introspect_cross_type_checks,
    _introspect_per_field_operators,
    _introspect_violation_kinds,
    list_rule_taxonomy,
    list_violation_taxonomy,
)


class TestDescribe:
    def test_returns_registered_description(self):
        assert _describe("k", {"k": "hello"}) == "hello"

    def test_placeholder_when_missing(self):
        out = _describe("ghost", {})
        assert "no description registered" in out
        assert "ghost" in out


class TestIntrospectionHelpers:
    def test_violation_kinds_non_empty_deterministic(self):
        kinds = _introspect_violation_kinds()
        assert kinds
        # deterministic ordering — same on repeat calls
        assert kinds == _introspect_violation_kinds()

    def test_per_field_operators_exclude_legacy(self):
        ops = _introspect_per_field_operators()
        assert ops
        # legacy raw operators are hidden from the BA-facing vocabulary
        for legacy in (">", "<", "in", "range", "not_null", "length"):
            assert legacy not in ops

    def test_cross_row_checks_sorted(self):
        checks = _introspect_cross_row_checks()
        assert checks == sorted(checks)

    def test_cross_type_checks_sorted(self):
        checks = _introspect_cross_type_checks()
        assert checks == sorted(checks)


class TestListViolationTaxonomy:
    def test_shape_and_order(self):
        entries = list_violation_taxonomy()
        assert entries
        for e in entries:
            assert set(e.keys()) == {"name", "description"}
        names = [e["name"] for e in entries]
        assert names == _introspect_violation_kinds()


class TestListRuleTaxonomy:
    def test_categories_present_and_ordered(self):
        entries = list_rule_taxonomy()
        assert entries
        categories = [e["category"] for e in entries]
        # per-field block comes before cross-row before cross-type
        first_cross_row = categories.index("cross-row") if "cross-row" in categories else len(categories)
        first_cross_type = (
            categories.index("cross-type") if "cross-type" in categories else len(categories)
        )
        last_per_field = max(
            (i for i, c in enumerate(categories) if c == "per-field"), default=-1
        )
        assert last_per_field < first_cross_row
        assert first_cross_row <= first_cross_type

    def test_every_entry_has_required_keys(self):
        for e in list_rule_taxonomy():
            assert set(e.keys()) == {"name", "category", "description"}
            assert e["description"]
