"""EE-S1 UI scaffold smoke checks (no browser).

We assert structurally that:

* ``ui.html`` carries the new tab button + panel.
* ``ui.js`` registers the fetch / submit handlers wired into the tab.
* ``ui.css`` carries the new ``.se-*`` classes used by the panel.

These are intentionally string-match level — exercising the rendered
DOM is an EE-S2 concern that ships alongside the Playwright tree
preview. EE-S1's scaffold is small enough that a string-level guard
keeps the regression cost low while still catching accidental
deletes.
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


def test_html_has_source_editor_tab():
    """The tab button and panel must be present in ``ui.html``."""
    html = _read(_UI_HTML)
    assert "Source Editor" in html, "Tab label missing from ui.html"
    assert 'id="tab-onboarding"' in html, "Tab button id missing"
    assert 'id="panel-onboarding"' in html, "Tab panel id missing"
    # Drag-and-drop region + preview-result block.
    assert 'id="seDropZone"' in html, "Source Editor drop zone missing"
    assert 'id="sePreviewResult"' in html, "Preview result block missing"


def test_js_has_onboarding_fetch_handler():
    """ui.js must wire the new tab activation handler + fetch calls."""
    js = _read(_UI_JS)
    # Tab activation triggers a load of the committed source list.
    assert "loadOnboardingSources" in js, "loadOnboardingSources not exposed"
    assert "/api/v2/onboarding/sources" in js, "Sources fetch URL missing"
    # Preview form posts to the preview endpoint.
    assert "submitOnboardingPreview" in js, "submitOnboardingPreview not exposed"
    assert "/api/v2/onboarding/preview" in js, "Preview POST URL missing"
    # switchTab is taught about the new tab name.
    assert "'onboarding'" in js, "switchTab() not updated for onboarding tab"


def test_css_has_source_editor_styles():
    """ui.css must carry the new .se-* style block."""
    css = _read(_UI_CSS)
    assert ".se-layout" in css, ".se-layout style missing"
    assert ".se-panel" in css, ".se-panel style missing"
    assert ".se-sources-table" in css, ".se-sources-table style missing"
    assert ".se-preview-json" in css, ".se-preview-json style missing"


def test_html_panel_appears_after_existing_tabs():
    """The new tab is appended after File Downloader (not inserted mid-nav)."""
    html = _read(_UI_HTML)
    downloader_idx = html.find('id="tab-downloader"')
    onboarding_idx = html.find('id="tab-onboarding"')
    assert downloader_idx != -1 and onboarding_idx != -1
    assert onboarding_idx > downloader_idx, (
        "Source Editor tab should appear AFTER File Downloader in the nav"
    )
