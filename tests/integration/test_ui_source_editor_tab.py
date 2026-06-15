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


# ---------------------------------------------------------------------------
# EE-S2: tree view + artefact viewer + diff
# ---------------------------------------------------------------------------


def test_html_has_tree_structure():
    """EE-S2: the panel must carry the new ``onboarding-tree`` container
    plus the artefact-content modal."""
    html = _read(_UI_HTML)
    assert 'id="sePreviewTree"' in html, "Tree container missing"
    assert 'class="onboarding-tree"' in html, "onboarding-tree class missing"
    assert 'id="seArtefactModal"' in html, "Artefact modal missing"
    assert 'id="seArtefactModalBody"' in html, "Modal body missing"
    assert 'id="seArtefactModalTitle"' in html, "Modal title missing"


def test_html_intro_documents_drift_badges():
    """The intro paragraph must mention the three drift statuses so the
    BA understands the colour key before clicking anything."""
    html = _read(_UI_HTML)
    intro_idx = html.find('class="se-intro"')
    assert intro_idx != -1
    intro_block = html[intro_idx:intro_idx + 800]
    for status_token in ("new", "changed", "unchanged"):
        assert status_token in intro_block, (
            f"Intro paragraph missing the {status_token!r} status badge "
            f"reference"
        )


def test_js_has_tree_render_handler():
    """ui.js must expose the EE-S2 tree-render + drift-status helpers."""
    js = _read(_UI_JS)
    assert "renderOnboardingTree" in js, "renderOnboardingTree missing"
    assert "viewArtefactContent" in js, "viewArtefactContent missing"
    assert "closeArtefactModal" in js, "closeArtefactModal missing"
    assert "/api/v2/onboarding/committed-artefact" in js, (
        "Committed-artefact fetch URL missing"
    )
    # Kind tokens consumed by the section-rendering pass.
    for kind in (
        "source_yaml",
        "mapping_json",
        "rules_json",
        "reconciliation_yaml",
        "sql",
    ):
        assert "'" + kind + "'" in js, f"Kind token {kind!r} not present in JS"


def test_js_has_diff_handler():
    """ui.js must implement a unified-diff renderer (renderUnifiedDiff)."""
    js = _read(_UI_JS)
    assert "renderUnifiedDiff" in js, "renderUnifiedDiff function missing"
    # Diff CSS markers — verifies the diff classes the renderer emits.
    assert "diff-line" in js, "diff-line CSS class not emitted by JS"


def test_css_has_tree_and_badge_styles():
    """ui.css must carry the new tree / badge / diff style blocks."""
    css = _read(_UI_CSS)
    assert ".onboarding-tree" in css, ".onboarding-tree style missing"
    assert ".onboarding-section" in css, ".onboarding-section style missing"
    assert ".se-badge-new" in css, ".se-badge-new style missing"
    assert ".se-badge-changed" in css, ".se-badge-changed style missing"
    assert ".se-badge-unchanged" in css, ".se-badge-unchanged style missing"
    assert ".se-modal" in css, ".se-modal style missing"
    assert ".se-diff" in css, ".se-diff style missing"
