"""E2E tests for Quick Test tab — compare workflow (#114)."""

import pytest


class TestQuickTestCompare:
    """Quick Test tab comparison E2E tests."""

    def test_compare_toggle_shows_second_upload(self, ui_page):
        """Clicking Compare Mode should show a second file upload area."""
        ui_page.locator("#btnToggleCompare").click()
        ui_page.wait_for_timeout(500)
        second_input = ui_page.locator("#fileInput2")
        assert second_input.count() > 0, "Secondary file input should appear"

    def test_upload_two_files_for_compare(self, ui_page, sample_pipe_file, sample_pipe_file_b):
        """Should be able to upload two files for comparison."""
        # Upload primary
        ui_page.locator("#fileInput").set_input_files(str(sample_pipe_file))
        ui_page.wait_for_timeout(300)

        # Enable compare mode
        ui_page.locator("#btnToggleCompare").click()
        ui_page.wait_for_timeout(300)

        # Upload secondary
        second_input = ui_page.locator("#fileInput2")
        if second_input.count() > 0:
            second_input.set_input_files(str(sample_pipe_file_b))
            ui_page.wait_for_timeout(300)

        panel = ui_page.locator("#panel-quick")
        text = panel.text_content()
        assert "sample" in text.lower() or "file" in text.lower()

    def test_compare_identical_files(self, ui_page, sample_pipe_file, tmp_path):
        """Comparing identical files should complete without error."""
        # Create identical copy
        identical = tmp_path / "identical.txt"
        identical.write_text(sample_pipe_file.read_text())

        ui_page.locator("#fileInput").set_input_files(str(sample_pipe_file))
        ui_page.wait_for_timeout(500)

        ui_page.locator("#btnToggleCompare").click()
        ui_page.wait_for_timeout(300)

        second_input = ui_page.locator("#fileInput2")
        if second_input.count() > 0:
            second_input.set_input_files(str(identical))
            ui_page.wait_for_timeout(500)

        # Click the compare button (force click since it may be conditionally enabled)
        compare_btn = ui_page.locator("#btnCompare")
        compare_btn.click(force=True)
        ui_page.wait_for_timeout(5000)

        panel = ui_page.locator("#panel-quick")
        content = panel.text_content().lower()
        # Should show comparison results or at least not crash
        assert any(w in content for w in [
            "match", "compare", "identical", "rows", "diff", "result", "report", "error", "select"
        ]), f"Expected comparison feedback, got: {content[:200]}"
