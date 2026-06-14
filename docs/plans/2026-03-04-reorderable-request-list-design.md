# Reorderable Request List Design

**Date:** 2026-03-04
**Issue:** #45

## Goal

Allow users to drag-and-drop requests within a suite to control execution order, with an explicit "Save Order" button to persist the new order.

## Architecture

Pure front-end change in `src/reports/static/ui.html`. No backend changes — the existing `PUT /api/v1/api-tester/suites/{id}` endpoint already accepts `requests[]` in any order.

## Drag-and-Drop Behavior

In `atRenderRunnerList`, each `at-req-row` div gets `draggable="true"` and three event listeners:
- `dragstart` — stores the dragged row's index in `_atDragSrcIdx`
- `dragover` — adds `.drag-over` highlight to the target row
- `drop` — splices the dragged item to the new position in `_atCurrentSuite.requests`, re-renders the list, shows the "Save Order" button

A module-level variable `_atDragSrcIdx = -1` tracks the index being dragged.

## "Save Order" Button

A `#btnSaveOrder` button sits below `#atRunnerReqList`, hidden by default (`display: none`). It appears when a drag completes (order is dirty). Clicking it calls `PUT /api/v1/api-tester/suites/{id}` with the full suite in new order, then hides itself.

## CSS Additions

```css
.at-req-row[draggable] { cursor: grab; }
.at-req-row.drag-over  { border-color: #4a9eff; background: #1a2a3a; }
```

## Files Changed

- `src/reports/static/ui.html` only — CSS, HTML, JS

## Testing

Playwright step in `scripts/e2e_full_ui.py` Workflow 4 (API Tester): after loading a suite with ≥2 requests, drag row 0 to position 1, assert DOM order changed, click "Save Order", assert button disappears.
