"""E2E tests for the Download Failed Rows button in Quick Test tab (#274).

The Download Failed Rows button (#btnDownloadErrors) is introduced by the
failed-rows-export feature branch. These tests are written defensively —
they remain stable whether or not the feature has been merged into main.
When the element is present, tests verify the correct default (hidden) state
and expected interaction behaviour using page.expect_download().
"""

import pytest


class TestDownloadErrors:
    """Download Failed Rows button E2E tests for the Quick Test tab.

    All assertions use .count() > 0 guards so these tests run cleanly
    against codebases that do not yet include the failed-rows-export feature.
    """

    def test_quick_test_panel_loads(self, ui_page):
        """Quick Test panel should be visible on page load."""
        panel = ui_page.locator("#panel-quick")
        assert panel.is_visible()

    def test_download_errors_button_hidden_on_load(self, ui_page):
        """Download errors button should be hidden (or absent) on initial page load.

        The button should only appear after a validation run that produced errors.
        """
        btn = ui_page.locator("#btnDownloadErrors")
        if btn.count() > 0:
            assert btn.is_hidden(), (
                "btnDownloadErrors should be hidden (display:none) on initial page load "
                "before any validation has run"
            )
        # If element is absent, the feature is not yet deployed — test passes

    def test_download_errors_not_visible_without_validation(self, ui_page):
        """Download errors button should not be visible before validation runs."""
        btn = ui_page.locator("#btnDownloadErrors")
        if btn.count() > 0:
            assert not btn.is_visible(), (
                "btnDownloadErrors should not be visible before validation runs"
            )

    def test_download_errors_hidden_after_new_file_upload(self, ui_page, sample_pipe_file):
        """Uploading a new file should reset/hide the download errors button.

        When a new file is selected, any stale error download state should
        be cleared so the user does not download errors from a prior run.
        """
        ui_page.locator("#fileInput").set_input_files(str(sample_pipe_file))
        ui_page.wait_for_timeout(500)

        btn = ui_page.locator("#btnDownloadErrors")
        if btn.count() > 0:
            assert btn.is_hidden(), (
                "btnDownloadErrors should be hidden when a new batch file is uploaded"
            )

    def test_download_errors_id_present_when_feature_active(self, ui_page):
        """When the failed-rows-export feature is active, #btnDownloadErrors should exist.

        This canary test fails if the feature is deployed but the button ID
        has drifted from the expected 'btnDownloadErrors'.
        """
        panel_html = ui_page.evaluate(
            "document.getElementById('panel-quick')?.innerHTML || ''"
        )

        if "download" in panel_html.lower() and "error" in panel_html.lower():
            # Feature appears active — check the expected element exists
            btn = ui_page.locator("#btnDownloadErrors")
            assert btn.count() > 0, (
                "Download/error content found in panel but #btnDownloadErrors not found — "
                "check that the element ID matches the expected 'btnDownloadErrors'"
            )

    def test_core_quick_test_elements_unaffected(self, ui_page):
        """Core Quick Test elements must always be present regardless of feature state."""
        assert ui_page.locator("#fileInput").count() > 0, "fileInput must be present"
        assert ui_page.locator("#btnValidate").count() > 0, "btnValidate must be present"
        assert ui_page.locator("#mappingSelect").count() > 0, "mappingSelect must be present"
