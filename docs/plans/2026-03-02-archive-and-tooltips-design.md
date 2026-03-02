# Archive, CLI Commands, and UI Tooltips Design

**Date:** 2026-03-02
**Issues:** #28 (Persistent test archive with tamper-evident storage), UI tooltips (whole UI)
**Scope:** Backend archive manager, two new CLI commands, CSS tooltip system across all tabs

---

## Goal

Every suite run produces a permanent, tamper-evident archive entry in `reports/archive/`. Run
evidence survives the 24h TTL cleanup on `uploads/`. Auditors can verify reports were not modified
after generation. BA/QA teams get hover tooltips across the entire web UI.

---

## Architecture

### Archive directory layout

```
reports/archive/
  YYYY/MM/DD/
    {run_id}/
      {suite_name}_{run_id}_suite.html
      {test_name}.html              ← one per test that produced a report
      {run_id}_manifest.json        ← SHA-256 of every file + self-hash
```

### Manifest JSON

```json
{
  "run_id": "uuid",
  "suite_name": "P327 UAT",
  "environment": "uat",
  "timestamp": "2026-03-02T09:15:32Z",
  "files": [
    {"name": "P327_UAT_suite.html", "sha256": "abc123..."},
    {"name": "P327_structure_check.html", "sha256": "def456..."}
  ],
  "manifest_hash": "sha256 of the JSON above (excluding this key)"
}
```

`manifest_hash` is computed over the serialised JSON that contains only `run_id`, `suite_name`,
`environment`, `timestamp`, and `files`. This lets an auditor re-derive the hash from the file
list without a circular dependency.

### Config (`.env` / environment variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `REPORT_ARCHIVE_PATH` | `reports/archive` | Root of the archive tree |
| `REPORT_RETENTION_DAYS` | `365` | Runs older than this are deleted by `list-runs` |

Paths resolved via `Path(__file__).resolve().parent` anchoring — no hardcoded absolute paths.

---

## Components

### `src/utils/archive.py` — `ArchiveManager`

Public methods:

| Method | Description |
|--------|-------------|
| `archive_run(run_id, suite_name, env, timestamp, files)` | Copy files to dated dir, write manifest |
| `list_runs()` | Walk archive tree, return list of manifest dicts sorted newest-first |
| `get_run(run_id)` | Find and return manifest + file paths for a run |
| `purge_old_runs(retention_days)` | Delete run dirs older than retention_days |

### Integration point — `run_suite_from_path`

After `_append_run_history(...)` is called, add:

```python
from src.utils.archive import ArchiveManager
archive = ArchiveManager()
archive.archive_run(
    run_id=run_id,
    suite_name=suite.name,
    env=env or suite.environment,
    timestamp=datetime.utcnow().isoformat() + "Z",
    files=[suite_report_path] + [r["report_path"] for r in results if r.get("report_path")],
)
```

### CLI commands (added to `src/main.py`)

**`cm3-batch list-runs`**
- Calls `ArchiveManager().purge_old_runs(retention_days)` first
- Prints table: run_id | suite_name | env | timestamp | status
- Status derived from manifest (or `run_history.json` lookup)
- `--limit N` option (default 20)

**`cm3-batch get-run {run_id}`**
- Calls `ArchiveManager().get_run(run_id)`
- Prints manifest JSON and lists file paths
- Exits 1 with message if run_id not found

### `run_history.json` — archive path added

The entry written by `_append_run_history` gains one new field:

```json
"archive_path": "reports/archive/2026/03/02/{run_id}"
```

---

## UI Tooltips

### Pattern

A single CSS `data-tooltip` pattern — no JS, no extra library. Any HTML element with a
`data-tooltip="..."` attribute gets a styled dark tooltip on hover.

```css
[data-tooltip] { position: relative; }
[data-tooltip]:hover::after {
  content: attr(data-tooltip);
  position: absolute; bottom: 125%; left: 50%;
  transform: translateX(-50%);
  background: #1e293b; color: #f8fafc;
  padding: 6px 10px; border-radius: 6px;
  font-size: 12px; white-space: nowrap; z-index: 100;
  pointer-events: none;
}
[data-tooltip]:hover::before {
  content: '';
  position: absolute; bottom: 110%; left: 50%;
  transform: translateX(-50%);
  border: 5px solid transparent;
  border-top-color: #1e293b; z-index: 100;
}
```

### Tooltip coverage per tab

**Quick Test tab**

| Element | Tooltip text |
|---------|-------------|
| File upload zone | "Drop a batch file here, or click to browse (.txt, .csv, .dat, .pipe)" |
| Mapping dropdown | "Select a mapping schema to validate field positions and lengths" |
| Rules dropdown | "Select a business rules config to validate field values and patterns" |
| Validate button | "Run structural validation against the selected mapping and rules" |
| Compare button | "Compare two batch files row-by-row and highlight differences" |
| Parse button | "Parse the file and preview the first rows as a table" |

**Mapping Generator tab**

| Element | Tooltip text |
|---------|-------------|
| Mapping upload zone | "Upload an Excel or CSV template to generate a mapping JSON config" |
| Rules upload zone | "Upload an Excel or CSV template to generate a rules JSON config" |
| Rules Type selector | "BA-friendly: human-readable rules. Technical: strict regex/type rules" |
| Generate Mapping button | "Convert the uploaded template into a mapping JSON file" |
| Generate Rules button | "Convert the uploaded template into a rules JSON file" |
| Download link | "Download the generated JSON config to your machine" |
| Use in Quick Test link | "Load this config into the Quick Test tab and switch to it" |

**Runs tab**

| Element | Tooltip text |
|---------|-------------|
| PASS badge | "All tests passed or were skipped" |
| FAIL badge | "One or more tests failed" |
| PARTIAL badge | "Some tests passed and some failed" |
| Run ID cell | "Unique identifier for this suite run" |
| Archive icon | "This run is permanently archived with a tamper-evident SHA-256 manifest" |
| Timestamp cell | "UTC time when the suite run completed" |

---

## Testing

- `tests/unit/test_archive_manager.py` — unit tests for all `ArchiveManager` methods
- `tests/unit/test_web_ui.py` — add smoke tests: `data-tooltip` attributes present on key elements
- `tests/unit/test_list_runs_command.py` — CLI command tests via Click test runner
- `tests/unit/test_get_run_command.py` — CLI command tests via Click test runner

Coverage target: ≥80% on new `src/utils/archive.py`

---

## Acceptance Criteria (from issue #28)

- [x] Every suite run produces an immutable archive entry
- [x] Manifest SHA-256 hashes allow detection of post-run tampering
- [x] `list-runs` and `get-run` commands work
- [x] Retention policy deletes runs older than `REPORT_RETENTION_DAYS`
- [ ] ~~Archive path and manifest hash included in Splunk audit event~~ (Splunk deprioritized)

## Out of scope

- Splunk audit events (#21 deprioritized)
- API endpoints for archive access (CLI only for now)
- #35 notifications (separate plan, depends on this archive path)
