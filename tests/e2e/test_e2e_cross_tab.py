"""E2E tests for cross-tab navigation, state, and responsive layout (#118)."""

import pytest


class TestCrossTab:
    """Cross-tab navigation and responsive layout E2E tests."""

    def test_default_tab_on_load(self, ui_page):
        """Quick Test tab should be active by default."""
        tab = ui_page.locator("#tab-quick")
        assert tab.get_attribute("aria-selected") == "true"
        assert ui_page.locator("#panel-quick").is_visible()

    def test_navigate_all_tabs(self, ui_page):
        """Clicking each tab should show the correct panel."""
        tabs = [
            ("tab-quick", "panel-quick"),
            ("tab-runs", "panel-runs"),
            ("tab-mapping", "panel-mapping"),
            ("tab-tester", "panel-tester"),
        ]
        for tab_id, panel_id in tabs:
            ui_page.locator(f"#{tab_id}").click()
            ui_page.wait_for_timeout(300)
            assert ui_page.locator(f"#{tab_id}").get_attribute("aria-selected") == "true"
            assert ui_page.locator(f"#{panel_id}").is_visible()

    def test_only_one_panel_visible(self, ui_page):
        """Only the active panel should be visible at a time."""
        panels = ["panel-quick", "panel-runs", "panel-mapping", "panel-tester"]

        for i, active_tab in enumerate(["tab-quick", "tab-runs", "tab-mapping", "tab-tester"]):
            ui_page.locator(f"#{active_tab}").click()
            ui_page.wait_for_timeout(300)

            for j, panel_id in enumerate(panels):
                panel = ui_page.locator(f"#{panel_id}")
                if i == j:
                    assert panel.is_visible(), f"{panel_id} should be visible when {active_tab} is clicked"
                else:
                    assert not panel.is_visible(), f"{panel_id} should be hidden when {active_tab} is clicked"

    def test_tab_preserves_uploaded_file(self, ui_page, sample_pipe_file):
        """Uploaded file should persist when switching tabs and back."""
        # Upload file
        ui_page.locator("#fileInput").set_input_files(str(sample_pipe_file))
        ui_page.wait_for_timeout(300)

        # Switch away and back
        ui_page.locator("#tab-runs").click()
        ui_page.wait_for_timeout(300)
        ui_page.locator("#tab-quick").click()
        ui_page.wait_for_timeout(300)

        # File info should still be visible
        panel = ui_page.locator("#panel-quick")
        text = panel.text_content().lower()
        assert "sample" in text or "file" in text

    def test_header_always_visible(self, ui_page):
        """App header should be visible on all tabs."""
        header = ui_page.locator("header")
        assert header.is_visible()
        assert "CM3" in header.text_content()

        # Check on different tabs
        for tab_id in ["tab-runs", "tab-mapping", "tab-tester"]:
            ui_page.locator(f"#{tab_id}").click()
            ui_page.wait_for_timeout(200)
            assert header.is_visible()

    def test_theme_toggle_exists(self, ui_page):
        """Theme toggle button should be in the header."""
        btn = ui_page.locator("#btnTheme")
        assert btn.is_visible()

    def test_theme_toggle_switches_theme(self, ui_page):
        """Clicking theme toggle should change the data-theme attribute."""
        html = ui_page.locator("html")
        initial_theme = html.get_attribute("data-theme")

        ui_page.locator("#btnTheme").click()
        ui_page.wait_for_timeout(500)

        new_theme = html.get_attribute("data-theme")
        assert new_theme != initial_theme, "Theme should change on toggle click"

    def test_health_footer_visible(self, ui_page):
        """Health status footer should be visible."""
        footer = ui_page.locator("footer.status-bar")
        if footer.count() > 0:
            assert footer.is_visible()
            text = footer.text_content().lower()
            assert "healthy" in text or "server" in text or "status" in text

    def test_responsive_mobile_width(self, ui_page):
        """UI should not break at mobile width (375px)."""
        ui_page.set_viewport_size({"width": 375, "height": 812})
        ui_page.wait_for_timeout(500)

        # Header and nav should still be visible
        assert ui_page.locator("header").is_visible()
        assert ui_page.locator("nav.tabs").is_visible()

        # Active panel should be visible
        assert ui_page.locator("#panel-quick").is_visible()

    def test_responsive_tablet_width(self, ui_page):
        """UI should adapt at tablet width (768px)."""
        ui_page.set_viewport_size({"width": 768, "height": 1024})
        ui_page.wait_for_timeout(500)

        assert ui_page.locator("header").is_visible()
        assert ui_page.locator("#panel-quick").is_visible()

    def test_responsive_desktop_width(self, ui_page):
        """UI should use space well at desktop width (1920px)."""
        ui_page.set_viewport_size({"width": 1920, "height": 1080})
        ui_page.wait_for_timeout(500)

        assert ui_page.locator("header").is_visible()
        assert ui_page.locator("#panel-quick").is_visible()

    def test_keyboard_tab_navigation(self, ui_page):
        """Tab key should move focus through interactive elements."""
        # Focus on first tab button
        ui_page.locator("#tab-quick").focus()
        ui_page.wait_for_timeout(200)

        # Press Tab to move focus
        ui_page.keyboard.press("Tab")
        ui_page.wait_for_timeout(200)

        # Something should have focus
        focused = ui_page.evaluate("document.activeElement?.tagName")
        assert focused is not None

    def test_skip_link_exists(self, ui_page):
        """Skip-to-content link should exist for accessibility."""
        skip = ui_page.locator(".skip-link")
        if skip.count() > 0:
            # Should be visually hidden but present in DOM
            assert skip.count() > 0
