# tests/e2e/test_db_compare_profiles.py
"""E2E Playwright tests for DB Compare profile dropdown feature."""
from __future__ import annotations

import os

import pytest
from playwright.sync_api import Page, expect


BASE_URL = os.environ.get("VALDO_BASE_URL", "http://localhost:8000")


def _go_to_db_compare(page: Page) -> None:
    """Navigate to the DB Compare tab."""
    page.goto(f"{BASE_URL}/ui")
    # Click the DB Compare tab — use text matching since it's the 5th tab
    page.get_by_role("button", name="DB Compare").click()
    page.wait_for_selector("#dbcConnChip", timeout=5000)


class TestDbCompareProfileDropdown:
    def test_profile_select_exists_in_dom(self, page: Page) -> None:
        """dbcProfileSelect element must exist in the DB Compare tab."""
        _go_to_db_compare(page)
        # The select exists in the DOM even before expanding the form
        expect(page.locator("#dbcProfileSelect")).to_be_attached()

    def test_custom_option_always_present(self, page: Page) -> None:
        """'Custom…' option must always appear in the dropdown."""
        _go_to_db_compare(page)
        page.click("#dbcConnChip")
        page.wait_for_selector("#dbcConnForm", state="visible")
        options_count = page.locator("#dbcProfileSelect option").count()
        values = [
            page.locator("#dbcProfileSelect option").nth(i).get_attribute("value")
            for i in range(options_count)
        ]
        assert "__custom__" in values, f"'Custom…' option not found. Options: {values}"

    def test_blank_option_is_default(self, page: Page) -> None:
        """Blank placeholder must be the default selection."""
        _go_to_db_compare(page)
        page.click("#dbcConnChip")
        page.wait_for_selector("#dbcConnForm", state="visible")
        selected = page.locator("#dbcProfileSelect").input_value()
        assert selected == "", f"Expected blank default, got: {selected!r}"

    def test_manual_fields_visible_when_blank_selected(self, page: Page) -> None:
        """Manual credential fields must be visible when blank is selected."""
        _go_to_db_compare(page)
        page.click("#dbcConnChip")
        page.wait_for_selector("#dbcConnForm", state="visible")
        # Blank is default — manual fields should be visible
        expect(page.locator("#dbcManualFields")).to_be_visible()

    def test_manual_fields_shown_when_custom_selected(self, page: Page) -> None:
        """Selecting 'Custom…' must show the manual credential fields."""
        _go_to_db_compare(page)
        page.click("#dbcConnChip")
        page.wait_for_selector("#dbcConnForm", state="visible")
        page.locator("#dbcProfileSelect").select_option("__custom__")
        expect(page.locator("#dbcManualFields")).to_be_visible()

    def test_manual_fields_contain_expected_inputs(self, page: Page) -> None:
        """Manual fields wrapper must contain adapter, host, user, password, schema inputs."""
        _go_to_db_compare(page)
        page.click("#dbcConnChip")
        page.wait_for_selector("#dbcConnForm", state="visible")
        page.locator("#dbcProfileSelect").select_option("__custom__")
        for input_id in ["dbcAdapter", "dbcHost", "dbcUser", "dbcPassword", "dbcSchema"]:
            expect(page.locator(f"#{input_id}")).to_be_visible()

    def test_named_profile_hides_manual_fields(self, page: Page) -> None:
        """Selecting a named profile must hide the manual credential fields."""
        _go_to_db_compare(page)
        page.click("#dbcConnChip")
        page.wait_for_selector("#dbcConnForm", state="visible")

        # Wait a moment for the profile fetch to complete
        page.wait_for_timeout(500)

        sel = page.locator("#dbcProfileSelect")
        options_count = sel.locator("option").count()
        named_values = [
            sel.locator("option").nth(i).get_attribute("value")
            for i in range(options_count)
            if sel.locator("option").nth(i).get_attribute("value") not in ("", "__custom__")
        ]

        if not named_values:
            pytest.skip("No named profiles loaded from server — skipping named profile selection test")

        sel.select_option(named_values[0])
        expect(page.locator("#dbcManualFields")).to_be_hidden()

    def test_switching_to_custom_after_profile_shows_fields(self, page: Page) -> None:
        """Switching from a named profile back to Custom must re-show manual fields."""
        _go_to_db_compare(page)
        page.click("#dbcConnChip")
        page.wait_for_selector("#dbcConnForm", state="visible")
        page.wait_for_timeout(500)

        sel = page.locator("#dbcProfileSelect")
        options_count = sel.locator("option").count()
        named_values = [
            sel.locator("option").nth(i).get_attribute("value")
            for i in range(options_count)
            if sel.locator("option").nth(i).get_attribute("value") not in ("", "__custom__")
        ]

        if not named_values:
            pytest.skip("No named profiles loaded — skipping profile→custom switch test")

        # Select named profile → fields hidden
        sel.select_option(named_values[0])
        expect(page.locator("#dbcManualFields")).to_be_hidden()

        # Switch to Custom → fields shown
        sel.select_option("__custom__")
        expect(page.locator("#dbcManualFields")).to_be_visible()
