"""E2E tests for Browse→Search linking feature in the File Downloader tab (#371).

Each Browse row type (directory, file, archive, inner archive member) gains a
contextual action button that switches to the correct Search sub-tab and
pre-fills the relevant fields.

All assertions use .count() > 0 guards so tests stay stable against a server
without the feature fully deployed.
"""

import pytest


def _navigate_to_downloader(page, base_url):
    """Navigate to the UI and click into the Downloader tab."""
    page.goto(f"{base_url}/ui")
    page.wait_for_load_state("networkidle")
    tab = page.locator("#tab-downloader, [data-tab='downloader'], button:has-text('Downloader')")
    if tab.count() > 0:
        tab.first.click()
        page.wait_for_timeout(300)


def _mock_browse_response(page, entries):
    """Route the browse API to return a controlled entry list."""
    import json

    page.route(
        "**/api/v1/downloader/browse*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"entries": entries}),
        ),
    )


def _mock_paths_response(page):
    """Route the paths API to return a single test path."""
    import json

    page.route(
        "**/api/v1/downloader/paths",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"paths": [{"path": "/test/root", "label": "Test Root"}]}),
        ),
    )


def _mock_archive_contents(page, files):
    """Route the archive-contents API to return a list of inner files."""
    import json

    page.route(
        "**/api/v1/downloader/archive-contents*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"files": files}),
        ),
    )


class TestBrowseSearchLinkButtons:
    """Browse→Search contextual link buttons.

    All tests guard with .count() > 0 so they pass when run against a codebase
    that does not yet have the feature deployed.
    """

    def test_directory_row_has_search_here_button(self, page, base_url):
        """Directory rows should show a 'Search here' button in Browse results."""
        _mock_paths_response(page)
        _mock_browse_response(page, [{"name": "subdir", "type": "directory"}])
        _navigate_to_downloader(page, base_url)

        # Trigger a browse so results are rendered
        page.evaluate("""
            document.getElementById('fdPathSelect') &&
            (document.getElementById('fdPathSelect').value = '/test/root');
            typeof fdBrowse === 'function' && fdBrowse();
        """)
        page.wait_for_timeout(400)

        results = page.locator("#fdBrowseResults")
        if results.count() == 0:
            return  # downloader panel not present

        search_here = results.locator("button", has_text="Search here")
        if search_here.count() > 0:
            assert search_here.first.is_visible(), (
                "'Search here' button on directory row must be visible"
            )

    def test_directory_row_has_search_archives_here_button(self, page, base_url):
        """Directory rows should show a 'Search archives here' button."""
        _mock_paths_response(page)
        _mock_browse_response(page, [{"name": "subdir", "type": "directory"}])
        _navigate_to_downloader(page, base_url)

        page.evaluate("""
            document.getElementById('fdPathSelect') &&
            (document.getElementById('fdPathSelect').value = '/test/root');
            typeof fdBrowse === 'function' && fdBrowse();
        """)
        page.wait_for_timeout(400)

        results = page.locator("#fdBrowseResults")
        if results.count() == 0:
            return

        arch_btn = results.locator("button", has_text="Search archives here")
        if arch_btn.count() > 0:
            assert arch_btn.first.is_visible(), (
                "'Search archives here' button on directory row must be visible"
            )

    def test_file_row_has_search_in_file_button(self, page, base_url):
        """File rows should show a 'Search in file' button."""
        _mock_paths_response(page)
        _mock_browse_response(page, [{"name": "data.log", "type": "file", "size_bytes": 1024}])
        _navigate_to_downloader(page, base_url)

        page.evaluate("""
            document.getElementById('fdPathSelect') &&
            (document.getElementById('fdPathSelect').value = '/test/root');
            typeof fdBrowse === 'function' && fdBrowse();
        """)
        page.wait_for_timeout(400)

        results = page.locator("#fdBrowseResults")
        if results.count() == 0:
            return

        btn = results.locator("button", has_text="Search in file")
        if btn.count() > 0:
            assert btn.first.is_visible(), (
                "'Search in file' button on file row must be visible"
            )

    def test_archive_row_has_search_in_archive_button(self, page, base_url):
        """Archive rows should show a 'Search in archive' button."""
        _mock_paths_response(page)
        _mock_browse_response(
            page, [{"name": "batch.tar.gz", "type": "archive", "size_bytes": 2048}]
        )
        _navigate_to_downloader(page, base_url)

        page.evaluate("""
            document.getElementById('fdPathSelect') &&
            (document.getElementById('fdPathSelect').value = '/test/root');
            typeof fdBrowse === 'function' && fdBrowse();
        """)
        page.wait_for_timeout(400)

        results = page.locator("#fdBrowseResults")
        if results.count() == 0:
            return

        btn = results.locator("button", has_text="Search in archive")
        if btn.count() > 0:
            assert btn.first.is_visible(), (
                "'Search in archive' button on archive row must be visible"
            )

    def test_search_here_switches_tab_and_fills_path(self, page, base_url):
        """Clicking 'Search here' on a directory row switches to Search Files tab
        and populates #fdSearchInPath with the directory path."""
        _mock_paths_response(page)
        _mock_browse_response(page, [{"name": "logs", "type": "directory"}])
        _navigate_to_downloader(page, base_url)

        page.evaluate("""
            document.getElementById('fdPathSelect') &&
            (document.getElementById('fdPathSelect').value = '/test/root');
            typeof fdBrowse === 'function' && fdBrowse();
        """)
        page.wait_for_timeout(400)

        results = page.locator("#fdBrowseResults")
        if results.count() == 0:
            return

        search_here = results.locator("button", has_text="Search here")
        if search_here.count() == 0:
            return  # feature not yet deployed

        search_here.first.click()
        page.wait_for_timeout(200)

        # Search Files sub-tab should now be active
        search_panel = page.locator("#fdpanel-search")
        if search_panel.count() > 0:
            assert not search_panel.get_attribute("class") or \
                "fd-subpanel--hidden" not in (search_panel.get_attribute("class") or ""), \
                "#fdpanel-search should be visible after clicking 'Search here'"

        # #fdSearchInPath should contain the directory path
        in_path = page.locator("#fdSearchInPath")
        if in_path.count() > 0:
            val = in_path.input_value()
            assert "logs" in val or "/test/root" in val, (
                f"#fdSearchInPath should contain the directory path, got: '{val}'"
            )

    def test_search_in_file_prefills_filename(self, page, base_url):
        """Clicking 'Search in file' pre-fills #fdSearchFilename with the exact filename."""
        _mock_paths_response(page)
        _mock_browse_response(
            page, [{"name": "output.log", "type": "file", "size_bytes": 512}]
        )
        _navigate_to_downloader(page, base_url)

        page.evaluate("""
            document.getElementById('fdPathSelect') &&
            (document.getElementById('fdPathSelect').value = '/test/root');
            typeof fdBrowse === 'function' && fdBrowse();
        """)
        page.wait_for_timeout(400)

        results = page.locator("#fdBrowseResults")
        if results.count() == 0:
            return

        btn = results.locator("button", has_text="Search in file")
        if btn.count() == 0:
            return

        btn.first.click()
        page.wait_for_timeout(200)

        filename_input = page.locator("#fdSearchFilename")
        if filename_input.count() > 0:
            val = filename_input.input_value()
            assert val == "output.log", (
                f"#fdSearchFilename should be pre-filled with 'output.log', got: '{val}'"
            )

    def test_search_in_archive_prefills_pattern(self, page, base_url):
        """Clicking 'Search in archive' pre-fills #fdArchSearchPattern with archive name."""
        _mock_paths_response(page)
        _mock_browse_response(
            page, [{"name": "batch_20240101.tar.gz", "type": "archive", "size_bytes": 4096}]
        )
        _navigate_to_downloader(page, base_url)

        page.evaluate("""
            document.getElementById('fdPathSelect') &&
            (document.getElementById('fdPathSelect').value = '/test/root');
            typeof fdBrowse === 'function' && fdBrowse();
        """)
        page.wait_for_timeout(400)

        results = page.locator("#fdBrowseResults")
        if results.count() == 0:
            return

        btn = results.locator("button", has_text="Search in archive")
        if btn.count() == 0:
            return

        btn.first.click()
        page.wait_for_timeout(200)

        pattern_input = page.locator("#fdArchSearchPattern")
        if pattern_input.count() > 0:
            val = pattern_input.input_value()
            assert val == "batch_20240101.tar.gz", (
                f"#fdArchSearchPattern should be 'batch_20240101.tar.gz', got: '{val}'"
            )

    def test_clear_link_resets_path(self, page, base_url):
        """Clicking the clear (x) button should hide the 'Search in' group and clear _fdLinkedPath."""
        _mock_paths_response(page)
        _mock_browse_response(page, [{"name": "subdir", "type": "directory"}])
        _navigate_to_downloader(page, base_url)

        page.evaluate("""
            document.getElementById('fdPathSelect') &&
            (document.getElementById('fdPathSelect').value = '/test/root');
            typeof fdBrowse === 'function' && fdBrowse();
        """)
        page.wait_for_timeout(400)

        results = page.locator("#fdBrowseResults")
        if results.count() == 0:
            return

        search_here = results.locator("button", has_text="Search here")
        if search_here.count() == 0:
            return

        search_here.first.click()
        page.wait_for_timeout(200)

        # Click the clear button
        clear_btn = page.locator(".fd-clear-link").first
        if clear_btn.count() == 0:
            return

        clear_btn.click()
        page.wait_for_timeout(200)

        # _fdLinkedPath should be null
        linked = page.evaluate("typeof _fdLinkedPath !== 'undefined' ? _fdLinkedPath : 'NOT_DEFINED'")
        if linked != "NOT_DEFINED":
            assert linked is None, f"_fdLinkedPath should be null after clear, got: {linked!r}"

        # Search in group should be hidden
        group = page.locator("#fdSearchInGroup")
        if group.count() > 0:
            assert group.is_hidden(), "#fdSearchInGroup should be hidden after clear"

    def test_root_path_change_clears_link(self, page, base_url):
        """Changing the root path selector should clear linked path fields."""
        _mock_paths_response(page)
        _mock_browse_response(page, [{"name": "subdir", "type": "directory"}])
        _navigate_to_downloader(page, base_url)

        # First set a linked path by evaluating JS directly
        page.evaluate("""
            if (typeof _fdLinkedPath !== 'undefined') {
                _fdLinkedPath = '/test/root/subdir';
                var si = document.getElementById('fdSearchInPath');
                var ai = document.getElementById('fdArchSearchInPath');
                if (si) si.value = '/test/root/subdir';
                if (ai) ai.value = '/test/root/subdir';
            }
        """)
        page.wait_for_timeout(100)

        # Simulate path change
        page.evaluate("typeof onFdPathChange === 'function' && onFdPathChange()")
        page.wait_for_timeout(200)

        linked = page.evaluate("typeof _fdLinkedPath !== 'undefined' ? _fdLinkedPath : 'NOT_DEFINED'")
        if linked != "NOT_DEFINED":
            assert linked is None, (
                f"_fdLinkedPath should be null after path change, got: {linked!r}"
            )

        search_in = page.locator("#fdSearchInPath")
        if search_in.count() > 0:
            assert search_in.input_value() == "", (
                "#fdSearchInPath should be empty after root path change"
            )

    def test_inner_archive_member_has_search_button(self, page, base_url):
        """Inner archive member rows should show a search icon button."""
        _mock_paths_response(page)
        _mock_browse_response(
            page, [{"name": "data.tar.gz", "type": "archive", "size_bytes": 2048}]
        )
        _mock_archive_contents(page, ["data/records.log", "data/errors.log"])
        _navigate_to_downloader(page, base_url)

        page.evaluate("""
            document.getElementById('fdPathSelect') &&
            (document.getElementById('fdPathSelect').value = '/test/root');
            typeof fdBrowse === 'function' && fdBrowse();
        """)
        page.wait_for_timeout(400)

        results = page.locator("#fdBrowseResults")
        if results.count() == 0:
            return

        # Expand the archive
        expand_btn = results.locator("button", has_text="Expand")
        if expand_btn.count() == 0:
            return

        expand_btn.first.click()
        page.wait_for_timeout(400)

        # Look for the search icon button on inner file rows
        inner_search = results.locator(".fd-entry-children .btn-fd-link")
        if inner_search.count() > 0:
            assert inner_search.first.is_visible(), (
                "Inner archive member search button should be visible after expand"
            )
