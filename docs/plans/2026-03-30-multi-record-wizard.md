# Multi-Record Config Wizard — Implementation Plan

**Issue:** #180
**Date:** 2026-03-30
**Priority:** Medium

---

## Overview

Add a 5-step wizard inside the Mapping Generator tab that lets non-technical users build a multi-record YAML config through a guided UI. The wizard calls the existing `POST /api/v1/multi-record/generate` endpoint and adds one new backend endpoint `POST /api/v1/multi-record/detect-discriminator`.

---

## 1. Files to Create or Modify

### Backend — new endpoint

**`src/api/routers/multi_record.py`**
- Add `POST /detect-discriminator` route (~40 lines) below the existing `generate` handler.

**`src/services/multi_record_wizard_service.py`** *(new)*
- `detect_discriminator(content: str, max_lines: int = 20) -> dict` — pure Python service function.
- Router delegates to this; no algorithm logic in the router.

### Backend — tests

**`tests/unit/test_multi_record_wizard_service.py`** *(new)*
- Unit tests for `detect_discriminator` covering happy path, empty file, all-unique values, single record type, max_lines param, 1-indexed position.

**`tests/unit/test_api_multi_record_wizard.py`** *(new)*
- FastAPI `TestClient` tests for `POST /api/v1/multi-record/detect-discriminator`.

### Frontend

**`src/reports/static/ui.html`**
- Add `div#mrWizardSection` inside `#panel-mapping` (after Validation Rules section, before closing `</div>`). ~80 lines of HTML structure only — no inline JS or CSS.

**`src/reports/static/ui.css`**
- Add wizard classes (`.mr-wizard`, `.mr-step-panel`, `.mr-step-panel.active`, `.mr-record-type-row`, `.mr-cross-rule-row`, `.mr-yaml-preview`) after existing `.gen-section` block. ~80 lines. Reuse existing CSS variables for theme compatibility.

**`src/reports/static/ui.js`**
- Add `// === Multi-Record Config Wizard ===` block after the existing Mapping Generator rules section (~line 1008). ~350–400 lines.

### Docs

**`docs/USAGE_AND_OPERATIONS_GUIDE.md`** — new sub-section under "Web UI" describing the wizard flow.
**`docs/sphinx/modules.rst`** — register `src.services.multi_record_wizard_service`.

---

## 2. New API Endpoint

### `POST /api/v1/multi-record/detect-discriminator`

**Request:** `multipart/form-data`
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | `UploadFile` | yes | Batch file to scan |
| `max_lines` | `int` (query, default 20) | no | Lines to inspect |

**Response:**
```json
{
  "candidates": [
    {
      "position": 1,
      "length": 3,
      "values": ["HDR", "DTL", "TRL"],
      "confidence": 0.95
    }
  ],
  "best": {
    "position": 1,
    "length": 3,
    "values": ["HDR", "DTL", "TRL"],
    "confidence": 0.95
  }
}
```
`candidates: []` and `best: null` when no pattern is found.

**Algorithm (`detect_discriminator` service):**
1. Read up to `max_lines` lines from file text.
2. For each candidate `(position p, length l)` where `p` ∈ 0–10 (0-indexed) and `l` ∈ 1–6: extract substring from each line.
3. Score by: `n_distinct_values / n_lines` — reward 2–8 distinct values that each repeat at least twice; penalise all-unique or single-value.
4. Return all pairs with confidence ≥ 0.5, sorted descending. Position returned as 1-indexed.

---

## 3. UI Component Breakdown

### HTML Structure

```
div.gen-section  id="mrWizardSection"
  div.gen-section-title   "Multi-Record Config Wizard"
  div.step-indicators     id="mrStepIndicators"   (steps 1–5)
  div.mr-wizard
    div.mr-step-panel  id="mrStep1"   [record type selection]
    div.mr-step-panel  id="mrStep2"   [discriminator config]
    div.mr-step-panel  id="mrStep3"   [code→mapping rows]
    div.mr-step-panel  id="mrStep4"   [cross-type rules]
    div.mr-step-panel  id="mrStep5"   [YAML preview & download]
  div.btn-row   [Back / Next / Download / Validate buttons]
```

Step panels hidden/shown via toggling `.active` (`display:none` / `display:block`). No animation — ADA/reduced-motion compliant.

### Step Detail

**Step 1 — Select Record Types**
- Calls `GET /api/v1/mappings/` (reuses `_allMappingOptions` or re-fetches).
- Renders checkbox list. Validates ≥ 1 selected before advancing.

**Step 2 — Configure Discriminator**
- Three inputs: Field Name, Position (number), Length (number).
- Optional file upload + "Auto-detect" button → calls `POST /api/v1/multi-record/detect-discriminator` → populates fields with `best` candidate.

**Step 3 — Map Codes to Record Types**
- Per selected mapping: discriminator code input, "First row"/"Last row" select, rules dropdown, cardinality select.

**Step 4 — Cross-Type Rules (optional)**
- "Add Rule" appends a row with: rule type select, dynamic extra fields (via `mrRenderRuleFields(type, container)`), severity select, message input, remove (×) button.
- Rule types: `required_companion`, `header_trailer_count`, `header_trailer_sum`, `header_trailer_match`, `header_detail_consistent`, `type_sequence`, `expect_count`.
- "Skip" link advances to Step 5.

**Step 5 — Preview & Download**
- `mrBuildPayload()` → `POST /api/v1/multi-record/generate` → renders YAML in `<pre class="mr-yaml-preview">`.
- Copy YAML button: `navigator.clipboard.writeText(yamlText)`.
- Download YAML button: Blob + `<a download>` trigger.
- "Validate File With This Config": stores YAML blob in `_mrPendingYaml`, switches to Quick Test tab.

### JavaScript State Variables

```javascript
var _mrStep          = 1;    // current step 1–5
var _mrMappings      = [];   // [{id, label}] from API
var _mrSelected      = [];   // selected mapping ids
var _mrDiscriminator = {};   // {field, position, length}
var _mrRecordTypes   = {};   // {typeName: {match, position, mapping, rules, expect}}
var _mrCrossRules    = [];   // [CrossTypeRule objects]
var _mrYamlText      = null; // last generated YAML
```

---

## 4. Implementation Sequence

### Phase A — Backend (TDD)

1. Write failing unit tests for `detect_discriminator` in `test_multi_record_wizard_service.py`
2. Implement `src/services/multi_record_wizard_service.py`
3. Write failing endpoint tests in `test_api_multi_record_wizard.py`
4. Add `POST /detect-discriminator` route to `src/api/routers/multi_record.py`
5. Run full suite — confirm pass + ≥ 80% coverage

### Phase B — CSS

6. Add wizard CSS classes to `ui.css`

### Phase C — HTML

7. Add `div#mrWizardSection` to `ui.html`

### Phase D — JavaScript

8. Add wizard JS block to `ui.js`:
   - `mrInit()` — reset state, populate Step 1, show step 1 panel
   - `mrGoTo(step)` — panel visibility + step indicator updates
   - `mrNext()` / `mrBack()` — validate then navigate
   - Step 1: `mrPopulateMappingList()`
   - Step 2: `mrAutoDetect()`
   - Step 3: `mrRenderTypeRows()`
   - Step 4: `mrAddCrossRule()`, `mrRenderRuleFields(type, container)`, `mrRemoveCrossRule(row)`
   - Step 5: `mrBuildPayload()`, `mrGenerateYaml()`, `mrDownloadYaml()`, `mrValidateWithConfig()`

### Phase E — Docs & Quality

9. Update `docs/USAGE_AND_OPERATIONS_GUIDE.md`
10. Update `docs/sphinx/modules.rst`
11. `cd docs/sphinx && make html`
12. `pytest tests/unit/ --cov=src -q`
13. Architecture review against 5 principles

---

## 5. Test Plan

### Unit — `test_multi_record_wizard_service.py`

| Test | Verifies |
|------|----------|
| `test_detects_consistent_3char_code_at_col1` | 3 distinct codes repeating → best candidate has position=1, length=3, confidence≥0.8 |
| `test_returns_empty_for_empty_file` | Empty string → `candidates=[]`, `best=None` |
| `test_returns_empty_when_all_values_unique` | All unique codes → no candidates |
| `test_confidence_above_threshold_for_clean_file` | 20-line, 3 codes, consistent → confidence≥0.9 |
| `test_max_lines_parameter_respected` | 100-line file, `max_lines=5` → only 5 lines inspected |
| `test_returns_1indexed_position` | First char position → position=1 (not 0) |
| `test_single_record_type_file_returns_empty` | All same code → confidence below threshold |

### API — `test_api_multi_record_wizard.py`

| Test | Verifies |
|------|----------|
| `test_detect_discriminator_returns_best_candidate` | Clean fixed-width file → 200, `best` not None |
| `test_detect_discriminator_empty_file_returns_no_candidates` | Empty file → 200, `candidates=[]`, `best=None` |
| `test_detect_discriminator_missing_file_returns_422` | No file → 422 |
| `test_detect_discriminator_max_lines_param` | `max_lines=3` → processes 3 lines |

### E2E checklist (Playwright skeleton)

- Wizard section visible and collapsed on Mapping Generator tab load
- "Start Wizard" opens Step 1 with mapping checkboxes
- Zero selections + Next → inline validation error
- Step 2 auto-detect populates position/length inputs
- Step 5 shows non-empty YAML preview
- Copy button works
- Download triggers file save
- "Validate" switches to Quick Test tab

---

## 6. Risks and Edge Cases

**R1: Auto-detect false positives** — penalise candidates where distinct count is 1 or equals line count. Only accept 2–8 distinct values each repeating ≥ 2 times.

**R2: "Validate File" button cannot programmatically set a browser file input** — MVP: store YAML blob in `_mrPendingYaml`, switch to Quick Test tab, call `mrApplyPendingConfig()`. May start as a "copy YAML" instruction if full pre-load is blocked by browser security.

**R3: ui.js file growth** — from 2120 to ~2500 lines. Acceptable given single-file convention already established; split is a separate future refactor.

**R4: API key auth** — `multi_record_router` already has `dependencies=[Depends(require_api_key)]` at mount time. New endpoint inherits this automatically.

**R5: type_sequence rule needs ordered list** — MVP: comma-separated textarea input. Drag-and-drop reorder is a follow-up.

**R6: Mapping paths in generated YAML** — `mrBuildPayload()` must interpolate `config/mappings/{id}.json` from the mapping id returned by `GET /api/v1/mappings/`.
