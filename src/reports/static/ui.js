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

// ---------------------------------------------------------------------------
// Tab switching — animated panels + sliding underline indicator
// ---------------------------------------------------------------------------
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
}

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
function clearStatus() {
  var el = document.getElementById('statusMsg');
  el.className = 'status-msg info';
  while (el.firstChild) { el.removeChild(el.firstChild); }
}

function setStatusText(msg, type) {
  var el = document.getElementById('statusMsg');
  el.className = 'status-msg ' + type;
  while (el.firstChild) { el.removeChild(el.firstChild); }
  el.textContent = msg;
}

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

function showInlineReport(url) {
  document.getElementById('reportFrame').src = url;
  document.getElementById('reportOpenLink').href = url;
  document.getElementById('reportPanel').classList.add('visible');
}

function hideInlineReport() {
  document.getElementById('reportPanel').classList.remove('visible');
  document.getElementById('reportFrame').src = '';
}

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
  if (this.files.length) { setFile(this.files[0], 'primary'); }
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
  updateButtons();
});
document.getElementById('btnRemoveSecondary').addEventListener('click', function(e) {
  e.stopPropagation();
  secondaryFile = null;
  document.getElementById('secondaryFileCard').classList.remove('visible');
  document.getElementById('fileInput2').value = '';
  updateButtons();
});

function detectFormat(filename) {
  var ext = (filename.split('.').pop() || '').toLowerCase();
  var map = { txt: 'TXT', csv: 'CSV', dat: 'DAT', pipe: 'PIPE', tsv: 'TSV', xlsx: 'XLSX', xls: 'XLS' };
  return map[ext] || ext.toUpperCase() || 'FILE';
}

function formatBytes(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

function setFile(file, slot) {
  if (slot === 'primary') {
    primaryFile = file;
    document.getElementById('primaryFileName').textContent = file.name;
    document.getElementById('primaryFileMeta').textContent = formatBytes(file.size);
    document.getElementById('primaryFormatBadge').textContent = detectFormat(file.name);
    document.getElementById('primaryFileCard').classList.add('visible');
  } else {
    secondaryFile = file;
    document.getElementById('secondaryFileName').textContent = file.name;
    document.getElementById('secondaryFileMeta').textContent = formatBytes(file.size);
    document.getElementById('secondaryFormatBadge').textContent = detectFormat(file.name);
    document.getElementById('secondaryFileCard').classList.add('visible');
  }
  updateButtons();
}

function updateButtons() {
  var hasPrimary   = !!primaryFile;
  var hasMapping   = !!document.getElementById('mappingSelect').value;
  var hasSecondary = !!secondaryFile;
  document.getElementById('btnValidate').disabled = !(hasPrimary && hasMapping);
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

async function loadRules() {
  var sel = document.getElementById('rulesSelect');
  try {
    var resp = await fetch('/api/v1/rules/');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    var list = await resp.json();
    while (sel.childNodes.length > 1) sel.removeChild(sel.lastChild);
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

// ===========================================================================
// Validate
// ===========================================================================
document.getElementById('btnValidate').addEventListener('click', async function() {
  if (!primaryFile) { setStatusText('Please select a file.', 'error'); return; }
  var mapping = document.getElementById('mappingSelect').value;
  if (!mapping) { setStatusText('Please select a mapping.', 'error'); return; }

  setLoading('Validating\u2026');
  setBtnLoading(this, true);
  var btn = this;

  try {
    var fd = new FormData();
    fd.append('file', primaryFile);
    fd.append('mapping_id', mapping);
    var rulesVal = document.getElementById('rulesSelect').value;
    if (rulesVal) { fd.append('rules_id', rulesVal); }

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

    var msg = 'Validated \u2014 ' + data.valid_rows + '/' + data.total_rows + ' rows valid'
      + (data.invalid_rows ? ' (' + data.invalid_rows + ' errors)' : '') + '.';
    if (data.report_url) {
      setStatusWithLink(msg, data.valid ? 'success' : 'error', 'Open report', data.report_url);
      showInlineReport(data.report_url);
    } else {
      setStatusText(msg, data.valid ? 'success' : 'error');
    }
  } catch (err) {
    setStatusText('Error: ' + err.message, 'error');
  } finally {
    setBtnLoading(btn, false);
    updateButtons();
  }
});

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
  ];
  cols.forEach(function(col) {
    var th = document.createElement('th');
    th.textContent = col.label;
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

    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
}

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
function toggleAutoRefresh() {
  var toggle = document.getElementById('autoRefreshToggle');
  var label  = document.getElementById('refreshToggleLabel');
  if (_autoRefreshTimer) {
    clearInterval(_autoRefreshTimer);
    _autoRefreshTimer = null;
    toggle.classList.remove('active');
    label.textContent = 'Auto-refresh off';
  } else {
    _autoRefreshTimer = setInterval(loadRunHistory, 30000);
    toggle.classList.add('active');
    label.textContent = 'Auto-refresh on (30s)';
  }
}

// ===========================================================================
// Mapping Generator — state
// ===========================================================================
var mapFile   = null;
var rulesFile = null;

// ===========================================================================
// Mapping Generator — step progress
// ===========================================================================
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
function atUpdateMethodColor(sel) {
  sel.className = 'method-' + sel.value;
}

// -- Horizontal request tabs --------------------------------------------------
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

function atAddHeader(key, val) {
  document.getElementById('atHeaderRows').appendChild(atMakeKvRow(key, val, false));
}

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

function atAddFormField(isFile, key, val) {
  document.getElementById('atFormRows').appendChild(atMakeKvRow(key, val, isFile));
}

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

  function openHelp() {
    overlay.classList.add('open');
    sidebar.classList.add('open');
    document.body.style.overflow = 'hidden';
    if (!loaded) loadGuide();
    setTimeout(function() { searchInput.focus(); }, 350);
  }

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

  function escHtml(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

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
