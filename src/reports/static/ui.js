// ===========================================================================
// State
// ===========================================================================
var primaryFile   = null;
var secondaryFile = null;
var compareMode   = false;
var _autoRefreshTimer = null;
var _runsData = [];
var _runsSortCol = 'timestamp';
var _runsSortDir = 'desc';
var _trendDays = 7;
var _trendSuite = '';

// ---------------------------------------------------------------------------
// Tab switching — animated panels + sliding underline indicator
// ---------------------------------------------------------------------------
/**
 * Switch the active tab panel and update the sliding underline indicator.
 *
 * @param {string} name - Tab identifier: 'quick', 'runs', 'mapping', or 'tester'.
 */
function switchTab(name) {
  ['quick', 'runs', 'mapping', 'tester'].forEach(function(t) {
    var panel = document.getElementById('panel-' + t);
    var btn   = document.getElementById('tab-' + t);
    if (t === name) {
      panel.classList.remove('panel-hidden');
      panel.classList.add('panel-entering');
      // Remove entering class after animation completes
      panel.addEventListener('animationend', function onEnd() {
        panel.classList.remove('panel-entering');
        panel.removeEventListener('animationend', onEnd);
      });
    } else {
      panel.classList.add('panel-hidden');
      panel.classList.remove('panel-entering');
    }
    btn.classList.toggle('active', t === name);
    btn.setAttribute('aria-selected', String(t === name));
  });
  updateTabIndicator();
  // Reload trend chart whenever the Recent Runs tab is activated (#249)
  if (name === 'runs') { loadTrendChart(); loadSummaryCards(); }
}

/**
 * Reposition the sliding underline indicator under the currently active tab button.
 * Safe to call at any time; no-op when the required DOM elements are absent.
 */
// Move the sliding underline indicator to the active tab button
function updateTabIndicator() {
  var activeBtn = document.querySelector('nav.tabs button.active');
  var indicator = document.getElementById('tabIndicator');
  var inner     = document.getElementById('tabsInner');
  if (!activeBtn || !indicator || !inner) { return; }
  var innerRect = inner.getBoundingClientRect();
  var btnRect   = activeBtn.getBoundingClientRect();
  indicator.style.left  = (btnRect.left - innerRect.left + inner.scrollLeft) + 'px';
  indicator.style.width = btnRect.width + 'px';
}

// ===========================================================================
// Status helpers — use textContent to avoid XSS
// ===========================================================================
/** Reset the status message element to a blank info state. */
function clearStatus() {
  var el = document.getElementById('statusMsg');
  el.className = 'status-msg info';
  while (el.firstChild) { el.removeChild(el.firstChild); }
}

/**
 * Display a plain-text status message.  Uses textContent to prevent XSS.
 *
 * @param {string} msg  - Message to display.
 * @param {string} type - CSS modifier class: 'info', 'success', 'error', or 'loading'.
 */
function setStatusText(msg, type) {
  var el = document.getElementById('statusMsg');
  el.className = 'status-msg ' + type;
  while (el.firstChild) { el.removeChild(el.firstChild); }
  el.textContent = msg;
}

/**
 * Display a status message followed by an optional hyperlink.
 *
 * @param {string} msg      - Text portion of the message.
 * @param {string} type     - CSS modifier class ('info', 'success', 'error', 'loading').
 * @param {string} linkText - Visible anchor text.
 * @param {string} linkHref - URL for the anchor href.
 */
function setStatusWithLink(msg, type, linkText, linkHref) {
  var el = document.getElementById('statusMsg');
  el.className = 'status-msg ' + type;
  while (el.firstChild) { el.removeChild(el.firstChild); }
  el.appendChild(document.createTextNode(msg + ' '));
  if (linkText && linkHref) {
    var a = document.createElement('a');
    a.className = 'report-link';
    a.textContent = linkText;
    a.href = linkHref;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    el.appendChild(a);
  }
}

/**
 * Load a report URL into the inline iframe panel and make it visible.
 *
 * @param {string} url - URL of the HTML report to display.
 */
function showInlineReport(url) {
  document.getElementById('reportFrame').src = url;
  document.getElementById('reportOpenLink').href = url;
  document.getElementById('reportPanel').classList.add('visible');
}

/** Hide the inline report iframe panel and clear its src. */
function hideInlineReport() {
  document.getElementById('reportPanel').classList.remove('visible');
  document.getElementById('reportFrame').src = '';
}

/**
 * Show a spinner alongside a loading message in the status area.
 * Also hides any currently displayed inline report.
 *
 * @param {string} msg - Text to show next to the spinner.
 */
function setLoading(msg) {
  hideInlineReport();
  var el = document.getElementById('statusMsg');
  el.className = 'status-msg loading';
  while (el.firstChild) { el.removeChild(el.firstChild); }
  var sp = document.createElement('span');
  sp.className = 'spinner';
  el.appendChild(sp);
  el.appendChild(document.createTextNode(msg));
}

// ---------------------------------------------------------------------------
// Drag-and-drop
// ---------------------------------------------------------------------------
/**
 * Attach drag-and-drop and click-to-browse handlers to a drop zone element.
 *
 * @param {string} zoneId - DOM id of the drop zone container.
 * @param {string} slot   - File slot identifier: 'primary' or 'secondary'.
 */
function setupDrop(zoneId, slot) {
  var zone = document.getElementById(zoneId);
  zone.addEventListener('dragover', function(e) {
    e.preventDefault();
    zone.classList.add('dragover');
  });
  zone.addEventListener('dragleave', function() {
    zone.classList.remove('dragover');
  });
  zone.addEventListener('drop', function(e) {
    e.preventDefault();
    zone.classList.remove('dragover');
    var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) { setFile(f, slot); }
  });
  zone.addEventListener('click', function() {
    document.getElementById(slot === 'primary' ? 'fileInput' : 'fileInput2').click();
  });
  zone.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' || e.key === ' ') {
      document.getElementById(slot === 'primary' ? 'fileInput' : 'fileInput2').click();
    }
  });
}
setupDrop('dropZone',  'primary');
setupDrop('dropZone2', 'secondary');
document.getElementById('btnCloseReport').addEventListener('click', hideInlineReport);

document.getElementById('fileInput').addEventListener('change', function() {
  if (this.files.length) {
    setFile(this.files[0], 'primary');
    // Hide export button whenever a new file is selected.
    document.getElementById('exportErrorsRow').style.display = 'none';
  }
});
document.getElementById('fileInput2').addEventListener('change', function() {
  if (this.files.length) { setFile(this.files[0], 'secondary'); }
});

// Remove-file buttons
document.getElementById('btnRemovePrimary').addEventListener('click', function(e) {
  e.stopPropagation();
  primaryFile = null;
  document.getElementById('primaryFileCard').classList.remove('visible');
  document.getElementById('fileInput').value = '';
  _hideDriftBadge();
  updateButtons();
});
document.getElementById('btnRemoveSecondary').addEventListener('click', function(e) {
  e.stopPropagation();
  secondaryFile = null;
  document.getElementById('secondaryFileCard').classList.remove('visible');
  document.getElementById('fileInput2').value = '';
  updateButtons();
});

/**
 * Derive a short display-format label from a filename extension.
 *
 * @param {string} filename - File name (e.g. 'data.txt', 'export.csv').
 * @returns {string} Upper-case format badge text (e.g. 'CSV', 'TXT').
 */
function detectFormat(filename) {
  var ext = (filename.split('.').pop() || '').toLowerCase();
  var map = { txt: 'TXT', csv: 'CSV', dat: 'DAT', pipe: 'PIPE', tsv: 'TSV', xlsx: 'XLSX', xls: 'XLS' };
  return map[ext] || ext.toUpperCase() || 'FILE';
}

/**
 * Format a byte count as a human-readable string.
 *
 * @param {number} bytes - Raw byte count.
 * @returns {string} Formatted string such as '1.4 KB' or '3.2 MB'.
 */
function formatBytes(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

/**
 * Store a selected file and update the corresponding file card UI.
 *
 * @param {File}   file - The File object selected by the user.
 * @param {string} slot - 'primary' or 'secondary'.
 */
function setFile(file, slot) {
  if (slot === 'primary') {
    primaryFile = file;
    document.getElementById('primaryFileName').textContent = file.name;
    document.getElementById('primaryFileMeta').textContent = formatBytes(file.size);
    document.getElementById('primaryFormatBadge').textContent = detectFormat(file.name);
    document.getElementById('primaryFileCard').classList.add('visible');
    // Hide any stale drift badge when a new file is loaded.
    _hideDriftBadge();
  } else {
    secondaryFile = file;
    document.getElementById('secondaryFileName').textContent = file.name;
    document.getElementById('secondaryFileMeta').textContent = formatBytes(file.size);
    document.getElementById('secondaryFormatBadge').textContent = detectFormat(file.name);
    document.getElementById('secondaryFileCard').classList.add('visible');
  }
  updateButtons();
}

/**
 * Enable or disable the Validate and Compare action buttons based on
 * whether the required files and a mapping selection are present.
 */
function updateButtons() {
  var hasPrimary   = !!primaryFile;
  var hasMapping   = !!document.getElementById('mappingSelect').value;
  var hasSecondary = !!secondaryFile;
  var mrYamlInput  = document.getElementById('qtMrYamlInput');
  var hasMrYaml    = !!(mrYamlInput && mrYamlInput.files && mrYamlInput.files[0]);
  document.getElementById('btnValidate').disabled = !(hasPrimary && (hasMapping || hasMrYaml));
  document.getElementById('btnCompare').disabled  = !(hasPrimary && hasMapping && hasSecondary);
}

// ===========================================================================
// Toggle compare
// ===========================================================================
document.getElementById('btnToggleCompare').addEventListener('click', function() {
  compareMode = !compareMode;
  document.getElementById('compare-section').classList.toggle('visible', compareMode);
  this.classList.toggle('active', compareMode);
});

// ===========================================================================
// Searchable mapping dropdown filter
// ===========================================================================
var _allMappingOptions = [];

document.getElementById('mappingFilter').addEventListener('input', function() {
  var q = this.value.toLowerCase();
  var sel = document.getElementById('mappingSelect');
  while (sel.firstChild) { sel.removeChild(sel.firstChild); }
  var placeholder = document.createElement('option');
  placeholder.value = '';
  placeholder.textContent = q ? '— filtered results —' : '— select a mapping —';
  sel.appendChild(placeholder);
  _allMappingOptions.forEach(function(opt) {
    if (!q || opt.label.toLowerCase().includes(q)) {
      var o = document.createElement('option');
      o.value = opt.value;
      o.textContent = opt.label;
      sel.appendChild(o);
    }
  });
  updateButtons();
});

// ===========================================================================
// Load mappings
// ===========================================================================
/**
 * Fetch the list of available mappings from the API and populate the mapping
 * dropdown.  Caches options in `_allMappingOptions` for client-side filtering.
 *
 * @returns {Promise<void>}
 */
async function loadMappings() {
  var sel = document.getElementById('mappingSelect');
  try {
    var resp = await fetch('/api/v1/mappings/');
    if (!resp.ok) { throw new Error('HTTP ' + resp.status); }
    var list = await resp.json();
    _allMappingOptions = [];
    while (sel.firstChild) { sel.removeChild(sel.firstChild); }
    if (!list.length) {
      var opt = document.createElement('option');
      opt.value = '';
      opt.textContent = 'No mappings available';
      sel.appendChild(opt);
    } else {
      var placeholder = document.createElement('option');
      placeholder.value = '';
      placeholder.textContent = '— select a mapping —';
      sel.appendChild(placeholder);
      list.forEach(function(m) {
        var label = m.mapping_name + ' (' + m.format + ')';
        _allMappingOptions.push({ value: m.id, label: label });
        var o = document.createElement('option');
        o.value = m.id;
        o.textContent = label;
        sel.appendChild(o);
      });
    }
  } catch (err) {
    while (sel.firstChild) { sel.removeChild(sel.firstChild); }
    var errOpt = document.createElement('option');
    errOpt.value = '';
    errOpt.textContent = 'Could not load mappings';
    sel.appendChild(errOpt);
    console.error('Failed to load mappings:', err);
  }
  sel.addEventListener('change', updateButtons);
}

/**
 * Fetch the list of available rules configs from the API and populate the
 * rules dropdown.
 *
 * @returns {Promise<void>}
 */
async function loadRules() {
  var sel = document.getElementById('rulesSelect');
  try {
    var resp = await fetch('/api/v1/rules/');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    var list = await resp.json();
    while (sel.options.length > 1) sel.removeChild(sel.lastChild);
    if (Array.isArray(list)) {
      list.forEach(function(r) {
        var o = document.createElement('option');
        o.value = r.id || r.filename || r;
        o.textContent = r.name || r.filename || r;
        sel.appendChild(o);
      });
    }
  } catch (err) {
    console.warn('Could not load rules list:', err);
  }
}

// ===========================================================================
// Validate button loading state helper
// ===========================================================================
/**
 * Toggle a button's loading state by replacing its content with a spinner or
 * restoring the original HTML.
 *
 * @param {HTMLButtonElement} btn       - The button element to update.
 * @param {boolean}           isLoading - True to show spinner; false to restore.
 */
function setBtnLoading(btn, isLoading) {
  if (isLoading) {
    btn.disabled = true;
    btn._origHTML = btn.innerHTML;
    btn.innerHTML = '<span class="spinner spinner-white"></span> Running\u2026';
  } else {
    if (btn._origHTML) { btn.innerHTML = btn._origHTML; }
    btn.disabled = false;
  }
}

document.getElementById('qtMrYamlInput').addEventListener('change', updateButtons);

// ===========================================================================
// Validate
// ===========================================================================
document.getElementById('btnValidate').addEventListener('click', async function() {
  if (!primaryFile) { setStatusText('Please select a file.', 'error'); return; }
  var mrYamlInput = document.getElementById('qtMrYamlInput');
  var mrYamlFile = mrYamlInput && mrYamlInput.files[0] ? mrYamlInput.files[0] : null;
  var mapping = document.getElementById('mappingSelect').value;
  if (!mrYamlFile && !mapping) { setStatusText('Please select a mapping or provide a multi-record YAML.', 'error'); return; }

  setLoading('Validating\u2026');
  setBtnLoading(this, true);
  var btn = this;

  try {
    var fd = new FormData();
    fd.append('file', primaryFile);
    if (mrYamlFile) {
      fd.append('multi_record_config', mrYamlFile);
    } else {
      fd.append('mapping_id', mapping);
    }
    var rulesVal = document.getElementById('rulesSelect').value;
    if (rulesVal && !mrYamlFile) { fd.append('rules_id', rulesVal); }
    fd.append('suppress_pii', document.getElementById('suppressPii').checked ? 'true' : 'false');

    var resp = await fetch('/api/v1/files/validate', { method: 'POST', body: fd });
    if (!resp.ok) {
      var errData = await resp.json().catch(function() { return { detail: resp.statusText }; });
      throw new Error(errData.detail || resp.statusText);
    }
    var data = await resp.json();

    // Populate metric cards
    var totalRows = data.total_rows || 0;
    var errors = data.invalid_rows || 0;
    var quality = totalRows > 0 ? Math.round(((totalRows - errors) / totalRows) * 100) : 0;
    document.getElementById('metricTotalRows').textContent = totalRows.toLocaleString();
    document.getElementById('metricErrors').textContent = errors.toLocaleString();
    document.getElementById('metricQuality').textContent = quality + '%';
    document.getElementById('quickMetrics').style.display = '';

    // Show Download Failed Rows button when there are errors.
    document.getElementById('exportErrorsRow').style.display = errors > 0 ? '' : 'none';

    var msg = 'Validated \u2014 ' + data.valid_rows + '/' + data.total_rows + ' rows valid'
      + (data.invalid_rows ? ' (' + data.invalid_rows + ' errors)' : '') + '.';
    if (data.report_url) {
      setStatusWithLink(msg, data.valid ? 'success' : 'error', 'Open report', data.report_url);
      showInlineReport(data.report_url);
    } else {
      setStatusText(msg, data.valid ? 'success' : 'error');
    }

    // Fire drift check non-blocking (standard mapping path only, not multi-record YAML).
    if (!mrYamlFile && mapping) {
      _runDriftCheck(primaryFile, mapping);
    }
  } catch (err) {
    setStatusText('Error: ' + err.message, 'error');
  } finally {
    setBtnLoading(btn, false);
    updateButtons();
  }
});

// ---------------------------------------------------------------------------
// Download Failed Rows
// ---------------------------------------------------------------------------
/**
 * POST to /api/v1/files/export-errors with the same file + mapping/yaml used
 * for the validate call, then trigger a browser file download from the response.
 */
document.getElementById('btnDownloadErrors').addEventListener('click', async function() {
  if (!primaryFile) { setStatusText('No file selected.', 'error'); return; }

  var mrYamlInput = document.getElementById('qtMrYamlInput');
  var mrYamlFile = mrYamlInput && mrYamlInput.files[0] ? mrYamlInput.files[0] : null;
  var mapping = document.getElementById('mappingSelect').value;
  if (!mrYamlFile && !mapping) {
    setStatusText('Please select a mapping or provide a multi-record YAML.', 'error');
    return;
  }

  var btn = this;
  setBtnLoading(btn, true);

  try {
    var fd = new FormData();
    fd.append('file', primaryFile);
    if (mrYamlFile) {
      fd.append('multi_record_config', mrYamlFile);
    } else {
      fd.append('mapping_id', mapping);
    }

    var resp = await fetch('/api/v1/files/export-errors', { method: 'POST', body: fd });
    if (!resp.ok) {
      var errData = await resp.json().catch(function() { return { detail: resp.statusText }; });
      throw new Error(errData.detail || resp.statusText);
    }

    // Trigger browser download from the response blob.
    var blob = await resp.blob();
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = 'errors_' + primaryFile.name;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  } catch (err) {
    setStatusText('Error downloading failed rows: ' + err.message, 'error');
  } finally {
    setBtnLoading(btn, false);
  }
});

// ---------------------------------------------------------------------------
// Drift detection badge helpers
// ---------------------------------------------------------------------------

/**
 * Show the drift warning badge and populate the fields table.
 *
 * @param {Array<Object>} fields - Array of drifted field objects from the
 *   detect-drift API response.  Each object has ``name``, ``expected_start``,
 *   ``actual_start``, ``expected_length``, and ``severity``.
 */
function _showDriftBadge(fields) {
  var badge = document.getElementById('driftBadge');
  var tbody = document.getElementById('driftFieldsBody');
  if (!badge || !tbody) return;

  // Clear previous rows.
  while (tbody.firstChild) { tbody.removeChild(tbody.firstChild); }

  fields.forEach(function(f) {
    var tr = document.createElement('tr');
    var sevClass = f.severity === 'error' ? 'drift-severity-error' : 'drift-severity-warning';

    [
      f.name || '\u2014',
      f.expected_start != null ? f.expected_start : '\u2014',
      f.actual_start   != null ? f.actual_start   : '\u2014',
      f.expected_length != null ? f.expected_length : '\u2014',
    ].forEach(function(val) {
      var td = document.createElement('td');
      td.textContent = val;
      tr.appendChild(td);
    });

    var tdSev = document.createElement('td');
    tdSev.className = sevClass;
    tdSev.textContent = f.severity || '\u2014';
    tr.appendChild(tdSev);

    tbody.appendChild(tr);
  });

  badge.style.display = '';
}

/**
 * Hide the drift warning badge.
 */
function _hideDriftBadge() {
  var badge = document.getElementById('driftBadge');
  if (badge) badge.style.display = 'none';
}

/**
 * Fire-and-forget drift check after a successful validate response.
 *
 * Posts the file and mapping_id to the detect-drift endpoint.  Silently
 * swallows any errors so it never disrupts the validation result display.
 *
 * @param {File} file - The primary file object already selected by the user.
 * @param {string} mappingId - The mapping ID string from the mapping select.
 */
async function _runDriftCheck(file, mappingId) {
  try {
    var fd = new FormData();
    fd.append('file', file);
    fd.append('mapping_id', mappingId);
    var resp = await fetch('/api/v1/files/detect-drift', { method: 'POST', body: fd });
    if (!resp.ok) { _hideDriftBadge(); return; }
    var data = await resp.json();
    if (data && data.drifted && Array.isArray(data.fields) && data.fields.length > 0) {
      _showDriftBadge(data.fields);
    } else {
      _hideDriftBadge();
    }
  } catch (_e) {
    _hideDriftBadge();
  }
}

// Expand/collapse drift badge on header click or Enter/Space keypress.
(function() {
  var header = document.getElementById('driftBadgeHeader');
  if (!header) return;
  function _toggleDriftBadge() {
    var badge = document.getElementById('driftBadge');
    if (!badge) return;
    var expanded = badge.classList.toggle('expanded');
    header.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  }
  header.addEventListener('click', _toggleDriftBadge);
  header.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); _toggleDriftBadge(); }
  });
})();

// ---------------------------------------------------------------------------
// Compare
// ---------------------------------------------------------------------------
document.getElementById('btnCompare').addEventListener('click', async function() {
  if (!primaryFile || !secondaryFile) { setStatusText('Please select both files.', 'error'); return; }
  var mapping = document.getElementById('mappingSelect').value;
  if (!mapping) { setStatusText('Please select a mapping.', 'error'); return; }

  setLoading('Comparing files…');
  this.disabled = true;

  try {
    var fd = new FormData();
    fd.append('file1', primaryFile);
    fd.append('file2', secondaryFile);
    fd.append('mapping_id', mapping);
    fd.append('key_columns', '');
    fd.append('detailed', 'true');

    var resp = await fetch('/api/v1/files/compare', { method: 'POST', body: fd });
    if (!resp.ok) {
      var errData = await resp.json().catch(function() { return { detail: resp.statusText }; });
      throw new Error(errData.detail || resp.statusText);
    }
    var data = await resp.json();
    var matchPct = data.total_rows_file1
      ? Math.round((data.matching_rows / data.total_rows_file1) * 100)
      : 0;
    var msg = 'Comparison complete — ' + data.matching_rows + '/' + data.total_rows_file1 +
              ' rows match (' + matchPct + '%).';
    if (data.report_url) {
      setStatusWithLink(msg, 'success', 'Open report', data.report_url);
      showInlineReport(data.report_url);
    } else {
      setStatusText(msg, 'success');
    }
  } catch (err) {
    setStatusText('Error: ' + err.message, 'error');
  } finally {
    updateButtons();
  }
});

// ===========================================================================
// Build run history table — DOM methods, sortable columns
// ===========================================================================
/**
 * Render (or re-render) the run history table and summary cards.
 *
 * Clears the existing table DOM, sorts rows according to the current
 * ``_runsSortCol`` / ``_runsSortDir`` state, and builds a new sortable table
 * with column-click handlers.  Also updates the four summary metric cards.
 *
 * @param {Array<Object>} rows - Run history records from the API.
 */
function buildRunsTable(rows) {
  var wrap = document.getElementById('runsTableWrap');
  while (wrap.firstChild) { wrap.removeChild(wrap.firstChild); }

  // Update summary cards
  var total = rows.length;
  var passed = rows.filter(function(r) { return (r.status || '').toUpperCase() === 'PASS'; }).length;
  var passRate = total > 0 ? Math.round((passed / total) * 100) : 0;
  document.getElementById('cardTotalRuns').textContent = total || '\u2014';
  document.getElementById('cardPassRate').textContent = total > 0 ? passRate + '%' : '\u2014';
  document.getElementById('cardAvgQuality').textContent = '\u2014';
  var lastRow = rows.length > 0 ? rows[rows.length - 1] : null;
  if (lastRow && lastRow.timestamp) {
    document.getElementById('cardLastRun').textContent = new Date(lastRow.timestamp).toLocaleDateString();
  } else {
    document.getElementById('cardLastRun').textContent = '\u2014';
  }
  var countEl = document.getElementById('runsCount');
  if (countEl) { countEl.textContent = total + ' run' + (total !== 1 ? 's' : ''); }

  if (!rows.length) {
    var emptyDiv = document.createElement('div');
    emptyDiv.className = 'empty-msg';
    var iconDiv = document.createElement('div');
    iconDiv.className = 'empty-icon';
    iconDiv.textContent = '\uD83D\uDCC4';
    var msgDiv = document.createElement('div');
    msgDiv.textContent = 'No run history yet.';
    var subDiv = document.createElement('div');
    subDiv.style.marginTop = '6px';
    var goLink = document.createElement('a');
    goLink.textContent = 'Go to Quick Test \u2192';
    goLink.href = '#';
    goLink.addEventListener('click', function(e) { e.preventDefault(); switchTab('quick'); });
    subDiv.appendChild(goLink);
    emptyDiv.appendChild(iconDiv);
    emptyDiv.appendChild(msgDiv);
    emptyDiv.appendChild(subDiv);
    wrap.appendChild(emptyDiv);
    return;
  }

  var sortedRows = rows.slice().sort(function(a, b) {
    var av = a[_runsSortCol] || '';
    var bv = b[_runsSortCol] || '';
    if (av < bv) return _runsSortDir === 'asc' ? -1 : 1;
    if (av > bv) return _runsSortDir === 'asc' ? 1 : -1;
    return 0;
  });

  var table = document.createElement('table');

  // thead with sortable columns
  var thead = document.createElement('thead');
  var headerRow = document.createElement('tr');
  var cols = [
    { label: 'Run ID',      key: 'run_id' },
    { label: 'Suite Name',  key: 'suite_name' },
    { label: 'Environment', key: 'environment' },
    { label: 'Date',        key: 'timestamp' },
    { label: 'Status',      key: 'status' },
    { label: 'Tests',       key: 'pass_count' },
    { label: 'Report',      key: null },
    { label: 'vs Baseline', key: null, id: 'thBaseline', hidden: true },
  ];
  cols.forEach(function(col) {
    var th = document.createElement('th');
    th.textContent = col.label;
    if (col.id) { th.id = col.id; }
    if (col.hidden) {
      th.style.display = localStorage.getItem('valdo_baseline_col_visible') === 'true' ? '' : 'none';
    }
    if (col.key) {
      var sortSpan = document.createElement('span');
      sortSpan.className = 'sort-icon';
      sortSpan.textContent = col.key === _runsSortCol
        ? (_runsSortDir === 'asc' ? '\u25B2' : '\u25BC') : '\u25BC';
      th.appendChild(sortSpan);
      if (col.key === _runsSortCol) { th.classList.add('sort-' + _runsSortDir); }
      th.addEventListener('click', (function(k) {
        return function() {
          if (_runsSortCol === k) { _runsSortDir = _runsSortDir === 'asc' ? 'desc' : 'asc'; }
          else { _runsSortCol = k; _runsSortDir = 'asc'; }
          buildRunsTable(_runsData);
        };
      })(col.key));
    } else {
      th.style.cursor = 'default';
    }
    headerRow.appendChild(th);
  });
  thead.appendChild(headerRow);
  table.appendChild(thead);

  // tbody
  var tbody = document.createElement('tbody');
  sortedRows.forEach(function(r) {
    var tr = document.createElement('tr');

    // Run ID
    var tdId = document.createElement('td');
    tdId.textContent = r.run_id ? r.run_id.substring(0, 8) + '\u2026' : '\u2014';
    tdId.style.fontFamily = 'monospace';
    tdId.style.fontSize = '11px';
    tdId.setAttribute('data-tooltip', 'Unique identifier for this suite run');
    tr.appendChild(tdId);

    // Suite name
    var tdName = document.createElement('td');
    tdName.textContent = r.suite_name || '\u2014';
    tr.appendChild(tdName);

    // Environment
    var tdEnv = document.createElement('td');
    tdEnv.textContent = r.environment || '\u2014';
    tr.appendChild(tdEnv);

    // Date
    var tdDate = document.createElement('td');
    tdDate.textContent = r.timestamp ? new Date(r.timestamp).toLocaleString() : '\u2014';
    tdDate.setAttribute('data-tooltip', 'UTC time when the suite run completed');
    tr.appendChild(tdDate);

    // Status badge with icon
    var tdStatus = document.createElement('td');
    var st  = (r.status || 'UNKNOWN').toUpperCase();
    var cls = st === 'PASS' ? 'pass' : st === 'FAIL' ? 'fail' : 'partial';
    var badge = document.createElement('span');
    badge.className = 'badge badge-' + cls;
    var iconSpan = document.createElement('span');
    iconSpan.textContent = cls === 'pass' ? '\u2713' : cls === 'fail' ? '\u2717' : '\u26A0';
    badge.appendChild(iconSpan);
    badge.appendChild(document.createTextNode(' ' + st));
    if (cls === 'pass') {
      badge.setAttribute('data-tooltip', 'All tests passed or were skipped');
    } else if (cls === 'fail') {
      badge.setAttribute('data-tooltip', 'One or more tests failed');
    } else {
      badge.setAttribute('data-tooltip', 'Some tests passed and some failed');
    }
    tdStatus.appendChild(badge);
    tr.appendChild(tdStatus);

    // Tests
    var tdTests = document.createElement('td');
    tdTests.textContent = (r.pass_count || 0) + '/' + (r.total_count || 0) + ' passed';
    tr.appendChild(tdTests);

    // Report link + archive icon
    var tdReport = document.createElement('td');
    if (r.report_url) {
      var a = document.createElement('a');
      a.className = 'report-link';
      a.textContent = 'View Report';
      a.href = r.report_url;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      tdReport.appendChild(a);
    } else {
      tdReport.textContent = '\u2014';
    }
    if (r.archive_path) {
      var archiveIcon = document.createElement('span');
      archiveIcon.textContent = ' \uD83D\uDD12';
      archiveIcon.setAttribute('data-tooltip', 'This run is permanently archived with a tamper-evident SHA-256 manifest');
      tdReport.appendChild(archiveIcon);
    }
    tr.appendChild(tdReport);

    // vs Baseline cell — filled in asynchronously by _fetchBaselineStatuses()
    var tdBaseline = document.createElement('td');
    tdBaseline.className = 'td-baseline';
    tdBaseline.style.display = localStorage.getItem('valdo_baseline_col_visible') === 'true' ? '' : 'none';
    tdBaseline.textContent = '\u2014';
    tdBaseline.setAttribute('data-run-id', r.run_id || r.id || '');
    tdBaseline.setAttribute('data-suite', r.suite_name || '');
    tr.appendChild(tdBaseline);

    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
}

/**
 * Fetch run history from the API and render it into the runs table.
 * Displays a loading message while the request is in-flight and an error
 * message if the request fails.
 *
 * @returns {Promise<void>}
 */
async function loadRunHistory() {
  var wrap = document.getElementById('runsTableWrap');
  while (wrap.firstChild) { wrap.removeChild(wrap.firstChild); }
  var loadingP = document.createElement('p');
  loadingP.className = 'empty-msg';
  loadingP.textContent = 'Loading run history\u2026';
  wrap.appendChild(loadingP);
  try {
    var resp = await fetch('/api/v1/runs/history');
    if (!resp.ok) { throw new Error('HTTP ' + resp.status); }
    _runsData = await resp.json();
    buildRunsTable(_runsData);
    _fetchBaselineStatuses().then(_attachDeviationClickHandlers);
    // Populate trend chart suite selector with unique suite names (#249)
    var _suiteNames = Array.from(new Set(
      _runsData.map(function(r) { return r.suite_name || r.suite || ''; }).filter(Boolean)
    )).sort();
    _populateTrendSuiteSelector(_suiteNames);
  } catch (err) {
    while (wrap.firstChild) { wrap.removeChild(wrap.firstChild); }
    var p = document.createElement('p');
    p.className = 'empty-msg';
    p.textContent = 'Could not load run history: ' + err.message;
    wrap.appendChild(p);
  }
}

// ===========================================================================
// Auto-refresh toggle
// ===========================================================================
/**
 * Toggle the auto-refresh interval for the run history table on or off.
 * When enabled, reloads the table every 30 seconds.
 */
function toggleAutoRefresh() {
  var toggle = document.getElementById('autoRefreshToggle');
  var label  = document.getElementById('refreshToggleLabel');
  if (_autoRefreshTimer) {
    clearInterval(_autoRefreshTimer);
    _autoRefreshTimer = null;
    toggle.classList.remove('active');
    label.textContent = 'Auto-refresh off';
  } else {
    _autoRefreshTimer = setInterval(function() { loadRunHistory(); loadTrendChart(); loadSummaryCards(); }, 30000);
    toggle.classList.add('active');
    label.textContent = 'Auto-refresh on (30s)';
  }
}

// ===========================================================================
// Trend Chart — fetch + wiring (#249)
// ===========================================================================
/**
 * Set the active day-range for the trend chart and reload it.
 *
 * Updates button active state and triggers a fresh fetch.
 *
 * @param {number} days - Number of days to display (7, 14, or 30).
 */
function setTrendDays(days) {
  _trendDays = days;
  document.querySelectorAll('.trend-day-btn').forEach(function(btn) {
    var isActive = parseInt(btn.getAttribute('data-days')) === days;
    btn.classList.toggle('active', isActive);
    btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
  });
  loadTrendChart();
}

/**
 * Fetch trend data from the API and render it into the #trendsChart container.
 *
 * Uses the current values of _trendDays and the #trendSuiteSelect element.
 * Safe to call before the DOM element exists — returns early if container is absent.
 */
function loadTrendChart() {
  var container = document.getElementById('trendsChart');
  if (!container) { return; }

  var suite = document.getElementById('trendSuiteSelect') ?
    document.getElementById('trendSuiteSelect').value : '';

  var url = '/api/v1/runs/trend?days=' + _trendDays;
  if (suite) { url += '&suite=' + encodeURIComponent(suite); }

  container.innerHTML = '<span style="font-size:12px;color:var(--text-secondary);">Loading\u2026</span>';

  fetch(url, { headers: window._apiKey ? { 'X-API-Key': window._apiKey } : {} })
    .then(function(r) { return r.ok ? r.json() : []; })
    .then(function(data) { renderTrendChart(data, container); })
    .catch(function() {
      container.innerHTML = '<span style="font-size:12px;color:var(--fail);">Failed to load trend data.</span>';
    });
}

/**
 * Populate the suite filter <select> in the trend chart section.
 *
 * Always preserves the leading "All suites" option and rebuilds the rest
 * from the provided list.
 *
 * @param {string[]} suites - Unique suite names extracted from run history.
 */
function _populateTrendSuiteSelector(suites) {
  var sel = document.getElementById('trendSuiteSelect');
  if (!sel) { return; }
  sel.innerHTML = '<option value="">All suites</option>';
  (suites || []).forEach(function(s) {
    var opt = document.createElement('option');
    opt.value = s;
    opt.textContent = s;
    sel.appendChild(opt);
  });
}

// ===========================================================================
// Trend Chart — SVG renderer
// ===========================================================================
/**
 * Render a pass-rate / quality-score trend line chart into a container element.
 *
 * The function is a pure DOM-manipulation renderer — it performs no fetch calls
 * and has no side-effects beyond writing into `container`.
 *
 * Testability note: renderTrendChart is a pure DOM-manipulation function.
 * Verified by: node --check src/reports/static/ui.js (syntax check)
 * Manual test: open /ui in browser, check Recent Runs tab after chart is wired in (#249).
 *
 * @param {Array<{date: string, pass_rate: number, avg_quality_score: number|null}>} data
 *   Array of daily trend objects, ordered oldest → newest.
 * @param {HTMLElement} container - DOM element into which the SVG chart is injected.
 */
function renderTrendChart(data, container) {
  // Clear container
  container.innerHTML = '';

  if (!data || data.length === 0) {
    var emptyMsg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    emptyMsg.setAttribute('width', '100%');
    emptyMsg.setAttribute('height', '120');
    emptyMsg.setAttribute('role', 'img');
    emptyMsg.setAttribute('aria-label', 'No trend data available');
    var txt = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    txt.setAttribute('x', '50%');
    txt.setAttribute('y', '60');
    txt.setAttribute('text-anchor', 'middle');
    txt.setAttribute('fill', 'var(--text-secondary)');
    txt.setAttribute('font-size', '13');
    txt.textContent = 'No trend data yet \u2014 run some suites to see history';
    emptyMsg.appendChild(txt);
    container.appendChild(emptyMsg);
    return;
  }

  var W = container.clientWidth || 600;
  var H = 180;
  var PAD = { top: 16, right: 80, bottom: 32, left: 40 };
  var chartW = W - PAD.left - PAD.right;
  var chartH = H - PAD.top - PAD.bottom;

  var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('width', W);
  svg.setAttribute('height', H);
  svg.setAttribute('role', 'img');

  // Compute pass rate range for aria-label
  var passRates = data.map(function(d) { return d.pass_rate || 0; });
  var first = Math.round(passRates[0]);
  var last = Math.round(passRates[passRates.length - 1]);
  svg.setAttribute('aria-label', 'Pass rate trend: ' + first + '% to ' + last + '% over ' + data.length + ' days');

  var g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  g.setAttribute('transform', 'translate(' + PAD.left + ',' + PAD.top + ')');

  // Y-axis reference lines at 0, 25, 50, 75, 100
  [0, 25, 50, 75, 100].forEach(function(val) {
    var y = chartH - (val / 100) * chartH;
    var line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line.setAttribute('x1', 0); line.setAttribute('x2', chartW);
    line.setAttribute('y1', y); line.setAttribute('y2', y);
    line.setAttribute('stroke', 'var(--border, #e0e0e0)');
    line.setAttribute('stroke-width', '1');
    g.appendChild(line);

    var label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    label.setAttribute('x', -4); label.setAttribute('y', y + 4);
    label.setAttribute('text-anchor', 'end');
    label.setAttribute('font-size', '10');
    label.setAttribute('fill', 'var(--text-secondary, #888)');
    label.textContent = val + '%';
    g.appendChild(label);
  });

  function makePolyline(values, color) {
    var n = data.length;
    var points = values.map(function(v, i) {
      var x = (i / Math.max(n - 1, 1)) * chartW;
      var y = chartH - (Math.min(Math.max(v || 0, 0), 100) / 100) * chartH;
      return x + ',' + y;
    }).join(' ');
    var pl = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
    pl.setAttribute('points', points);
    pl.setAttribute('fill', 'none');
    pl.setAttribute('stroke', color);
    pl.setAttribute('stroke-width', '2');
    pl.setAttribute('stroke-linejoin', 'round');
    return pl;
  }

  // Pass rate line
  g.appendChild(makePolyline(data.map(function(d){ return d.pass_rate; }), 'var(--accent, #2563eb)'));
  // Quality score line
  var qualScores = data.map(function(d){ return d.avg_quality_score; });
  if (qualScores.some(function(v){ return v != null; })) {
    g.appendChild(makePolyline(qualScores, 'var(--text-secondary, #888)'));
  }

  // X-axis tick labels every 7 points
  var step = Math.max(1, Math.floor(data.length / 5));
  data.forEach(function(d, i) {
    if (i % step !== 0 && i !== data.length - 1) return;
    var x = (i / Math.max(data.length - 1, 1)) * chartW;
    var datePart = (d.date || '').slice(5); // MM-DD
    var tick = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    tick.setAttribute('x', x);
    tick.setAttribute('y', chartH + 14);
    tick.setAttribute('text-anchor', 'middle');
    tick.setAttribute('font-size', '9');
    tick.setAttribute('fill', 'var(--text-secondary, #888)');
    tick.textContent = datePart;
    g.appendChild(tick);
  });

  // Legend
  var legY = PAD.top / 2;
  var legItems = [
    { color: 'var(--accent, #2563eb)', label: 'Pass rate' },
  ];
  if (qualScores.some(function(v){ return v != null; })) {
    legItems.push({ color: 'var(--text-secondary, #888)', label: 'Quality score' });
  }
  legItems.forEach(function(item, idx) {
    var lx = PAD.left + idx * 110;
    var rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    rect.setAttribute('x', lx); rect.setAttribute('y', legY - 6);
    rect.setAttribute('width', 20); rect.setAttribute('height', 3);
    rect.setAttribute('fill', item.color);
    svg.appendChild(rect);
    var lt = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    lt.setAttribute('x', lx + 24); lt.setAttribute('y', legY);
    lt.setAttribute('font-size', '10');
    lt.setAttribute('fill', 'var(--text-secondary, #888)');
    lt.textContent = item.label;
    svg.appendChild(lt);
  });

  svg.appendChild(g);
  container.appendChild(svg);
}

// ===========================================================================
// Suite summary cards (#252)
// ===========================================================================
/**
 * Fetch suite summaries from the API and render them as dashboard cards.
 *
 * Safe to call before the container element exists — returns early when absent.
 */
function loadSummaryCards() {
  var container = document.getElementById('suiteSummaryCards');
  if (!container) return;

  fetch('/api/v1/runs/summaries', {
    headers: window._apiKey ? { 'X-API-Key': window._apiKey } : {}
  })
  .then(function(r) { return r.ok ? r.json() : []; })
  .then(function(summaries) { _renderSummaryCards(summaries, container); })
  .catch(function() { container.textContent = ''; });
}

/**
 * Render suite summary cards into the given container element.
 *
 * Displays up to 8 cards in a responsive grid. Each card shows the suite name,
 * last-run status badge, relative time, 30-day pass rate, and trend arrow.
 * Clicking a card sets the trendSuiteSelect filter and reloads the trend chart.
 *
 * @param {Array<Object>} summaries - Array of suite summary objects from the API.
 * @param {HTMLElement} container - The DOM element to render cards into.
 */
function _renderSummaryCards(summaries, container) {
  container.innerHTML = '';

  if (!summaries || summaries.length === 0) {
    var empty = document.createElement('p');
    empty.style.fontSize = '13px';
    empty.style.color = 'var(--text-secondary)';
    empty.textContent = 'No suite history yet.';
    container.appendChild(empty);
    return;
  }

  var grid = document.createElement('div');
  grid.style.cssText = 'display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px;';

  var MAX_CARDS = 8;
  var shown = summaries.slice(0, MAX_CARDS);

  shown.forEach(function(s) {
    var card = document.createElement('div');
    card.style.cssText = 'border:1px solid var(--border);border-radius:6px;padding:10px 12px;cursor:pointer;background:var(--bg-secondary);';
    card.setAttribute('role', 'button');
    card.setAttribute('tabindex', '0');
    card.setAttribute('data-suite', s.suite_name);

    // Suite name
    var nameEl = document.createElement('div');
    nameEl.style.cssText = 'font-weight:600;font-size:13px;margin-bottom:6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
    nameEl.textContent = s.suite_name;
    card.appendChild(nameEl);

    // Status badge
    var badge = document.createElement('span');
    var isPass = s.last_run_status === 'PASS';
    badge.style.cssText = 'font-size:10px;font-weight:700;padding:1px 6px;border-radius:3px;' +
      (isPass ? 'background:#dcfce7;color:#16a34a;' : 'background:#fee2e2;color:#dc2626;');
    badge.textContent = s.last_run_status;
    card.appendChild(badge);

    // Relative time
    var timeEl = document.createElement('div');
    timeEl.style.cssText = 'font-size:11px;color:var(--text-secondary);margin-top:4px;';
    timeEl.textContent = _relativeTime(s.last_run_at);
    card.appendChild(timeEl);

    // Pass rate and trend
    var statsEl = document.createElement('div');
    statsEl.style.cssText = 'font-size:11px;margin-top:6px;display:flex;gap:8px;';
    var prEl = document.createElement('span');
    prEl.textContent = (s.pass_rate_30d || 0) + '% pass';
    var trendEl = document.createElement('span');
    var arrow = s.trend_direction === 'up' ? '\u2191' : s.trend_direction === 'down' ? '\u2193' : '\u2192';
    var arrowColor = s.trend_direction === 'up' ? '#16a34a' : s.trend_direction === 'down' ? '#dc2626' : 'var(--text-secondary)';
    trendEl.style.color = arrowColor;
    trendEl.textContent = arrow;
    statsEl.appendChild(prEl);
    statsEl.appendChild(trendEl);
    card.appendChild(statsEl);

    // Click to filter trend chart
    card.addEventListener('click', function() {
      var sel = document.getElementById('trendSuiteSelect');
      if (sel) {
        sel.value = s.suite_name;
        if (typeof loadTrendChart === 'function') loadTrendChart();
      }
    });

    grid.appendChild(card);
  });

  container.appendChild(grid);
}

// ===========================================================================
// Baseline column toggle and deviation fetch — issue #253
// ===========================================================================

/**
 * Toggle the 'vs Baseline' column visibility in the Recent Runs table and
 * persist the preference in localStorage.
 */
function toggleBaselineColumn() {
  var visible = localStorage.getItem('valdo_baseline_col_visible') === 'true';
  visible = !visible;
  localStorage.setItem('valdo_baseline_col_visible', visible);
  _applyBaselineColVisibility();
}

/**
 * Apply the stored baseline column visibility preference to the table header
 * and all baseline cells.  Safe to call before the table is rendered — it is
 * a no-op when the relevant elements are absent.
 */
function _applyBaselineColVisibility() {
  var visible = localStorage.getItem('valdo_baseline_col_visible') === 'true';
  var th = document.getElementById('thBaseline');
  var btn = document.getElementById('btnToggleBaselineCol');
  if (th) { th.style.display = visible ? '' : 'none'; }
  document.querySelectorAll('.td-baseline').forEach(function(td) {
    td.style.display = visible ? '' : 'none';
  });
  if (btn) { btn.textContent = visible ? 'Hide baseline' : 'Show baseline'; }
}

/**
 * Fetch deviation status from the baseline-check API for every baseline cell
 * currently in the table and update each cell's text and style in-place.
 *
 * Requests are fired in parallel.  Cells with no run_id or suite are left
 * unchanged.  Network errors are silently swallowed so the table remains
 * functional when the baseline service is unavailable.
 *
 * @returns {Promise<void[]>}
 */
function _fetchBaselineStatuses() {
  var cells = document.querySelectorAll('.td-baseline[data-run-id]');
  var promises = Array.from(cells).map(function(td) {
    var runId = td.getAttribute('data-run-id');
    var suite = td.getAttribute('data-suite');
    if (!runId || !suite) { return Promise.resolve(); }
    return fetch(
      '/api/v1/runs/baseline-check?suite=' + encodeURIComponent(suite) +
      '&run_id=' + encodeURIComponent(runId),
      { headers: { 'X-API-Key': window._apiKey || '' } }
    )
    .then(function(r) { return r.ok ? r.json() : null; })
    .then(function(data) {
      if (!data) { td.textContent = '\u2014'; return; }
      if (data.reason === 'no_baseline') {
        td.textContent = '\u2014';
      } else if (data.deviated) {
        var firstAlert = data.alerts && data.alerts[0];
        var delta = firstAlert
          ? ' (' + (firstAlert.delta > 0 ? '+' : '') + Math.round(firstAlert.delta) + '%)'
          : '';
        td.textContent = '\u26A0\uFE0F Deviated' + delta;
        td.style.color = 'var(--error, #dc2626)';
        td.setAttribute(
          'title',
          'Threshold: ' + (firstAlert
            ? firstAlert.metric + ' drop > ' + firstAlert.threshold + '%'
            : '')
        );
        td.style.cursor = 'pointer';
        td.setAttribute('data-deviation', JSON.stringify(data));
      } else {
        td.textContent = '\u2713 Within baseline';
        td.style.color = 'var(--success, #16a34a)';
      }
    })
    .catch(function() { td.textContent = '\u2014'; });
  });
  return Promise.all(promises);
}

/**
 * Attach click handlers to deviation badge cells so that clicking a warning cell
 * expands an inline detail row showing deviation metrics (Metric, Baseline,
 * Current, Delta, Threshold).  Only cells with a `data-deviation` attribute
 * (set by _fetchBaselineStatuses) are wired.
 */
function _attachDeviationClickHandlers() {
  document.querySelectorAll('.td-baseline[data-deviation]').forEach(function(td) {
    td.addEventListener('click', function() {
      var row = td.closest('tr');
      if (!row) return;

      // Close any existing open detail row
      var existing = document.querySelector('tr.deviation-detail-row');
      if (existing) {
        var prevRow = existing.previousElementSibling;
        if (prevRow === row) {
          // Toggle off — same cell clicked again
          existing.remove();
          td.setAttribute('aria-expanded', 'false');
          return;
        }
        // Close the other row's aria state
        if (prevRow) {
          var prevTd = prevRow.querySelector('.td-baseline');
          if (prevTd) { prevTd.setAttribute('aria-expanded', 'false'); }
        }
        existing.remove();
      }

      var deviationData = JSON.parse(td.getAttribute('data-deviation') || '{}');
      var alerts = deviationData.alerts || [];

      var detailRow = document.createElement('tr');
      detailRow.className = 'deviation-detail-row';

      var colCount = row.cells.length;
      var detailTd = document.createElement('td');
      detailTd.setAttribute('colspan', colCount);
      detailTd.style.cssText = 'padding:8px 16px;background:var(--bg-secondary);border-bottom:1px solid var(--border);';

      if (alerts.length === 0) {
        detailTd.textContent = 'No deviation details available.';
      } else {
        var table = document.createElement('table');
        table.style.cssText = 'font-size:11px;border-collapse:collapse;width:auto;';

        // Header row
        var thead = document.createElement('thead');
        var hrow = document.createElement('tr');
        ['Metric', 'Baseline', 'Current', 'Delta', 'Threshold'].forEach(function(h) {
          var th = document.createElement('th');
          th.style.cssText = 'padding:3px 10px;text-align:left;color:var(--text-secondary);font-weight:600;border-bottom:1px solid var(--border);';
          th.textContent = h;
          hrow.appendChild(th);
        });
        thead.appendChild(hrow);
        table.appendChild(thead);

        // Data rows
        var tbody = document.createElement('tbody');
        alerts.forEach(function(alert) {
          var tr = document.createElement('tr');
          var deltaStr = (alert.delta > 0 ? '+' : '') + Math.round(alert.delta * 10) / 10 + '%';
          var deltaColor = alert.delta < 0 ? 'var(--error, #dc2626)' : 'var(--success, #16a34a)';
          [
            alert.metric,
            Math.round(alert.baseline_value * 10) / 10 + '%',
            Math.round(alert.current_value * 10) / 10 + '%',
            deltaStr,
            alert.metric + ' drop > ' + alert.threshold + '%',
          ].forEach(function(val, idx) {
            var td2 = document.createElement('td');
            td2.style.cssText = 'padding:3px 10px;';
            if (idx === 3) { td2.style.color = deltaColor; }
            td2.textContent = val;
            tr.appendChild(td2);
          });
          tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        detailTd.appendChild(table);
      }

      detailRow.appendChild(detailTd);
      row.parentNode.insertBefore(detailRow, row.nextSibling);
      td.setAttribute('aria-expanded', 'true');
    });
  });
}

// ===========================================================================
// Mapping Generator — state
// ===========================================================================
var mapFile   = null;
var rulesFile = null;

// ===========================================================================
// Mapping Generator — step progress
// ===========================================================================
/**
 * Update the mapping generator step-progress indicator.
 *
 * Steps 1–3 map to: (1) upload, (2) configure, (3) done.
 * Steps before *step* are marked 'done'; the current step is marked 'active'.
 *
 * @param {number} step - Active step number (1, 2, or 3).
 */
function setMapStep(step) {
  for (var i = 1; i <= 3; i++) {
    var el = document.getElementById('mapStep' + i);
    if (!el) continue;
    el.classList.remove('active', 'done');
    if (i < step) el.classList.add('done');
    else if (i === step) el.classList.add('active');
  }
}

// ===========================================================================
// Copy JSON helper
// ===========================================================================
/**
 * Copy the JSON content of a preview element to the clipboard.
 * Briefly shows 'Copied!' feedback on the trigger button.
 *
 * @param {string} previewId - DOM id of the `<pre>` element containing JSON text.
 * @param {string} btnId     - DOM id of the copy button to update with feedback.
 */
function copyJson(previewId, btnId) {
  var pre = document.getElementById(previewId);
  var btn = document.getElementById(btnId);
  if (!pre || !btn) return;
  var text = pre.textContent || pre.innerText || '';
  if (!text || text === 'No mapping generated yet.' || text === 'No rules generated yet.') { return; }
  if (navigator.clipboard) {
    navigator.clipboard.writeText(text).then(function() {
      btn.textContent = 'Copied!';
      btn.classList.add('copied');
      setTimeout(function() {
        btn.textContent = 'Copy JSON';
        btn.classList.remove('copied');
      }, 2000);
    }).catch(function() {});
  }
}

// ===========================================================================
// JSON preview syntax highlighting (safe: & < > escaped before innerHTML)
// ===========================================================================
/**
 * Apply syntax-highlight span tags to a JSON string for the mapping preview.
 * All token content is HTML-escaped before being wrapped in `<span>` tags
 * to prevent XSS when the result is written to innerHTML.
 *
 * @param {string} str - Pretty-printed JSON string.
 * @returns {string} HTML string with colour spans for keys, strings, numbers,
 *     booleans, and null values.
 */
function highlightJsonPreview(str) {
  return str.replace(
    /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g,
    function(match) {
      var safe = match.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      var cls  = 'jp-num';
      if (/^"/.test(match))              { cls = /:$/.test(match) ? 'jp-key' : 'jp-str'; }
      else if (/true|false/.test(match)) { cls = 'jp-bool'; }
      else if (/null/.test(match))       { cls = 'jp-null'; }
      return '<span class="' + cls + '">' + safe + '</span>';
    }
  );
}

/**
 * Pretty-print and syntax-highlight a JSON value into a preview `<pre>` element.
 *
 * @param {string} previewId - DOM id of the target `<pre>` element.
 * @param {*}      jsonData  - Value to serialise and display.
 */
function renderJsonPreview(previewId, jsonData) {
  var pre = document.getElementById(previewId);
  if (!pre) return;
  try {
    var pretty = JSON.stringify(jsonData, null, 2);
    /* safe: highlightJsonPreview escapes & < > in every token before setting innerHTML */
    pre.innerHTML = highlightJsonPreview(pretty); // safe-innerHTML: sanitized by highlightJsonPreview
    pre.classList.add('has-content');
  } catch (_) {
    pre.textContent = String(jsonData);
    pre.classList.remove('has-content');
  }
}

/**
 * Render a field-summary table for the mapping generator results panel.
 * Hides the container if no fields are provided.
 *
 * @param {Array<Object>} fields - Field objects from the mapping response,
 *     expected to have `field_name`, `data_type`, `start_position`, `length`.
 */
function renderFieldSummary(fields) {
  var container = document.getElementById('mapFieldSummary');
  var tbody = document.getElementById('mapFieldSummaryBody');
  if (!fields || !fields.length || !container || !tbody) { if (container) container.style.display = 'none'; return; }
  while (tbody.firstChild) { tbody.removeChild(tbody.firstChild); }
  fields.forEach(function(f) {
    var tr = document.createElement('tr');
    ['field_name','data_type','start_position','length'].forEach(function(key) {
      var td = document.createElement('td');
      td.textContent = f[key] !== undefined ? String(f[key]) : '\u2014';
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  container.style.display = '';
}

// ===========================================================================
// Mapping Generator — result bar helper
// ===========================================================================
/**
 * Update a generator result bar with a status message and optional action links.
 *
 * @param {string}          barId - DOM id of the result bar element.
 * @param {string}          type  - CSS modifier: 'loading', 'success', or 'error'.
 * @param {string}          msg   - Text message to display.
 * @param {Array<Object>|null} links - Optional array of link config objects, each
 *     with { text, href?, download?, onClick?, tooltip? }.
 */
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
      a.href = lnk.href || '#';
      if (lnk.download) { a.setAttribute('download', lnk.download); }
      if (lnk.onClick)  { a.addEventListener('click', lnk.onClick); }
      if (lnk.tooltip)  { a.setAttribute('data-tooltip', lnk.tooltip); }
      el.appendChild(a);
    });
  }
}

// ---------------------------------------------------------------------------
// Mapping Generator — drop zones
// ===========================================================================
// Mapping Generator — drop zones
// ===========================================================================
/**
 * Attach drag-and-drop and click-to-browse handlers for a Mapping Generator
 * upload zone.
 *
 * @param {string} zoneId  - DOM id of the drop zone container.
 * @param {string} inputId - DOM id of the hidden `<input type="file">` element.
 * @param {string} slot    - File slot identifier: 'map' or 'rules'.
 */
function setupGenDrop(zoneId, inputId, slot) {
  var zone   = document.getElementById(zoneId);
  var input  = document.getElementById(inputId);
  var nameId = slot === 'map' ? 'mapFileName'  : 'rulesFileName';
  var btnId  = slot === 'map' ? 'btnGenMapping' : 'btnGenRules';

  zone.addEventListener('dragover',  function(e) { e.preventDefault(); zone.classList.add('dragover'); });
  zone.addEventListener('dragleave', function()  { zone.classList.remove('dragover'); });
  zone.addEventListener('drop', function(e) {
    e.preventDefault(); zone.classList.remove('dragover');
    var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) { setGenFile(f, slot, nameId, btnId); }
  });
  zone.addEventListener('click',   function()  { input.click(); });
  zone.addEventListener('keydown', function(e) { if (e.key === 'Enter' || e.key === ' ') { input.click(); } });
  input.addEventListener('change', function()  { if (this.files.length) { setGenFile(this.files[0], slot, nameId, btnId); } });
}

/**
 * Store a selected generator file and update the associated filename label and
 * generate button state.
 *
 * @param {File}   file   - The selected File object.
 * @param {string} slot   - 'map' or 'rules'.
 * @param {string} nameId - DOM id of the filename label element.
 * @param {string} btnId  - DOM id of the generate button to enable.
 */
function setGenFile(file, slot, nameId, btnId) {
  document.getElementById(nameId).textContent = file.name;
  document.getElementById(btnId).disabled = false;
  if (slot === 'map')   { mapFile   = file; setMapStep(2); }
  if (slot === 'rules') { rulesFile = file; }
}

setupGenDrop('mapDropZone',   'mapFileInput',   'map');
setupGenDrop('rulesDropZone', 'rulesFileInput', 'rules');

// ---------------------------------------------------------------------------
// Mapping Generator — Generate Mapping button
// ---------------------------------------------------------------------------
document.getElementById('btnGenMapping').addEventListener('click', async function() {
  if (!mapFile) { return; }
  setGenResult('mapResultBar', 'loading', 'Generating mapping\u2026', null);
  setMapStep(3);
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
    if (data.mapping_content) { renderJsonPreview('mapJsonPreview', data.mapping_content); }
    if (data.fields) { renderFieldSummary(data.fields); }
    setGenResult('mapResultBar', 'success', '\u2705 \u2018' + mid + '\u2019 created\u00a0\u00a0', [
      { text: 'Download JSON', href: '/api/v1/mappings/' + encodeURIComponent(mid), download: mid + '.json', tooltip: 'Download the generated JSON config to your machine' },
      { text: 'Use in Quick Test \u2192', tooltip: 'Load this config into the Quick Test tab and switch to it', onClick: async function(e) {
          e.preventDefault();
          await loadMappings();
          var sel = document.getElementById('mappingSelect');
          for (var i = 0; i < sel.options.length; i++) {
            if (sel.options[i].value === mid) { sel.selectedIndex = i; break; }
          }
          switchTab('quick');
      }}
    ]);
  } catch (err) {
    setGenResult('mapResultBar', 'error', 'Error: ' + err.message, null);
    setMapStep(2);
  } finally {
    document.getElementById('btnGenMapping').disabled = false;
  }
});

// ---------------------------------------------------------------------------
// Mapping Generator — Generate Rules button
// ---------------------------------------------------------------------------
document.getElementById('btnGenRules').addEventListener('click', async function() {
  if (!rulesFile) { return; }
  setGenResult('rulesResultBar', 'loading', 'Generating rules\u2026', null);
  this.disabled = true;
  try {
    var rulesName = document.getElementById('rulesNameInput').value.trim();
    var rulesType = document.getElementById('rulesTypeSelect').value;
    var url = '/api/v1/rules/upload?rules_type=' + encodeURIComponent(rulesType);
    if (rulesName) { url += '&rules_name=' + encodeURIComponent(rulesName); }
    var fd = new FormData();
    fd.append('file', rulesFile);
    var resp = await fetch(url, { method: 'POST', body: fd });
    if (!resp.ok) {
      var err = await resp.json().catch(function() { return { detail: resp.statusText }; });
      throw new Error(err.detail || resp.statusText);
    }
    var data = await resp.json();
    var rid  = data.rules_id;
    if (data.rules_content) { renderJsonPreview('rulesJsonPreview', data.rules_content); }
    setGenResult('rulesResultBar', 'success', '\u2705 \u2018' + rid + '\u2019 created\u00a0\u00a0', [
      { text: 'Download JSON', href: '/api/v1/rules/' + encodeURIComponent(rid) + '.json', download: rid + '.json', tooltip: 'Download the generated JSON config to your machine' },
      { text: 'Use in Quick Test \u2192', tooltip: 'Load these rules into the Quick Test tab and switch to it', onClick: async function(e) {
          e.preventDefault();
          await loadRules();
          var sel = document.getElementById('rulesSelect');
          for (var i = 0; i < sel.options.length; i++) {
            if (sel.options[i].value === rid) { sel.selectedIndex = i; break; }
          }
          switchTab('quick');
      }}
    ]);
  } catch (err) {
    setGenResult('rulesResultBar', 'error', 'Error: ' + err.message, null);
  } finally {
    document.getElementById('btnGenRules').disabled = false;
  }
});

// ---------------------------------------------------------------------------
// === Multi-Record Config Wizard ===
// ---------------------------------------------------------------------------
var _mrStep          = 1;    // current step 1-5
var _mrMappings      = [];   // [{id, label}] from API
var _mrSelected      = [];   // selected mapping ids
var _mrDiscriminator = {};   // {field, position, length}
var _mrRecordTypes   = {};   // {mappingId: {code, first_row, last_row, expect}}
var _mrCrossRules    = [];   // [{type, ...fields}]
var _mrYamlText      = null; // last generated YAML
var _mrPendingYaml   = null; // YAML to pre-load into Quick Test

function mrToggle() {
  var body = document.getElementById('mrWizardBody');
  var btn  = document.getElementById('mrToggleBtn');
  var open = body.style.display !== 'none';
  if (open) {
    body.style.display = 'none';
    btn.textContent = 'Start Wizard';
    btn.setAttribute('aria-expanded', 'false');
  } else {
    body.style.display = '';
    btn.textContent = 'Close Wizard';
    btn.setAttribute('aria-expanded', 'true');
    mrInit();
  }
}

function mrInit() {
  _mrStep = 1;
  _mrSelected      = [];
  _mrDiscriminator = {};
  _mrRecordTypes   = {};
  _mrCrossRules    = [];
  _mrYamlText      = null;
  mrPopulateMappingList();
  mrGoTo(1);
}

function mrGoTo(n) {
  _mrStep = n;
  document.querySelectorAll('.mr-step-panel').forEach(function(p) { p.classList.remove('active'); });
  var target = document.getElementById('mrStep' + n);
  if (target) target.classList.add('active');
  document.querySelectorAll('.step-indicator').forEach(function(ind) {
    var s = parseInt(ind.dataset.step, 10);
    ind.classList.toggle('active', s === n);
    ind.setAttribute('aria-selected', s === n ? 'true' : 'false');
  });
  document.getElementById('mrBackBtn').style.display = n > 1 ? '' : 'none';
  var nextBtn = document.getElementById('mrNextBtn');
  if (n === 5) {
    nextBtn.style.display = 'none';
  } else {
    nextBtn.style.display = '';
    nextBtn.textContent = n === 4 ? 'Finish \u2192' : 'Next \u2192';
  }
}

function mrNext() {
  if (_mrStep === 1) {
    _mrSelected = [];
    document.querySelectorAll('#mrMappingList input[type="checkbox"]:checked').forEach(function(cb) {
      _mrSelected.push(cb.value);
    });
    if (_mrSelected.length === 0) {
      document.getElementById('mrStep1Error').textContent = 'Please select at least one record type.';
      return;
    }
    document.getElementById('mrStep1Error').textContent = '';
    mrRenderTypeRows();
    mrGoTo(2);
  } else if (_mrStep === 2) {
    var field = document.getElementById('mrDiscField').value.trim();
    var pos   = parseInt(document.getElementById('mrDiscPosition').value, 10);
    var len   = parseInt(document.getElementById('mrDiscLength').value, 10);
    if (!field || isNaN(pos) || pos < 1 || isNaN(len) || len < 1) {
      document.getElementById('mrStep2Error').textContent = 'Please fill in Field Name, Position, and Length.';
      return;
    }
    document.getElementById('mrStep2Error').textContent = '';
    _mrDiscriminator = { field: field, position: pos, length: len };
    mrGoTo(3);
  } else if (_mrStep === 3) {
    var err3 = '';
    _mrSelected.forEach(function(id) {
      var codeEl = document.getElementById('mrCode_' + id);
      if (!codeEl || !codeEl.value.trim()) { err3 = 'Please enter a discriminator code for every record type.'; }
    });
    if (err3) { document.getElementById('mrStep3Error').textContent = err3; return; }
    document.getElementById('mrStep3Error').textContent = '';
    _mrSelected.forEach(function(id) {
      _mrRecordTypes[id] = {
        code:      document.getElementById('mrCode_' + id).value.trim(),
        first_row: (document.getElementById('mrFirstRow_' + id) || {}).value || '',
        last_row:  (document.getElementById('mrLastRow_' + id) || {}).value || '',
        expect:    (document.getElementById('mrExpect_' + id) || {}).value || '',
      };
    });
    mrGoTo(4);
  } else if (_mrStep === 4) {
    mrGoTo(5);
  }
}

function mrBack() { if (_mrStep > 1) mrGoTo(_mrStep - 1); }

// -- Step 1: mapping checklist --

function mrPopulateMappingList() {
  var container = document.getElementById('mrMappingList');
  container.textContent = 'Loading mappings\u2026';
  if (_allMappingOptions && _allMappingOptions.length > 0) {
    _mrRenderMappingCheckboxes(_allMappingOptions);
    return;
  }
  fetch('/api/v1/mappings/', { headers: _apiHeaders() })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var opts = (data.mappings || data || []).map(function(m) {
        return { value: m.id || m.value, label: m.label || m.name || m.id };
      });
      _mrRenderMappingCheckboxes(opts);
    })
    .catch(function() { container.textContent = 'Failed to load mappings. Please refresh and try again.'; });
}

function _mrRenderMappingCheckboxes(opts) {
  var container = document.getElementById('mrMappingList');
  if (!opts || opts.length === 0) {
    container.textContent = 'No mappings found. Upload a mapping template first.';
    return;
  }
  while (container.firstChild) container.removeChild(container.firstChild);
  opts.forEach(function(opt) {
    var lbl = document.createElement('label');
    var cb  = document.createElement('input');
    cb.type  = 'checkbox';
    cb.value = opt.value;
    cb.id    = 'mrMapCb_' + opt.value;
    var txt  = document.createTextNode(' ' + opt.label);  // plain text — no XSS
    lbl.appendChild(cb);
    lbl.appendChild(txt);
    container.appendChild(lbl);
  });
}

// -- Step 2: auto-detect discriminator --

function mrAutoDetect() {
  var fileInput = document.getElementById('mrAutoDetectFile');
  var status    = document.getElementById('mrAutoDetectStatus');
  var btn       = document.getElementById('mrAutoDetectBtn');
  if (!fileInput.files || fileInput.files.length === 0) {
    status.textContent = 'Please select a file first.';
    return;
  }
  status.textContent = 'Detecting\u2026';
  btn.disabled = true;
  var formData = new FormData();
  formData.append('file', fileInput.files[0]);
  fetch('/api/v1/multi-record/detect-discriminator', { method: 'POST', headers: _apiHeaders(), body: formData })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      btn.disabled = false;
      var best = data.best;
      if (!best) {
        status.textContent = 'No discriminator detected. Fill in fields manually.';
        return;
      }
      document.getElementById('mrDiscPosition').value = best.position;
      document.getElementById('mrDiscLength').value   = best.length;
      // textContent used — values are numbers/strings from API, no XSS risk
      status.textContent = 'Detected: position=' + best.position +
        ', length=' + best.length +
        ', values=[' + best.values.join(', ') + ']' +
        ' (confidence=' + Math.round(best.confidence * 100) + '%)';
    })
    .catch(function(err) {
      btn.disabled = false;
      status.textContent = 'Auto-detect failed: ' + err.message;
    });
}

// -- Step 3: record type rows --

function mrRenderTypeRows() {
  var container = document.getElementById('mrTypeRows');
  while (container.firstChild) container.removeChild(container.firstChild);
  _mrSelected.forEach(function(id) {
    var labelText = ((_allMappingOptions || []).find(function(o) { return o.value === id; }) || {}).label || id;
    var div = document.createElement('div');
    div.className = 'mr-record-type-row';
    var h4 = document.createElement('h4');
    h4.textContent = labelText;  // textContent -- safe
    div.appendChild(h4);
    var fields = document.createElement('div');
    fields.className = 'mr-type-fields';
    _mrAppendLabelInput(fields, 'Disc. Code', 'mrCode_' + id, 'text', 'e.g. HDR', 'width:90px');
    _mrAppendLabelSelect(fields, 'First row',  'mrFirstRow_' + id, ['', 'required', 'optional'], ['\u2014', 'required', 'optional']);
    _mrAppendLabelSelect(fields, 'Last row',   'mrLastRow_'  + id, ['', 'required', 'optional'], ['\u2014', 'required', 'optional']);
    _mrAppendLabelSelect(fields, 'Expect', 'mrExpect_' + id,
      ['', 'exactly_one', 'at_least_one', 'any_number', 'none'],
      ['\u2014', 'exactly_one', 'at_least_one', 'any_number', 'none']);
    div.appendChild(fields);
    container.appendChild(div);
  });
}

function _mrAppendLabelInput(parent, labelText, inputId, inputType, placeholder, style) {
  var lbl = document.createElement('label');
  lbl.htmlFor = inputId;
  lbl.textContent = labelText;
  var inp = document.createElement('input');
  inp.type = inputType;
  inp.id = inputId;
  inp.placeholder = placeholder || '';
  inp.autocomplete = 'off';
  if (style) inp.style.cssText = style;
  parent.appendChild(lbl);
  parent.appendChild(inp);
}

function _mrAppendLabelSelect(parent, labelText, selectId, values, labels) {
  var lbl = document.createElement('label');
  lbl.htmlFor = selectId;
  lbl.textContent = labelText;
  var sel = document.createElement('select');
  sel.id = selectId;
  values.forEach(function(v, i) {
    var opt = document.createElement('option');
    opt.value = v;
    opt.textContent = labels[i];
    sel.appendChild(opt);
  });
  parent.appendChild(lbl);
  parent.appendChild(sel);
}

// -- Step 4: cross-type rules --

var _mrRuleTypeOptions = [
  'required_companion', 'header_trailer_count', 'header_trailer_sum',
  'header_trailer_match', 'header_detail_consistent', 'type_sequence', 'expect_count'
];

function mrAddCrossRule() {
  var container = document.getElementById('mrCrossRuleRows');
  var idx = _mrCrossRules.length;
  _mrCrossRules.push({ type: _mrRuleTypeOptions[0] });

  var row = document.createElement('div');
  row.className = 'mr-cross-rule-row';
  row.id = 'mrCrossRule_' + idx;

  // Rule type selector
  var typeSel = document.createElement('select');
  typeSel.id = 'mrRuleType_' + idx;
  _mrRuleTypeOptions.forEach(function(rt) {
    var opt = document.createElement('option');
    opt.value = rt;
    opt.textContent = rt;
    typeSel.appendChild(opt);
  });
  typeSel.addEventListener('change', function() {
    _mrCrossRules[idx].type = typeSel.value;
    mrRenderRuleFields(typeSel.value, extra, idx);
  });

  // Extra fields container
  var extra = document.createElement('div');
  extra.className = 'mr-cross-rule-extra';

  // Remove button
  var rmBtn = document.createElement('button');
  rmBtn.className = 'mr-remove-btn';
  rmBtn.textContent = '\u00d7';
  rmBtn.title = 'Remove rule';
  rmBtn.addEventListener('click', function() { mrRemoveCrossRule(row, idx); });

  row.appendChild(typeSel);
  row.appendChild(extra);
  row.appendChild(rmBtn);
  container.appendChild(row);
  mrRenderRuleFields(_mrRuleTypeOptions[0], extra, idx);
}

function mrRemoveCrossRule(row, idx) {
  _mrCrossRules[idx] = null;
  if (row && row.parentNode) row.parentNode.removeChild(row);
}

function mrRenderRuleFields(ruleType, container, idx) {
  while (container.firstChild) container.removeChild(container.firstChild);
  var fields = [];
  if (ruleType === 'required_companion') {
    fields = [['when_type','When type'],['requires_type','Requires type']];
  } else if (ruleType === 'header_trailer_count') {
    fields = [['record_type','Trailer type'],['trailer_field','Trailer field'],['count_of','Count of']];
  } else if (ruleType === 'header_trailer_sum') {
    fields = [['record_type','Trailer type'],['trailer_field','Trailer field'],['sum_of','Sum field'],['detail_type','Detail type']];
  } else if (ruleType === 'header_trailer_match') {
    fields = [['record_type','Trailer type'],['trailer_field','Trailer field'],['header_type','Header type'],['header_field','Header field']];
  } else if (ruleType === 'header_detail_consistent') {
    fields = [['header_type','Header type'],['header_field','Header field'],['detail_type','Detail type'],['detail_field','Detail field']];
  } else if (ruleType === 'type_sequence') {
    fields = [['sequence','Sequence (comma-sep)']];
  } else if (ruleType === 'expect_count') {
    fields = [['record_type','Record type'],['count','Count']];
  }
  fields.forEach(function(pair) {
    var key = pair[0], lbl = pair[1];
    var label = document.createElement('label');
    label.textContent = lbl + ':';
    var inp = document.createElement('input');
    inp.type = 'text';
    inp.placeholder = key;
    inp.style.width = '110px';
    inp.addEventListener('change', function() {
      if (_mrCrossRules[idx]) _mrCrossRules[idx][key] = inp.value.trim();
    });
    container.appendChild(label);
    container.appendChild(inp);
  });
  // Severity + message always present
  var sevLbl = document.createElement('label');
  sevLbl.textContent = 'Severity:';
  var sevSel = document.createElement('select');
  ['error','warning'].forEach(function(s) {
    var opt = document.createElement('option');
    opt.value = s; opt.textContent = s; sevSel.appendChild(opt);
  });
  sevSel.addEventListener('change', function() { if (_mrCrossRules[idx]) _mrCrossRules[idx].severity = sevSel.value; });
  var msgLbl = document.createElement('label');
  msgLbl.textContent = 'Message:';
  var msgInp = document.createElement('input');
  msgInp.type = 'text';
  msgInp.placeholder = 'optional message';
  msgInp.style.width = '160px';
  msgInp.addEventListener('change', function() { if (_mrCrossRules[idx]) _mrCrossRules[idx].message = msgInp.value.trim(); });
  container.appendChild(sevLbl);
  container.appendChild(sevSel);
  container.appendChild(msgLbl);
  container.appendChild(msgInp);
}

// -- Step 5: build payload, generate, copy, download, validate --

function mrBuildPayload() {
  var discriminator = {};
  if (_mrDiscriminator.field)    discriminator.field    = _mrDiscriminator.field;
  if (_mrDiscriminator.position) discriminator.position = _mrDiscriminator.position;
  if (_mrDiscriminator.length)   discriminator.length   = _mrDiscriminator.length;

  var record_types = {};
  _mrSelected.forEach(function(id) {
    var rt = _mrRecordTypes[id] || {};
    var entry = {};
    entry.match   = rt.code   || '';
    entry.mapping = 'config/mappings/' + id + '.json';
    if (rt.first_row) entry.first_row = rt.first_row;
    if (rt.last_row)  entry.last_row  = rt.last_row;
    if (rt.expect)    entry.expect    = rt.expect;
    record_types[id] = entry;
  });

  var cross_type_rules = (_mrCrossRules || []).filter(Boolean).map(function(r) {
    var rule = { check: r.type };
    Object.keys(r).forEach(function(k) { if (k !== 'type' && r[k]) rule[k] = r[k]; });
    return rule;
  });

  var payload = { discriminator: discriminator, record_types: record_types };
  if (cross_type_rules.length > 0) payload.cross_type_rules = cross_type_rules;
  return payload;
}

function mrGenerateYaml() {
  var preEl  = document.getElementById('mrYamlPreview');
  var copyBtn = document.getElementById('mrCopyBtn');
  var dlBtn   = document.getElementById('mrDownloadBtn');
  var valBtn  = document.getElementById('mrValidateBtn');
  preEl.textContent = 'Generating\u2026';
  [copyBtn, dlBtn, valBtn].forEach(function(b) { b.disabled = true; });

  var payload = mrBuildPayload();
  fetch('/api/v1/multi-record/generate', {
    method: 'POST',
    headers: Object.assign({ 'Content-Type': 'application/json' }, _apiHeaders()),
    body: JSON.stringify(payload),
  })
    .then(function(r) {
      if (!r.ok) return r.text().then(function(t) { throw new Error(t); });
      return r.text();
    })
    .then(function(yaml) {
      _mrYamlText = yaml;
      preEl.textContent = yaml;  // textContent — no XSS
      [copyBtn, dlBtn, valBtn].forEach(function(b) { b.disabled = false; });
    })
    .catch(function(err) {
      preEl.textContent = 'Error: ' + err.message;
    });
}

function mrCopyYaml() {
  if (!_mrYamlText) return;
  navigator.clipboard.writeText(_mrYamlText).then(function() {
    var btn = document.getElementById('mrCopyBtn');
    var orig = btn.textContent;
    btn.textContent = 'Copied!';
    setTimeout(function() { btn.textContent = orig; }, 1500);
  });
}

function mrDownloadYaml() {
  if (!_mrYamlText) return;
  var blob = new Blob([_mrYamlText], { type: 'application/x-yaml' });
  var url  = URL.createObjectURL(blob);
  var a    = document.createElement('a');
  a.href     = url;
  a.download = 'multi_record_config.yaml';
  document.body.appendChild(a);
  a.click();
  setTimeout(function() { document.body.removeChild(a); URL.revokeObjectURL(url); }, 100);
}

function mrValidateWithConfig() {
  if (!_mrYamlText) return;
  _mrPendingYaml = new Blob([_mrYamlText], { type: 'application/x-yaml' });

  // Pre-populate the Quick Test multi-record YAML file input.
  var yamlFile = new File([_mrPendingYaml], 'multi_record_config.yaml', { type: 'application/x-yaml' });
  var dt = new DataTransfer();
  dt.items.add(yamlFile);
  var input = document.getElementById('qtMrYamlInput');
  if (input) {
    input.files = dt.files;
    updateButtons();
  }

  switchTab('quick');
  var status = document.getElementById('statusText');
  if (status) status.textContent = 'Multi-record YAML config loaded. Upload your batch file and click Validate.';
}

// ---------------------------------------------------------------------------
// Theme toggle
// ---------------------------------------------------------------------------
document.getElementById('btnTheme').addEventListener('click', function() {
  var html = document.documentElement;
  var next = html.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
  html.setAttribute('data-theme', next);
  localStorage.setItem('valdo-theme', next);
  this.textContent = next === 'dark' ? '\u2600' : '\u263D';  /* sun / crescent */
  this.setAttribute('aria-label', next === 'dark' ? 'Switch to light theme' : 'Switch to dark theme');
});

// Set initial icon to match stored theme
(function() {
  var saved = localStorage.getItem('valdo-theme') || 'dark';
  var btn = document.getElementById('btnTheme');
  btn.textContent = saved === 'dark' ? '\u2600' : '\u263D';
})();

// ---------------------------------------------------------------------------
// Footer health polling
// ---------------------------------------------------------------------------
var HEALTH_INTERVAL_MS = 30000;

/**
 * Poll the server health endpoint and update the footer health indicator.
 * Sets the dot to green on HTTP 200, red on error or non-200 response.
 *
 * @returns {Promise<void>}
 */
async function checkHealth() {
  var dot   = document.getElementById('healthDot');
  var label = document.getElementById('healthLabel');
  try {
    var resp = await fetch('/api/v1/system/health', { cache: 'no-store' });
    if (resp.ok) {
      dot.className = 'health-dot ok';
      dot.setAttribute('aria-label', 'Server health: healthy');
      label.textContent = 'Server healthy';
    } else {
      dot.className = 'health-dot error';
      dot.setAttribute('aria-label', 'Server health: error ' + resp.status);
      label.textContent = 'Server returned ' + resp.status;
    }
  } catch (_) {
    dot.className = 'health-dot error';
    dot.setAttribute('aria-label', 'Server health: unreachable');
    label.textContent = 'Server unreachable';
  }
}

checkHealth();
setInterval(checkHealth, HEALTH_INTERVAL_MS);

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
loadMappings();
loadRules();
loadRunHistory();
loadTrendChart();
loadSummaryCards();
_applyBaselineColVisibility();

var trendSel = document.getElementById('trendSuiteSelect');
if (trendSel) { trendSel.addEventListener('change', loadTrendChart); }

// ===========================================================================
// API TESTER
// ===========================================================================
var _atBodyType     = 'none';
var _atRespData     = null;
var _atSuites       = [];
var _atCurrentSuite    = null;
var _atDragSrcIdx      = -1;
var _atCurrentResults  = [];
var AT_PROXY_TIMEOUT = 30;

// -- Method colour badge -------------------------------------------------------
/**
 * Apply a CSS class to a method `<select>` element based on its current value.
 *
 * @param {HTMLSelectElement} sel - The HTTP method selector element.
 */
function atUpdateMethodColor(sel) {
  sel.className = 'method-' + sel.value;
}

// -- Horizontal request tabs --------------------------------------------------
/**
 * Switch the active request sub-tab (Headers, Body, or Assertions).
 *
 * @param {string}          name - Tab name: 'Headers', 'Body', or 'Assertions'.
 * @param {HTMLButtonElement} btn - The tab button that was clicked.
 */
function atReqTab(name, btn) {
  ['Headers', 'Body', 'Assertions'].forEach(function(t) {
    var panel = document.getElementById('atTab' + t);
    if (panel) panel.classList.toggle('active', t === name);
  });
  btn.parentNode.querySelectorAll('button').forEach(function(b) {
    b.classList.remove('active');
    b.setAttribute('aria-selected', 'false');
  });
  btn.classList.add('active');
  btn.setAttribute('aria-selected', 'true');
}

// -- Body type toggle ---------------------------------------------------------
/**
 * Set the active request body type and show/hide the corresponding editor.
 *
 * @param {string} type - Body type: 'none', 'json', or 'form'.
 */
function atSetBodyType(type) {
  _atBodyType = type;
  ['none','json','form'].forEach(function(t) {
    var id = 'atBody' + t.charAt(0).toUpperCase() + t.slice(1);
    var el = document.getElementById(id);
    if (el) {
      el.classList.toggle('active', t === type);
      el.setAttribute('aria-pressed', String(t === type));
    }
  });
  document.getElementById('atBodyJsonArea').style.display = (type === 'json') ? '' : 'none';
  document.getElementById('atBodyFormArea').style.display = (type === 'form') ? '' : 'none';
}

// -- Header rows (DOM-only, no innerHTML with user data) ----------------------
/**
 * Create a key-value (or key-file) row DOM element for headers or form fields.
 *
 * @param {string}  keyVal - Initial value for the key input.
 * @param {string}  valVal - Initial value for the value input (ignored when isFile is true).
 * @param {boolean} isFile - When true, renders a file picker instead of a value input.
 * @returns {HTMLDivElement} The constructed row element (not yet appended to the DOM).
 */
function atMakeKvRow(keyVal, valVal, isFile) {
  var row = document.createElement('div');
  row.className = 'at-kv-row';

  var keyInput = document.createElement('input');
  keyInput.className = 'text-input at-kv-key';
  keyInput.placeholder = 'Key';
  keyInput.value = keyVal || '';

  var rmBtn = document.createElement('button');
  rmBtn.className = 'at-kv-remove';
  rmBtn.title = 'Remove';
  rmBtn.setAttribute('aria-label', 'Remove row');
  rmBtn.textContent = '\u00d7';
  rmBtn.addEventListener('click', function() { row.remove(); });

  if (isFile) {
    row.dataset.isFile = '1';
    var fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.style.cssText = 'flex:2;font-size:12px';
    row.appendChild(keyInput);
    row.appendChild(fileInput);
  } else {
    var valInput = document.createElement('input');
    valInput.className = 'text-input at-kv-val';
    valInput.placeholder = isFile ? '' : 'Value';
    valInput.value = valVal || '';
    row.appendChild(keyInput);
    row.appendChild(valInput);
  }
  row.appendChild(rmBtn);
  return row;
}

/**
 * Append a new header key-value row to the headers panel.
 *
 * @param {string} key - Initial key value (may be empty string).
 * @param {string} val - Initial value (may be empty string).
 */
function atAddHeader(key, val) {
  document.getElementById('atHeaderRows').appendChild(atMakeKvRow(key, val, false));
}

/**
 * Collect all non-empty header rows from the headers panel.
 *
 * @returns {Array<{key: string, value: string}>} Array of header objects.
 */
function atGetHeaders() {
  var rows = document.getElementById('atHeaderRows').querySelectorAll('.at-kv-row');
  var result = [];
  rows.forEach(function(row) {
    var inputs = row.querySelectorAll('input');
    var k = inputs[0].value.trim();
    if (k) result.push({key: k, value: inputs[1] ? inputs[1].value.trim() : ''});
  });
  return result;
}

/**
 * Append a new form-data row (text field or file picker) to the form body panel.
 *
 * @param {boolean} isFile - When true, renders a file picker input.
 * @param {string}  key    - Initial key value.
 * @param {string}  val    - Initial value (ignored when isFile is true).
 */
function atAddFormField(isFile, key, val) {
  document.getElementById('atFormRows').appendChild(atMakeKvRow(key, val, isFile));
}

/**
 * Collect all non-empty form-data rows and separate file inputs from text fields.
 *
 * @returns {{ fields: Array<{key: string, value: string, is_file: boolean}>, files: Array<File> }}
 *     `fields` contains the structured form field descriptors; `files` contains
 *     the actual File objects for multipart upload (parallel-indexed with file fields).
 */
function atGetFormFields() {
  var rows = document.getElementById('atFormRows').querySelectorAll('.at-kv-row');
  var fields = []; var files = [];
  rows.forEach(function(row) {
    var keyEl = row.querySelector('.at-kv-key');
    var k = keyEl ? keyEl.value.trim() : '';
    if (!k) return;
    if (row.dataset.isFile === '1') {
      var fileEl = row.querySelector('input[type=file]');
      if (fileEl && fileEl.files[0]) {
        fields.push({key: k, value: '', is_file: true});
        files.push(fileEl.files[0]);
      }
    } else {
      var valEl = row.querySelector('.at-kv-val');
      fields.push({key: k, value: valEl ? valEl.value : '', is_file: false});
    }
  });
  return {fields: fields, files: files};
}

// -- Assertion rows (DOM-only) ------------------------------------------------
/**
 * Append a new assertion row to the Assertions panel.
 *
 * @param {string} field    - JSONPath or 'status_code' target (e.g. '$.id').
 * @param {string} op       - Operator: 'equals', 'contains', or 'exists'.
 * @param {string} expected - Expected value string.
 */
function atAddAssertion(field, op, expected) {
  var row = document.createElement('div');
  row.className = 'at-kv-row';

  var fieldIn = document.createElement('input');
  fieldIn.className = 'text-input';
  fieldIn.style.flex = '2';
  fieldIn.placeholder = 'status_code or $.field';
  fieldIn.value = field || '';

  var opSel = document.createElement('select');
  opSel.className = 'text-input';
  opSel.style.flex = '1';
  ['equals','contains','exists'].forEach(function(o) {
    var opt = document.createElement('option');
    opt.value = o;
    opt.textContent = o;
    if (o === op) opt.selected = true;
    opSel.appendChild(opt);
  });

  var expIn = document.createElement('input');
  expIn.className = 'text-input at-kv-val';
  expIn.style.flex = '2';
  expIn.placeholder = 'expected';
  expIn.value = expected || '';

  var rmBtn = document.createElement('button');
  rmBtn.className = 'at-kv-remove';
  rmBtn.setAttribute('aria-label', 'Remove assertion');
  rmBtn.textContent = '\u00d7';
  rmBtn.addEventListener('click', function() { row.remove(); });

  row.appendChild(fieldIn);
  row.appendChild(opSel);
  row.appendChild(expIn);
  row.appendChild(rmBtn);
  document.getElementById('atAssertionRows').appendChild(row);
}

/**
 * Collect all non-empty assertion rows from the Assertions panel.
 *
 * @returns {Array<{field: string, operator: string, expected: string}>}
 */
function atGetAssertions() {
  var rows = document.getElementById('atAssertionRows').querySelectorAll('.at-kv-row');
  var result = [];
  rows.forEach(function(row) {
    var inputs = row.querySelectorAll('input, select');
    var f = inputs[0].value.trim();
    if (f) result.push({field: f, operator: inputs[1].value, expected: inputs[2].value.trim()});
  });
  return result;
}

// -- Send ---------------------------------------------------------------------
/**
 * Collect the current request configuration and send it through the API-tester
 * proxy endpoint, then render the response.
 *
 * @returns {Promise<void>}
 */
async function atSend() {
  var method  = document.getElementById('atMethod').value;
  var baseUrl = document.getElementById('atBaseUrl').value.trim().replace(/\/$/, '');
  var path    = document.getElementById('atPath').value.trim();
  var url     = baseUrl + path;
  if (!url) {
    document.getElementById('atRespBody').textContent = 'Enter a Base URL and Path before sending.';
    return;
  }

  var cfg = {
    method: method, url: url,
    headers: atGetHeaders(),
    body_type: _atBodyType,
    body_json: document.getElementById('atBodyJsonText').value,
    form_fields: [],
    timeout: AT_PROXY_TIMEOUT,
  };

  var fd = new FormData();
  if (_atBodyType === 'form') {
    var formData = atGetFormFields();
    cfg.form_fields = formData.fields;
    formData.files.forEach(function(f) { fd.append('uploaded_files', f); });
  }
  fd.append('config', JSON.stringify(cfg));

  var respBody = document.getElementById('atRespBody');
  respBody.textContent = 'Sending\u2026';
  document.getElementById('atRespStatusLine').classList.remove('visible');

  try {
    var resp = await fetch('/api/v1/api-tester/proxy', {method: 'POST', body: fd});
    var data = await resp.json();
    if (!resp.ok) {
      respBody.textContent = 'Proxy error: ' + JSON.stringify(data.detail || resp.statusText);
      return;
    }
    _atRespData = data;
    atRenderResponse(data);
  } catch (err) {
    respBody.textContent = 'Error: ' + err.message;
  }
}

// -- Response render ----------------------------------------------------------
/**
 * Populate the response pane (status badge, elapsed time, body, headers, raw)
 * with data returned by the proxy endpoint.
 *
 * @param {Object} data             - Proxy response payload.
 * @param {number} data.status_code - HTTP status code.
 * @param {number} data.elapsed_ms  - Round-trip time in milliseconds.
 * @param {string} data.body        - Raw response body string.
 * @param {Object} data.headers     - Response header key-value pairs.
 */
function atRenderResponse(data) {
  var code  = data.status_code;
  var badge = document.getElementById('atStatusBadge');
  badge.textContent = String(code);
  badge.className = 'at-status-badge at-status-' +
    (code < 300 ? '2xx' : code < 400 ? '3xx' : code < 500 ? '4xx' : '5xx');
  document.getElementById('atElapsed').textContent = data.elapsed_ms.toFixed(1) + ' ms';
  var sizeEl = document.getElementById('atRespSize');
  if (sizeEl && data.body) {
    var bytes = new Blob([data.body]).size;
    sizeEl.textContent = formatBytes(bytes);
  } else if (sizeEl) {
    sizeEl.textContent = '';
  }
  document.getElementById('atRespStatusLine').classList.add('visible');

  // Body tab -- JSON highlighter escapes all < and > tokens before innerHTML
  var bodyEl = document.getElementById('atRespBody');
  try {
    var parsed = JSON.parse(data.body);
    bodyEl.innerHTML = atHighlightJson(JSON.stringify(parsed, null, 2));
  } catch (_) {
    bodyEl.textContent = data.body;
  }

  // Headers tab -- textContent only
  var headersEl = document.getElementById('atRespHeaders');
  headersEl.textContent = Object.entries(data.headers)
    .map(function(kv) { return kv[0] + ': ' + kv[1]; }).join('\n');

  // Raw tab -- textContent only
  document.getElementById('atRespRaw').textContent = data.body;
}

/**
 * Switch the active response sub-tab (Body, Headers, or Raw).
 *
 * @param {string}           name - Tab name: 'Body', 'Headers', or 'Raw'.
 * @param {HTMLButtonElement} btn  - The tab button that was clicked.
 */
function atRespTab(name, btn) {
  ['Body','Headers','Raw'].forEach(function(t) {
    document.getElementById('atResp' + t).style.display = (t === name) ? '' : 'none';
  });
  btn.parentNode.querySelectorAll('button').forEach(function(b) {
    b.classList.remove('active');
    b.setAttribute('aria-selected', 'false');
  });
  btn.classList.add('active');
  btn.setAttribute('aria-selected', 'true');
}

/**
 * Apply syntax-highlight span tags to a JSON string for the response body panel.
 * All token content is HTML-escaped before being wrapped in `<span>` tags
 * to prevent XSS when the result is written to innerHTML.
 *
 * @param {string} str - Pretty-printed JSON string.
 * @returns {string} HTML string with colour spans for keys, strings, numbers,
 *     booleans, and null values.
 */
// JSON syntax highlighter -- safe: escapes & < > in every matched token before innerHTML
function atHighlightJson(str) {
  return str.replace(
    /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g,
    function(match) {
      var safe = match.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      var cls  = 'jh-num';
      if (/^"/.test(match))              { cls = /:$/.test(match) ? 'jh-key' : 'jh-str'; }
      else if (/true|false/.test(match)) { cls = 'jh-bool'; }
      else if (/null/.test(match))       { cls = 'jh-null'; }
      return '<span class="' + cls + '">' + safe + '</span>';
    }
  );
}

// -- Suite selector -----------------------------------------------------------
/**
 * Fetch all test suites from the API and populate both the request-builder
 * suite selector and the runner suite selector.
 *
 * @returns {Promise<void>}
 */
async function atLoadSuites() {
  try {
    var resp = await fetch('/api/v1/api-tester/suites');
    _atSuites = await resp.json();
    ['atSuiteSel','atRunnerSuiteSel'].forEach(function(selId) {
      var sel = document.getElementById(selId);
      while (sel.options.length > 1) sel.remove(1);
      _atSuites.forEach(function(s) {
        var opt = document.createElement('option');
        opt.value = s.id;
        opt.textContent = s.name + ' (' + s.request_count + ')';
        sel.appendChild(opt);
      });
    });
  } catch (_) {}
}

// -- Save request into a suite ------------------------------------------------
/**
 * Save the current request builder form as a new request inside the selected suite.
 *
 * @returns {Promise<void>}
 */
async function atSaveRequest() {
  var suiteId = document.getElementById('atSuiteSel').value;
  var name    = document.getElementById('atReqName').value.trim() || 'Unnamed';
  if (!suiteId) { alert('Select a suite first, or create one with New Suite.'); return; }

  var req = {
    id:         (crypto.randomUUID ? crypto.randomUUID() : String(Date.now())),
    name:       name,
    method:     document.getElementById('atMethod').value,
    path:       document.getElementById('atPath').value.trim(),
    headers:    atGetHeaders(),
    body_type:  _atBodyType,
    body_json:  document.getElementById('atBodyJsonText').value,
    form_fields: _atBodyType === 'form' ? atGetFormFields().fields : [],
    assertions: atGetAssertions(),
  };

  try {
    var getResp = await fetch('/api/v1/api-tester/suites/' + suiteId);
    if (!getResp.ok) { alert('Could not load suite (status ' + getResp.status + ').'); return; }
    var suite = await getResp.json();
    suite.requests.push(req);
    var putResp = await fetch('/api/v1/api-tester/suites/' + suiteId, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(suite),
    });
    if (!putResp.ok) { alert('Could not save request (status ' + putResp.status + ').'); return; }
  } catch (err) {
    alert('Network error: ' + err.message);
    return;
  }
  await atLoadSuites();
  alert('Saved \u201c' + name + '\u201d to suite.');
}

// -- New suite ----------------------------------------------------------------
/**
 * Prompt the user for a suite name and create a new empty suite via the API.
 *
 * @returns {Promise<void>}
 */
async function atNewSuite() {
  var name = prompt('Suite name:');
  if (!name) return;
  var base = document.getElementById('atBaseUrl').value.trim() || 'http://127.0.0.1:8000';
  await fetch('/api/v1/api-tester/suites', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: name, base_url: base, requests: []}),
  });
  await atLoadSuites();
}

// -- Suite Runner -------------------------------------------------------------
/**
 * Load the selected suite into the runner panel and render its request list.
 *
 * @returns {Promise<void>}
 */
async function atLoadSuiteIntoRunner() {
  var suiteId = document.getElementById('atRunnerSuiteSel').value;
  document.getElementById('atRunnerReqList').textContent = '';
  document.getElementById('atRunnerSummary').style.display = 'none';
  document.getElementById('btnSaveOrder').style.display = 'none';
  _atCurrentSuite = null;
  _atCurrentResults = [];
  if (!suiteId) return;
  var resp = await fetch('/api/v1/api-tester/suites/' + suiteId);
  _atCurrentSuite = await resp.json();
  atRenderRunnerList(_atCurrentSuite.requests, []);
}

/**
 * Render the runner request list with drag-and-drop reorder support and
 * inline assertion result badges (when results are available).
 *
 * @param {Array<Object>} requests - Request config objects for the loaded suite.
 * @param {Array<Object>} results  - Assertion result arrays (parallel-indexed with requests);
 *     empty array before a run.
 */
function atRenderRunnerList(requests, results) {
  var list = document.getElementById('atRunnerReqList');
  list.textContent = '';
  requests.forEach(function(req, idx) {
    var row = document.createElement('div');
    row.className = 'at-req-row';
    row.draggable = true;

    var methodSpan = document.createElement('span');
    methodSpan.className = 'at-req-method method-' + req.method;
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
      atRenderRunnerList(_atCurrentSuite.requests, _atCurrentResults);
      document.getElementById('btnSaveOrder').style.display = '';
    });
    row.addEventListener('dragend', function() {
      _atDragSrcIdx = -1;
      list.querySelectorAll('.at-req-row').forEach(function(r) { r.classList.remove('drag-over'); });
    });

    list.appendChild(row);
  });
}

/**
 * Persist the current request order for the loaded suite to the API.
 *
 * @returns {Promise<void>}
 */
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

/**
 * Execute all requests in the loaded suite sequentially through the proxy and
 * evaluate their assertions, then display a summary of pass/fail counts.
 *
 * @returns {Promise<void>}
 */
async function atRunSuite() {
  if (!_atCurrentSuite) { alert('Select a suite first.'); return; }
  var requests = _atCurrentSuite.requests;
  if (!requests.length) { alert('Suite has no requests.'); return; }

  var results = [];
  var totalPass = 0, totalFail = 0;
  var t0 = Date.now();

  for (var i = 0; i < requests.length; i++) {
    var req = requests[i];
    var url = (req.path.startsWith('http') ? '' : _atCurrentSuite.base_url) + req.path;

    var cfg = {
      method: req.method, url: url,
      headers: req.headers || [],
      body_type: req.body_type || 'none',
      body_json: req.body_json || '',
      form_fields: req.form_fields || [],
      timeout: AT_PROXY_TIMEOUT,
    };
    var fd = new FormData();
    fd.append('config', JSON.stringify(cfg));

    var proxyResp = {status_code: 0, body: '', headers: {}, elapsed_ms: 0};
    try {
      var resp = await fetch('/api/v1/api-tester/proxy', {method: 'POST', body: fd});
      proxyResp = await resp.json();
    } catch (_) {}

    var assertResults = (req.assertions || []).map(function(a) {
      var pass = atEvaluateAssertion(a, proxyResp);
      if (pass) totalPass++; else totalFail++;
      return {field: a.field, operator: a.operator, expected: a.expected, pass: pass};
    });
    results.push({assertions: assertResults});
    _atCurrentResults = results;
    atRenderRunnerList(requests, results);
  }

  var elapsed = Date.now() - t0;
  var summary = document.getElementById('atRunnerSummary');
  summary.style.display = '';
  summary.textContent = totalPass + ' passed  /  ' + totalFail + ' failed  /  ' +
    (totalPass + totalFail) + ' assertions  /  ' + elapsed + ' ms total';
  summary.style.color = totalFail > 0 ? 'var(--fail)' : 'var(--pass)';
}

/**
 * Evaluate a single assertion against a proxy response.
 *
 * Supports ``status_code`` as a literal field and ``$.field.path`` JSONPath
 * expressions for response body fields.
 *
 * @param {{ field: string, operator: string, expected: string }} assertion - Assertion config.
 * @param {{ status_code: number, body: string }}                 proxyResp - Proxy response.
 * @returns {boolean} True if the assertion passes.
 */
function atEvaluateAssertion(assertion, proxyResp) {
  var field = assertion.field, operator = assertion.operator, expected = String(assertion.expected);
  var actual;
  if (field === 'status_code') {
    actual = String(proxyResp.status_code);
  } else if (field.startsWith('$.')) {
    try {
      var data = JSON.parse(proxyResp.body);
      actual = atJsonPath(data, field.slice(2));
    } catch (_) { return false; }
  } else { return false; }

  if (operator === 'exists')   return actual !== undefined && actual !== null;
  if (operator === 'equals')   return String(actual) === expected;
  if (operator === 'contains') return String(actual).includes(expected);
  return false;
}

/**
 * Resolve a simple dot-notation JSONPath expression against an object.
 * Supports array index notation (e.g. ``items[0].name``).
 *
 * @param {Object} obj  - The root object to traverse.
 * @param {string} path - Dot-separated path string (e.g. 'data.items[0].id').
 * @returns {*} The value at the path, or ``undefined`` if not found.
 */
function atJsonPath(obj, path) {
  return path.split('.').reduce(function(cur, part) {
    if (cur === undefined || cur === null) return undefined;
    var m = part.match(/^(\w+)\[(\d+)\]$/);
    return m ? (cur[m[1]] && cur[m[1]][parseInt(m[2])]) : cur[part];
  }, obj);
}

// Load suites when tab is opened
document.getElementById('tab-tester').addEventListener('click', atLoadSuites);

// ---------------------------------------------------------------------------
// Indicator initialisation — run after layout is painted
// ---------------------------------------------------------------------------
window.addEventListener('load', function() {
  updateTabIndicator();
});
// Also reposition on window resize (handles font scaling / zoom)
window.addEventListener('resize', updateTabIndicator);

// ===========================================================================
// #125: System preference detection for theme
// ===========================================================================
(function() {
  var stored = localStorage.getItem('valdo-theme');
  if (!stored) {
    // No stored preference — use system preference
    var preferLight = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches;
    var systemTheme = preferLight ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', systemTheme);
    var btn = document.getElementById('btnTheme');
    if (btn) btn.textContent = systemTheme === 'dark' ? '\u2600' : '\u263D';
  }
  // Listen for live system preference changes when no stored preference
  if (window.matchMedia) {
    window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', function(e) {
      if (localStorage.getItem('valdo-theme')) return; // user chose manually
      var newTheme = e.matches ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', newTheme);
      var btn = document.getElementById('btnTheme');
      if (btn) {
        btn.textContent = newTheme === 'dark' ? '\u2600' : '\u263D';
        btn.setAttribute('aria-label', newTheme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme');
      }
    });
  }
})();

// ===========================================================================
// #126: Toast notification system
// ===========================================================================
/**
 * Display an auto-dismissing toast notification.
 *
 * @param {string} message  - Message text to show.
 * @param {string} [type='info'] - Visual type: 'info', 'success', 'error', or 'warning'.
 * @param {number} [duration=4000] - Auto-dismiss delay in milliseconds.
 */
function showToast(message, type, duration) {
  type = type || 'info';
  duration = duration || 4000;
  var container = document.getElementById('toastContainer');
  if (!container) return;

  var toast = document.createElement('div');
  toast.className = 'toast toast-' + type;
  toast.textContent = message;

  container.appendChild(toast);

  // Trigger slide-in after DOM insertion
  requestAnimationFrame(function() {
    requestAnimationFrame(function() {
      toast.classList.add('toast-visible');
    });
  });

  // Auto-dismiss
  var timer = setTimeout(function() {
    dismissToast(toast);
  }, duration);

  // Allow click to dismiss
  toast.addEventListener('click', function() {
    clearTimeout(timer);
    dismissToast(toast);
  });
}

/**
 * Animate a toast out and remove it from the DOM after the transition ends.
 *
 * @param {HTMLElement} toast - The toast element to dismiss.
 */
function dismissToast(toast) {
  toast.classList.remove('toast-visible');
  toast.classList.add('toast-exit');
  toast.addEventListener('transitionend', function() {
    if (toast.parentNode) toast.parentNode.removeChild(toast);
  });
}

// ===========================================================================
// #127: Keyboard support for collapsible/toggle elements
// ===========================================================================
// Auto-refresh toggle — Enter/Space
(function() {
  var toggle = document.getElementById('autoRefreshToggle');
  if (toggle) {
    toggle.addEventListener('keydown', function(e) {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        toggleAutoRefresh();
      }
    });
  }
})();

// Update aria-expanded when toggling auto-refresh
var _origToggleAutoRefresh = toggleAutoRefresh;
toggleAutoRefresh = function() {
  _origToggleAutoRefresh();
  var toggle = document.getElementById('autoRefreshToggle');
  if (toggle) {
    toggle.setAttribute('aria-expanded', String(!!_autoRefreshTimer));
  }
};

// Compare toggle — add aria-expanded
(function() {
  var btn = document.getElementById('btnToggleCompare');
  if (btn) {
    btn.setAttribute('aria-expanded', 'false');
    var origListener = null;
    btn.addEventListener('click', function() {
      btn.setAttribute('aria-expanded', String(compareMode));
    });
  }
})();

// ===========================================================================
// Help sidebar
// ===========================================================================
(function() {
  var overlay = document.getElementById('helpOverlay');
  var sidebar = document.getElementById('helpSidebar');
  var btnOpen = document.getElementById('btnHelp');
  var btnClose = document.getElementById('btnHelpClose');
  var body = document.getElementById('helpBody');
  var searchInput = document.getElementById('helpSearch');
  var guideHtml = '';
  var loaded = false;

  /** Open the help sidebar and load the usage guide on first open. */
  function openHelp() {
    overlay.classList.add('open');
    sidebar.classList.add('open');
    document.body.style.overflow = 'hidden';
    if (!loaded) loadGuide();
    setTimeout(function() { searchInput.focus(); }, 350);
  }

  /** Close the help sidebar overlay. */
  function closeHelp() {
    overlay.classList.remove('open');
    sidebar.classList.remove('open');
    document.body.style.overflow = '';
  }

  btnOpen.addEventListener('click', openHelp);
  btnClose.addEventListener('click', closeHelp);
  overlay.addEventListener('click', closeHelp);

  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && sidebar.classList.contains('open')) closeHelp();
  });

  /**
   * Escape HTML special characters in a string.
   *
   * @param {string} s - Raw string to escape.
   * @returns {string} HTML-safe string.
   */
  function escHtml(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  /**
   * Convert a Markdown string to safe DOM elements and append them to a container.
   * Supports headings (h1-h3), paragraphs, unordered lists, horizontal rules,
   * fenced code blocks, and inline bold/code/links.
   * Does NOT use innerHTML with untrusted content.
   *
   * @param {string}      md        - Markdown source text.
   * @param {HTMLElement} container - Container element to populate.
   */
  // Simple markdown to safe DOM elements (no innerHTML with untrusted content)
  function renderMarkdownToDOM(md, container) {
    while (container.firstChild) container.removeChild(container.firstChild);
    var lines = md.split('\n');
    var inCode = false;
    var codeBlock = null;
    var listEl = null;

    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];

      // Code blocks
      if (line.match(/^```/)) {
        if (inCode) {
          container.appendChild(codeBlock);
          codeBlock = null;
          inCode = false;
        } else {
          codeBlock = document.createElement('pre');
          var codeEl = document.createElement('code');
          codeBlock.appendChild(codeEl);
          inCode = true;
        }
        continue;
      }
      if (inCode) {
        codeBlock.querySelector('code').textContent += line + '\n';
        continue;
      }

      // Close list if line is not a list item
      if (listEl && !line.match(/^\s*[-*] /)) {
        container.appendChild(listEl);
        listEl = null;
      }

      // Headings
      var headingMatch = line.match(/^(#{1,3}) (.+)/);
      if (headingMatch) {
        var level = headingMatch[1].length;
        var hEl = document.createElement('h' + level);
        // Generate ID for anchor links (e.g. "1. Getting Started" → "1-getting-started")
        var headingId = headingMatch[2].toLowerCase()
          .replace(/[^a-z0-9\s-]/g, '')
          .replace(/\s+/g, '-')
          .replace(/-+/g, '-')
          .replace(/^-|-$/g, '');
        hEl.id = headingId;
        appendInlineContent(hEl, headingMatch[2]);
        container.appendChild(hEl);
        continue;
      }

      // HR
      if (line.match(/^---+$/)) {
        container.appendChild(document.createElement('hr'));
        continue;
      }

      // List items
      var listMatch = line.match(/^\s*[-*] (.+)/);
      if (listMatch) {
        if (!listEl) listEl = document.createElement('ul');
        var li = document.createElement('li');
        appendInlineContent(li, listMatch[1]);
        listEl.appendChild(li);
        continue;
      }

      // Empty lines
      if (line.trim() === '') continue;

      // Paragraphs
      var p = document.createElement('p');
      appendInlineContent(p, line);
      container.appendChild(p);
    }

    if (codeBlock) container.appendChild(codeBlock);
    if (listEl) container.appendChild(listEl);
  }

  /**
   * Parse and append inline Markdown tokens (backtick code, **bold**, and
   * [text](url) links) as safe DOM child nodes.
   *
   * @param {HTMLElement} parent - Element to append child nodes to.
   * @param {string}      text   - Inline Markdown text to parse.
   */
  // Render inline markdown (bold, code, links) as safe DOM nodes
  function appendInlineContent(parent, text) {
    // Split on patterns: `code`, **bold**, [text](url)
    var regex = /(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\))/;
    var parts = text.split(regex);
    for (var i = 0; i < parts.length; i++) {
      var part = parts[i];
      if (!part) continue;
      if (part.match(/^`([^`]+)`$/)) {
        var code = document.createElement('code');
        code.textContent = part.slice(1, -1);
        parent.appendChild(code);
      } else if (part.match(/^\*\*([^*]+)\*\*$/)) {
        var strong = document.createElement('strong');
        strong.textContent = part.slice(2, -2);
        parent.appendChild(strong);
      } else if (part.match(/^\[([^\]]+)\]\(([^)]+)\)$/)) {
        var linkMatch = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
        var a = document.createElement('a');
        a.textContent = linkMatch[1];
        var href = linkMatch[2];
        if (href.charAt(0) === '#') {
          // Anchor link — scroll within sidebar
          a.href = 'javascript:void(0)';
          a.setAttribute('data-anchor', href.slice(1));
          a.addEventListener('click', (function(anchor) {
            return function(e) {
              e.preventDefault();
              var target = document.getElementById(anchor);
              if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
            };
          })(href.slice(1)));
        } else {
          a.href = href;
          a.target = '_blank';
          a.rel = 'noopener';
        }
        parent.appendChild(a);
      } else {
        parent.appendChild(document.createTextNode(part));
      }
    }
  }

  /**
   * Fetch the usage guide from the API in Markdown format and render it into
   * the help sidebar body.  Shows loading and error states as needed.
   */
  function loadGuide() {
    body.textContent = '';
    var loadMsg = document.createElement('div');
    loadMsg.className = 'help-loading';
    loadMsg.textContent = 'Loading usage guide...';
    body.appendChild(loadMsg);

    fetch('/api/v1/guide?format=markdown')
      .then(function(r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.text();
      })
      .then(function(md) {
        guideHtml = md;
        renderMarkdownToDOM(md, body);
        loaded = true;
      })
      .catch(function() {
        body.textContent = '';
        var errDiv = document.createElement('div');
        errDiv.className = 'help-error';
        errDiv.textContent = 'Could not load usage guide. Ensure the server is running.';
        body.appendChild(errDiv);
      });
  }

  // Search with highlight
  searchInput.addEventListener('input', function() {
    var q = searchInput.value.trim().toLowerCase();
    if (!q || !guideHtml) {
      renderMarkdownToDOM(guideHtml, body);
      return;
    }
    renderMarkdownToDOM(guideHtml, body);
    var walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT, null, false);
    var textNodes = [];
    while (walker.nextNode()) textNodes.push(walker.currentNode);
    for (var j = 0; j < textNodes.length; j++) {
      var node = textNodes[j];
      var idx = node.textContent.toLowerCase().indexOf(q);
      if (idx >= 0) {
        var mark = document.createElement('mark');
        mark.style.background = 'rgba(74,158,255,0.3)';
        mark.style.borderRadius = '2px';
        mark.style.padding = '0 1px';
        var after = node.splitText(idx);
        var rest = after.splitText(q.length);
        mark.textContent = after.textContent;
        after.parentNode.replaceChild(mark, after);
      }
    }
    var firstMark = body.querySelector('mark');
    if (firstMark) firstMark.scrollIntoView({ behavior: 'smooth', block: 'center' });
  });
})();
