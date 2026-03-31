"""E2E tests for the help sidebar in the Valdo UI (#274)."""

import pytest


class TestHelpSidebar:
    """Help sidebar E2E tests.

    The help sidebar is opened by the '?' button (#btnHelp) in the page header.
    It contains a search input (#helpSearch) and a body div (#helpBody).
    Closing is handled by '#btnHelpClose' or clicking the overlay (#helpOverlay).
    """

    def test_help_button_visible_in_header(self, ui_page):
        """The '?' help button should be visible in the page header."""
        btn = ui_page.locator("#btnHelp")
        assert btn.count() > 0, "btnHelp should be in the DOM"
        assert btn.is_visible(), "btnHelp should be visible in the header"

    def test_help_button_has_accessible_label(self, ui_page):
        """Help button should have an aria-label for screen readers."""
        btn = ui_page.locator("#btnHelp")
        aria_label = btn.get_attribute("aria-label") or ""
        assert len(aria_label) > 0, "btnHelp should have a non-empty aria-label"

    def test_help_sidebar_exists_in_dom(self, ui_page):
        """Help sidebar element (#helpSidebar) should be in the DOM."""
        sidebar = ui_page.locator("#helpSidebar")
        assert sidebar.count() > 0, "helpSidebar should be present in the DOM"

    def test_help_sidebar_hidden_on_load(self, ui_page):
        """Help sidebar should NOT be open on initial page load."""
        sidebar = ui_page.locator("#helpSidebar")
        # Sidebar starts without the 'open' class — it should not be positioned visibly
        # Check that it does not have the open/active state class
        class_attr = sidebar.get_attribute("class") or ""
        assert "open" not in class_attr, (
            "helpSidebar should not have 'open' class on initial page load"
        )

    def test_clicking_help_button_opens_sidebar(self, ui_page):
        """Clicking the '?' button should open the help sidebar."""
        ui_page.locator("#btnHelp").click()
        ui_page.wait_for_timeout(500)

        sidebar = ui_page.locator("#helpSidebar")
        class_attr = sidebar.get_attribute("class") or ""
        assert "open" in class_attr, (
            "helpSidebar should have 'open' class after clicking btnHelp"
        )

    def test_help_search_input_exists(self, ui_page):
        """Help search input (#helpSearch) should be present in the sidebar."""
        search = ui_page.locator("#helpSearch")
        assert search.count() > 0, "helpSearch input should be in the DOM"

    def test_help_search_visible_when_sidebar_open(self, ui_page):
        """Search input should be visible after the sidebar is opened."""
        ui_page.locator("#btnHelp").click()
        ui_page.wait_for_timeout(500)

        search = ui_page.locator("#helpSearch")
        assert search.is_visible(), "helpSearch should be visible when sidebar is open"

    def test_help_close_button_exists(self, ui_page):
        """Close button (#btnHelpClose) should be present inside the sidebar."""
        close_btn = ui_page.locator("#btnHelpClose")
        assert close_btn.count() > 0, "btnHelpClose should be in the DOM"

    def test_clicking_close_button_dismisses_sidebar(self, ui_page):
        """Clicking the close button should dismiss the help sidebar."""
        # Open the sidebar first
        ui_page.locator("#btnHelp").click()
        ui_page.wait_for_timeout(500)

        sidebar = ui_page.locator("#helpSidebar")
        assert "open" in (sidebar.get_attribute("class") or ""), (
            "Sidebar should be open before testing close"
        )

        # Close it
        ui_page.locator("#btnHelpClose").click()
        ui_page.wait_for_timeout(500)

        class_attr = sidebar.get_attribute("class") or ""
        assert "open" not in class_attr, (
            "helpSidebar should not have 'open' class after clicking btnHelpClose"
        )

    def test_help_overlay_exists(self, ui_page):
        """Help overlay element (#helpOverlay) should be in the DOM."""
        overlay = ui_page.locator("#helpOverlay")
        assert overlay.count() > 0, "helpOverlay should be present in the DOM"

    def test_help_body_exists(self, ui_page):
        """Help sidebar body (#helpBody) should be present for content rendering."""
        body = ui_page.locator("#helpBody")
        assert body.count() > 0, "helpBody should be present in the DOM"

    def test_help_search_is_typeable(self, ui_page):
        """Typing into the help search input should work without errors."""
        ui_page.locator("#btnHelp").click()
        ui_page.wait_for_timeout(500)

        search = ui_page.locator("#helpSearch")
        search.fill("validate")
        ui_page.wait_for_timeout(300)

        value = search.input_value()
        assert value == "validate", (
            f"helpSearch input should contain 'validate' after typing, got: '{value}'"
        )

    def test_overlay_click_dismisses_sidebar(self, ui_page):
        """Clicking the overlay should dismiss the help sidebar."""
        ui_page.locator("#btnHelp").click()
        ui_page.wait_for_timeout(500)

        sidebar = ui_page.locator("#helpSidebar")
        assert "open" in (sidebar.get_attribute("class") or ""), (
            "Sidebar should be open before testing overlay dismiss"
        )

        # Click the overlay to close
        ui_page.locator("#helpOverlay").click()
        ui_page.wait_for_timeout(500)

        class_attr = sidebar.get_attribute("class") or ""
        assert "open" not in class_attr, (
            "helpSidebar should close when the overlay is clicked"
        )
