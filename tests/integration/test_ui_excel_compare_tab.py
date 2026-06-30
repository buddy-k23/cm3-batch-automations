"""S24-5 Excel Compare tab UI-presence smoke checks (no browser).

These mirror the Source Editor / DB Compare structural guards: we assert at the
string level that the Excel Compare tab is wired across the three split static
assets (``ui.html`` / ``ui.js`` / ``ui.css``). Full click-through is an E2E
(Playwright) concern under ``tests/e2e/`` and requires a running server; these
string-match guards keep regression cost low while catching accidental deletes.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_UI_HTML = _REPO_ROOT / "src" / "reports" / "static" / "ui.html"
_UI_JS = _REPO_ROOT / "src" / "reports" / "static" / "ui.js"
_UI_CSS = _REPO_ROOT / "src" / "reports" / "static" / "ui.css"


def _read(path: Path) -> str:
    """Return the file contents as a UTF-8 string."""
    assert path.is_file(), f"Expected static asset at {path}"
    return path.read_text(encoding="utf-8")


def test_html_has_excel_compare_tab():
    """The tab button + panel must be present in ``ui.html`` with distinct ids."""
    html = _read(_UI_HTML)
    assert "Excel Compare" in html, "Tab label missing from ui.html"
    assert 'id="tab-excelcompare"' in html, "Tab button id missing"
    assert 'id="panel-excelcompare"' in html, "Tab panel id missing"
    assert 'onclick="switchTab(\'excelcompare\')"' in html, (
        "Tab button must call switchTab('excelcompare')"
    )


def test_html_has_excel_compare_key_element_ids():
    """The panel must carry the distinct, non-ambiguous element ids the JS wires."""
    html = _read(_UI_HTML)
    # Distinct compare button id (E2E selector rule — never a generic text match).
    assert 'id="excelCompareBtn"' in html, "excelCompareBtn id missing"
    # File upload + Excel-specific inputs.
    assert 'id="xlcFileInput"' in html, "Excel file input id missing"
    assert 'id="xlcDropZone"' in html, "Excel drop zone id missing"
    assert 'id="xlcSqlEditor"' in html, "Query/table input id missing"
    assert 'id="xlcKeyColumns"' in html, "Key columns input id missing"
    assert 'id="xlcSheet"' in html, "Sheet input id missing"
    assert 'id="xlcHeaderRow"' in html, "Header row input id missing"
    assert 'id="xlcSwapBtn"' in html, "Direction swap button id missing"
    # Connection inputs mirrored from DB Compare.
    assert 'id="xlcHost"' in html, "DB host input id missing"
    assert 'id="xlcUser"' in html, "DB user input id missing"
    assert 'id="xlcPassword"' in html, "DB password input id missing"
    assert 'id="xlcSchema"' in html, "DB schema input id missing"
    assert 'id="xlcConnectionSelect"' in html, "Named connection select id missing"
    assert 'id="xlcProfileSelect"' in html, "Profile select id missing"
    # Results region + report link row + diff download.
    assert 'id="xlcResults"' in html, "Results region id missing"
    assert 'id="xlcMetrics"' in html, "Metrics container id missing"
    assert 'id="xlcReportRow"' in html, "HTML report link row id missing"
    assert 'id="xlcDownloadDiffBtn"' in html, "Diff CSV download button id missing"


def test_html_excel_panel_appears_after_db_compare():
    """The Excel Compare tab is inserted right after DB Compare in the nav."""
    html = _read(_UI_HTML)
    dbcompare_idx = html.find('id="tab-dbcompare"')
    excel_idx = html.find('id="tab-excelcompare"')
    downloader_idx = html.find('id="tab-downloader"')
    assert dbcompare_idx != -1 and excel_idx != -1 and downloader_idx != -1
    assert dbcompare_idx < excel_idx < downloader_idx, (
        "Excel Compare tab should appear AFTER DB Compare and BEFORE File Downloader"
    )


def test_html_direction_labels_are_user_friendly():
    """The direction control must spell out which side is the source of truth."""
    html = _read(_UI_HTML)
    assert "DB is source of truth" in html, (
        "User-facing 'DB is source of truth' direction label missing"
    )


def test_js_has_excel_compare_handler_and_endpoint():
    """ui.js must wire the Excel Compare handler and POST to the S24-3 endpoint."""
    js = _read(_UI_JS)
    assert "/api/v1/files/excel-compare" in js, "Excel compare POST URL missing"
    # switchTab is taught about the new tab name.
    assert "'excelcompare'" in js, "switchTab() not updated for excelcompare tab"
    # Core handlers / helpers.
    assert "_xlcShowResults" in js, "_xlcShowResults renderer missing"
    assert "_xlcBuildDiffCsv" in js, "_xlcBuildDiffCsv helper missing"
    assert "_xlcUpdateDirection" in js, "_xlcUpdateDirection helper missing"
    assert "loadExcelDbConnections" in js, "loadExcelDbConnections helper missing"


def test_js_excel_compare_diff_csv_handles_dict_field_statistics():
    """S24-5 fix: the diff-CSV download must not assume field_statistics is a list.

    The excel-compare response (ExcelCompareResult / run_compare_service) returns
    ``field_statistics`` as a summary DICT (field_difference_counts /
    field_difference_types), so the old ``data.field_statistics.length > 0``
    gate silently no-op'd. The handler must normalize the dict shape (via the
    field_difference_counts keys) rather than testing ``.length`` on an object.
    """
    js = _read(_UI_JS)
    # The list-assuming gate must be gone.
    assert "data.field_statistics.length" not in js, (
        "Excel/DB compare must not test .length on the field_statistics object"
    )
    # The gate must go through the normalizing guard helper.
    assert "_xlcHasFieldStats(data.field_statistics)" in js, (
        "Excel compare download gate must use _xlcHasFieldStats(...) guard"
    )
    # The CSV builder must read the dict-shaped summary fields.
    assert "field_difference_counts" in js, (
        "Diff CSV must be built from the field_difference_counts dict"
    )
    assert "_xlcNormalizeFieldStats" in js, (
        "_xlcNormalizeFieldStats helper (dict/list normalizer) missing"
    )


def test_js_excel_compare_uses_apifetch_not_raw_fetch():
    """The Excel compare POST must go through apiFetch() (#427), not raw fetch()."""
    js = _read(_UI_JS)
    # The endpoint must be reached via apiFetch(...).
    assert "apiFetch('/api/v1/files/excel-compare'" in js, (
        "Excel compare must call apiFetch('/api/v1/files/excel-compare', ...)"
    )
    # Guard against a raw fetch() to the same endpoint sneaking in.
    assert "fetch('/api/v1/files/excel-compare'" not in js, (
        "Excel compare must not use raw fetch() — use apiFetch()"
    )


def test_js_excel_compare_password_never_persisted():
    """Password must be excluded from sessionStorage; only non-secret fields stored."""
    js = _read(_UI_JS)
    # The sessionStorage key set for Excel Compare must NOT include the password id.
    key_list = "['xlcHost', 'xlcUser', 'xlcSchema', 'xlcAdapter']"
    assert key_list in js, "Excel Compare sessionStorage key list changed unexpectedly"
    assert "'xlcPassword'" not in key_list, (
        "xlcPassword must never be persisted to sessionStorage"
    )
    # sessionStorage writes must only ever use the valdo-xlc- key namespace.
    assert "sessionStorage.setItem('valdo-xlc-'" in js, (
        "Excel Compare must persist non-secret fields under the valdo-xlc- namespace"
    )
    # Explicit comment marker documenting the posture.
    assert "Password (xlcPassword) is intentionally excluded" in js, (
        "Password-exclusion intent comment missing"
    )


def test_js_excel_compare_posts_direction_field():
    """The handler must send the direction field (db-source / excel-source)."""
    js = _read(_UI_JS)
    assert "fd.append('direction'" in js, "direction form field not sent"
    assert "'db-source'" in js and "'excel-source'" in js, (
        "Both direction values must be present in ui.js"
    )


def test_css_has_excel_report_row_style():
    """ui.css must carry the .dbc-report-row style used by the report link row."""
    css = _read(_UI_CSS)
    assert ".dbc-report-row" in css, ".dbc-report-row style missing"
