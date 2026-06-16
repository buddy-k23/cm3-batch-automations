"""E2E Playwright tests for the #407 Reconcile-mapping-vs-table panel.

The reconcile panel lives in the DB Compare tab (``#panel-dbcompare``). These
tests use stable element ids only (never ``button:has-text(...)``) per the
CLAUDE.md E2E selector rule.

Live-DB note (stated per the #407 deliverable): wiring a SQLite fixture DB
into the shared e2e server (booted externally at ``APP_E2E_BASE_URL`` with
the demo's DB adapter) is heavyweight and out of scope for this harness. So
the click-through test intercepts ``POST /api/v2/reconcile`` with
``page.route`` and serves a representative verdict, then asserts the panel
renders that verdict into ``#reconcileResult``. This proves the panel POSTs to
the endpoint and renders the field-level verdict (banner + counts + the
mismatch / advisory lists). The genuine SQLite reconcile path is covered live
by ``tests/integration/test_reconcile_service_api.py`` and
``tests/integration/test_mcp_reconcile_tool.py``.
"""

from __future__ import annotations

import json

from playwright.sync_api import Page, expect


_MOCK_VERDICT = {
    "status": "mismatch",
    "valid": True,
    "mapping_name": "reconcile_fixture",
    "table": "CUSTOMER",
    "schema": None,
    "db_adapter": "SQLiteAdapter",
    "summary": {
        "mapped_columns": 4,
        "database_columns": 4,
        "error_count": 0,
        "warning_count": 2,
        "advisory_count": 1,
        "mismatch_count": 1,
    },
    "errors": [],
    "mismatches": [
        "Type mismatch for AGE: mapping expects 'integer', database has 'VARCHAR'"
    ],
    "advisories": [
        "Column IS_ACTIVE: declared 'boolean' stored as 'INTEGER' — no native "
        "boolean on this backend (advisory, not an error)"
    ],
    "warnings": [],
    "unmapped_required": [],
}


class TestReconcilePanelPresence:
    """The reconcile panel + its stable ids exist in the DB Compare tab."""

    def test_reconcile_section_visible_on_dbcompare(self, ui_page: Page) -> None:
        """The reconcile section shows when the DB Compare tab is active."""
        ui_page.click("#tab-dbcompare")
        expect(ui_page.locator("#reconcileSection")).to_be_visible()

    def test_reconcile_inputs_present(self, ui_page: Page) -> None:
        """Mapping select, table + schema inputs, and the button exist."""
        ui_page.click("#tab-dbcompare")
        expect(ui_page.locator("#reconcileMappingSelect")).to_be_visible()
        expect(ui_page.locator("#reconcileTable")).to_be_visible()
        expect(ui_page.locator("#reconcileSchema")).to_be_visible()
        expect(ui_page.locator("#btnReconcile")).to_be_visible()

    def test_result_area_hidden_initially(self, ui_page: Page) -> None:
        """The result area is hidden before any reconcile runs."""
        ui_page.click("#tab-dbcompare")
        expect(ui_page.locator("#reconcileResult")).to_be_hidden()


class TestReconcileClickThrough:
    """Clicking Reconcile POSTs and renders the verdict (mocked backend)."""

    def test_reconcile_posts_and_renders_verdict(self, ui_page: Page) -> None:
        """Filling the panel and clicking #btnReconcile renders the verdict.

        The backend call is intercepted so the test does not require a live
        DB on the e2e server (see module docstring). The assertion proves the
        POST happened and the field-level verdict is rendered.
        """
        posted = {"hit": False}

        def _handle(route, request):
            posted["hit"] = True
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(_MOCK_VERDICT),
            )

        ui_page.route("**/api/v2/reconcile", _handle)

        ui_page.click("#tab-dbcompare")

        # Pick the first real mapping option (skip the placeholder at index 0).
        options = ui_page.locator("#reconcileMappingSelect option")
        if options.count() > 1:
            value = options.nth(1).get_attribute("value")
            ui_page.select_option("#reconcileMappingSelect", value)
        else:
            # No mappings configured on the e2e server — set the select's
            # value directly so the client-side guard passes and the POST
            # still fires (the backend is mocked anyway).
            ui_page.evaluate(
                "() => { const s = document.getElementById('reconcileMappingSelect');"
                " const o = document.createElement('option'); o.value = 'reconcile_fixture';"
                " o.textContent = 'reconcile_fixture'; s.appendChild(o); s.value = 'reconcile_fixture'; }"
            )

        ui_page.fill("#reconcileTable", "CUSTOMER")
        ui_page.click("#btnReconcile")

        # The verdict banner + the mismatch list must render.
        expect(ui_page.locator("#reconcileResult")).to_be_visible()
        expect(ui_page.locator("#reconcileResult .rec-verdict-mismatch")).to_be_visible()
        expect(ui_page.locator("#reconcileResult")).to_contain_text("AGE")
        expect(ui_page.locator("#reconcileResult")).to_contain_text("IS_ACTIVE")
        assert posted["hit"], "POST /api/v2/reconcile was not called"
