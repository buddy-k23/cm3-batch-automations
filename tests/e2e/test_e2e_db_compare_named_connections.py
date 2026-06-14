"""E2E Playwright tests for the named connection dropdowns in the DB Compare tab.

These tests verify Issue #296: two-dropdown named connection selector.
"""
from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect


class TestDbcAdapterSelect:
    """Tests for the DB Type dropdown (#dbcAdapterSelect)."""

    def test_adapter_select_exists(self, ui_page: Page) -> None:
        """#dbcAdapterSelect is present in the DB Compare panel."""
        ui_page.click('#tab-dbcompare')
        expect(ui_page.locator('#dbcAdapterSelect')).to_be_visible()

    def test_adapter_select_has_oracle_option(self, ui_page: Page) -> None:
        """DB Type dropdown contains an Oracle option."""
        ui_page.click('#tab-dbcompare')
        expect(ui_page.locator('#dbcAdapterSelect option[value="oracle"]')).to_have_count(1)

    def test_adapter_select_has_postgresql_option(self, ui_page: Page) -> None:
        """DB Type dropdown contains a PostgreSQL option."""
        ui_page.click('#tab-dbcompare')
        expect(ui_page.locator('#dbcAdapterSelect option[value="postgresql"]')).to_have_count(1)

    def test_adapter_select_has_sqlite_option(self, ui_page: Page) -> None:
        """DB Type dropdown contains a SQLite option."""
        ui_page.click('#tab-dbcompare')
        expect(ui_page.locator('#dbcAdapterSelect option[value="sqlite"]')).to_have_count(1)

    def test_adapter_select_default_is_oracle(self, ui_page: Page) -> None:
        """DB Type dropdown defaults to 'oracle'."""
        ui_page.click('#tab-dbcompare')
        expect(ui_page.locator('#dbcAdapterSelect')).to_have_value('oracle')


class TestDbcConnectionSelect:
    """Tests for the Named Connection dropdown (#dbcConnectionSelect)."""

    def test_connection_select_exists(self, ui_page: Page) -> None:
        """#dbcConnectionSelect is present in the DB Compare panel."""
        ui_page.click('#tab-dbcompare')
        expect(ui_page.locator('#dbcConnectionSelect')).to_be_visible()

    def test_connection_select_has_manual_option_first(self, ui_page: Page) -> None:
        """First option in the connection dropdown is '— enter manually —' with empty value."""
        ui_page.click('#tab-dbcompare')
        first_option = ui_page.locator('#dbcConnectionSelect option').first
        expect(first_option).to_have_attribute('value', '')

    def test_connection_select_manual_option_text(self, ui_page: Page) -> None:
        """First option text indicates manual entry."""
        ui_page.click('#tab-dbcompare')
        first_option = ui_page.locator('#dbcConnectionSelect option').first
        # Text should contain 'enter manually' (case-insensitive partial match)
        option_text = first_option.inner_text()
        assert 'manually' in option_text.lower(), (
            f"Expected first option to mention 'manually', got: {option_text!r}"
        )

    def test_connection_select_default_is_manual(self, ui_page: Page) -> None:
        """Named connection dropdown defaults to the '— enter manually —' option (empty value)."""
        ui_page.click('#tab-dbcompare')
        expect(ui_page.locator('#dbcConnectionSelect')).to_have_value('')


class TestDbcManualFormVisibility:
    """Tests for show/hide of the manual connection form based on dropdown selection."""

    def test_manual_form_visible_when_manual_selected(self, ui_page: Page) -> None:
        """Connection chip is visible when '— enter manually —' is selected."""
        ui_page.click('#tab-dbcompare')
        # Default is manual entry — chip should be visible
        expect(ui_page.locator('#dbcConnChip')).to_be_visible()

    def test_dropdowns_appear_above_connection_chip(self, ui_page: Page) -> None:
        """The adapter and connection selects appear above the connection chip in the DOM."""
        ui_page.click('#tab-dbcompare')
        panel_html = ui_page.locator('#dbcDbPanel').inner_html()
        adapter_pos = panel_html.find('dbcAdapterSelect')
        conn_pos = panel_html.find('dbcConnectionSelect')
        chip_pos = panel_html.find('dbcConnChip')
        assert adapter_pos != -1, "#dbcAdapterSelect not found in DB panel"
        assert conn_pos != -1, "#dbcConnectionSelect not found in DB panel"
        assert chip_pos != -1, "#dbcConnChip not found in DB panel"
        assert adapter_pos < chip_pos, "Adapter select should appear before connection chip"
        assert conn_pos < chip_pos, "Connection select should appear before connection chip"
