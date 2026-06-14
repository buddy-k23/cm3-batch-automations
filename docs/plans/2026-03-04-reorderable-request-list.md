# Reorderable Request List Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let users drag-and-drop requests within a suite to control execution order, with an explicit "Save Order" button to persist the change.

**Architecture:** Pure front-end change in `src/reports/static/ui.html`. Native HTML5 drag-and-drop on `.at-req-row` divs — no library, no build step. A module-level variable `_atDragSrcIdx` tracks which row is being dragged. On drop, the `_atCurrentSuite.requests` array is spliced in-place and the list re-rendered. A `#btnSaveOrder` button appears when the order is dirty; clicking it calls the existing `PUT /api/v1/api-tester/suites/{id}` endpoint with the reordered suite, then hides itself.

**Tech Stack:** Vanilla JS, HTML5 Drag-and-Drop API, Playwright (E2E verification).

---

## Background

Key locations in `src/reports/static/ui.html`:
- **CSS** `.at-req-row` block: lines 398–403
- **HTML** suite runner section: lines 661–673 — `#atRunnerReqList` div at line 671
- **JS** module-level AT variables: line 1203–1207 (`_atSuites`, `_atCurrentSuite`, etc.)
- **JS** `atLoadSuiteIntoRunner()`: lines 1518–1527 (loads suite, calls `atRenderRunnerList`)
- **JS** `atRenderRunnerList(requests, results)`: lines 1529–1564 (builds `.at-req-row` divs)
- **JS** `atSaveRequest()`: lines 1468–1502 (uses `PUT /api/v1/api-tester/suites/{id}` — same pattern for save order)

E2E test: `scripts/e2e_full_ui.py`, `workflow_api_tester()`, lines 305–374. The "E2E UI Suite" is created at line 361 and asserted in the dropdown at line 366.

---

## Task 1 — Drag-and-drop reorder + Save Order button

**Files:**
- Modify: `src/reports/static/ui.html` (CSS ~line 403, HTML ~line 671, JS ~line 1207 and ~line 1529)
- Modify: `scripts/e2e_full_ui.py` (add steps after line 374)

---

### Step 1: Add failing Playwright assertion to `workflow_api_tester`

In `scripts/e2e_full_ui.py`, add five new steps at the end of `workflow_api_tester`, after the existing `assert_suite_in_dropdown` step (after line 374):

```python
    def seed_suite_with_requests():
        dropdown = page.locator("#atRunnerSuiteSel")
        options = dropdown.evaluate(
            "sel => Array.from(sel.options).map(o => ({value: o.value, text: o.textContent}))"
        )
        suite = next((o for o in options if "E2E UI Suite" in o["text"]), None)
        assert suite, f"E2E UI Suite not found in runner dropdown: {options}"
        page.evaluate("""async (suiteId) => {
            var resp = await fetch('/api/v1/api-tester/suites/' + suiteId);
            var suite = await resp.json();
            suite.requests = [
                {id: 'req-e2e-1', name: 'Health', method: 'GET',
                 path: '/api/v1/system/health',
                 headers: [], body_type: 'none', body_json: '',
                 form_fields: [], assertions: []},
                {id: 'req-e2e-2', name: 'Version', method: 'GET',
                 path: '/api/v1/system/version',
                 headers: [], body_type: 'none', body_json: '',
                 form_fields: [], assertions: []},
            ];
            await fetch('/api/v1/api-tester/suites/' + suiteId, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(suite),
            });
        }""", suite["value"])
        page.wait_for_timeout(600)
    step(tag, "seed suite with 2 requests", page, out_dir, seed_suite_with_requests)

    def load_suite_in_runner():
        dropdown = page.locator("#atRunnerSuiteSel")
        options = dropdown.evaluate(
            "sel => Array.from(sel.options).map(o => ({value: o.value, text: o.textContent}))"
        )
        suite = next((o for o in options if "E2E UI Suite" in o["text"]), None)
        assert suite, "E2E UI Suite not found in runner dropdown"
        dropdown.select_option(value=suite["value"])
        page.evaluate("atLoadSuiteIntoRunner()")
        page.wait_for_timeout(800)
    step(tag, "load suite in runner", page, out_dir, load_suite_in_runner)

    def assert_rows_draggable_and_button_hidden():
        rows = page.locator(".at-req-row[draggable='true']")
        assert rows.count() == 2, f"Expected 2 draggable rows, got {rows.count()}"
        hidden = page.locator("#btnSaveOrder").evaluate("el => el.style.display")
        assert hidden == "none", f"Save Order button should start hidden, got display={hidden!r}"
    step(tag, "assert rows draggable and Save Order hidden", page, out_dir,
         assert_rows_draggable_and_button_hidden)

    def drag_row_and_assert_save_visible():
        rows = page.locator(".at-req-row")
        rows.nth(0).drag_to(rows.nth(1))
        page.wait_for_timeout(500)
        pw_expect(page.locator("#btnSaveOrder")).to_be_visible(timeout=3000)
    step(tag, "drag row 0 to row 1 and assert Save Order visible", page, out_dir,
         drag_row_and_assert_save_visible)

    def click_save_order_and_assert_hidden():
        page.locator("#btnSaveOrder").click()
        page.wait_for_timeout(800)
        hidden = page.locator("#btnSaveOrder").evaluate("el => el.style.display")
        assert hidden == "none", f"Save Order button should hide after save, got display={hidden!r}"
    step(tag, "click Save Order and assert button hidden", page, out_dir,
         click_save_order_and_assert_hidden)
```

### Step 2: Run to confirm the steps fail

```bash
cd /Users/buddy/claude-code/automations/valdo
python3 scripts/e2e_full_ui.py 2>&1 | grep -E "PASS|FAIL|draggable|Save Order|seed suite|load suite"
```

Expected: `FAIL` on `assert rows draggable and Save Order hidden` (no `draggable` attribute exists yet).

---

### Step 3: Add CSS for drag-and-drop

In `src/reports/static/ui.html`, find the `.at-runner-summary` block ending at line 413:

```css
  .at-runner-summary {
    background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius);
    font-size: 13px; font-weight: 600; padding: 8px 14px; margin-top: 8px;
  }
```

Add immediately after it (before `</style>`):

```css
  .at-req-row[draggable] { cursor: grab; }
  .at-req-row.drag-over  { border-color: #4a9eff; background: #1a2a3a; }
```

---

### Step 4: Add `#btnSaveOrder` button to the HTML

In `src/reports/static/ui.html`, find the suite runner section (lines 671–672):

```html
      <div class="at-req-list"      id="atRunnerReqList"></div>
      <div class="at-runner-summary" id="atRunnerSummary" style="display:none"></div>
```

Replace with:

```html
      <div class="at-req-list" id="atRunnerReqList"></div>
      <button id="btnSaveOrder" class="btn btn-secondary"
              onclick="atSaveOrder()" style="display:none;margin-bottom:8px">Save Order</button>
      <div class="at-runner-summary" id="atRunnerSummary" style="display:none"></div>
```

---

### Step 5: Add JS — `_atDragSrcIdx` variable, drag logic, `atSaveOrder`, hide on load

**5a.** In `src/reports/static/ui.html`, find the AT module variables block (lines 1203–1207):

```javascript
var _atBodyType     = 'none';
var _atRespData     = null;
var _atSuites       = [];
var _atCurrentSuite = null;
var AT_PROXY_TIMEOUT = 30;
```

Add `_atDragSrcIdx` after `_atCurrentSuite`:

```javascript
var _atBodyType     = 'none';
var _atRespData     = null;
var _atSuites       = [];
var _atCurrentSuite = null;
var _atDragSrcIdx   = -1;
var AT_PROXY_TIMEOUT = 30;
```

**5b.** In `src/reports/static/ui.html`, replace the entire `atRenderRunnerList` function (lines 1529–1564) with this version that adds `draggable` and drag listeners:

```javascript
function atRenderRunnerList(requests, results) {
  var list = document.getElementById('atRunnerReqList');
  list.textContent = '';
  requests.forEach(function(req, idx) {
    var row = document.createElement('div');
    row.className = 'at-req-row';
    row.draggable = true;

    var methodSpan = document.createElement('span');
    methodSpan.className = 'at-req-method';
    methodSpan.textContent = req.method;

    var nameSpan = document.createElement('span');
    nameSpan.className = 'at-req-name';
    nameSpan.textContent = req.name;

    var pathSpan = document.createElement('span');
    pathSpan.className = 'at-req-path';
    pathSpan.textContent = req.path;

    row.appendChild(methodSpan);
    row.appendChild(nameSpan);
    row.appendChild(pathSpan);

    var result = results[idx];
    if (result) {
      result.assertions.forEach(function(a) {
        var span = document.createElement('span');
        span.className = 'at-assertion-result ' + (a.pass ? 'at-assertion-pass' : 'at-assertion-fail');
        span.textContent = (a.pass ? '\u2713 ' : '\u2717 ') + a.field + ' ' + a.operator +
          (a.operator !== 'exists' ? ' ' + a.expected : '');
        row.appendChild(span);
      });
    }

    // Drag-and-drop reorder
    row.addEventListener('dragstart', function() {
      _atDragSrcIdx = idx;
    });
    row.addEventListener('dragover', function(e) {
      e.preventDefault();
      list.querySelectorAll('.at-req-row').forEach(function(r) { r.classList.remove('drag-over'); });
      row.classList.add('drag-over');
    });
    row.addEventListener('dragleave', function() {
      row.classList.remove('drag-over');
    });
    row.addEventListener('drop', function(e) {
      e.preventDefault();
      row.classList.remove('drag-over');
      var destIdx = idx;
      if (_atDragSrcIdx < 0 || _atDragSrcIdx === destIdx) return;
      var moved = _atCurrentSuite.requests.splice(_atDragSrcIdx, 1)[0];
      _atCurrentSuite.requests.splice(destIdx, 0, moved);
      _atDragSrcIdx = -1;
      atRenderRunnerList(_atCurrentSuite.requests, []);
      document.getElementById('btnSaveOrder').style.display = '';
    });
    row.addEventListener('dragend', function() {
      list.querySelectorAll('.at-req-row').forEach(function(r) { r.classList.remove('drag-over'); });
    });

    list.appendChild(row);
  });
}
```

**5c.** Add `atSaveOrder` function immediately after the closing `}` of `atRenderRunnerList`:

```javascript
async function atSaveOrder() {
  if (!_atCurrentSuite) return;
  var btn = document.getElementById('btnSaveOrder');
  try {
    var resp = await fetch('/api/v1/api-tester/suites/' + _atCurrentSuite.id, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(_atCurrentSuite),
    });
    if (!resp.ok) { alert('Could not save order (status ' + resp.status + ').'); return; }
    btn.style.display = 'none';
  } catch (err) {
    alert('Network error: ' + err.message);
  }
}
```

**5d.** In `atLoadSuiteIntoRunner` (lines 1518–1527), add one line to hide `#btnSaveOrder` when a new suite is loaded. Find:

```javascript
async function atLoadSuiteIntoRunner() {
  var suiteId = document.getElementById('atRunnerSuiteSel').value;
  document.getElementById('atRunnerReqList').textContent = '';
  document.getElementById('atRunnerSummary').style.display = 'none';
  _atCurrentSuite = null;
```

Add one line after `_atCurrentSuite = null;`:

```javascript
async function atLoadSuiteIntoRunner() {
  var suiteId = document.getElementById('atRunnerSuiteSel').value;
  document.getElementById('atRunnerReqList').textContent = '';
  document.getElementById('atRunnerSummary').style.display = 'none';
  document.getElementById('btnSaveOrder').style.display = 'none';
  _atCurrentSuite = null;
```

---

### Step 6: Run Playwright to confirm the steps pass

```bash
python3 scripts/e2e_full_ui.py 2>&1 | grep -E "PASS|FAIL|draggable|Save Order|seed suite|load suite"
```

Expected: all five new steps `PASS`.

---

### Step 7: Run full test suites to confirm no regressions

```bash
python3 -m pytest tests/unit/ \
  --ignore=tests/unit/test_contracts_pipeline.py \
  --ignore=tests/unit/test_pipeline_runner.py \
  --ignore=tests/unit/test_workflow_wrapper_parity.py -q 2>&1 | tail -5
```

Expected: 409 passed, 0 failed, ≥80% coverage.

```bash
python3 -m pytest tests/integration/ -q -o addopts='' 2>&1 | tail -5
```

Expected: 28 passed, 0 failed.

---

### Step 8: Commit

```bash
git add src/reports/static/ui.html scripts/e2e_full_ui.py
git commit -m "feat(ui): add drag-and-drop reorder for API Tester request list

Adds draggable='true' to each .at-req-row in atRenderRunnerList.
Drag events reorder _atCurrentSuite.requests in-place and show a
Save Order button. Clicking Save Order calls PUT on the suite to
persist the new order, then hides the button.
Closes #45"
```
