"""End-to-end Playwright test for the Excel Compare tab (S24-5, Sprint 24).

Mirrors ``tests/e2e/test_e2e_db_compare.py`` but drives a *real round-trip*:
uploads an ``.xlsx`` workbook, points the tab at a seeded SQLite database, runs
the comparison via ``POST /api/v1/files/excel-compare``, and asserts the
results panel (``#xlcResults`` / ``#xlcMetrics``) renders the expected
matching / difference counts.

Requirements (provided by the harness, not by this file):
  * A live Valdo server reachable at ``APP_E2E_BASE_URL`` with the UI enabled.
  * The server must be started with ``DB_ADAPTER=sqlite`` and ``API_KEYS`` set,
    pointed at a SQLite database that contains an ``ACCOUNTS(ACCT_NUM, AMOUNT,
    STATUS)`` table seeded with the rows this test expects to match against.
  * The SQLite database path must be supplied to this test via the
    ``APP_E2E_SQLITE_DB`` env var (the tab sends it as ``db_host`` and the
    server's ``_build_adapter`` treats the host slot as the SQLite path).
  * The browser-side API key must be supplied via ``APP_E2E_API_KEY`` (one of
    the keys configured in the server's ``API_KEYS``). The static UI never sets
    ``window._apiKey`` itself, so the test injects it before issuing requests —
    this is harness wiring, not a product change.

Selector rule (per CLAUDE.md): every interaction targets a distinct ``#id``,
never a generic text selector.
"""
from __future__ import annotations

import os

import openpyxl
import pytest
from playwright.sync_api import Page, expect

# SQLite DB path the seeded server is using. The Excel Compare tab sends this as
# db_host; the server's sqlite adapter treats the host slot as the DB path.
SQLITE_DB = os.getenv("APP_E2E_SQLITE_DB")

# API key matching one configured in the server's API_KEYS env. Injected into
# window._apiKey so apiFetch() attaches X-API-Key (the static UI never sets it).
API_KEY = os.getenv("APP_E2E_API_KEY", "dev-key")

# Skip cleanly (rather than fail) when the harness did not provide the seeded
# SQLite path — this test is meaningless without the matching DB side.
pytestmark = pytest.mark.skipif(
    not SQLITE_DB,
    reason="APP_E2E_SQLITE_DB not set; seeded SQLite DB required for the round-trip",
)


@pytest.fixture
def accounts_xlsx(tmp_path):
    """Build an ``.xlsx`` whose rows line up with the seeded ACCOUNTS table.

    Designed against the seeded rows::

        1001 | 100.00 | ACTIVE
        1002 | 250.50 | ACTIVE
        1003 | 999.99 | CLOSED
        1004 |  42.00 | ACTIVE

    The workbook reproduces three exact matches (1001, 1002, 1004), one
    deliberate value mismatch on AMOUNT (1003: 111.11 vs DB 999.99), and one
    row that exists only in Excel (9999) — yielding a predictable
    ``matching=3``, ``differences=1``, ``only-in-actual=1`` result.

    Returns:
        Path to the written ``accounts.xlsx`` under ``tmp_path``.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["ACCT_NUM", "AMOUNT", "STATUS"])
    ws.append([1001, "100.00", "ACTIVE"])
    ws.append([1002, "250.50", "ACTIVE"])
    ws.append([1003, "111.11", "CLOSED"])  # deliberate mismatch vs DB 999.99
    ws.append([1004, "42.00", "ACTIVE"])
    ws.append([9999, "5.00", "ACTIVE"])    # only in Excel
    out = tmp_path / "accounts.xlsx"
    wb.save(out)
    return out


class TestExcelCompareTabPresence:
    """Tab and panel wiring — mirrors the DB Compare presence tests."""

    def test_excel_compare_tab_visible(self, ui_page: Page) -> None:
        """Excel Compare tab button is visible in the tab bar."""
        expect(ui_page.locator("#tab-excelcompare")).to_be_visible()

    def test_panel_hidden_initially(self, ui_page: Page) -> None:
        """Excel Compare panel is hidden when another tab is active."""
        expect(ui_page.locator("#panel-excelcompare")).to_be_hidden()

    def test_clicking_tab_shows_panel(self, ui_page: Page) -> None:
        """Clicking the Excel Compare tab reveals its panel."""
        ui_page.click("#tab-excelcompare")
        expect(ui_page.locator("#panel-excelcompare")).to_be_visible()

    def test_run_button_disabled_initially(self, ui_page: Page) -> None:
        """Run button is disabled before a file + query + connection exist."""
        ui_page.click("#tab-excelcompare")
        expect(ui_page.locator("#excelCompareBtn")).to_be_disabled()

    def test_results_hidden_on_load(self, ui_page: Page) -> None:
        """Results panel is hidden before any run."""
        ui_page.click("#tab-excelcompare")
        expect(ui_page.locator("#xlcResults")).to_be_hidden()


class TestExcelCompareRoundTrip:
    """Full upload → sqlite compare → results render round-trip."""

    def _arrange_run(self, page: Page, xlsx_path) -> None:
        """Open the tab, inject the API key, fill the form, upload the workbook.

        Leaves the form ready with the Run button enabled. Does NOT click Run.
        """
        # Inject the API key so apiFetch() attaches X-API-Key. The static UI
        # never sets window._apiKey; this is harness wiring, not a product edit.
        page.evaluate("(k) => { window._apiKey = k; }", API_KEY)

        page.click("#tab-excelcompare")
        expect(page.locator("#panel-excelcompare")).to_be_visible()

        # Expand the connection chip so the manual connection fields are usable.
        page.click("#xlcConnChip")
        expect(page.locator("#xlcConnForm")).to_be_visible()

        # SQLite: adapter=sqlite, and the DB path goes in the Host/DSN slot
        # (#xlcHost). The Run handler reads #xlcAdapter for db_adapter.
        page.select_option("#xlcAdapter", "sqlite")
        page.fill("#xlcHost", SQLITE_DB)

        # Query / table + key column for row matching.
        page.fill("#xlcSqlEditor", "ACCOUNTS")
        page.fill("#xlcKeyColumns", "ACCT_NUM")

        # Upload the workbook to the hidden file input (#xlcFileInput).
        page.set_input_files("#xlcFileInput", str(xlsx_path))

        # With file + query + host present, the Run button must enable.
        expect(page.locator("#excelCompareBtn")).to_be_enabled()

    def test_round_trip_renders_expected_metrics(
        self, ui_page: Page, accounts_xlsx
    ) -> None:
        """Run the compare and assert the metric cards show the expected counts.

        Against the seeded DB the workbook yields 3 matches, 1 AMOUNT
        difference (acct 1003), and 1 Excel-only row (acct 9999).
        """
        self._arrange_run(ui_page, accounts_xlsx)

        ui_page.click("#excelCompareBtn")

        # The results panel becomes visible once the response renders.
        expect(ui_page.locator("#xlcResults")).to_be_visible(timeout=30000)

        metrics = ui_page.locator("#xlcMetrics")
        expect(metrics).to_be_visible()

        # Metric cards render label + value pairs. Assert the load-bearing
        # numbers by reading the rendered metric text. Each card is a
        # ".dbc-metric-card" with a ".dbc-metric-value" and ".dbc-metric-label".
        def metric_value(label: str) -> str:
            card = ui_page.locator(
                ".dbc-metric-card",
                has=ui_page.locator(".dbc-metric-label", has_text=label),
            ).first
            return card.locator(".dbc-metric-value").inner_text().strip()

        assert metric_value("DB Rows") == "4", "expected 4 DB rows extracted"
        assert metric_value("Excel Rows") == "5", "expected 5 Excel rows read"
        assert metric_value("Matching") == "3", "expected 3 matching rows"
        assert metric_value("Differences") == "1", "expected 1 differing row"
        # Direction is db-source: only_in_file2 == only-in-Excel (the actual).
        assert metric_value("Only in Actual") == "1", (
            "expected 1 row only in Excel (acct 9999)"
        )

    def test_status_banner_reports_differences(
        self, ui_page: Page, accounts_xlsx
    ) -> None:
        """With diffs present, the status banner shows the 'failed' compare text.

        ``workflow_status`` is ``failed`` whenever any difference exists, so the
        banner uses the failure copy. This asserts the deliberate mismatch is
        surfaced to the user rather than silently passing.
        """
        self._arrange_run(ui_page, accounts_xlsx)
        ui_page.click("#excelCompareBtn")

        banner = ui_page.locator("#xlcStatusBanner")
        expect(banner).to_be_visible(timeout=30000)
        expect(banner).to_contain_text("Compare failed")
