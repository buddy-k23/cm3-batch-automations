"""E2E tests for the Multi-Record Config Wizard in the Mapping Generator tab (#274)."""

import pytest


class TestMultiRecordWizard:
    """Multi-Record Config Wizard E2E tests.

    The wizard lives in the Mapping Generator tab under the section with
    id="mrWizardSection". It is toggled open by the "Start Wizard" button
    (#mrToggleBtn) and collapsed by default.
    """

    def test_mapping_tab_loads(self, ui_page):
        """Clicking Mapping Generator tab should show the panel."""
        tab = ui_page.locator("#tab-mapping")
        tab.click()
        ui_page.wait_for_timeout(500)

        assert tab.get_attribute("aria-selected") == "true"
        panel = ui_page.locator("#panel-mapping")
        assert panel.is_visible()

    def test_wizard_section_exists_in_mapping_tab(self, ui_page):
        """Mapping Generator panel should contain the wizard section."""
        ui_page.locator("#tab-mapping").click()
        ui_page.wait_for_timeout(500)

        section = ui_page.locator("#mrWizardSection")
        assert section.count() > 0, "mrWizardSection should be in the DOM"

    def test_wizard_toggle_button_exists(self, ui_page):
        """Start Wizard button (#mrToggleBtn) should be present."""
        ui_page.locator("#tab-mapping").click()
        ui_page.wait_for_timeout(500)

        btn = ui_page.locator("#mrToggleBtn")
        assert btn.count() > 0, "mrToggleBtn should be present in the wizard section"

    def test_wizard_body_hidden_by_default(self, ui_page):
        """Wizard body (#mrWizardBody) should be collapsed before toggling."""
        ui_page.locator("#tab-mapping").click()
        ui_page.wait_for_timeout(500)

        wizard_body = ui_page.locator("#mrWizardBody")
        assert wizard_body.count() > 0, "mrWizardBody should be in the DOM"
        # Body is collapsed by default (display:none)
        assert wizard_body.is_hidden(), "Wizard body should be hidden on initial load"

    def test_wizard_opens_on_toggle(self, ui_page):
        """Clicking Start Wizard should expand the wizard body."""
        ui_page.locator("#tab-mapping").click()
        ui_page.wait_for_timeout(500)

        ui_page.locator("#mrToggleBtn").click()
        ui_page.wait_for_timeout(500)

        wizard_body = ui_page.locator("#mrWizardBody")
        assert wizard_body.is_visible(), "Wizard body should be visible after toggle"

    def test_wizard_step_indicators_exist(self, ui_page):
        """Wizard step indicators should be present once wizard is open."""
        ui_page.locator("#tab-mapping").click()
        ui_page.wait_for_timeout(500)
        ui_page.locator("#mrToggleBtn").click()
        ui_page.wait_for_timeout(500)

        indicators = ui_page.locator("#mrStepIndicators")
        assert indicators.count() > 0, "Step indicators container should exist"
        steps = indicators.locator("button")
        assert steps.count() >= 4, "Should have at least 4 step indicator buttons"

    def test_wizard_next_button_exists(self, ui_page):
        """Next navigation button should be present once wizard is open."""
        ui_page.locator("#tab-mapping").click()
        ui_page.wait_for_timeout(500)
        ui_page.locator("#mrToggleBtn").click()
        ui_page.wait_for_timeout(500)

        next_btn = ui_page.locator("#mrNextBtn")
        assert next_btn.count() > 0, "mrNextBtn should exist in the wizard"

    def test_wizard_back_button_exists_in_dom(self, ui_page):
        """Back navigation button should exist in the DOM (hidden on first step)."""
        ui_page.locator("#tab-mapping").click()
        ui_page.wait_for_timeout(500)
        ui_page.locator("#mrToggleBtn").click()
        ui_page.wait_for_timeout(500)

        back_btn = ui_page.locator("#mrBackBtn")
        assert back_btn.count() > 0, "mrBackBtn should exist in the DOM"

    def test_wizard_step1_record_types_visible(self, ui_page):
        """Step 1 panel should be visible when wizard first opens."""
        ui_page.locator("#tab-mapping").click()
        ui_page.wait_for_timeout(500)
        ui_page.locator("#mrToggleBtn").click()
        ui_page.wait_for_timeout(500)

        step1 = ui_page.locator("#mrStep1")
        assert step1.count() > 0, "mrStep1 panel should exist"
        assert step1.is_visible(), "Step 1 should be visible on wizard open"

    def test_wizard_download_button_exists(self, ui_page):
        """Download YAML button should exist in the wizard's preview step."""
        ui_page.locator("#tab-mapping").click()
        ui_page.wait_for_timeout(500)
        ui_page.locator("#mrToggleBtn").click()
        ui_page.wait_for_timeout(500)

        download_btn = ui_page.locator("#mrDownloadBtn")
        assert download_btn.count() > 0, "mrDownloadBtn should be in the DOM"

    def test_wizard_validate_button_exists(self, ui_page):
        """Validate File With This Config button should exist in the wizard."""
        ui_page.locator("#tab-mapping").click()
        ui_page.wait_for_timeout(500)
        ui_page.locator("#mrToggleBtn").click()
        ui_page.wait_for_timeout(500)

        validate_btn = ui_page.locator("#mrValidateBtn")
        assert validate_btn.count() > 0, "mrValidateBtn should be in the DOM"

    def test_wizard_generate_yaml_button_exists(self, ui_page):
        """Generate YAML button should exist in the wizard's preview step."""
        ui_page.locator("#tab-mapping").click()
        ui_page.wait_for_timeout(500)
        ui_page.locator("#mrToggleBtn").click()
        ui_page.wait_for_timeout(500)

        gen_btn = ui_page.locator("#mrGenerateBtn")
        assert gen_btn.count() > 0, "mrGenerateBtn should be in the DOM"
