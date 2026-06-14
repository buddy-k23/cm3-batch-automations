# Inline Report Panel Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Show validation and comparison HTML reports inline in the Quick Test panel via an `<iframe>` with a close button, eliminating the need to open a new tab.

**Architecture:** Pure front-end change in `src/reports/static/ui.html`. No backend changes needed — the API already returns `report_url`. Add a hidden `#reportPanel` div below `#statusMsg`; show it by setting the iframe `src` after validate/compare succeeds. The existing "Open report" link in the status bar is kept as-is.

**Tech Stack:** Vanilla JS, CSS, HTML — no build step.

---

## Context

**Quick Test panel structure** (`src/reports/static/ui.html:419-467`):
```
#panel-quick
  .drop-zone / #fileInput
  .form-row (#mappingSelect)
  .btn-row (#btnValidate, #btnToggleCompare)
  #compare-section (hidden by default)
    .drop-zone / #fileInput2
    .btn-row (#btnCompare)
  #statusMsg        ← status bar lives here
                    ← NEW: #reportPanel goes here
```

**Validate handler** (`lines 821-852`): calls `setStatusWithLink(msg, type, 'Open report', data.report_url)` when `data.report_url` is present.

**Compare handler** (`lines 857-894`): same pattern.

**`setStatusWithLink`** (`lines 687-701`): renders status text + `<a target="_blank">` link in `#statusMsg`.

**Key CSS** (`lines 132-210`): `.status-msg`, `a.report-link`, `#compare-section`.

---

## Task 1: Add inline report panel to Quick Test

**Files:**
- Modify: `src/reports/static/ui.html` (CSS ~line 210, HTML ~line 466, JS ~line 701)
- Modify: `scripts/e2e_full_ui.py` (Workflow 1 — add assertion step)

---

### Step 1: Add a failing Playwright assertion to e2e_full_ui.py

In `workflow_quick_test` in `scripts/e2e_full_ui.py`, after the existing
`"assert valid result (customers)"` step, add a step that checks the inline
report panel is visible. This will fail until the HTML change is made.

Find this block in `workflow_quick_test` (around line 143-148):
```python
    def assert_valid_result():
        page.wait_for_timeout(2000)
        content = page.content().lower()
        assert any(kw in content for kw in ["valid", "row", "record", "pass"]), \
            "No valid result visible after customers.txt validate"
    step(tag, "assert valid result (customers)", page, out_dir, assert_valid_result)
```

Add immediately after it:
```python
    def assert_inline_report_visible():
        panel = page.locator("#reportPanel")
        pw_expect(panel).to_be_visible(timeout=5000)
        frame_src = page.locator("#reportFrame").get_attribute("src") or ""
        assert frame_src.startswith("/uploads/"), \
            f"Expected iframe src to start with /uploads/, got {frame_src!r}"
    step(tag, "assert inline report visible (customers)", page, out_dir,
         assert_inline_report_visible)
```

### Step 2: Run to confirm the step fails

```bash
cd /Users/buddy/claude-code/automations/valdo
python3 scripts/e2e_full_ui.py 2>&1 | grep -E "PASS|FAIL|inline report"
```

Expected: `[UI]   quick-test — assert inline report visible (customers)    FAIL`

---

### Step 3: Add CSS for the report panel

In `src/reports/static/ui.html`, find the `a.report-link` block (~line 209):
```css
  a.report-link { color: #4a9eff; text-decoration: none; font-size: 12px; }
  a.report-link:hover { text-decoration: underline; }
```

Add immediately after it:
```css
  #reportPanel { display: none; margin-top: 14px; }
  #reportPanel.visible { display: block; }
  .report-panel-bar {
    display: flex; align-items: center; gap: 10px;
    font-size: 12px; color: var(--muted);
  }
  .report-panel-bar span { flex: 1; }
  #btnCloseReport {
    background: none; border: none; color: var(--muted);
    font-size: 16px; cursor: pointer; padding: 0 4px; line-height: 1;
  }
  #btnCloseReport:hover { color: var(--text); }
  #reportFrame {
    width: 100%; height: 520px; margin-top: 8px;
    border: 1px solid #2a2a3a; border-radius: 4px;
    background: #fff;
  }
```

---

### Step 4: Add HTML for the report panel

In `src/reports/static/ui.html`, find the statusMsg div (~line 466):
```html
    <div class="status-msg info" id="statusMsg">Select a file and mapping to get started.</div>
  </div>
```

Add the `#reportPanel` div between `#statusMsg` and the closing `</div>`:
```html
    <div class="status-msg info" id="statusMsg">Select a file and mapping to get started.</div>

    <div id="reportPanel">
      <div class="report-panel-bar">
        <span>Inline Report</span>
        <a id="reportOpenLink" href="#" target="_blank" rel="noopener noreferrer"
           class="report-link">Open in new tab ↗</a>
        <button id="btnCloseReport" aria-label="Close report">×</button>
      </div>
      <iframe id="reportFrame" src="" title="Validation / Comparison Report"></iframe>
    </div>
  </div>
```

---

### Step 5: Add showInlineReport / hideInlineReport JS helpers

In `src/reports/static/ui.html`, find `setStatusWithLink` (~line 687). Add
these two functions immediately after the closing `}` of `setStatusWithLink`
(before `setLoading`):

```javascript
function showInlineReport(url) {
  document.getElementById('reportFrame').src = url;
  document.getElementById('reportOpenLink').href = url;
  document.getElementById('reportPanel').classList.add('visible');
}

function hideInlineReport() {
  document.getElementById('reportPanel').classList.remove('visible');
  document.getElementById('reportFrame').src = '';
}
```

Also wire up the close button — add this after `setupDrop` calls (~line 780):
```javascript
document.getElementById('btnCloseReport').addEventListener('click', hideInlineReport);
```

And clear the report when loading starts — update `setLoading` to call
`hideInlineReport()` at the top:
```javascript
function setLoading(msg) {
  hideInlineReport();                        // ← add this line
  var el = document.getElementById('statusMsg');
  ...
```

---

### Step 6: Call showInlineReport from validate and compare handlers

**Validate handler** — find (~line 842):
```javascript
    if (data.report_url) {
      setStatusWithLink(msg, data.valid ? 'success' : 'error', 'Open report', data.report_url);
    } else {
```

Replace with:
```javascript
    if (data.report_url) {
      setStatusWithLink(msg, data.valid ? 'success' : 'error', 'Open report', data.report_url);
      showInlineReport(data.report_url);
    } else {
```

**Compare handler** — find (~line 884):
```javascript
    if (data.report_url) {
      setStatusWithLink(msg, 'success', 'Open report', data.report_url);
    } else {
```

Replace with:
```javascript
    if (data.report_url) {
      setStatusWithLink(msg, 'success', 'Open report', data.report_url);
      showInlineReport(data.report_url);
    } else {
```

---

### Step 7: Run Playwright step to confirm it passes

```bash
python3 scripts/e2e_full_ui.py 2>&1 | grep -E "PASS|FAIL|inline report"
```

Expected: `[UI]   quick-test — assert inline report visible (customers)    PASS`

---

### Step 8: Run full unit + integration test suite

```bash
python3 -m pytest tests/unit/ \
  --ignore=tests/unit/test_contracts_pipeline.py \
  --ignore=tests/unit/test_pipeline_runner.py \
  --ignore=tests/unit/test_workflow_wrapper_parity.py -q
```

Expected: 389 passed, 0 failed

```bash
python3 -m pytest tests/integration/ -q -o addopts=''
```

Expected: 28 passed, 0 failed

---

### Step 9: Commit

```bash
git add src/reports/static/ui.html scripts/e2e_full_ui.py
git commit -m "feat(ui): show validation/comparison reports inline via iframe in Quick Test panel

Adds #reportPanel below #statusMsg in the Quick Test tab. After a
successful validate or compare call that returns report_url, the report
is loaded into an <iframe> without leaving the page. A close button
dismisses the panel. The existing 'Open report' external link is kept.
Closes #40"
```
