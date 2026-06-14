"""E2E tests for multi-record YAML upload in Quick Test tab (#274)."""

import pytest


class TestMultiRecordValidate:
    """Multi-record YAML section in Quick Test tab E2E tests.

    The Quick Test panel includes a file input (#qtMrYamlInput) that lets
    users upload a multi-record YAML config as an alternative to selecting
    a mapping. This section is always visible — not hidden behind a toggle.
    """

    def test_quick_test_panel_active(self, ui_page):
        """Quick Test tab should be active on load."""
        tab = ui_page.locator("#tab-quick")
        assert tab.get_attribute("aria-selected") == "true"
        panel = ui_page.locator("#panel-quick")
        assert panel.is_visible()

    def test_mr_yaml_input_exists(self, ui_page):
        """Multi-record YAML file input (#qtMrYamlInput) should be in the DOM."""
        yaml_input = ui_page.locator("#qtMrYamlInput")
        assert yaml_input.count() > 0, "qtMrYamlInput should exist in Quick Test panel"

    def test_mr_yaml_section_label_visible(self, ui_page):
        """The Multi-record YAML label should be visible in Quick Test."""
        panel = ui_page.locator("#panel-quick")
        text = panel.text_content().lower()
        assert "multi-record" in text or "yaml" in text, (
            "Quick Test panel should mention multi-record YAML"
        )

    def test_mr_yaml_input_accepts_yaml_files(self, ui_page):
        """qtMrYamlInput should accept .yaml and .yml file types."""
        yaml_input = ui_page.locator("#qtMrYamlInput")
        assert yaml_input.count() > 0, "qtMrYamlInput should exist"
        accept = yaml_input.get_attribute("accept") or ""
        assert ".yaml" in accept or ".yml" in accept, (
            "qtMrYamlInput should accept .yaml or .yml files"
        )

    def test_validate_button_exists_in_quick_test(self, ui_page):
        """The primary Validate button (#btnValidate) should be present."""
        btn = ui_page.locator("#btnValidate")
        assert btn.count() > 0, "btnValidate should be in Quick Test panel"
        assert btn.is_visible()

    def test_validate_button_disabled_without_file(self, ui_page):
        """Validate button should be disabled when no batch file is uploaded."""
        btn = ui_page.locator("#btnValidate")
        assert btn.is_disabled(), (
            "btnValidate should be disabled until a batch file is uploaded"
        )

    def test_upload_batch_file_enables_validate(self, ui_page, sample_pipe_file):
        """Uploading a batch file + selecting a mapping enables Validate."""
        ui_page.locator("#fileInput").set_input_files(str(sample_pipe_file))
        ui_page.wait_for_timeout(500)

        # Select the first available mapping if any exist
        mapping_select = ui_page.locator("#mappingSelect")
        options = mapping_select.locator("option").all()
        non_empty = [o for o in options if o.get_attribute("value")]
        if non_empty:
            mapping_select.select_option(index=1)
            ui_page.wait_for_timeout(300)
            btn = ui_page.locator("#btnValidate")
            assert not btn.is_disabled(), "btnValidate should be enabled with file + mapping"
        else:
            # No mappings loaded — button stays disabled; just verify it exists
            assert ui_page.locator("#btnValidate").count() > 0

    def test_toggle_mr_yaml_button_optional(self, ui_page):
        """If a btnToggleMrYaml exists, clicking it should reveal the YAML section."""
        # This element may exist in future versions — test defensively
        toggle_btn = ui_page.locator("#btnToggleMrYaml")
        if toggle_btn.count() > 0:
            toggle_btn.click()
            ui_page.wait_for_timeout(500)
            yaml_section = ui_page.locator("#mrYamlSection")
            assert yaml_section.count() > 0, (
                "mrYamlSection should appear after clicking btnToggleMrYaml"
            )
        else:
            # No toggle button — YAML input is always visible inline
            yaml_input = ui_page.locator("#qtMrYamlInput")
            assert yaml_input.count() > 0, (
                "qtMrYamlInput should be present as inline YAML input"
            )

    def test_mr_yaml_section_link_to_mapping_generator(self, ui_page):
        """The YAML section should link to the Mapping Generator tab."""
        panel = ui_page.locator("#panel-quick")
        # Look for a link that mentions Mapping Generator
        links = panel.locator("a")
        found_mapping_link = False
        count = links.count()
        for i in range(count):
            text = links.nth(i).text_content().lower()
            if "mapping" in text or "generator" in text:
                found_mapping_link = True
                break
        assert found_mapping_link, (
            "Quick Test panel should have a link to the Mapping Generator tab"
        )
