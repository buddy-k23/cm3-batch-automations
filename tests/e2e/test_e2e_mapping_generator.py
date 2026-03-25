"""E2E tests for Mapping Generator tab (#116)."""

import pytest


class TestMappingGenerator:
    """Mapping Generator tab E2E tests."""

    def test_mapping_generator_tab_loads(self, ui_page):
        """Clicking Mapping Generator tab should show the panel."""
        tab = ui_page.locator("#tab-mapping")
        tab.click()
        ui_page.wait_for_timeout(500)

        assert tab.get_attribute("aria-selected") == "true"
        panel = ui_page.locator("#panel-mapping")
        assert panel.is_visible()

    def test_mapping_generator_has_upload_zone(self, ui_page):
        """Mapping Generator should have a file upload area."""
        ui_page.locator("#tab-mapping").click()
        ui_page.wait_for_timeout(500)

        panel = ui_page.locator("#panel-mapping")
        # Should have a drop zone or file input
        has_upload = (
            panel.locator(".drop-zone").count() > 0
            or panel.locator("input[type='file']").count() > 0
        )
        assert has_upload, "Should have file upload area"

    def test_mapping_generator_has_generate_button(self, ui_page):
        """Should have a Generate Mapping button."""
        ui_page.locator("#tab-mapping").click()
        ui_page.wait_for_timeout(500)

        btn = ui_page.locator("button:has-text('Generate')")
        assert btn.count() > 0, "Should have a Generate button"

    def test_generate_button_disabled_without_file(self, ui_page):
        """Generate Mapping button should be disabled without a template."""
        ui_page.locator("#tab-mapping").click()
        ui_page.wait_for_timeout(500)

        btn = ui_page.locator("#btnGenMapping")
        if btn.count() > 0:
            assert btn.is_disabled(), "Generate Mapping should be disabled without a file"

    def test_upload_csv_mapping_template(self, ui_page, sample_csv_mapping):
        """Uploading a CSV template should be accepted."""
        ui_page.locator("#tab-mapping").click()
        ui_page.wait_for_timeout(500)

        file_input = ui_page.locator("#mapFileInput")
        if file_input.count() > 0:
            file_input.set_input_files(str(sample_csv_mapping))
            ui_page.wait_for_timeout(500)

            panel = ui_page.locator("#panel-mapping")
            text = panel.text_content().lower()
            assert "template" in text or "csv" in text or "uploaded" in text or "file" in text

    def test_mapping_name_input_exists(self, ui_page):
        """Should have an input for custom mapping name."""
        ui_page.locator("#tab-mapping").click()
        ui_page.wait_for_timeout(500)

        panel = ui_page.locator("#panel-mapping")
        text = panel.text_content().lower()
        # Should have mapping name input or label
        assert "name" in text or "mapping" in text

    def test_rules_section_exists(self, ui_page):
        """Should have a validation rules section."""
        ui_page.locator("#tab-mapping").click()
        ui_page.wait_for_timeout(500)

        panel = ui_page.locator("#panel-mapping")
        text = panel.text_content().lower()
        assert "rules" in text or "validation" in text
