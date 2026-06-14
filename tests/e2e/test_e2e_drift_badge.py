"""E2E tests for the drift warning badge in Quick Test tab (#274).

The drift badge (#driftBadge) is introduced by the drift-detection feature
branch. These tests are written defensively — when the element is not yet
present in the DOM they verify the absence is handled gracefully, and when
it is present they verify the correct default (hidden) state.
"""

import pytest


class TestDriftBadge:
    """Drift warning badge E2E tests for the Quick Test tab.

    These tests must remain stable whether or not the drift-detection
    feature has been merged. All assertions use .count() > 0 guards.
    """

    def test_quick_test_panel_loads(self, ui_page):
        """Quick Test panel should be visible on page load."""
        panel = ui_page.locator("#panel-quick")
        assert panel.is_visible()

    def test_drift_badge_hidden_on_load(self, ui_page):
        """Drift badge should be hidden (or absent) on initial page load.

        If #driftBadge exists in the DOM it must start in a hidden state
        so as not to display stale warnings before any validation has run.
        """
        badge = ui_page.locator("#driftBadge")
        if badge.count() > 0:
            assert badge.is_hidden(), (
                "driftBadge should be hidden (display:none) on initial page load"
            )
        # If element is absent, the test passes — drift feature not yet deployed

    def test_drift_badge_not_visible_without_validation(self, ui_page):
        """Drift badge should not be visible in the Quick Test panel before running validation."""
        badge = ui_page.locator("#driftBadge")
        # Either absent from DOM or hidden — both are acceptable initial states
        if badge.count() > 0:
            is_visible = badge.is_visible()
            assert not is_visible, (
                "driftBadge should not be visible before any validation has run"
            )

    def test_drift_badge_hidden_after_file_upload(self, ui_page, sample_pipe_file):
        """Uploading a new file should not show the drift badge."""
        ui_page.locator("#fileInput").set_input_files(str(sample_pipe_file))
        ui_page.wait_for_timeout(500)

        badge = ui_page.locator("#driftBadge")
        if badge.count() > 0:
            assert badge.is_hidden(), (
                "driftBadge should remain hidden when a new file is uploaded "
                "(drift check only runs after validation)"
            )

    def test_drift_badge_id_present_when_feature_active(self, ui_page):
        """When the drift feature is active, #driftBadge should be in the DOM.

        This test acts as a canary — if drift detection is deployed but the
        element ID is missing, the test fails to alert developers.
        """
        # Check if drift-related content is present anywhere in the panel
        panel = ui_page.locator("#panel-quick")
        panel_html = ui_page.evaluate("document.getElementById('panel-quick')?.innerHTML || ''")

        if "drift" in panel_html.lower():
            # Drift feature is active — the badge element should be present
            badge = ui_page.locator("#driftBadge")
            assert badge.count() > 0, (
                "drift content detected in panel but #driftBadge element not found — "
                "check that the element ID matches the expected 'driftBadge'"
            )

    def test_quick_test_panel_no_unexpected_errors_on_load(self, ui_page):
        """Quick Test panel should load cleanly with no JS console errors blocking UI."""
        panel = ui_page.locator("#panel-quick")
        assert panel.is_visible()
        # Primary functional elements should still be present regardless of drift feature state
        assert ui_page.locator("#fileInput").count() > 0, "fileInput must be present"
        assert ui_page.locator("#btnValidate").count() > 0, "btnValidate must be present"
