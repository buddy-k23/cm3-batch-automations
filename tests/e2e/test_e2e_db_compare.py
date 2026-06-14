"""E2E Playwright tests for the DB Compare tab."""
from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect


class TestDbCompareTabPresence:
    """Tests that the DB Compare tab exists and navigates correctly."""

    def test_db_compare_tab_visible(self, ui_page: Page) -> None:
        """DB Compare tab button is visible in the tab bar."""
        expect(ui_page.locator('#tab-dbcompare')).to_be_visible()

    def test_db_compare_tab_text(self, ui_page: Page) -> None:
        """Tab button text is 'DB Compare'."""
        expect(ui_page.locator('#tab-dbcompare')).to_have_text('DB Compare')

    def test_db_compare_panel_hidden_initially(self, ui_page: Page) -> None:
        """DB Compare panel is hidden when Quick Test is the active tab."""
        expect(ui_page.locator('#panel-dbcompare')).to_be_hidden()

    def test_clicking_tab_shows_panel(self, ui_page: Page) -> None:
        """Clicking DB Compare tab makes the panel visible."""
        ui_page.click('#tab-dbcompare')
        expect(ui_page.locator('#panel-dbcompare')).to_be_visible()

    def test_switching_away_hides_panel(self, ui_page: Page) -> None:
        """Switching to Quick Test tab hides the DB Compare panel."""
        ui_page.click('#tab-dbcompare')
        ui_page.click('#tab-quick')
        expect(ui_page.locator('#panel-dbcompare')).to_be_hidden()


class TestDbCompareDirectionSwap:
    """Tests for the swap direction button."""

    def test_initial_direction_label(self, ui_page: Page) -> None:
        """Initial direction label shows 'DB is source · File is actual'."""
        ui_page.click('#tab-dbcompare')
        expect(ui_page.locator('#dbcDirectionLabel')).to_have_text('DB is source \u00B7 File is actual')

    def test_swap_changes_label(self, ui_page: Page) -> None:
        """Swap button changes direction label."""
        ui_page.click('#tab-dbcompare')
        ui_page.click('#dbcSwapBtn')
        expect(ui_page.locator('#dbcDirectionLabel')).to_have_text('File is source \u00B7 DB is actual')

    def test_swap_again_restores_label(self, ui_page: Page) -> None:
        """Second swap restores original label."""
        ui_page.click('#tab-dbcompare')
        ui_page.click('#dbcSwapBtn')
        ui_page.click('#dbcSwapBtn')
        expect(ui_page.locator('#dbcDirectionLabel')).to_have_text('DB is source \u00B7 File is actual')


class TestDbCompareConnectionChip:
    """Tests for the DB connection chip expand/collapse."""

    def test_connection_form_hidden_initially(self, ui_page: Page) -> None:
        """Connection form is hidden on load."""
        ui_page.click('#tab-dbcompare')
        expect(ui_page.locator('#dbcConnForm')).to_be_hidden()

    def test_clicking_chip_expands_form(self, ui_page: Page) -> None:
        """Clicking the connection chip reveals the form."""
        ui_page.click('#tab-dbcompare')
        ui_page.click('#dbcConnChip')
        expect(ui_page.locator('#dbcConnForm')).to_be_visible()

    def test_https_warning_element_exists(self, ui_page: Page) -> None:
        """HTTPS warning element is present in the DOM."""
        ui_page.click('#tab-dbcompare')
        assert ui_page.locator('#dbcHttpsWarning').count() == 1


class TestDbCompareRunButton:
    """Tests for the Run button state."""

    def test_run_button_disabled_initially(self, ui_page: Page) -> None:
        """Run button is disabled on load."""
        ui_page.click('#tab-dbcompare')
        expect(ui_page.locator('#dbcRunBtn')).to_be_disabled()

    def test_results_hidden_on_load(self, ui_page: Page) -> None:
        """Results area is hidden before any run."""
        ui_page.click('#tab-dbcompare')
        expect(ui_page.locator('#dbcResults')).to_be_hidden()

    def test_download_diff_btn_row_hidden_on_load(self, ui_page: Page) -> None:
        """Download Diff CSV row is hidden before any run."""
        ui_page.click('#tab-dbcompare')
        expect(ui_page.locator('#dbcDownloadRow')).to_be_hidden()
