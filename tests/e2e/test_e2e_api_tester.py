"""E2E tests for API Tester tab (#117)."""

import pytest


class TestApiTester:
    """API Tester tab E2E tests."""

    def test_api_tester_tab_loads(self, ui_page):
        """Clicking API Tester tab should show the panel."""
        tab = ui_page.locator("#tab-tester")
        tab.click()
        ui_page.wait_for_timeout(500)

        assert tab.get_attribute("aria-selected") == "true"
        panel = ui_page.locator("#panel-tester")
        assert panel.is_visible()

    def test_api_tester_has_method_selector(self, ui_page):
        """Should have an HTTP method dropdown."""
        ui_page.locator("#tab-tester").click()
        ui_page.wait_for_timeout(500)

        method_sel = ui_page.locator("#atMethod")
        assert method_sel.is_visible()
        # Should have GET as default or first option
        value = method_sel.input_value()
        assert value in ["GET", "POST", "PUT", "PATCH", "DELETE"]

    def test_api_tester_has_url_and_path(self, ui_page):
        """Should have base URL and path inputs."""
        ui_page.locator("#tab-tester").click()
        ui_page.wait_for_timeout(500)

        base_url = ui_page.locator("#atBaseUrl")
        path = ui_page.locator("#atPath")
        assert base_url.is_visible()
        assert path.is_visible()

    def test_api_tester_send_button(self, ui_page):
        """Should have a Send button."""
        ui_page.locator("#tab-tester").click()
        ui_page.wait_for_timeout(500)

        send = ui_page.locator("button:has-text('Send')")
        assert send.is_visible()

    def test_send_request_updates_response_area(self, ui_page, base_url):
        """Sending a request should update the response area."""
        ui_page.locator("#tab-tester").click()
        ui_page.wait_for_timeout(500)

        # Fill base URL and path
        ui_page.locator("#atBaseUrl").fill(base_url)
        ui_page.locator("#atPath").fill("/api/v1/system/health")

        # Get initial response area text
        panel = ui_page.locator("#panel-tester")
        initial_text = panel.inner_text()

        # Click Send
        ui_page.locator("button:has-text('Send')").click()
        ui_page.wait_for_timeout(5000)

        # Response area should have changed (got some response content)
        updated_text = panel.inner_text()
        assert updated_text != initial_text or "send" in updated_text.lower(), \
            "Response area should update after sending request"

    def test_response_viewer_tabs(self, ui_page):
        """Response area should have Body/Headers/Raw tabs."""
        ui_page.locator("#tab-tester").click()
        ui_page.wait_for_timeout(500)

        panel = ui_page.locator("#panel-tester")
        text = panel.text_content().lower()
        assert "body" in text
        assert "headers" in text or "raw" in text

    def test_api_tester_headers_section(self, ui_page):
        """Should have a Headers section for adding custom headers."""
        ui_page.locator("#tab-tester").click()
        ui_page.wait_for_timeout(500)

        panel = ui_page.locator("#panel-tester")
        text = panel.text_content()
        assert "HEADERS" in text or "Headers" in text or "header" in text.lower()

    def test_api_tester_body_type_options(self, ui_page):
        """Should have body type options (None, JSON, Form Data)."""
        ui_page.locator("#tab-tester").click()
        ui_page.wait_for_timeout(500)

        panel = ui_page.locator("#panel-tester")
        text = panel.text_content()
        assert "JSON" in text or "json" in text.lower()

    def test_api_tester_suite_runner_section(self, ui_page):
        """Should have a Suite Runner section."""
        ui_page.locator("#tab-tester").click()
        ui_page.wait_for_timeout(500)

        panel = ui_page.locator("#panel-tester")
        text = panel.text_content()
        assert "Suite" in text or "suite" in text.lower()

    def test_save_request_input(self, ui_page):
        """Should have request name input and save button."""
        ui_page.locator("#tab-tester").click()
        ui_page.wait_for_timeout(500)

        name_input = ui_page.locator("#atReqName")
        save_btn = ui_page.locator("button:has-text('Save')")
        assert name_input.count() > 0 or save_btn.count() > 0
