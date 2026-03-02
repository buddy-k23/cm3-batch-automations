# Mapping Generator UI Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a "Mapping Generator" tab to the web UI so users can upload Excel/CSV templates and generate mapping JSON and validation rules JSON without using the CLI.

**Architecture:** One new backend router (`src/api/routers/rules.py`) exposes `POST /api/v1/rules/upload`, registered in `src/api/main.py`. The frontend (`src/reports/static/ui.html`) gets a third tab with two independent drop-zone sections — one calling the existing mappings upload endpoint, one calling the new rules endpoint.

**Tech Stack:** FastAPI, Python 3.9+, vanilla JS (no framework), existing `BARulesTemplateConverter` / `RulesTemplateConverter`

---

## Task 1: New rules upload API endpoint + tests

**Files:**
- Create: `src/api/routers/rules.py`
- Create: `tests/unit/test_api_rules_upload.py`
- Modify: `src/api/main.py`

---

### Step 1: Write the failing tests

Create `tests/unit/test_api_rules_upload.py`:

```python
"""Tests for POST /api/v1/rules/upload endpoint."""

import json
from io import BytesIO
from fastapi.testclient import TestClient
from src.api.main import app

client = TestClient(app)


def test_upload_rules_rejects_invalid_extension():
    files = {"file": ("bad.txt", b"x", "text/plain")}
    r = client.post("/api/v1/rules/upload", files=files)
    assert r.status_code == 400


def test_upload_rules_ba_friendly_csv_success(monkeypatch, tmp_path):
    class DummyConverter:
        def from_csv(self, path):
            return {"metadata": {"name": "test_rules"}, "rules": []}
        def save(self, output_path):
            import os; os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "w") as f:
                json.dump({"metadata": {"name": "test_rules"}, "rules": []}, f)

    monkeypatch.setattr("src.api.routers.rules.BARulesTemplateConverter", DummyConverter)

    files = {"file": ("rules.csv", b"Rule ID,Rule Name\n", "text/csv")}
    r = client.post("/api/v1/rules/upload?rules_name=test_rules&rules_type=ba_friendly", files=files)
    assert r.status_code == 200
    payload = r.json()
    assert payload["rules_id"] == "test_rules"
    assert "created" in payload["message"].lower()

    # cleanup
    import os
    p = "config/rules/test_rules.json"
    if os.path.exists(p): os.remove(p)


def test_upload_rules_technical_xlsx_success(monkeypatch):
    class DummyConverter:
        def from_excel(self, path):
            return {"metadata": {"name": "tech_rules"}, "rules": []}
        def save(self, output_path):
            import os; os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "w") as f:
                json.dump({"metadata": {"name": "tech_rules"}, "rules": []}, f)

    monkeypatch.setattr("src.api.routers.rules.RulesTemplateConverter", DummyConverter)

    files = {"file": ("rules.xlsx", b"PK", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    r = client.post("/api/v1/rules/upload?rules_name=tech_rules&rules_type=technical", files=files)
    assert r.status_code == 200
    payload = r.json()
    assert payload["rules_id"] == "tech_rules"

    # cleanup
    import os
    p = "config/rules/tech_rules.json"
    if os.path.exists(p): os.remove(p)


def test_upload_rules_defaults_to_ba_friendly(monkeypatch):
    """Omitting rules_type should use ba_friendly."""
    called_with = {}

    class DummyConverter:
        def from_csv(self, path):
            called_with['converter'] = 'ba_friendly'
            return {"metadata": {"name": "default_rules"}, "rules": []}
        def save(self, output_path):
            import os; os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "w") as f:
                json.dump({}, f)

    monkeypatch.setattr("src.api.routers.rules.BARulesTemplateConverter", DummyConverter)

    files = {"file": ("rules.csv", b"x", "text/csv")}
    r = client.post("/api/v1/rules/upload?rules_name=default_rules", files=files)
    assert r.status_code == 200
    assert called_with.get('converter') == 'ba_friendly'

    import os
    p = "config/rules/default_rules.json"
    if os.path.exists(p): os.remove(p)
```

### Step 2: Run tests — expect FAIL (router not registered yet)

```bash
cd /tmp/cm3-batch-automations
pytest tests/unit/test_api_rules_upload.py -v
```

Expected: `FAILED` — 404 or import error because `/api/v1/rules/upload` doesn't exist yet.

### Step 3: Create `src/api/routers/rules.py`

```python
"""Rules upload endpoint."""

import shutil
import sys
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from src.config.ba_rules_template_converter import BARulesTemplateConverter
from src.config.rules_template_converter import RulesTemplateConverter

router = APIRouter()

RULES_DIR = Path("config/rules")
RULES_DIR.mkdir(parents=True, exist_ok=True)

UPLOADS_DIR = Path("uploads")
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


@router.post("/upload")
async def upload_rules_template(
    file: UploadFile = File(...),
    rules_name: str = Query(None, description="Name for the rules config"),
    rules_type: str = Query("ba_friendly", description="ba_friendly or technical"),
):
    """Upload Excel/CSV rules template and convert to rules JSON."""
    if not file.filename.endswith((".xlsx", ".xls", ".csv")):
        raise HTTPException(status_code=400, detail="Only .xlsx, .xls, and .csv files are supported.")

    upload_path = UPLOADS_DIR / file.filename
    with open(upload_path, "wb") as buf:
        shutil.copyfileobj(file.file, buf)

    try:
        if rules_type == "technical":
            converter = RulesTemplateConverter()
        else:
            converter = BARulesTemplateConverter()

        if file.filename.endswith((".xlsx", ".xls")):
            converter.from_excel(str(upload_path))
        else:
            converter.from_csv(str(upload_path))

        rules_id = rules_name or Path(file.filename).stem
        # Patch the name into metadata so it's consistent
        if hasattr(converter, "rules_config") and converter.rules_config:
            converter.rules_config.setdefault("metadata", {})["name"] = rules_id

        output_path = RULES_DIR / f"{rules_id}.json"
        converter.save(str(output_path))

        return {
            "rules_id": rules_id,
            "filename": file.filename,
            "size": upload_path.stat().st_size,
            "message": f"Rules template converted successfully. Rules saved as '{rules_id}'",
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error converting rules template: {str(e)}")

    finally:
        if upload_path.exists():
            upload_path.unlink()
```

### Step 4: Register the router in `src/api/main.py`

Add after the existing `from src.api.routers import mappings, files, system` import line:

```python
from src.api.routers import rules as rules_router_mod
```

Add after the last `app.include_router(...)` call (before the static mounts):

```python
app.include_router(
    rules_router_mod.router,
    prefix="/api/v1/rules",
    tags=["Rules"]
)
```

### Step 5: Run tests — expect PASS

```bash
pytest tests/unit/test_api_rules_upload.py -v
```

Expected: 4 tests PASSED.

### Step 6: Run full test suite — expect no regressions

```bash
pytest tests/unit/ -v --ignore=tests/unit/test_contracts_pipeline.py \
  --ignore=tests/unit/test_pipeline_runner.py \
  --ignore=tests/unit/test_workflow_wrapper_parity.py -q
```

Expected: All previously passing tests still pass.

### Step 7: Commit

```bash
cd /tmp/cm3-batch-automations
git add src/api/routers/rules.py tests/unit/test_api_rules_upload.py src/api/main.py
git commit -m "feat(api): add POST /api/v1/rules/upload endpoint for BA and technical rules templates"
```

---

## Task 2: Mapping Generator tab in the web UI

**Files:**
- Modify: `src/reports/static/ui.html`
- Modify: `tests/unit/test_web_ui.py` (add tab smoke tests)

---

### Step 1: Write the failing tests

Add to the end of `tests/unit/test_web_ui.py`:

```python
# ---------------------------------------------------------------------------
# Mapping Generator tab smoke tests
# ---------------------------------------------------------------------------

def test_ui_contains_mapping_generator_tab(client):
    """The UI must contain the Mapping Generator tab."""
    response = client.get("/ui")
    assert response.status_code == 200
    assert b"Mapping Generator" in response.content


def test_ui_contains_generate_mapping_button(client):
    """The UI must contain a Generate Mapping button."""
    response = client.get("/ui")
    assert b"Generate Mapping" in response.content


def test_ui_contains_generate_rules_button(client):
    """The UI must contain a Generate Rules button."""
    response = client.get("/ui")
    assert b"Generate Rules" in response.content


def test_ui_contains_rules_type_dropdown(client):
    """The UI must contain the BA-friendly rules type option."""
    response = client.get("/ui")
    assert b"BA-friendly" in response.content or b"ba_friendly" in response.content
```

### Step 2: Run tests — expect FAIL

```bash
pytest tests/unit/test_web_ui.py::test_ui_contains_mapping_generator_tab \
       tests/unit/test_web_ui.py::test_ui_contains_generate_mapping_button \
       tests/unit/test_web_ui.py::test_ui_contains_generate_rules_button \
       tests/unit/test_web_ui.py::test_ui_contains_rules_type_dropdown -v
```

Expected: 4 FAILED — strings not present yet.

### Step 3: Add the Mapping Generator tab to `src/reports/static/ui.html`

**3a — Add tab button** (in `<nav class="tabs">`):

Find:
```html
  <button id="tab-runs" role="tab" aria-selected="false"
          onclick="switchTab('runs')">Recent Runs</button>
```

Replace with:
```html
  <button id="tab-runs" role="tab" aria-selected="false"
          onclick="switchTab('runs')">Recent Runs</button>
  <button id="tab-mapping" role="tab" aria-selected="false"
          onclick="switchTab('mapping')">Mapping Generator</button>
```

**3b — Add CSS** for the new input, section divider, and result area (add before `</style>`):

```css
  /* mapping generator */
  .gen-section { margin-bottom: 28px; }
  .gen-section:last-child { margin-bottom: 0; }
  .gen-section-title {
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.6px;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 14px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
  }
  input.text-input {
    background: var(--accent);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    color: var(--text);
    font-size: 13px;
    outline: none;
    padding: 6px 10px;
    width: 200px;
    transition: border-color 0.15s;
  }
  input.text-input:focus { border-color: #4a9eff; }
  input.text-input::placeholder { color: var(--muted); }
  .result-bar {
    margin-top: 14px;
    min-height: 22px;
    font-size: 13px;
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
  }
  .result-bar.loading { color: #4a9eff; }
  .result-bar.success { color: var(--pass); }
  .result-bar.error   { color: var(--fail); }
  .result-bar.info    { color: var(--muted); }
  .result-bar a { color: #4a9eff; font-size: 12px; text-decoration: none; }
  .result-bar a:hover { text-decoration: underline; }
```

**3c — Add panel HTML** (after the `<!-- RECENT RUNS PANEL -->` closing `</div>`):

```html
  <!-- MAPPING GENERATOR PANEL -->
  <div id="panel-mapping" class="panel" role="tabpanel" aria-labelledby="tab-mapping" style="display:none">
    <h2>Mapping Generator</h2>

    <!-- Section 1: Field Mapping -->
    <div class="gen-section">
      <div class="gen-section-title">Field Mapping</div>
      <div class="drop-zone" id="mapDropZone" tabindex="0"
           role="button" aria-label="Click or drag a mapping template here">
        <svg width="32" height="32" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round"
            d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5"/>
        </svg>
        <span class="dz-label">Drop mapping template here or click to browse (.xlsx, .csv)</span>
        <span class="dz-filename" id="mapFileName"></span>
      </div>
      <input type="file" id="mapFileInput" accept=".xlsx,.xls,.csv">
      <div class="form-row">
        <label class="field-label" for="mapNameInput">Mapping name</label>
        <input type="text" id="mapNameInput" class="text-input" placeholder="(optional — uses filename)">
        <label class="field-label" for="mapFormatSelect">Format</label>
        <select id="mapFormatSelect">
          <option value="">Auto-detect</option>
          <option value="fixed_width">Fixed Width</option>
          <option value="pipe_delimited">Pipe-delimited</option>
          <option value="csv">CSV</option>
          <option value="tsv">TSV</option>
        </select>
      </div>
      <div class="btn-row">
        <button class="btn btn-primary" id="btnGenMapping" disabled>Generate Mapping</button>
      </div>
      <div class="result-bar info" id="mapResultBar">Select a mapping template to get started.</div>
    </div>

    <!-- Section 2: Validation Rules -->
    <div class="gen-section">
      <div class="gen-section-title">Validation Rules</div>
      <div class="drop-zone" id="rulesDropZone" tabindex="0"
           role="button" aria-label="Click or drag a rules template here">
        <svg width="32" height="32" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round"
            d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5"/>
        </svg>
        <span class="dz-label">Drop rules template here or click to browse (.xlsx, .csv)</span>
        <span class="dz-filename" id="rulesFileName"></span>
      </div>
      <input type="file" id="rulesFileInput" accept=".xlsx,.xls,.csv">
      <div class="form-row">
        <label class="field-label" for="rulesNameInput">Rules name</label>
        <input type="text" id="rulesNameInput" class="text-input" placeholder="(optional — uses filename)">
        <label class="field-label" for="rulesTypeSelect">Type</label>
        <select id="rulesTypeSelect">
          <option value="ba_friendly">BA-friendly</option>
          <option value="technical">Technical</option>
        </select>
      </div>
      <div class="btn-row">
        <button class="btn btn-primary" id="btnGenRules" disabled>Generate Rules</button>
      </div>
      <div class="result-bar info" id="rulesResultBar">Select a rules template to get started.</div>
    </div>
  </div>
```

**3d — Update `switchTab()` JS** to include `'mapping'`:

Find:
```javascript
function switchTab(name) {
  ['quick', 'runs'].forEach(function(t) {
```

Replace with:
```javascript
function switchTab(name) {
  ['quick', 'runs', 'mapping'].forEach(function(t) {
```

**3e — Add JS for Mapping Generator** (add before the `// Init` section at the bottom of the `<script>` block):

```javascript
// ---------------------------------------------------------------------------
// Mapping Generator — state
// ---------------------------------------------------------------------------
var mapFile   = null;
var rulesFile = null;

// ---------------------------------------------------------------------------
// Mapping Generator — result bar helpers (independent of Quick Test status)
// ---------------------------------------------------------------------------
function setGenResult(barId, type, msg, links) {
  var el = document.getElementById(barId);
  el.className = 'result-bar ' + type;
  while (el.firstChild) { el.removeChild(el.firstChild); }

  if (type === 'loading') {
    var sp = document.createElement('span');
    sp.className = 'spinner';
    el.appendChild(sp);
  }
  el.appendChild(document.createTextNode(msg));

  if (links) {
    links.forEach(function(lnk) {
      var a = document.createElement('a');
      a.textContent = lnk.text;
      a.href = lnk.href;
      if (lnk.download) { a.setAttribute('download', lnk.download); }
      if (lnk.onClick) { a.href = '#'; a.addEventListener('click', lnk.onClick); }
      el.appendChild(a);
    });
  }
}

// ---------------------------------------------------------------------------
// Mapping Generator — drop zones
// ---------------------------------------------------------------------------
function setupGenDrop(zoneId, inputId, slot) {
  var zone  = document.getElementById(zoneId);
  var input = document.getElementById(inputId);
  var nameId = slot === 'map' ? 'mapFileName' : 'rulesFileName';
  var btnId  = slot === 'map' ? 'btnGenMapping' : 'btnGenRules';

  zone.addEventListener('dragover', function(e) { e.preventDefault(); zone.classList.add('dragover'); });
  zone.addEventListener('dragleave', function() { zone.classList.remove('dragover'); });
  zone.addEventListener('drop', function(e) {
    e.preventDefault();
    zone.classList.remove('dragover');
    var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) { setGenFile(f, slot, nameId, btnId); }
  });
  zone.addEventListener('click', function() { input.click(); });
  zone.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' || e.key === ' ') { input.click(); }
  });
  input.addEventListener('change', function() {
    if (this.files.length) { setGenFile(this.files[0], slot, nameId, btnId); }
  });
}

function setGenFile(file, slot, nameId, btnId) {
  document.getElementById(nameId).textContent = file.name;
  document.getElementById(btnId).disabled = false;
  if (slot === 'map')   { mapFile   = file; }
  if (slot === 'rules') { rulesFile = file; }
}

setupGenDrop('mapDropZone',   'mapFileInput',   'map');
setupGenDrop('rulesDropZone', 'rulesFileInput', 'rules');

// ---------------------------------------------------------------------------
// Mapping Generator — Generate Mapping
// ---------------------------------------------------------------------------
document.getElementById('btnGenMapping').addEventListener('click', async function() {
  if (!mapFile) { return; }
  setGenResult('mapResultBar', 'loading', 'Generating mapping\u2026', null);
  this.disabled = true;

  try {
    var mappingName = document.getElementById('mapNameInput').value.trim();
    var format      = document.getElementById('mapFormatSelect').value;
    var url = '/api/v1/mappings/upload';
    var params = [];
    if (mappingName) { params.push('mapping_name=' + encodeURIComponent(mappingName)); }
    if (format)      { params.push('file_format='  + encodeURIComponent(format)); }
    if (params.length) { url += '?' + params.join('&'); }

    var fd = new FormData();
    fd.append('file', mapFile);

    var resp = await fetch(url, { method: 'POST', body: fd });
    if (!resp.ok) {
      var err = await resp.json().catch(function() { return { detail: resp.statusText }; });
      throw new Error(err.detail || resp.statusText);
    }
    var data = await resp.json();
    var mid  = data.mapping_id;
    setGenResult('mapResultBar', 'success', '\u2705 \u2018' + mid + '\u2019 created\u00a0\u00a0', [
      { text: 'Download JSON', href: '/api/v1/mappings/' + encodeURIComponent(mid), download: mid + '.json' },
      {
        text: 'Use in Quick Test \u2192',
        href: '#',
        onClick: function(e) {
          e.preventDefault();
          loadMappings();
          switchTab('quick');
          document.getElementById('mappingSelect').value = mid;
        }
      }
    ]);
  } catch (err) {
    setGenResult('mapResultBar', 'error', 'Error: ' + err.message, null);
  } finally {
    document.getElementById('btnGenMapping').disabled = false;
  }
});

// ---------------------------------------------------------------------------
// Mapping Generator — Generate Rules
// ---------------------------------------------------------------------------
document.getElementById('btnGenRules').addEventListener('click', async function() {
  if (!rulesFile) { return; }
  setGenResult('rulesResultBar', 'loading', 'Generating rules\u2026', null);
  this.disabled = true;

  try {
    var rulesName = document.getElementById('rulesNameInput').value.trim();
    var rulesType = document.getElementById('rulesTypeSelect').value;
    var url = '/api/v1/rules/upload';
    var params = ['rules_type=' + encodeURIComponent(rulesType)];
    if (rulesName) { params.push('rules_name=' + encodeURIComponent(rulesName)); }
    url += '?' + params.join('&');

    var fd = new FormData();
    fd.append('file', rulesFile);

    var resp = await fetch(url, { method: 'POST', body: fd });
    if (!resp.ok) {
      var err = await resp.json().catch(function() { return { detail: resp.statusText }; });
      throw new Error(err.detail || resp.statusText);
    }
    var data = await resp.json();
    var rid  = data.rules_id;
    setGenResult('rulesResultBar', 'success', '\u2705 \u2018' + rid + '\u2019 created\u00a0\u00a0', [
      { text: 'Download JSON', href: '/api/v1/rules/' + encodeURIComponent(rid) + '.json', download: rid + '.json' }
    ]);
  } catch (err) {
    setGenResult('rulesResultBar', 'error', 'Error: ' + err.message, null);
  } finally {
    document.getElementById('btnGenRules').disabled = false;
  }
});
```

### Step 4: Run the new UI tests — expect PASS

```bash
pytest tests/unit/test_web_ui.py -v
```

Expected: All tests PASS (including 4 new ones).

### Step 5: Run full test suite — expect no regressions

```bash
pytest tests/unit/ -v --ignore=tests/unit/test_contracts_pipeline.py \
  --ignore=tests/unit/test_pipeline_runner.py \
  --ignore=tests/unit/test_workflow_wrapper_parity.py -q
```

Expected: All previously passing tests still pass.

### Step 6: Commit

```bash
cd /tmp/cm3-batch-automations
git add src/reports/static/ui.html tests/unit/test_web_ui.py
git commit -m "feat(ui): add Mapping Generator tab with field mapping and validation rules sections"
```

---

## Task 3: Serve rules JSON for download + push

**Files:**
- Modify: `src/api/main.py` (serve `config/rules/` as static)
- Modify: `src/api/routers/rules.py` (add GET endpoint for download)

The Download JSON link in the UI hits `/api/v1/rules/<id>.json`. We need a download endpoint.

### Step 1: Add GET download endpoint to `src/api/routers/rules.py`

Add after the upload route:

```python
from fastapi.responses import FileResponse

@router.get("/{rules_id}.json")
async def download_rules(rules_id: str):
    """Download a generated rules JSON file."""
    path = RULES_DIR / f"{rules_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Rules '{rules_id}' not found")
    return FileResponse(str(path), media_type="application/json", filename=f"{rules_id}.json")
```

### Step 2: Add a test for the download endpoint

Add to `tests/unit/test_api_rules_upload.py`:

```python
def test_download_rules_not_found():
    r = client.get("/api/v1/rules/definitely_missing_rules.json")
    assert r.status_code == 404


def test_download_rules_success(tmp_path, monkeypatch):
    import json as _json
    rules_dir = Path("config/rules")
    rules_dir.mkdir(parents=True, exist_ok=True)
    test_file = rules_dir / "dl_test_rules.json"
    test_file.write_text(_json.dumps({"rules": []}))

    r = client.get("/api/v1/rules/dl_test_rules.json")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")

    test_file.unlink(missing_ok=True)
```

### Step 3: Run tests

```bash
pytest tests/unit/test_api_rules_upload.py -v
```

Expected: All 6 tests PASS.

### Step 4: Push branch and open PR

```bash
cd /tmp/cm3-batch-automations
git push origin feature/database-validations-pilot
```

Then open a PR on GitHub:
```bash
gh pr create \
  --title "feat(ui): Mapping Generator tab — generate mapping + rules JSON from Excel/CSV" \
  --body "$(cat <<'EOF'
## Summary
- Adds a **Mapping Generator** tab to the web UI with two sections: Field Mapping and Validation Rules
- New `POST /api/v1/rules/upload` endpoint backed by existing `BARulesTemplateConverter` / `RulesTemplateConverter`
- New `GET /api/v1/rules/<id>.json` download endpoint
- "Use in Quick Test →" button auto-switches tab and refreshes mapping dropdown after generation
- No changes to existing Quick Test or Recent Runs behaviour

## Test plan
- [ ] Upload a CSV mapping template → mapping JSON generated, download link works, Use in Quick Test switches tab
- [ ] Upload an XLSX mapping template → same flow
- [ ] Upload a BA-friendly rules CSV → rules JSON generated, download link works
- [ ] Upload a technical rules XLSX → same flow
- [ ] Upload a .txt file → 400 error shown in UI
- [ ] Run full pytest suite, confirm no regressions

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
