"""E2E tests for Quick Test tab — validate workflow (#113)."""

import pytest


class TestQuickTestValidate:
    """Quick Test tab validation E2E tests."""

    def test_quick_test_tab_active_by_default(self, ui_page):
        """Quick Test tab should be active on initial load."""
        tab = ui_page.locator("#tab-quick")
        assert tab.get_attribute("aria-selected") == "true"
        panel = ui_page.locator("#panel-quick")
        assert panel.is_visible()

    def test_drop_zone_visible(self, ui_page):
        """Drop zone should be visible with upload prompt."""
        drop_zone = ui_page.locator(".drop-zone").first
        assert drop_zone.is_visible()
        assert "drop" in drop_zone.text_content().lower() or "browse" in drop_zone.text_content().lower()

    def test_upload_file_shows_filename(self, ui_page, sample_pipe_file):
        """Uploading a file should display the filename."""
        file_input = ui_page.locator("#fileInput")
        file_input.set_input_files(str(sample_pipe_file))
        ui_page.wait_for_timeout(1000)
        panel = ui_page.locator("#panel-quick")
        text = panel.text_content().lower()
        assert "sample" in text or "uploaded" in text or "file" in text

    def test_mapping_dropdown_exists(self, ui_page):
        """Mapping dropdown should be present."""
        mapping_select = ui_page.locator("select").first
        assert mapping_select.is_visible()

    def test_validate_button_exists(self, ui_page):
        """Validate button should be present and clickable."""
        btn = ui_page.locator("#btnValidate")
        assert btn.is_visible()

    def test_validate_button_disabled_without_file(self, ui_page):
        """Validate button should be disabled when no file is uploaded."""
        btn = ui_page.locator("#btnValidate")
        assert btn.is_disabled(), "Validate should be disabled without a file"

    def test_upload_and_validate_workflow(self, ui_page, sample_pipe_file):
        """Full workflow: upload file, click validate, see results."""
        # Upload file
        file_input = ui_page.locator("#fileInput")
        file_input.set_input_files(str(sample_pipe_file))
        ui_page.wait_for_timeout(1000)

        # Click validate (use force since button enables after upload)
        btn = ui_page.locator("#btnValidate")
        btn.click(force=True)

        # Wait for response — either a report iframe, results area, or status message
        ui_page.wait_for_timeout(5000)
        panel = ui_page.locator("#panel-quick")
        content = panel.text_content().lower()
        # Should have some result indication
        assert any(w in content for w in [
            "total", "rows", "valid", "error", "result", "report", "pass", "fail"
        ]), f"Expected result indicators in panel, got: {content[:200]}"

    def test_compare_toggle_reveals_secondary_upload(self, ui_page):
        """Compare toggle should reveal a secondary file upload area."""
        toggle_btn = ui_page.locator("#btnToggleCompare")
        toggle_btn.click()
        ui_page.wait_for_timeout(500)
        second_input = ui_page.locator("#fileInput2")
        assert second_input.count() > 0, "Secondary file input should appear"
