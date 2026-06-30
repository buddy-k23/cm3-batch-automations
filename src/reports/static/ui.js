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
 * @param {string} name - Tab identifier: 'quick', 'runs', 'mapping', 'tester', 'dbcompare', or 'downloader'.
 */
function switchTab(name) {
  ['quick', 'runs', 'mapping', 'tester', 'dbcompare', 'excelcompare', 'downloader', 'onboarding'].forEach(function(t) {
    var panel = document.getElementById('panel-' + t);
    var btn   = document.getElementById('tab-' + t);
    if (!panel || !btn) return;
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
  // Load named connections whenever DB Compare tab is activated (#296)
  if (name === 'dbcompare') { loadDbConnections(); }
  // Load named connections + profiles whenever Excel Compare tab is activated (S24-5)
  if (name === 'excelcompare') {
    if (typeof loadExcelDbConnections === 'function') loadExcelDbConnections();
    if (typeof window._xlcLoadProfiles === 'function') window._xlcLoadProfiles();
  }
  // Load downloader paths when Downloader tab is activated
  if (name === 'downloader') { loadDownloaderPaths(); }
  // EE-S1: refresh committed sources whenever Source Editor tab is activated
  if (name === 'onboarding') { loadOnboardingSources(); }
}

// ---------------------------------------------------------------------------
// Tab visibility — driven by GET /api/v1/system/ui-config
// ---------------------------------------------------------------------------
/**
 * Fetch tab visibility config and hide disabled tabs.
 *
 * Called once on page load. Tabs where the server returns ``false`` have
 * their button and panel hidden with ``display:none``. Fails open: if the
 * fetch errors, all tabs remain visible.
 */
function initTabVisibility() {
  // Login bootstrap — when auth is enabled and the user has no session,
  // the server returns 401 from /auth/whoami and we redirect to /auth/login.
  // On LDAPS-disabled deployments /auth/whoami returns 404 (route not
  // registered), the catch swallows it, and the API-key UX continues unchanged.
  try {
    apiFetch('/auth/whoami', { silent: true })
      .then(function (r) {
        if (r.status === 401) {
          var nxt = encodeURIComponent(window.location.pathname + window.location.search);
          window.location.href = '/auth/login?next=' + nxt;
          return null;
        }
        return r.ok ? r.json() : null;
      })
      .then(function (user) {
        if (user) {
          window._currentUser = user;
        }
      })
      .catch(function () { /* auth disabled — fall through to API-key UX */ });
  } catch (e) { /* fetch unavailable — ignore */ }

  apiFetch('/api/v1/system/ui-config', { silent: true })
    .then(function(r) { return r.ok ? r.json() : null; })
    .then(function(data) {
      if (!data || !data.tabs) return;
      Object.keys(data.tabs).forEach(function(tab) {
        if (!data.tabs[tab]) {
          var btn = document.getElementById('tab-' + tab);
          var panel = document.getElementById('panel-' + tab);
          if (btn) btn.style.display = 'none';
          if (panel) panel.style.display = 'none';
        }
      });
      // If the default active tab was hidden, switch to the first remaining visible tab.
      // This handles configs where quick=false and only e.g. downloader=true.
      var activeBtn = document.querySelector('nav.tabs button.active');
      if (activeBtn && activeBtn.style.display === 'none') {
        var allBtns = document.querySelectorAll('nav.tabs button[id^="tab-"]');
        for (var i = 0; i < allBtns.length; i++) {
          if (allBtns[i].style.display !== 'none') {
            switchTab(allBtns[i].id.replace('tab-', ''));
            break;
          }
        }
      }
    })
    .catch(function() { /* fail open — config error must not break the app */ });
}

// ===========================================================================
// File Downloader Tab
// ===========================================================================
var _fdPaths = [];
var _fdCurrentPath = null;
var _fdLinkedPath = null;  // path set by Browse→Search linking buttons

/**
 * Return authentication headers for API requests.
 *
 * Uses the globally stored API key injected by the server into window._apiKey.
 * Returns an empty object when no key is configured so unauthenticated servers
 * continue to work without modification.
 *
 * @returns {Object} Headers object, optionally containing X-API-Key.
 */
function _apiHeaders() {
  return window._apiKey ? { 'X-API-Key': window._apiKey } : {}; // gitleaks:allow
}

/** Default request timeout (ms) applied by apiFetch when none is supplied. */
var API_FETCH_TIMEOUT_MS = 30000;

// Throttle auth-failure toasts so a burst of 401/403 (e.g. many baseline cells)
// does not stack dozens of identical messages.
var _apiAuthToastAt = 0;

/**
 * Surface a uniform, user-visible message for a failed API request.
 *
 * Uses showToast() when available and falls back to console only. Auth
 * failures (401/403) are de-duplicated within a short window so concurrent
 * requests do not spam the toast container.
 *
 * @param {string} message - Human-readable error text.
 * @param {boolean} [isAuth=false] - True for 401/403 (de-duplicated, prompts for key).
 */
function _apiFetchNotify(message, isAuth) {
  if (isAuth) {
    var now = Date.now();
    if (now - _apiAuthToastAt < 3000) { return; }
    _apiAuthToastAt = now;
  }
  if (typeof showToast === 'function') {
    showToast(message, 'error', 6000);
  } else {
    console.error(message);
  }
}

/**
 * Central fetch wrapper for all Valdo Web UI API calls.
 *
 * Injects authentication headers (via _apiHeaders()), applies a request
 * timeout through an AbortController, and provides a single, uniform place
 * for surfacing auth-failure (401/403), server (5xx), timeout and network
 * errors instead of dozens of ad-hoc per-call handlers.
 *
 * Contract:
 *   - Returns the raw Response on a completed HTTP exchange (any status),
 *     so callers keep their existing `.ok` / `.json()` / `.blob()` / `.status`
 *     handling. The wrapper does NOT throw on 4xx/5xx HTTP responses.
 *   - On 401/403 it shows a toast prompting for the API key (de-duplicated)
 *     and still returns the Response so callers may branch on `.status`.
 *   - On 5xx it shows a generic server-error toast and returns the Response.
 *   - Throws on timeout or network failure (TypeError / AbortError). Timeout
 *     aborts are normalised so callers can distinguish them (`err.isTimeout`).
 *   - Honours a caller-supplied `opts.signal` (e.g. a Stop button); the
 *     caller's abort and the timeout abort are composed — whichever fires
 *     first aborts the request.
 *
 * The path is taken verbatim, so both `/api/v1/*` and `/api/v2/*` work; no
 * version is hardcoded here.
 *
 * @param {string} path - Request URL/path (e.g. '/api/v1/files/validate').
 * @param {Object} [opts={}] - fetch() options. Extra keys:
 *   - timeout {number} Override the default timeout in ms.
 *   - silent {boolean} Suppress the uniform error toast (caller handles UX).
 *   - signal {AbortSignal} Caller abort signal, composed with the timeout.
 * @returns {Promise<Response>} The fetch Response.
 * @throws {Error} On network failure or timeout (err.isTimeout / err.isNetwork).
 */
function apiFetch(path, opts) {
  opts = opts || {};
  var timeoutMs = (typeof opts.timeout === 'number') ? opts.timeout : API_FETCH_TIMEOUT_MS;
  var silent = opts.silent === true;
  var callerSignal = opts.signal || null;

  var controller = new AbortController();
  var timedOut = false;
  var timer = setTimeout(function () {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  // Compose a caller-provided signal (e.g. the downloader Stop button) with
  // the timeout controller: if the caller aborts, abort the timeout one too.
  if (callerSignal) {
    if (callerSignal.aborted) {
      controller.abort();
    } else {
      callerSignal.addEventListener('abort', function () { controller.abort(); });
    }
  }

  // Build the final options: caller headers merged over the auth headers,
  // default credentials, and our composed abort signal.
  var fetchOpts = {};
  for (var k in opts) {
    if (Object.prototype.hasOwnProperty.call(opts, k) &&
        k !== 'timeout' && k !== 'silent' && k !== 'signal') {
      fetchOpts[k] = opts[k];
    }
  }
  fetchOpts.headers = Object.assign({}, _apiHeaders(), opts.headers || {});
  if (!('credentials' in fetchOpts)) { fetchOpts.credentials = 'include'; }
  fetchOpts.signal = controller.signal;

  return fetch(path, fetchOpts)
    .then(function (resp) {
      clearTimeout(timer);
      if (!silent) {
        if (resp.status === 401 || resp.status === 403) {
          _apiFetchNotify(
            'Authentication failed. Please enter or update your API key and try again.',
            true
          );
        } else if (resp.status >= 500) {
          _apiFetchNotify('Server error (' + resp.status + '). Please try again.', false);
        }
      }
      return resp;
    })
    .catch(function (err) {
      clearTimeout(timer);
      // A caller-driven abort (Stop button) is re-thrown unchanged so existing
      // AbortError handling keeps working; only the timeout/network paths are
      // re-shaped and surfaced.
      if (err && err.name === 'AbortError' && callerSignal && callerSignal.aborted && !timedOut) {
        throw err;
      }
      if (timedOut) {
        var to = new Error('Request timed out after ' + Math.round(timeoutMs / 1000) + 's');
        to.name = 'TimeoutError';
        to.isTimeout = true;
        if (!silent) { _apiFetchNotify('Request timed out. Please try again.', false); }
        throw to;
      }
      // Network failure (server down, CORS, DNS, offline, …).
      err.isNetwork = true;
      if (!silent) { _apiFetchNotify('Network error — could not reach the server.', false); }
      throw err;
    });
}

/**
 * Escape a string for safe insertion as HTML text.
 *
 * @param {string} str - Raw string that may contain HTML special characters.
 * @returns {string} HTML-escaped string.
 */
function _escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/**
 * Load allowed download paths from the server and populate #fdPathSelect.
 *
 * No-ops if paths were already loaded (cached in _fdPaths). Called each time
 * the Downloader tab is activated.
 */
function loadDownloaderPaths() {
  if (_fdPaths.length > 0) return;
  apiFetch('/api/v1/downloader/paths')
    .then(function(r) { return r.ok ? r.json() : Promise.reject(r.status); })
    .then(function(data) {
      _fdPaths = data.paths || [];
      var sel = document.getElementById('fdPathSelect');
      if (!sel) return;
      while (sel.options.length > 0) sel.remove(0);
      var placeholder = document.createElement('option');
      placeholder.value = '';
      placeholder.textContent = '\u2014 select a path \u2014';
      sel.appendChild(placeholder);
      _fdPaths.forEach(function(p) {
        var opt = document.createElement('option');
        opt.value = p.path;
        opt.textContent = p.label + ' \u2014 ' + p.path;
        sel.appendChild(opt);
      });
    })
    .catch(function(err) { console.error('Failed to load downloader paths:', err); });
}

/**
 * Clear browse/search result panels when the selected path changes.
 *
 * Also resets the linked-path state so that any prior Browse→Search link is
 * discarded when the user picks a different root from the selector.
 */
function onFdPathChange() {
  _fdCurrentPath = null;
  _fdLinkedPath = null;
  ['fdBrowseResults', 'fdSearchResults', 'fdArchSearchResults'].forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.textContent = '';
  });
  var bc = document.getElementById('fdBreadcrumb');
  if (bc) bc.textContent = '';
  var si = document.getElementById('fdSearchInPath');
  var ai = document.getElementById('fdArchSearchInPath');
  if (si) si.value = '';
  if (ai) ai.value = '';
  var sig = document.getElementById('fdSearchInGroup');
  var aig = document.getElementById('fdArchSearchInGroup');
  if (sig) sig.style.display = 'none';
  if (aig) aig.style.display = 'none';
}

/**
 * Switch the active File Downloader sub-tab.
 *
 * @param {string} name - Sub-tab name: 'browse', 'search', or 'searcharch'.
 */
function switchFdSubTab(name) {
  ['browse', 'search', 'searcharch'].forEach(function(t) {
    var panel = document.getElementById('fdpanel-' + t);
    var btn   = document.getElementById('fdtab-' + t);
    if (panel) panel.classList.toggle('fd-subpanel--hidden', t !== name);
    if (btn) {
      btn.classList.toggle('active', t === name);
      btn.setAttribute('aria-selected', String(t === name));
    }
  });
}

/**
 * Pre-fill a search sub-tab from a Browse row action button.
 *
 * Sets _fdLinkedPath so that the next search call uses the chosen directory
 * instead of the root path selector value. Optionally pre-fills filename and
 * archive pattern inputs then switches the active sub-tab and focuses the
 * search string input.
 *
 * @param {string} path   - Filesystem path to search in (sets _fdLinkedPath).
 * @param {string} subTab - Sub-tab to activate: 'search' or 'searcharch'.
 * @param {Object} opts   - Optional pre-fill values:
 *   filename {string}        - Pre-fills #fdSearchFilename (search sub-tab).
 *   archivePattern {string}  - Pre-fills #fdArchSearchPattern (searcharch sub-tab).
 *   filePattern {string}     - Pre-fills #fdArchFilePattern (searcharch sub-tab).
 *   focusId {string}         - Element ID to focus after switching tab.
 */
function _fdLinkToSearch(path, subTab, opts) {
  _fdLinkedPath = path;
  opts = opts || {};

  var searchInEl = document.getElementById('fdSearchInPath');
  var archInEl   = document.getElementById('fdArchSearchInPath');
  if (searchInEl) searchInEl.value = path;
  if (archInEl)   archInEl.value   = path;

  if (opts.filename) {
    var fn = document.getElementById('fdSearchFilename');
    if (fn) fn.value = opts.filename;
  }
  if (opts.archivePattern) {
    var ap = document.getElementById('fdArchSearchPattern');
    if (ap) ap.value = opts.archivePattern;
  }
  if (opts.filePattern) {
    var fp = document.getElementById('fdArchFilePattern');
    if (fp) fp.value = opts.filePattern;
  }

  // Show the appropriate "Search in" group
  var searchInGroup = document.getElementById('fdSearchInGroup');
  var archInGroup   = document.getElementById('fdArchSearchInGroup');
  if (subTab === 'search') {
    if (searchInGroup) searchInGroup.style.display = 'block';
  } else {
    if (archInGroup) archInGroup.style.display = 'block';
  }

  switchFdSubTab(subTab);

  var focusId = opts.focusId || (subTab === 'search' ? 'fdSearchString' : 'fdArchSearchString');
  var focusEl = document.getElementById(focusId);
  if (focusEl) setTimeout(function() { focusEl.focus(); }, 50);
}

/**
 * Format a byte count into a human-readable string (B / KB / MB).
 *
 * @param {number} bytes - Raw byte count.
 * @returns {string} Formatted size string.
 */
function _fdFormatBytes(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1048576).toFixed(1) + ' MB';
}

/**
 * List files in the selected root path and render them in #fdBrowseResults.
 *
 * Reads the optional archive pattern from #fdArchivePattern. Resets the
 * current sub-path state and breadcrumb back to the root before browsing.
 */
function fdBrowse() {
  var path = document.getElementById('fdPathSelect').value;
  if (!path) { alert('Please select a path first.'); return; }
  var pattern = document.getElementById('fdArchivePattern').value.trim() || null;
  _fdCurrentPath = path;
  _fdBrowseDir(path, pattern);
}

/**
 * Browse a specific directory path and render results in #fdBrowseResults.
 *
 * Updates the breadcrumb to reflect the current location. Called by fdBrowse
 * for the root and by directory entry clicks when drilling into sub-folders.
 *
 * @param {string}  dirPath - Absolute filesystem path to browse.
 * @param {string|null} pattern - Optional fnmatch pattern for file filtering.
 */
function _fdBrowseDir(dirPath, pattern) {
  var rootPath = document.getElementById('fdPathSelect').value;
  var container = document.getElementById('fdBrowseResults');
  container.textContent = 'Loading\u2026';
  _fdRenderBreadcrumb(rootPath, dirPath, pattern);

  var url = '/api/v1/downloader/browse?path=' + encodeURIComponent(dirPath);
  if (pattern) url += '&pattern=' + encodeURIComponent(pattern);

  apiFetch(url)
    .then(function(r) { return r.ok ? r.json() : Promise.reject(r.status); })
    .then(function(data) {
      container.textContent = '';
      if (!data.entries || data.entries.length === 0) {
        container.textContent = 'No files found.';
        return;
      }
      data.entries.forEach(function(entry) {
        container.appendChild(_fdRenderEntry(entry, dirPath, pattern));
      });
    })
    .catch(function(err) {
      container.textContent = 'Browse failed: ' + err;
    });
}

/**
 * Update the #fdBreadcrumb bar to show the current location within rootPath.
 *
 * Each ancestor segment is rendered as a clickable link. The current
 * (leaf) segment is rendered as plain text.
 *
 * @param {string}      rootPath    - The configured root (from #fdPathSelect).
 * @param {string}      currentPath - The path being browsed (rootPath or deeper).
 * @param {string|null} pattern     - Pattern forwarded to _fdBrowseDir on click.
 */
function _fdRenderBreadcrumb(rootPath, currentPath, pattern) {
  var bc = document.getElementById('fdBreadcrumb');
  if (!bc) return;
  bc.textContent = '';

  var rel = (currentPath.indexOf(rootPath) === 0) ? currentPath.slice(rootPath.length) : '';
  var segments = rel.split('/').filter(function(s) { return s.length > 0; });

  var rootSpan = document.createElement('span');
  rootSpan.className = 'fd-bc-seg fd-bc-link';
  rootSpan.textContent = '/';
  rootSpan.setAttribute('title', rootPath);
  rootSpan.onclick = function() { _fdBrowseDir(rootPath, pattern); };
  bc.appendChild(rootSpan);

  var accPath = rootPath;
  segments.forEach(function(seg, i) {
    accPath = accPath + '/' + seg;
    var sep = document.createElement('span');
    sep.className = 'fd-bc-sep';
    sep.textContent = ' / ';
    bc.appendChild(sep);

    var segSpan = document.createElement('span');
    segSpan.textContent = seg;
    if (i === segments.length - 1) {
      segSpan.className = 'fd-bc-seg fd-bc-current';
    } else {
      segSpan.className = 'fd-bc-seg fd-bc-link';
      var segPath = accPath;
      segSpan.onclick = function() { _fdBrowseDir(segPath, pattern); };
    }
    bc.appendChild(segSpan);
  });
}

/**
 * Build a DOM row for a single browse entry (file, archive, or directory).
 *
 * All server-provided strings (name, type, size) are assigned via textContent
 * to prevent XSS.
 *
 * @param {Object}      entry   - Entry object with name, type, and size_bytes.
 * @param {string}      path    - The current browsed path (for download/drill-down).
 * @param {string|null} pattern - Active file pattern (forwarded on drill-down).
 * @returns {HTMLElement} A div element representing the entry row.
 */
function _fdRenderEntry(entry, path, pattern) {
  var row = document.createElement('div');
  row.className = 'fd-entry';

  if (entry.type === 'directory') {
    var nameEl = document.createElement('span');
    nameEl.className = 'fd-entry-name';
    nameEl.textContent = '\uD83D\uDCC1  ' + entry.name;

    var meta = document.createElement('span');
    meta.className = 'fd-entry-meta';
    meta.textContent = 'directory';

    var openBtn = document.createElement('button');
    openBtn.className = 'btn btn-secondary';
    openBtn.style.cssText = 'font-size:11px;padding:3px 10px';
    openBtn.textContent = '\u25B6 Open';
    var subPath = path + '/' + entry.name;
    openBtn.onclick = function() { _fdBrowseDir(subPath, pattern); };

    var searchHereBtn = document.createElement('button');
    searchHereBtn.className = 'btn-fd-link';
    searchHereBtn.textContent = '\uD83D\uDD0D Search here';
    searchHereBtn.title = 'Search files in this directory';
    (function(sp) {
      searchHereBtn.onclick = function() {
        _fdLinkToSearch(sp, 'search', { focusId: 'fdSearchFilename' });
      };
    })(subPath);

    var searchArchHereBtn = document.createElement('button');
    searchArchHereBtn.className = 'btn-fd-link';
    searchArchHereBtn.textContent = '\uD83D\uDDDC Search archives here';
    searchArchHereBtn.title = 'Search inside archives in this directory';
    (function(sp) {
      searchArchHereBtn.onclick = function() {
        _fdLinkToSearch(sp, 'searcharch', { focusId: 'fdArchSearchString' });
      };
    })(subPath);

    row.appendChild(nameEl);
    row.appendChild(meta);
    row.appendChild(openBtn);
    row.appendChild(searchHereBtn);
    row.appendChild(searchArchHereBtn);
    return row;
  }

  var nameEl = document.createElement('span');
  nameEl.className = 'fd-entry-name';
  nameEl.textContent = (entry.type === 'archive' ? '\uD83D\uDDDC  ' : '\uD83D\uDCC4  ') + entry.name;

  var meta = document.createElement('span');
  meta.className = 'fd-entry-meta';
  meta.textContent = entry.type + ' \u00B7 ' + _fdFormatBytes(entry.size_bytes);

  row.appendChild(nameEl);
  row.appendChild(meta);

  if (entry.type === 'archive') {
    var expandBtn = document.createElement('button');
    expandBtn.className = 'btn btn-secondary';
    expandBtn.style.cssText = 'font-size:11px;padding:3px 10px';
    expandBtn.textContent = '\u25BC Expand';

    var childContainer = document.createElement('div');
    childContainer.className = 'fd-entry-children';
    childContainer.style.display = 'none';
    var expanded = false;

    expandBtn.onclick = function() {
      if (expanded) {
        childContainer.style.display = 'none';
        expandBtn.textContent = '\u25BC Expand';
        expanded = false;
      } else {
        fdExpandArchive(path, entry.name, childContainer, expandBtn);
        expanded = true;
      }
    };

    var searchInArchBtn = document.createElement('button');
    searchInArchBtn.className = 'btn-fd-link';
    searchInArchBtn.textContent = '\uD83D\uDD0D Search in archive';
    searchInArchBtn.title = 'Search string inside this archive';
    (function(p, n) {
      searchInArchBtn.onclick = function() {
        _fdLinkToSearch(p, 'searcharch', { archivePattern: n, filePattern: '*' });
      };
    })(path, entry.name);

    row.appendChild(expandBtn);
    row.appendChild(searchInArchBtn);
    var wrapper = document.createElement('div');
    wrapper.appendChild(row);
    wrapper.appendChild(childContainer);
    return wrapper;
  }

  var dlBtn = document.createElement('button');
  dlBtn.className = 'btn btn-primary';
  dlBtn.style.cssText = 'font-size:11px;padding:3px 10px';
  dlBtn.textContent = '\u2B07 Download';
  dlBtn.onclick = function() { fdDownload(path, entry.name, null); };
  row.appendChild(dlBtn);

  var searchInFileBtn = document.createElement('button');
  searchInFileBtn.className = 'btn-fd-link';
  searchInFileBtn.textContent = '\uD83D\uDD0D Search in file';
  searchInFileBtn.title = 'Search string inside this file';
  (function(p, n) {
    searchInFileBtn.onclick = function() {
      _fdLinkToSearch(p, 'search', { filename: n });
    };
  })(path, entry.name);
  row.appendChild(searchInFileBtn);

  return row;
}

/**
 * Fetch and render the contents of an archive entry inline.
 *
 * @param {string}      path      - Selected path alias.
 * @param {string}      archive   - Archive filename.
 * @param {HTMLElement} container - DOM element to render child file rows into.
 * @param {HTMLElement} btn       - The Expand button, updated to show Collapse label.
 */
function fdExpandArchive(path, archive, container, btn) {
  btn.textContent = '\u25B2 Collapse';
  container.style.display = 'block';
  container.textContent = 'Loading\u2026';

  var url = '/api/v1/downloader/archive-contents?path=' + encodeURIComponent(path)
            + '&archive=' + encodeURIComponent(archive);
  apiFetch(url)
    .then(function(r) { return r.ok ? r.json() : Promise.reject(r.status); })
    .then(function(data) {
      container.textContent = '';
      (data.files || []).forEach(function(innerFile) {
        var row = document.createElement('div');
        row.className = 'fd-entry';

        var nameEl = document.createElement('span');
        nameEl.className = 'fd-entry-name';
        nameEl.textContent = '\uD83D\uDCC4  ' + innerFile;

        var dlBtn = document.createElement('button');
        dlBtn.className = 'btn btn-primary';
        dlBtn.style.cssText = 'font-size:11px;padding:3px 10px';
        dlBtn.textContent = '\u2B07';
        dlBtn.onclick = (function(f) { return function() { fdDownload(path, f, archive); }; })(innerFile);

        var innerSearchBtn = document.createElement('button');
        innerSearchBtn.className = 'btn-fd-link';
        innerSearchBtn.textContent = '\uD83D\uDD0D';
        innerSearchBtn.title = 'Search in this archive member';
        (function(p, arc, inf) {
          innerSearchBtn.onclick = function() {
            _fdLinkToSearch(p, 'searcharch', { archivePattern: arc, filePattern: inf });
          };
        })(path, archive, innerFile);

        row.appendChild(nameEl);
        row.appendChild(dlBtn);
        row.appendChild(innerSearchBtn);
        container.appendChild(row);
      });
    })
    .catch(function(err) {
      container.textContent = 'Failed to load archive contents: ' + err;
    });
}

/**
 * Download a file (optionally from inside an archive) via the downloader API.
 *
 * Creates a temporary anchor element to trigger the browser file save dialog
 * without navigating away from the page.
 *
 * @param {string}      path     - Selected path alias.
 * @param {string}      filename - File path relative to the path root.
 * @param {string|null} archive  - Archive name if the file is inside one, else null.
 */
function fdDownload(path, filename, archive) {
  var body = { path: path, filename: filename };
  if (archive) body.archive = archive;

  apiFetch('/api/v1/downloader/download', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
    .then(function(r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.blob();
    })
    .then(function(blob) {
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url;
      a.download = filename.split('/').pop();
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    })
    .catch(function(err) { alert('Download error: ' + err); });
}

// ===========================================================================
// DB Compare — direction state + swap
// ===========================================================================
var _dbcDirection = 'db-to-file';

/**
 * Refresh all direction-dependent labels and CSS classes on the DB Compare panel.
 * Called on page load (implicitly via initial state) and whenever the swap button
 * is clicked.
 */
function _dbcUpdateDirection() {
  var isDbToFile = _dbcDirection === 'db-to-file';
  var lbl = document.getElementById('dbcDirectionLabel');
  if (lbl) lbl.textContent = isDbToFile ? 'DB is source \u00B7 File is actual' : 'File is source \u00B7 DB is actual';

  var dbPanel    = document.getElementById('dbcDbPanel');
  var filePanel  = document.getElementById('dbcFilePanel');
  var dbHeader   = document.getElementById('dbcDbPanelHeader');
  var fileHeader = document.getElementById('dbcFilePanelHeader');

  if (dbPanel)   dbPanel.className   = 'dbc-panel ' + (isDbToFile ? 'dbc-panel--source' : 'dbc-panel--actual');
  if (filePanel) filePanel.className = 'dbc-panel ' + (isDbToFile ? 'dbc-panel--actual' : 'dbc-panel--source');
  if (dbHeader)   dbHeader.className   = 'dbc-panel-header' + (isDbToFile ? '' : ' dbc-panel-header--actual');
  if (fileHeader) fileHeader.className = 'dbc-panel-header' + (isDbToFile ? ' dbc-panel-header--actual' : '');

  var sqlEd = document.getElementById('dbcSqlEditor');
  if (sqlEd) {
    sqlEd.placeholder = isDbToFile
      ? 'SELECT column1, column2 FROM SCHEMA.TABLE'
      : 'SELECT t1.col, t2.col FROM TARGET.TABLE1 t1 JOIN TARGET.TABLE2 t2 ON t1.id = t2.fk_id';
  }
}

(function() {
  var swapBtn = document.getElementById('dbcSwapBtn');
  if (swapBtn) {
    swapBtn.addEventListener('click', function() {
      _dbcDirection = (_dbcDirection === 'db-to-file') ? 'file-to-db' : 'db-to-file';
      _dbcUpdateDirection();
    });
  }
})();

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
    var resp = await apiFetch('/api/v1/mappings/');
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
      // Also populate DB Compare mapping select
      var dbcSel = document.getElementById('dbcMappingSelect');
      if (dbcSel) {
        while (dbcSel.firstChild) { dbcSel.removeChild(dbcSel.firstChild); }
        var dbcPlaceholder = document.createElement('option');
        dbcPlaceholder.value = '';
        dbcPlaceholder.textContent = '\u2014 select mapping \u2014';
        dbcSel.appendChild(dbcPlaceholder);
        list.forEach(function(m) {
          var opt = document.createElement('option');
          opt.value = m.id;
          opt.textContent = m.mapping_name + ' (' + m.format + ')';
          dbcSel.appendChild(opt);
        });
      }
      // #407 \u2014 populate the Reconcile-mapping-vs-table select (same list).
      var recSel = document.getElementById('reconcileMappingSelect');
      if (recSel) {
        while (recSel.firstChild) { recSel.removeChild(recSel.firstChild); }
        var recPlaceholder = document.createElement('option');
        recPlaceholder.value = '';
        recPlaceholder.textContent = '\u2014 select mapping \u2014';
        recSel.appendChild(recPlaceholder);
        list.forEach(function(m) {
          var opt = document.createElement('option');
          opt.value = m.id;
          opt.textContent = m.mapping_name + ' (' + m.format + ')';
          recSel.appendChild(opt);
        });
      }
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
    var resp = await apiFetch('/api/v1/rules/');
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

document.getElementById('qtMrYamlInput').addEventListener('change', function() {
  var label = document.getElementById('qtMrYamlLabelText');
  if (label) {
    if (this.files && this.files[0]) {
      label.innerHTML = '<strong style="color:var(--text-primary);">' +
        this.files[0].name + '</strong>';
    } else {
      label.innerHTML = 'Click to select a <strong style="color:var(--text-primary);">.yaml</strong> umbrella config&hellip;';
    }
  }
  updateButtons();
});

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

    var resp = await apiFetch('/api/v1/files/validate', { method: 'POST', body: fd });
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

    var resp = await apiFetch('/api/v1/files/export-errors', { method: 'POST', body: fd });
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
    var resp = await apiFetch('/api/v1/files/detect-drift', { method: 'POST', body: fd });
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

    var resp = await apiFetch('/api/v1/files/compare', { method: 'POST', body: fd });
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
    var resp = await apiFetch('/api/v1/runs/history');
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

  apiFetch(url)
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

  apiFetch('/api/v1/runs/summaries', { silent: true })
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
    return apiFetch(
      '/api/v1/runs/baseline-check?suite=' + encodeURIComponent(suite) +
      '&run_id=' + encodeURIComponent(runId),
      { silent: true }
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
    var resp = await apiFetch(url, { method: 'POST', body: fd });
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
    var resp = await apiFetch(url, { method: 'POST', body: fd });
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
  apiFetch('/api/v1/mappings/')
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
  apiFetch('/api/v1/multi-record/detect-discriminator', { method: 'POST', body: formData })
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
  apiFetch('/api/v1/multi-record/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
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
    var resp = await apiFetch('/api/v1/system/health', { cache: 'no-store', silent: true });
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
initTabVisibility();
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
    var resp = await apiFetch('/api/v1/api-tester/proxy', {method: 'POST', body: fd});
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
    var resp = await apiFetch('/api/v1/api-tester/suites');
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
    var getResp = await apiFetch('/api/v1/api-tester/suites/' + suiteId);
    if (!getResp.ok) { alert('Could not load suite (status ' + getResp.status + ').'); return; }
    var suite = await getResp.json();
    suite.requests.push(req);
    var putResp = await apiFetch('/api/v1/api-tester/suites/' + suiteId, {
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
  await apiFetch('/api/v1/api-tester/suites', {
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
  var resp = await apiFetch('/api/v1/api-tester/suites/' + suiteId);
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
    var resp = await apiFetch('/api/v1/api-tester/suites/' + _atCurrentSuite.id, {
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
      var resp = await apiFetch('/api/v1/api-tester/proxy', {method: 'POST', body: fd, silent: true});
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

    apiFetch('/api/v1/guide?format=markdown')
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

// ===========================================================================
// File Downloader — Search Files
// ===========================================================================

// Tracks in-flight searches keyed by kind ("files" | "archive"). Each entry is
// { id: <uuid>, controller: <AbortController> } so the Stop button (and the
// Esc shortcut) can both abort the fetch locally and tell the server to kill
// the search via POST /search-cancel.
var _fdActiveSearches = {};

/**
 * RFC4122-ish v4 UUID. Uses crypto.randomUUID when available, falls back to
 * Math.random for older browsers (good enough as a per-request correlation id).
 */
function _fdGenSearchId() {
  if (window.crypto && typeof window.crypto.randomUUID === 'function') {
    return window.crypto.randomUUID();
  }
  return 'xxxxxxxxxxxx4xxxyxxxxxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
    var r = Math.random() * 16 | 0;
    var v = c === 'x' ? r : (r & 0x3 | 0x8);
    return v.toString(16);
  });
}

/**
 * Toggle the Search/Stop buttons for the given search kind.
 *
 * @param {"files"|"archive"} kind     - Which search panel this applies to.
 * @param {boolean}           running  - True while a search is in flight.
 */
function _fdSetSearchRunning(kind, running) {
  var ids = kind === 'archive'
    ? { search: 'fdArchSearchBtn', stop: 'fdArchSearchStopBtn' }
    : { search: 'fdSearchBtn',     stop: 'fdSearchStopBtn' };
  var searchBtn = document.getElementById(ids.search);
  var stopBtn   = document.getElementById(ids.stop);
  if (searchBtn) searchBtn.disabled = !!running;
  if (stopBtn)   stopBtn.style.display = running ? '' : 'none';
}

/**
 * Stop an in-flight file or archive search.
 *
 * Aborts the local fetch (so the UI returns immediately) and best-effort
 * tells the server to kill the matching search via POST /search-cancel.
 *
 * @param {"files"|"archive"} kind
 */
function fdStopSearch(kind) {
  var active = _fdActiveSearches[kind];
  if (!active) return;
  try { active.controller.abort(); } catch (_) { /* no-op */ }
  // Best-effort server-side cancel; do not block the UI on this call.
  apiFetch('/api/v1/downloader/search-cancel', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ search_id: active.id }),
    silent: true,
  }).catch(function() { /* ignore — local abort already happened */ });
}

// Esc shortcut: stop whichever search is currently running.
document.addEventListener('keydown', function(e) {
  if (e.key !== 'Escape') return;
  if (_fdActiveSearches.files)   { fdStopSearch('files'); }
  if (_fdActiveSearches.archive) { fdStopSearch('archive'); }
});

/**
 * Search for a string inside files matching a filename pattern in the selected path.
 *
 * Reads inputs from #fdSearchFilename and #fdSearchString and renders results
 * (or a truncation notice) in #fdSearchResults. Generates a search_id so the
 * Stop button can cancel the running search server-side.
 */
function fdSearchFiles() {
  var path = _fdLinkedPath || document.getElementById('fdPathSelect').value;
  if (!path) { alert('Please select a path first.'); return; }
  var filenamePattern = document.getElementById('fdSearchFilename').value.trim();
  var searchString    = document.getElementById('fdSearchString').value.trim();
  if (!filenamePattern || !searchString) {
    alert('Please fill in both the filename pattern and search string.');
    return;
  }
  var container = document.getElementById('fdSearchResults');
  container.textContent = 'Searching\u2026 (press Esc or click Stop to cancel)';

  var searchId  = _fdGenSearchId();
  var controller = new AbortController();
  _fdActiveSearches.files = { id: searchId, controller: controller };
  _fdSetSearchRunning('files', true);

  apiFetch('/api/v1/downloader/search-files', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      path: path,
      filename_pattern: filenamePattern,
      search_string: searchString,
      search_id: searchId,
    }),
    signal: controller.signal,
    timeout: 120000,
    silent: true,
  })
    .then(function(r) { return r.ok ? r.json() : Promise.reject(r.status); })
    .then(function(data) { _fdRenderSearchResults(data, container); })
    .catch(function(err) {
      if (err && err.name === 'AbortError') {
        container.textContent = 'Search cancelled.';
      } else {
        container.textContent = 'Search failed: ' + err;
      }
    })
    .then(function() {
      delete _fdActiveSearches.files;
      _fdSetSearchRunning('files', false);
    });
}

/**
 * Render search results (from search-files or search-archive) into a container element.
 *
 * Builds a summary line, a results table, and an optional truncation notice. All
 * server-provided strings (filenames, line content, archive names) are assigned
 * via textContent to prevent XSS.
 *
 * @param {Object}      data      - API response with results, shown, total_matches, truncated fields.
 * @param {HTMLElement} container - DOM element to render into (cleared before rendering).
 */
function _fdRenderSearchResults(data, container) {
  container.textContent = '';

  // Surface server-side kill reason (timeout / cancelled) above results so the
  // user sees that the displayed matches are partial.
  if (data.stopped_reason) {
    var notice = document.createElement('p');
    notice.style.cssText = 'font-size:12px;color:var(--danger,#c00);margin-bottom:6px;font-weight:600';
    notice.textContent = data.stopped_reason === 'timeout'
      ? 'Search timed out — showing partial results. Narrow the pattern or raise the timeout in config/ui.yml (downloader.search.timeout_seconds).'
      : 'Search cancelled — showing partial results.';
    container.appendChild(notice);
  }

  if (!data.results || data.results.length === 0) {
    var msg = document.createElement('p');
    msg.textContent = data.stopped_reason
      ? 'No matches found before the search was stopped.'
      : 'No matches found.';
    container.appendChild(msg);
    return;
  }

  var summary = document.createElement('p');
  summary.style.cssText = 'font-size:12px;color:var(--text-secondary);margin-bottom:6px';
  summary.textContent = 'Showing ' + data.shown + ' of ' + data.total_matches + ' match(es)';
  container.appendChild(summary);

  var hasArchive = data.results.some(function(r) { return r.archive; });
  var headers = hasArchive ? ['Archive', 'File', 'Line', 'Content'] : ['File', 'Line', 'Content'];

  var table = document.createElement('table');
  table.className = 'fd-table';

  var thead = document.createElement('thead');
  var headRow = document.createElement('tr');
  headers.forEach(function(h) {
    var th = document.createElement('th');
    th.textContent = h;
    headRow.appendChild(th);
  });
  thead.appendChild(headRow);
  table.appendChild(thead);

  var tbody = document.createElement('tbody');
  data.results.forEach(function(hit) {
    var tr = document.createElement('tr');
    var cells = hasArchive
      ? [hit.archive || '', hit.file, String(hit.line), hit.content]
      : [hit.file, String(hit.line), hit.content];
    cells.forEach(function(val) {
      var td = document.createElement('td');
      td.textContent = val;
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  container.appendChild(table);

  if (data.truncated) {
    if (data.download_ref) {
      var warn = document.createElement('div');
      warn.className = 'fd-truncation-warn';

      var warnText = document.createElement('span');
      warnText.textContent = '\u26A0 Results truncated at 50 lines';
      warn.appendChild(warnText);

      var dlBtn = document.createElement('button');
      dlBtn.className = 'btn btn-primary';
      dlBtn.style.cssText = 'font-size:11px';
      var fname = data.download_ref.filename.split('/').pop();
      var arcLabel = data.download_ref.archive ? ' from ' + data.download_ref.archive : '';
      dlBtn.textContent = '\u2B07 Download ' + fname + arcLabel;
      dlBtn.onclick = (function(ref) {
        return function() { fdDownload(ref.path, ref.filename, ref.archive || null); };
      })(data.download_ref);
      warn.appendChild(dlBtn);
      container.appendChild(warn);
    } else {
      var refine = document.createElement('div');
      refine.className = 'fd-refine-prompt';
      refine.textContent = 'Too many results across multiple files \u2014 please refine your search using a single exact filename.';
      container.appendChild(refine);
    }
  }
}

// ===========================================================================
// File Downloader — Search Archives
// ===========================================================================

/**
 * Search for a string inside files within archives matching a pattern.
 *
 * Reads inputs from #fdArchSearchPattern, #fdArchFilePattern, and
 * #fdArchSearchString, then renders results in #fdArchSearchResults using
 * the shared _fdRenderSearchResults renderer.
 */
function fdSearchArchive() {
  var path = _fdLinkedPath || document.getElementById('fdPathSelect').value;
  if (!path) { alert('Please select a path first.'); return; }
  var archivePattern = document.getElementById('fdArchSearchPattern').value.trim();
  var filePattern    = document.getElementById('fdArchFilePattern').value.trim();
  var searchString   = document.getElementById('fdArchSearchString').value.trim();
  if (!archivePattern || !filePattern || !searchString) {
    alert('Please fill in the archive pattern, file pattern, and search string.');
    return;
  }
  var container = document.getElementById('fdArchSearchResults');
  container.textContent = 'Searching\u2026 (press Esc or click Stop to cancel)';

  var searchId  = _fdGenSearchId();
  var controller = new AbortController();
  _fdActiveSearches.archive = { id: searchId, controller: controller };
  _fdSetSearchRunning('archive', true);

  apiFetch('/api/v1/downloader/search-archive', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      path: path,
      archive_pattern: archivePattern,
      file_pattern: filePattern,
      search_string: searchString,
      search_id: searchId,
    }),
    signal: controller.signal,
    timeout: 120000,
    silent: true,
  })
    .then(function(r) { return r.ok ? r.json() : Promise.reject(r.status); })
    .then(function(data) { _fdRenderSearchResults(data, container); })
    .catch(function(err) {
      if (err && err.name === 'AbortError') {
        container.textContent = 'Search cancelled.';
      } else {
        container.textContent = 'Search failed: ' + err;
      }
    })
    .then(function() {
      delete _fdActiveSearches.archive;
      _fdSetSearchRunning('archive', false);
    });
}

// ===========================================================================
// DB Compare — named connection dropdown (#296)
// ===========================================================================

/**
 * Fetch named DB connections from the server and populate #dbcConnectionSelect.
 *
 * Called each time the DB Compare tab is activated. Silently no-ops when the
 * endpoint is unavailable (e.g. no API key configured) so the manual form
 * continues to work.
 */
async function loadDbConnections() {
  try {
    var resp = await apiFetch('/api/v1/system/db-connections', { silent: true });
    if (!resp.ok) return;
    var connections = await resp.json();
    var select = document.getElementById('dbcConnectionSelect');
    if (!select) return;
    // Preserve "— enter manually —" at index 0, remove any previously loaded options
    while (select.options.length > 1) select.remove(1);
    connections.forEach(function(c) {
      var opt = new Option((c.name || '') + ' \u00B7 ' + (c.schema || ''), c.name || '');
      // Store adapter on the option element for later use
      opt.dataset.adapter = c.adapter || 'oracle';
      select.add(opt);
    });
  } catch (_) {
    // Silently ignore — manual form still works
  }
}

/**
 * Handle changes to the named connection dropdown.
 *
 * When a named connection is selected the manual connection chip and form are
 * hidden. When "— enter manually —" is selected they are restored.  Updates
 * the run button enabled-state after every change.
 */
function onDbcConnectionSelectChange() {
  var select = document.getElementById('dbcConnectionSelect');
  var chip   = document.getElementById('dbcConnChip');
  var form   = document.getElementById('dbcConnForm');
  var warn   = document.getElementById('dbcHttpsWarning');
  if (!select) return;

  var selectedVal = select.value;

  if (selectedVal !== '') {
    // Named connection selected — hide manual form elements
    if (chip) chip.style.display = 'none';
    if (form) { form.style.display = 'none'; }
    if (warn) warn.style.display  = 'none';

    // Sync adapter dropdown to the connection's adapter when available
    var adapterSel = document.getElementById('dbcAdapterSelect');
    var selectedOpt = select.options[select.selectedIndex];
    if (adapterSel && selectedOpt && selectedOpt.dataset && selectedOpt.dataset.adapter) {
      adapterSel.value = selectedOpt.dataset.adapter;
    }
  } else {
    // Manual entry — restore chip visibility
    if (chip) chip.style.display = '';
    if (warn) warn.style.display = (location.protocol === 'http:') ? '' : 'none';
  }

  _updateDbcRunBtn();
}

// Wire up connection select change listener once DOM is ready
(function() {
  var connSel = document.getElementById('dbcConnectionSelect');
  if (connSel) connSel.addEventListener('change', onDbcConnectionSelectChange);
})();

// ===========================================================================
// DB Compare — profile dropdown (fetch from /api/v1/system/db-profiles)
// ===========================================================================
(function() {
  var _profiles = [];

  function _dbcLoadProfiles() {
    var sel = document.getElementById('dbcProfileSelect');
    if (!sel) return;
    var apiKeyEl = document.getElementById('apiKeyInput');
    var hdrs = apiKeyEl && apiKeyEl.value ? { 'X-API-Key': apiKeyEl.value } : {}; // gitleaks:allow
    apiFetch('/api/v1/system/db-profiles', { headers: hdrs, silent: true })
      .then(function(r) { return r.json(); })
      .then(function(data) {
        _profiles = data.profiles || [];
        var toRemove = [];
        for (var i = 0; i < sel.options.length; i++) {
          var v = sel.options[i].value;
          if (v !== '' && v !== '__custom__') toRemove.push(sel.options[i]);
        }
        toRemove.forEach(function(o) { sel.removeChild(o); });
        var customOpt = null;
        for (var j = 0; j < sel.options.length; j++) {
          if (sel.options[j].value === '__custom__') { customOpt = sel.options[j]; break; }
        }
        _profiles.forEach(function(p) {
          var opt = document.createElement('option');
          opt.value = p.name;
          opt.textContent = p.password_env_set ? p.name : (p.name + ' \u26A0\uFE0F');
          opt.dataset.passwordEnvSet = p.password_env_set ? '1' : '0';
          sel.insertBefore(opt, customOpt);
        });
      })
      .catch(function() {});
  }

  function _dbcApplyProfileSelection() {
    var sel    = document.getElementById('dbcProfileSelect');
    var manual = document.getElementById('dbcManualFields');
    var result = document.getElementById('dbcConnResult');
    if (!sel || !manual) return;
    var val = sel.value;
    var isNamed = val && val !== '__custom__';
    manual.style.display = isNamed ? 'none' : '';
    if (result) result.style.display = 'none';
    window._dbcRefreshChipFromProfile();
    if (typeof _updateDbcRunBtn === 'function') _updateDbcRunBtn();
  }

  window._dbcRefreshChipFromProfile = function() {
    var sel      = document.getElementById('dbcProfileSelect');
    var chipText = document.getElementById('dbcConnChipText');
    if (!chipText) return;
    var val = sel ? sel.value : '';
    chipText.textContent = '';
    chipText.appendChild(document.createTextNode('\uD83D\uDD0C '));
    var hostSpan = document.createElement('span');
    hostSpan.className = 'dbc-chip-host';
    if (val && val !== '__custom__') {
      hostSpan.textContent = val;
      chipText.appendChild(hostSpan);
    } else {
      var hostEl   = document.getElementById('dbcHost');
      var schemaEl = document.getElementById('dbcSchema');
      hostSpan.textContent = (hostEl && hostEl.value) ? hostEl.value : 'not configured';
      chipText.appendChild(hostSpan);
      var schema = schemaEl ? schemaEl.value : '';
      if (schema) {
        chipText.appendChild(document.createTextNode(' \u00B7 '));
        var schSpan = document.createElement('span');
        schSpan.textContent = schema;
        chipText.appendChild(schSpan);
      }
    }
  };

  window._dbcGetHost = function() {
    var sel = document.getElementById('dbcProfileSelect');
    if (sel && sel.value && sel.value !== '__custom__') return sel.value;
    return (document.getElementById('dbcHost') || {}).value || '';
  };

  var profileSel = document.getElementById('dbcProfileSelect');
  if (profileSel) {
    profileSel.addEventListener('change', _dbcApplyProfileSelection);
  }

  var dbcTabBtns = document.querySelectorAll('[data-tab="db-compare"], .tab-btn');
  dbcTabBtns.forEach(function(btn) {
    if (btn.textContent && btn.textContent.indexOf('DB Compare') !== -1) {
      btn.addEventListener('click', function() {
        if (_profiles.length === 0) _dbcLoadProfiles();
      });
    }
  });

  window._dbcLoadProfiles = _dbcLoadProfiles;
  window._dbcApplyProfileSelection = _dbcApplyProfileSelection;
})();

// ===========================================================================
// DB Compare — connection chip expand/collapse + sessionStorage + db-ping
// ===========================================================================
(function() {
  var _SS_KEYS = ['dbcHost', 'dbcUser', 'dbcSchema', 'dbcAdapter'];

  function _dbcRestoreSession() {
    _SS_KEYS.forEach(function(id) {
      var el  = document.getElementById(id);
      var val = sessionStorage.getItem('valdo-dbc-' + id);
      if (el && val) el.value = val;
    });
    _dbcRefreshChip();
  }

  function _dbcSaveSession() {
    _SS_KEYS.forEach(function(id) {
      var el = document.getElementById(id);
      if (el) sessionStorage.setItem('valdo-dbc-' + id, el.value);
    });
    // Password (dbcPassword) is intentionally excluded — never persisted
  }

  function _dbcRefreshChip() {
    var hostEl   = document.getElementById('dbcHost');
    var schemaEl = document.getElementById('dbcSchema');
    var chipText = document.getElementById('dbcConnChipText');
    if (!chipText) return;

    var host   = hostEl   ? hostEl.value   : '';
    var schema = schemaEl ? schemaEl.value : '';

    // Use textContent for user-supplied values to avoid XSS
    chipText.textContent = '';
    var icon = document.createTextNode('\uD83D\uDD0C ');
    var hostSpan = document.createElement('span');
    hostSpan.className = 'dbc-chip-host';
    hostSpan.textContent = host || 'not configured';
    chipText.appendChild(icon);
    chipText.appendChild(hostSpan);
    if (schema) {
      chipText.appendChild(document.createTextNode(' \u00B7 '));
      var schemaSpan = document.createElement('span');
      schemaSpan.textContent = schema;
      chipText.appendChild(schemaSpan);
    }
  }

  function _dbcToggleConnForm() {
    var chip = document.getElementById('dbcConnChip');
    var form = document.getElementById('dbcConnForm');
    var warn = document.getElementById('dbcHttpsWarning');
    if (!chip || !form) return;
    var isExpanded = chip.getAttribute('aria-expanded') === 'true';
    if (isExpanded) {
      _dbcSaveSession();
      form.style.display = 'none';
      chip.setAttribute('aria-expanded', 'false');
      if (warn) warn.style.display = 'none';
      _dbcRefreshChip();
    } else {
      form.style.display = '';
      chip.setAttribute('aria-expanded', 'true');
      if (warn && window.location.protocol !== 'https:') warn.style.display = '';
    }
  }

  var chip = document.getElementById('dbcConnChip');
  if (chip) {
    chip.addEventListener('click', _dbcToggleConnForm);
    chip.addEventListener('keydown', function(e) {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); _dbcToggleConnForm(); }
    });
  }

  // Test Connection
  var testBtn = document.getElementById('dbcTestConnBtn');
  if (testBtn) {
    testBtn.addEventListener('click', async function() {
      var btn    = this;
      var result = document.getElementById('dbcConnResult');
      btn.disabled = true;
      btn.textContent = '\u23F3 Testing\u2026';
      if (result) result.style.display = 'none';
      try {
        var fd = new FormData();
        var profileSel = document.getElementById('dbcProfileSelect');
        var profileVal = profileSel ? profileSel.value : '';
        if (profileVal && profileVal !== '__custom__') {
          var selOpt = profileSel.options[profileSel.selectedIndex];
          if (selOpt && selOpt.dataset.passwordEnvSet === '0') {
            if (result) {
              result.style.display = '';
              result.className = 'dbc-conn-result err';
              result.textContent = '\u274C Password env var for this profile is not set on the server';
            }
            btn.disabled = false;
            btn.textContent = '\uD83D\uDD17 Test Connection';
            return;
          }
          fd.append('profile_name', profileVal);
        } else {
          fd.append('db_host',     (document.getElementById('dbcHost')     || {}).value || '');
          fd.append('db_user',     (document.getElementById('dbcUser')     || {}).value || '');
          fd.append('db_password', (document.getElementById('dbcPassword') || {}).value || '');
          fd.append('db_schema',   (document.getElementById('dbcSchema')   || {}).value || '');
          fd.append('db_adapter',  (document.getElementById('dbcAdapter')  || {}).value || 'oracle');
        }
        var apiKeyEl = document.getElementById('apiKeyInput');
        var hdrs = apiKeyEl && apiKeyEl.value ? { 'X-API-Key': apiKeyEl.value } : {}; // gitleaks:allow
        var resp = await apiFetch('/api/v1/system/db-ping', { method: 'POST', body: fd, headers: hdrs });
        var data = await resp.json();
        if (result) {
          result.style.display = '';
          result.className = 'dbc-conn-result ' + (data.ok ? 'ok' : 'err');
          result.textContent = data.ok ? '\u2705 Connected' : '\u274C ' + (data.error || 'Connection failed');
        }
      } catch (err) {
        if (result) {
          result.style.display = '';
          result.className = 'dbc-conn-result err';
          result.textContent = '\u274C Request failed \u2014 check server is running';
        }
      } finally {
        btn.disabled = false;
        btn.textContent = '\uD83D\uDD17 Test Connection';
      }
    });
  }

  // Restore session on page load
  _dbcRestoreSession();
})();

// ===========================================================================
// DB Compare — drop zone, run button enable/disable, run handler, results
// ===========================================================================
var _dbcFile = null;

(function() {
  var dz = document.getElementById('dbcDropZone');
  var fi = document.getElementById('dbcFileInput');
  if (!dz || !fi) return;

  dz.addEventListener('click', function() { fi.click(); });
  dz.addEventListener('keydown', function(e) { if (e.key === 'Enter' || e.key === ' ') fi.click(); });
  dz.addEventListener('dragover', function(e) { e.preventDefault(); dz.classList.add('drag-over'); });
  dz.addEventListener('dragleave', function() { dz.classList.remove('drag-over'); });
  dz.addEventListener('drop', function(e) {
    e.preventDefault();
    dz.classList.remove('drag-over');
    var f = e.dataTransfer.files[0];
    if (f) { _dbcFile = f; dz.querySelector('.dz-label').textContent = f.name; _updateDbcRunBtn(); }
  });
  fi.addEventListener('change', function() {
    if (fi.files[0]) { _dbcFile = fi.files[0]; dz.querySelector('.dz-label').textContent = fi.files[0].name; _updateDbcRunBtn(); }
  });
})();

function _updateDbcRunBtn() {
  var btn = document.getElementById('dbcRunBtn');
  if (!btn) return;
  var hasFile        = !!_dbcFile;
  var hasMapping     = !!((document.getElementById('dbcMappingSelect') || {}).value);
  var hasSql         = !!(((document.getElementById('dbcSqlEditor') || {}).value || '').trim());
  var hasNamedConn   = !!((document.getElementById('dbcConnectionSelect') || {}).value);
  var hasHost        = !!(window._dbcGetHost ? window._dbcGetHost() : '');
  var hasConn        = hasNamedConn || hasHost;
  btn.disabled       = !(hasFile && hasMapping && hasSql && hasConn);
}

['dbcMappingSelect', 'dbcSqlEditor', 'dbcHost'].forEach(function(id) {
  var el = document.getElementById(id);
  if (el) el.addEventListener('input', _updateDbcRunBtn);
  if (el) el.addEventListener('change', _updateDbcRunBtn);
});

var _dbcRunBtn = document.getElementById('dbcRunBtn');
if (_dbcRunBtn) {
  _dbcRunBtn.addEventListener('click', async function() {
    var btn = this;
    btn.disabled = true;
    btn.textContent = '\u23F3 Running\u2026';
    var resultsEl = document.getElementById('dbcResults');
    if (resultsEl) resultsEl.style.display = 'none';

    try {
      var fd = new FormData();
      fd.append('actual_file',      _dbcFile);
      fd.append('query_or_table',   document.getElementById('dbcSqlEditor').value.trim());
      fd.append('mapping_id',       document.getElementById('dbcMappingSelect').value);
      fd.append('key_columns',      (document.getElementById('dbcKeyColumns') || {}).value || '');
      fd.append('output_format',    'json');
      fd.append('apply_transforms', document.getElementById('dbcApplyTransforms').checked ? 'true' : 'false');
      var profileSel2 = document.getElementById('dbcProfileSelect');
      var profileVal2 = profileSel2 ? profileSel2.value : '';
      if (profileVal2 && profileVal2 !== '__custom__') {
        fd.append('profile_name', profileVal2);
      } else {
        fd.append('db_host',     (document.getElementById('dbcHost')     || {}).value || '');
        fd.append('db_user',     (document.getElementById('dbcUser')     || {}).value || '');
        fd.append('db_password', (document.getElementById('dbcPassword') || {}).value || '');
        fd.append('db_schema',   (document.getElementById('dbcSchema')   || {}).value || '');
        fd.append('db_adapter',  (document.getElementById('dbcAdapter')  || {}).value || 'oracle');
      }

      var _connName = (document.getElementById('dbcConnectionSelect') || {}).value || '';
      if (_connName) {
        // Named connection — send connection_name; skip individual credential fields
        fd.append('connection_name', _connName);
      } else {
        // Manual entry — send individual credential fields
        fd.append('db_host',     (document.getElementById('dbcHost')     || {}).value || '');
        fd.append('db_user',     (document.getElementById('dbcUser')     || {}).value || '');
        fd.append('db_password', (document.getElementById('dbcPassword') || {}).value || '');
        fd.append('db_schema',   (document.getElementById('dbcSchema')   || {}).value || '');
      }

      var resp = await apiFetch('/api/v1/files/db-compare', { method: 'POST', body: fd });
      var data = await resp.json();

      if (!resp.ok) {
        var detail = (data && data.detail) ? data.detail : ('HTTP ' + resp.status);
        if (resp.status === 404) {
          _dbcShowResults(null, 'warn', '\u26A0\uFE0F Mapping not found: ' + detail);
        } else {
          _dbcShowResults(null, 'fail', '\u274C Server error \u2014 ' + detail);
        }
        return;
      }

      _dbcShowResults(
        data,
        data.workflow_status === 'passed' ? 'pass' : 'fail',
        data.workflow_status === 'passed'
          ? '\u2705 Compare complete'
          : '\u274C DB extraction failed \u2014 check your query and connection'
      );

      if (document.getElementById('dbcDownloadCsv').checked &&
          _dbcHasFieldStats(data.field_statistics)) {
        _dbcTriggerCsvDownload(data.field_statistics);
      }

    } catch (err) {
      _dbcShowResults(null, 'fail', '\u274C Request failed \u2014 check server is running');
    } finally {
      btn.disabled = false;
      btn.textContent = '\u25B6 Run DB Compare';
      _updateDbcRunBtn();
    }
  });
}

function _dbcShowResults(data, bannerClass, bannerText) {
  var resultsEl = document.getElementById('dbcResults');
  var bannerEl  = document.getElementById('dbcStatusBanner');
  var metricsEl = document.getElementById('dbcMetrics');
  if (!resultsEl) return;

  if (bannerEl) {
    bannerEl.className   = 'dbc-status-banner ' + bannerClass;
    bannerEl.textContent = bannerText;
  }

  if (metricsEl && data) {
    var isDbToFile = _dbcDirection === 'db-to-file';
    var cards = [
      { label: isDbToFile ? 'Source Rows' : 'Actual Rows', value: isDbToFile ? data.db_rows_extracted : data.total_rows_file2, color: '' },
      { label: isDbToFile ? 'Actual Rows' : 'Source Rows', value: isDbToFile ? data.total_rows_file2  : data.db_rows_extracted, color: '' },
      { label: 'Matching',       value: data.matching_rows, color: 'green' },
      { label: 'Differences',    value: data.differences,   color: 'amber' },
      { label: 'Only in Source', value: isDbToFile ? data.only_in_file1 : data.only_in_file2, color: 'red' },
      { label: 'Only in Actual', value: isDbToFile ? data.only_in_file2 : data.only_in_file1, color: 'red' },
    ];
    metricsEl.textContent = '';
    cards.forEach(function(c) {
      var card  = document.createElement('div');
      card.className = 'dbc-metric-card';
      var val   = document.createElement('div');
      val.className  = 'dbc-metric-value' + (c.color ? ' ' + c.color : '');
      val.textContent = c.value != null ? c.value.toLocaleString() : '\u2014';
      var lbl   = document.createElement('div');
      lbl.className  = 'dbc-metric-label';
      lbl.textContent = c.label;
      card.appendChild(val);
      card.appendChild(lbl);
      metricsEl.appendChild(card);
    });
  } else if (metricsEl) {
    metricsEl.textContent = '';
  }

  var dlRow = document.getElementById('dbcDownloadRow');
  var dlBtn = document.getElementById('dbcDownloadDiffBtn');
  if (dlRow && data) {
    var hasDiff = ((data.differences || 0) + (data.only_in_file1 || 0) + (data.only_in_file2 || 0)) > 0;
    dlRow.style.display = hasDiff ? '' : 'none';
    if (dlBtn) {
      if (!data.field_statistics) {
        dlBtn.disabled    = true;
        dlBtn.textContent = '\u26A0\uFE0F Detailed diff unavailable';
      } else {
        dlBtn.disabled    = false;
        dlBtn.textContent = '\u2B07 Download Diff CSV';
        dlBtn._fieldStatistics = data.field_statistics;
      }
    }
  } else if (dlRow) {
    dlRow.style.display = 'none';
  }

  resultsEl.style.display = '';
}

// ===========================================================================
// DB Compare — client-side diff CSV
//
// As with Excel Compare, the db-compare response returns ``field_statistics``
// as a SUMMARY DICT (field_difference_counts / field_difference_types), not a
// per-row diff list, so the CSV is a field-level diff summary. The normalizer
// tolerates either the dict or a legacy list shape (no .length-on-object bug).
// ===========================================================================
function _dbcNormalizeFieldStats(fieldStatistics) {
  if (!fieldStatistics) return [];
  if (Array.isArray(fieldStatistics)) {
    return fieldStatistics.map(function(stat) {
      return {
        field_name: stat.field_name || stat.field || '',
        count: stat.count != null ? stat.count : (stat.differences || 0),
        types: stat.field_difference_types || stat.types || null,
      };
    });
  }
  var counts = fieldStatistics.field_difference_counts || {};
  var types  = fieldStatistics.field_difference_types || {};
  return Object.keys(counts).map(function(field) {
    return { field_name: field, count: counts[field], types: types[field] || null };
  });
}

function _dbcHasFieldStats(fieldStatistics) {
  return _dbcNormalizeFieldStats(fieldStatistics).length > 0;
}

function _dbcBuildDiffCsv(fieldStatistics) {
  function esc(v) {
    var s = (v == null ? '' : String(v)).replace(/"/g, '""');
    return (s.indexOf(',') >= 0 || s.indexOf('"') >= 0 || s.indexOf('\n') >= 0) ? '"' + s + '"' : s;
  }
  var rows = ['field_name,difference_count,difference_types'];
  _dbcNormalizeFieldStats(fieldStatistics).forEach(function(stat) {
    var typesStr = stat.types && typeof stat.types === 'object'
      ? Object.keys(stat.types).map(function(t) { return t + ':' + stat.types[t]; }).join('|')
      : '';
    rows.push([esc(stat.field_name), esc(stat.count), esc(typesStr)].join(','));
  });
  return rows.join('\r\n');
}

function _dbcTriggerCsvDownload(fieldStatistics) {
  var csv  = _dbcBuildDiffCsv(fieldStatistics);
  var blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  var url  = URL.createObjectURL(blob);
  var a    = document.createElement('a');
  a.href     = url;
  a.download = 'db_compare_diff.csv';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(function() { URL.revokeObjectURL(url); }, 10000);
}

var _dbcDlBtn = document.getElementById('dbcDownloadDiffBtn');
if (_dbcDlBtn) {
  _dbcDlBtn.addEventListener('click', function() {
    var btn   = this;
    var stats = btn._fieldStatistics;
    if (!stats) return;
    btn.disabled    = true;
    btn.textContent = '\u23F3 Building CSV\u2026';
    setTimeout(function() {
      try { _dbcTriggerCsvDownload(stats); }
      finally {
        btn.disabled    = false;
        btn.textContent = '\u2B07 Download Diff CSV';
      }
    }, 0);
  });
}

// ===========================================================================
// Excel Compare (S24-5) \u2014 mirrors DB Compare exactly, calling the
// POST /api/v1/files/excel-compare endpoint (S24-3). Compares an uploaded
// .xlsx workbook against a database query/table. Direction swap toggles which
// side is treated as the source of truth (db-source <-> excel-source).
//
// Password posture is identical to DB Compare: host/user/schema/adapter are
// persisted to sessionStorage for convenience, the password is NEVER stored
// (not in sessionStorage, not in localStorage). All network calls go through
// the central apiFetch() wrapper (#427), never raw fetch().
// ===========================================================================

// 'db-source' (DB is source of truth) <-> 'excel-source' (Excel is source of truth)
var _xlcDirection = 'db-source';

/**
 * Update the Excel Compare direction bar + split-panel styling and the
 * placeholder hints when the direction is swapped.
 */
function _xlcUpdateDirection() {
  var isDbSource = _xlcDirection === 'db-source';
  var lbl = document.getElementById('xlcDirectionLabel');
  if (lbl) lbl.textContent = isDbSource
    ? 'DB is source of truth \u00B7 Excel is actual'
    : 'Excel is source of truth \u00B7 DB is actual';

  var dbPanel     = document.getElementById('xlcDbPanel');
  var excelPanel  = document.getElementById('xlcExcelPanel');
  var dbHeader    = document.getElementById('xlcDbPanelHeader');
  var excelHeader = document.getElementById('xlcExcelPanelHeader');

  if (dbPanel)     dbPanel.className     = 'dbc-panel ' + (isDbSource ? 'dbc-panel--source' : 'dbc-panel--actual');
  if (excelPanel)  excelPanel.className  = 'dbc-panel ' + (isDbSource ? 'dbc-panel--actual' : 'dbc-panel--source');
  if (dbHeader)    dbHeader.className     = 'dbc-panel-header' + (isDbSource ? '' : ' dbc-panel-header--actual');
  if (excelHeader) excelHeader.className  = 'dbc-panel-header' + (isDbSource ? ' dbc-panel-header--actual' : '');
}

(function() {
  var swapBtn = document.getElementById('xlcSwapBtn');
  if (swapBtn) {
    swapBtn.addEventListener('click', function() {
      _xlcDirection = (_xlcDirection === 'db-source') ? 'excel-source' : 'db-source';
      _xlcUpdateDirection();
    });
  }
})();

/**
 * Load named DB connections into the Excel Compare connection dropdown.
 * Silently no-ops when the endpoint is unavailable so the manual form works.
 */
async function loadExcelDbConnections() {
  try {
    var resp = await apiFetch('/api/v1/system/db-connections', { silent: true });
    if (!resp.ok) return;
    var connections = await resp.json();
    var select = document.getElementById('xlcConnectionSelect');
    if (!select) return;
    while (select.options.length > 1) select.remove(1);
    connections.forEach(function(c) {
      var opt = new Option((c.name || '') + ' \u00B7 ' + (c.schema || ''), c.name || '');
      opt.dataset.adapter = c.adapter || 'oracle';
      select.add(opt);
    });
  } catch (_) {
    // Silently ignore \u2014 manual form still works
  }
}

/**
 * Handle changes to the Excel Compare named connection dropdown \u2014 hide the
 * manual chip/form when a named connection is chosen, restore otherwise.
 */
function onXlcConnectionSelectChange() {
  var select = document.getElementById('xlcConnectionSelect');
  var chip   = document.getElementById('xlcConnChip');
  var form   = document.getElementById('xlcConnForm');
  var warn   = document.getElementById('xlcHttpsWarning');
  if (!select) return;

  if (select.value !== '') {
    if (chip) chip.style.display = 'none';
    if (form) form.style.display = 'none';
    if (warn) warn.style.display = 'none';
    var adapterSel  = document.getElementById('xlcAdapterSelect');
    var selectedOpt = select.options[select.selectedIndex];
    if (adapterSel && selectedOpt && selectedOpt.dataset && selectedOpt.dataset.adapter) {
      adapterSel.value = selectedOpt.dataset.adapter;
    }
  } else {
    if (chip) chip.style.display = '';
    if (warn) warn.style.display = (location.protocol === 'http:') ? '' : 'none';
  }
  _updateExcelCompareBtn();
}

(function() {
  var connSel = document.getElementById('xlcConnectionSelect');
  if (connSel) connSel.addEventListener('change', onXlcConnectionSelectChange);
})();

// ---------------------------------------------------------------------------
// Excel Compare \u2014 profile dropdown (fetch from /api/v1/system/db-profiles)
// ---------------------------------------------------------------------------
(function() {
  var _xlcProfiles = [];

  function _xlcLoadProfiles() {
    var sel = document.getElementById('xlcProfileSelect');
    if (!sel) return;
    var apiKeyEl = document.getElementById('apiKeyInput');
    var hdrs = apiKeyEl && apiKeyEl.value ? { 'X-API-Key': apiKeyEl.value } : {}; // gitleaks:allow
    apiFetch('/api/v1/system/db-profiles', { headers: hdrs, silent: true })
      .then(function(r) { return r.json(); })
      .then(function(data) {
        _xlcProfiles = data.profiles || [];
        var toRemove = [];
        for (var i = 0; i < sel.options.length; i++) {
          var v = sel.options[i].value;
          if (v !== '' && v !== '__custom__') toRemove.push(sel.options[i]);
        }
        toRemove.forEach(function(o) { sel.removeChild(o); });
        var customOpt = null;
        for (var j = 0; j < sel.options.length; j++) {
          if (sel.options[j].value === '__custom__') { customOpt = sel.options[j]; break; }
        }
        _xlcProfiles.forEach(function(p) {
          var opt = document.createElement('option');
          opt.value = p.name;
          opt.textContent = p.password_env_set ? p.name : (p.name + ' \u26A0\uFE0F');
          opt.dataset.passwordEnvSet = p.password_env_set ? '1' : '0';
          sel.insertBefore(opt, customOpt);
        });
      })
      .catch(function() {});
  }

  function _xlcApplyProfileSelection() {
    var sel    = document.getElementById('xlcProfileSelect');
    var manual = document.getElementById('xlcManualFields');
    var result = document.getElementById('xlcConnResult');
    if (!sel || !manual) return;
    var val = sel.value;
    var isNamed = val && val !== '__custom__';
    manual.style.display = isNamed ? 'none' : '';
    if (result) result.style.display = 'none';
    window._xlcRefreshChipFromProfile();
    if (typeof _updateExcelCompareBtn === 'function') _updateExcelCompareBtn();
  }

  window._xlcRefreshChipFromProfile = function() {
    var sel      = document.getElementById('xlcProfileSelect');
    var chipText = document.getElementById('xlcConnChipText');
    if (!chipText) return;
    var val = sel ? sel.value : '';
    chipText.textContent = '';
    chipText.appendChild(document.createTextNode('\uD83D\uDD0C '));
    var hostSpan = document.createElement('span');
    hostSpan.className = 'dbc-chip-host';
    if (val && val !== '__custom__') {
      hostSpan.textContent = val;
      chipText.appendChild(hostSpan);
    } else {
      var hostEl   = document.getElementById('xlcHost');
      var schemaEl = document.getElementById('xlcSchema');
      hostSpan.textContent = (hostEl && hostEl.value) ? hostEl.value : 'not configured';
      chipText.appendChild(hostSpan);
      var schema = schemaEl ? schemaEl.value : '';
      if (schema) {
        chipText.appendChild(document.createTextNode(' \u00B7 '));
        var schSpan = document.createElement('span');
        schSpan.textContent = schema;
        chipText.appendChild(schSpan);
      }
    }
  };

  window._xlcGetHost = function() {
    var sel = document.getElementById('xlcProfileSelect');
    if (sel && sel.value && sel.value !== '__custom__') return sel.value;
    return (document.getElementById('xlcHost') || {}).value || '';
  };

  var profileSel = document.getElementById('xlcProfileSelect');
  if (profileSel) profileSel.addEventListener('change', _xlcApplyProfileSelection);

  window._xlcLoadProfiles = _xlcLoadProfiles;
  window._xlcApplyProfileSelection = _xlcApplyProfileSelection;
})();

// ---------------------------------------------------------------------------
// Excel Compare \u2014 connection chip expand/collapse + sessionStorage + db-ping
// Password (xlcPassword) is intentionally excluded from sessionStorage.
// ---------------------------------------------------------------------------
(function() {
  var _SS_KEYS = ['xlcHost', 'xlcUser', 'xlcSchema', 'xlcAdapter'];

  function _xlcRestoreSession() {
    _SS_KEYS.forEach(function(id) {
      var el  = document.getElementById(id);
      var val = sessionStorage.getItem('valdo-xlc-' + id);
      if (el && val) el.value = val;
    });
    _xlcRefreshChip();
  }

  function _xlcSaveSession() {
    _SS_KEYS.forEach(function(id) {
      var el = document.getElementById(id);
      if (el) sessionStorage.setItem('valdo-xlc-' + id, el.value);
    });
    // Password (xlcPassword) is intentionally excluded \u2014 never persisted
  }

  function _xlcRefreshChip() {
    var hostEl   = document.getElementById('xlcHost');
    var schemaEl = document.getElementById('xlcSchema');
    var chipText = document.getElementById('xlcConnChipText');
    if (!chipText) return;

    var host   = hostEl   ? hostEl.value   : '';
    var schema = schemaEl ? schemaEl.value : '';

    chipText.textContent = '';
    var icon = document.createTextNode('\uD83D\uDD0C ');
    var hostSpan = document.createElement('span');
    hostSpan.className = 'dbc-chip-host';
    hostSpan.textContent = host || 'not configured';
    chipText.appendChild(icon);
    chipText.appendChild(hostSpan);
    if (schema) {
      chipText.appendChild(document.createTextNode(' \u00B7 '));
      var schemaSpan = document.createElement('span');
      schemaSpan.textContent = schema;
      chipText.appendChild(schemaSpan);
    }
  }

  function _xlcToggleConnForm() {
    var chip = document.getElementById('xlcConnChip');
    var form = document.getElementById('xlcConnForm');
    var warn = document.getElementById('xlcHttpsWarning');
    if (!chip || !form) return;
    var isExpanded = chip.getAttribute('aria-expanded') === 'true';
    if (isExpanded) {
      _xlcSaveSession();
      form.style.display = 'none';
      chip.setAttribute('aria-expanded', 'false');
      if (warn) warn.style.display = 'none';
      _xlcRefreshChip();
    } else {
      form.style.display = '';
      chip.setAttribute('aria-expanded', 'true');
      if (warn && window.location.protocol !== 'https:') warn.style.display = '';
    }
  }

  var chip = document.getElementById('xlcConnChip');
  if (chip) {
    chip.addEventListener('click', _xlcToggleConnForm);
    chip.addEventListener('keydown', function(e) {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); _xlcToggleConnForm(); }
    });
  }

  // Test Connection
  var testBtn = document.getElementById('xlcTestConnBtn');
  if (testBtn) {
    testBtn.addEventListener('click', async function() {
      var btn    = this;
      var result = document.getElementById('xlcConnResult');
      btn.disabled = true;
      btn.textContent = '\u23F3 Testing\u2026';
      if (result) result.style.display = 'none';
      try {
        var fd = new FormData();
        var profileSel = document.getElementById('xlcProfileSelect');
        var profileVal = profileSel ? profileSel.value : '';
        if (profileVal && profileVal !== '__custom__') {
          var selOpt = profileSel.options[profileSel.selectedIndex];
          if (selOpt && selOpt.dataset.passwordEnvSet === '0') {
            if (result) {
              result.style.display = '';
              result.className = 'dbc-conn-result err';
              result.textContent = '\u274C Password env var for this profile is not set on the server';
            }
            btn.disabled = false;
            btn.textContent = '\uD83D\uDD17 Test Connection';
            return;
          }
          fd.append('profile_name', profileVal);
        } else {
          fd.append('db_host',     (document.getElementById('xlcHost')     || {}).value || '');
          fd.append('db_user',     (document.getElementById('xlcUser')     || {}).value || '');
          fd.append('db_password', (document.getElementById('xlcPassword') || {}).value || '');
          fd.append('db_schema',   (document.getElementById('xlcSchema')   || {}).value || '');
          fd.append('db_adapter',  (document.getElementById('xlcAdapter')  || {}).value || 'oracle');
        }
        var apiKeyEl = document.getElementById('apiKeyInput');
        var hdrs = apiKeyEl && apiKeyEl.value ? { 'X-API-Key': apiKeyEl.value } : {}; // gitleaks:allow
        var resp = await apiFetch('/api/v1/system/db-ping', { method: 'POST', body: fd, headers: hdrs });
        var data = await resp.json();
        if (result) {
          result.style.display = '';
          result.className = 'dbc-conn-result ' + (data.ok ? 'ok' : 'err');
          result.textContent = data.ok ? '\u2705 Connected' : '\u274C ' + (data.error || 'Connection failed');
        }
      } catch (err) {
        if (result) {
          result.style.display = '';
          result.className = 'dbc-conn-result err';
          result.textContent = '\u274C Request failed \u2014 check server is running';
        }
      } finally {
        btn.disabled = false;
        btn.textContent = '\uD83D\uDD17 Test Connection';
      }
    });
  }

  // Restore session on page load (host/user/schema/adapter only \u2014 never password)
  _xlcRestoreSession();
})();

// ---------------------------------------------------------------------------
// Excel Compare \u2014 drop zone, run button enable/disable, run handler, results
// ---------------------------------------------------------------------------
var _xlcFile = null;

(function() {
  var dz = document.getElementById('xlcDropZone');
  var fi = document.getElementById('xlcFileInput');
  if (!dz || !fi) return;

  dz.addEventListener('click', function() { fi.click(); });
  dz.addEventListener('keydown', function(e) { if (e.key === 'Enter' || e.key === ' ') fi.click(); });
  dz.addEventListener('dragover', function(e) { e.preventDefault(); dz.classList.add('drag-over'); });
  dz.addEventListener('dragleave', function() { dz.classList.remove('drag-over'); });
  dz.addEventListener('drop', function(e) {
    e.preventDefault();
    dz.classList.remove('drag-over');
    var f = e.dataTransfer.files[0];
    if (f) { _xlcFile = f; dz.querySelector('.dz-label').textContent = f.name; _updateExcelCompareBtn(); }
  });
  fi.addEventListener('change', function() {
    if (fi.files[0]) { _xlcFile = fi.files[0]; dz.querySelector('.dz-label').textContent = fi.files[0].name; _updateExcelCompareBtn(); }
  });
})();

function _updateExcelCompareBtn() {
  var btn = document.getElementById('excelCompareBtn');
  if (!btn) return;
  var hasFile      = !!_xlcFile;
  var hasQuery     = !!(((document.getElementById('xlcSqlEditor') || {}).value || '').trim());
  var hasNamedConn = !!((document.getElementById('xlcConnectionSelect') || {}).value);
  var hasHost      = !!(window._xlcGetHost ? window._xlcGetHost() : '');
  var hasConn      = hasNamedConn || hasHost;
  btn.disabled     = !(hasFile && hasQuery && hasConn);
}

['xlcSqlEditor', 'xlcHost'].forEach(function(id) {
  var el = document.getElementById(id);
  if (el) el.addEventListener('input', _updateExcelCompareBtn);
  if (el) el.addEventListener('change', _updateExcelCompareBtn);
});

var _excelCompareBtn = document.getElementById('excelCompareBtn');
if (_excelCompareBtn) {
  _excelCompareBtn.addEventListener('click', async function() {
    var btn = this;
    btn.disabled = true;
    btn.textContent = '\u23F3 Running\u2026';
    var resultsEl = document.getElementById('xlcResults');
    if (resultsEl) resultsEl.style.display = 'none';

    var wantHtml = (document.getElementById('xlcHtmlReport') || {}).checked;

    try {
      var fd = new FormData();
      fd.append('excel_file',     _xlcFile);
      fd.append('query_or_table', document.getElementById('xlcSqlEditor').value.trim());
      fd.append('key_columns',    (document.getElementById('xlcKeyColumns') || {}).value || '');
      fd.append('direction',      _xlcDirection);
      fd.append('output_format',  wantHtml ? 'html' : 'json');

      var sheetVal = (document.getElementById('xlcSheet') || {}).value || '';
      if (sheetVal.trim()) fd.append('sheet', sheetVal.trim());
      var headerVal = (document.getElementById('xlcHeaderRow') || {}).value;
      fd.append('header_row', (headerVal === '' || headerVal == null) ? '0' : String(headerVal));

      var profileSel2 = document.getElementById('xlcProfileSelect');
      var profileVal2 = profileSel2 ? profileSel2.value : '';
      var connName    = (document.getElementById('xlcConnectionSelect') || {}).value || '';
      if (connName) {
        // Named connection \u2014 send connection_name; skip individual credential fields
        fd.append('connection_name', connName);
      } else if (profileVal2 && profileVal2 !== '__custom__') {
        // Named server-side profile \u2014 password resolved server-side from env
        fd.append('profile_name', profileVal2);
      } else {
        // Manual entry \u2014 send individual credential fields
        fd.append('db_host',     (document.getElementById('xlcHost')     || {}).value || '');
        fd.append('db_user',     (document.getElementById('xlcUser')     || {}).value || '');
        fd.append('db_password', (document.getElementById('xlcPassword') || {}).value || '');
        fd.append('db_schema',   (document.getElementById('xlcSchema')   || {}).value || '');
        fd.append('db_adapter',  (document.getElementById('xlcAdapter')  || {}).value || 'oracle');
      }

      var resp = await apiFetch('/api/v1/files/excel-compare', { method: 'POST', body: fd });
      var data = await resp.json();

      if (!resp.ok) {
        var detail = (data && data.detail) ? data.detail : ('HTTP ' + resp.status);
        if (resp.status === 404) {
          _xlcShowResults(null, 'warn', '\u26A0\uFE0F Not found: ' + detail);
        } else {
          _xlcShowResults(null, 'fail', '\u274C Server error \u2014 ' + detail);
        }
        return;
      }

      _xlcShowResults(
        data,
        data.workflow_status === 'passed' ? 'pass' : 'fail',
        data.workflow_status === 'passed'
          ? '\u2705 Compare complete'
          : '\u274C Compare failed \u2014 check your query, sheet/header, and connection'
      );

      if ((document.getElementById('xlcDownloadCsv') || {}).checked &&
          _xlcHasFieldStats(data.field_statistics)) {
        _xlcTriggerCsvDownload(data.field_statistics);
      }

    } catch (err) {
      _xlcShowResults(null, 'fail', '\u274C Request failed \u2014 check server is running');
    } finally {
      btn.disabled = false;
      btn.textContent = '\u25B6 Run Excel Compare';
      _updateExcelCompareBtn();
    }
  });
}

/**
 * Render the Excel Compare status banner, metric cards, HTML report link,
 * and diff-CSV download row from the excel-compare response.
 */
function _xlcShowResults(data, bannerClass, bannerText) {
  var resultsEl = document.getElementById('xlcResults');
  var bannerEl  = document.getElementById('xlcStatusBanner');
  var metricsEl = document.getElementById('xlcMetrics');
  if (!resultsEl) return;

  if (bannerEl) {
    bannerEl.className   = 'dbc-status-banner ' + bannerClass;
    bannerEl.textContent = bannerText;
  }

  if (metricsEl && data) {
    var isDbSource = (data.direction || _xlcDirection) === 'db-source';
    var cards = [
      { label: 'DB Rows',        value: data.db_rows_extracted, color: '' },
      { label: 'Excel Rows',     value: data.excel_rows_read,   color: '' },
      { label: 'Matching',       value: data.matching_rows,     color: 'green' },
      { label: 'Differences',    value: data.differences,       color: 'amber' },
      { label: 'Only in Source', value: data.only_in_file1,     color: 'red' },
      { label: 'Only in Actual', value: data.only_in_file2,     color: 'red' },
      { label: 'Structure',      value: data.structure_compatible ? '\u2713' : '\u2717',
        color: data.structure_compatible ? 'green' : 'red', raw: true },
    ];
    metricsEl.textContent = '';
    cards.forEach(function(c) {
      var card  = document.createElement('div');
      card.className = 'dbc-metric-card';
      var val   = document.createElement('div');
      val.className  = 'dbc-metric-value' + (c.color ? ' ' + c.color : '');
      if (c.raw) {
        val.textContent = c.value;
      } else {
        val.textContent = c.value != null ? c.value.toLocaleString() : '\u2014';
      }
      var lbl   = document.createElement('div');
      lbl.className  = 'dbc-metric-label';
      lbl.textContent = c.label;
      card.appendChild(val);
      card.appendChild(lbl);
      metricsEl.appendChild(card);
    });
  } else if (metricsEl) {
    metricsEl.textContent = '';
  }

  // HTML report link (when output_format=html produced a report_url)
  var reportRow = document.getElementById('xlcReportRow');
  if (reportRow) {
    reportRow.textContent = '';
    if (data && data.report_url) {
      var a = document.createElement('a');
      a.className = 'report-link';
      a.textContent = '\uD83D\uDCC4 Open HTML report';
      a.href = data.report_url;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      reportRow.appendChild(a);
      reportRow.style.display = '';
    } else {
      reportRow.style.display = 'none';
    }
  }

  // Structure error messages (if any)
  if (data && Array.isArray(data.structure_errors) && data.structure_errors.length && bannerEl) {
    bannerEl.textContent = bannerText + ' \u00B7 ' + data.structure_errors.join('; ');
  }

  var dlRow = document.getElementById('xlcDownloadRow');
  var dlBtn = document.getElementById('xlcDownloadDiffBtn');
  if (dlRow && data) {
    var hasDiff = ((data.differences || 0) + (data.only_in_file1 || 0) + (data.only_in_file2 || 0)) > 0;
    dlRow.style.display = hasDiff ? '' : 'none';
    if (dlBtn) {
      if (!data.field_statistics) {
        dlBtn.disabled    = true;
        dlBtn.textContent = '\u26A0\uFE0F Detailed diff unavailable';
      } else {
        dlBtn.disabled    = false;
        dlBtn.textContent = '\u2B07 Download Diff CSV';
        dlBtn._fieldStatistics = data.field_statistics;
      }
    }
  } else if (dlRow) {
    dlRow.style.display = 'none';
  }

  resultsEl.style.display = '';
}

// ---------------------------------------------------------------------------
// Excel Compare \u2014 client-side diff CSV (mirrors DB Compare)
//
// The excel-compare response (ExcelCompareResult / run_compare_service) returns
// ``field_statistics`` as a SUMMARY DICT, not a list of per-row diff records:
//   { fields_with_differences, field_difference_counts: {field: count, ...},
//     field_difference_types: {field: {type: count, ...}, ...},
//     most_different_field }
// The endpoint does not echo per-row diff rows (only_in_file1/2 and differences
// are integer counts), so the CSV is a field-level diff summary built from the
// aggregate dict. _xlcNormalizeFieldStats tolerates either the dict form or a
// legacy list form so the download never silently no-ops on the object shape.
// ---------------------------------------------------------------------------
function _xlcNormalizeFieldStats(fieldStatistics) {
  // Returns an array of {field_name, count, types} rows from either the
  // summary-dict shape or a legacy array shape. Empty array if no diffs.
  if (!fieldStatistics) return [];
  if (Array.isArray(fieldStatistics)) {
    return fieldStatistics.map(function(stat) {
      return {
        field_name: stat.field_name || stat.field || '',
        count: stat.count != null ? stat.count : (stat.differences || 0),
        types: stat.field_difference_types || stat.types || null,
      };
    });
  }
  var counts = fieldStatistics.field_difference_counts || {};
  var types  = fieldStatistics.field_difference_types || {};
  return Object.keys(counts).map(function(field) {
    return { field_name: field, count: counts[field], types: types[field] || null };
  });
}

function _xlcHasFieldStats(fieldStatistics) {
  return _xlcNormalizeFieldStats(fieldStatistics).length > 0;
}

function _xlcBuildDiffCsv(fieldStatistics) {
  function esc(v) {
    var s = (v == null ? '' : String(v)).replace(/"/g, '""');
    return (s.indexOf(',') >= 0 || s.indexOf('"') >= 0 || s.indexOf('\n') >= 0) ? '"' + s + '"' : s;
  }
  var rows = ['field_name,difference_count,difference_types'];
  _xlcNormalizeFieldStats(fieldStatistics).forEach(function(stat) {
    var typesStr = stat.types && typeof stat.types === 'object'
      ? Object.keys(stat.types).map(function(t) { return t + ':' + stat.types[t]; }).join('|')
      : '';
    rows.push([esc(stat.field_name), esc(stat.count), esc(typesStr)].join(','));
  });
  return rows.join('\r\n');
}

function _xlcTriggerCsvDownload(fieldStatistics) {
  var csv  = _xlcBuildDiffCsv(fieldStatistics);
  var blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  var url  = URL.createObjectURL(blob);
  var a    = document.createElement('a');
  a.href     = url;
  a.download = 'excel_compare_diff.csv';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(function() { URL.revokeObjectURL(url); }, 10000);
}

var _xlcDlBtn = document.getElementById('xlcDownloadDiffBtn');
if (_xlcDlBtn) {
  _xlcDlBtn.addEventListener('click', function() {
    var btn   = this;
    var stats = btn._fieldStatistics;
    if (!stats) return;
    btn.disabled    = true;
    btn.textContent = '\u23F3 Building CSV\u2026';
    setTimeout(function() {
      try { _xlcTriggerCsvDownload(stats); }
      finally {
        btn.disabled    = false;
        btn.textContent = '\u2B07 Download Diff CSV';
      }
    }, 0);
  });
}

// ===========================================================================
// #407 \u2014 Reconcile mapping vs table (DB Compare tab)
//
// Thin client over POST /api/v2/reconcile. Picks the selected mapping +
// table + schema and renders the structured field-level verdict (status
// banner + counts + mismatch / advisory / error lists). The backend selects
// the DB adapter from DB_ADAPTER; the panel surfaces whatever the server
// resolved in the verdict's db_adapter field. Password is never handled
// here \u2014 reconcile reads schema metadata via the server-side adapter config.
// ===========================================================================

/**
 * Render a single titled list of verdict messages into a container.
 *
 * @param {string} title    Section heading (e.g. "Type mismatches").
 * @param {string[]} items  Message strings.
 * @param {string} kind     CSS suffix: 'mismatch' | 'advisory' | 'error'.
 * @returns {string} HTML string (empty when items is empty).
 */
function _recRenderList(title, items, kind) {
  if (!items || !items.length) { return ''; }
  var lis = items.map(function(m) { return '<li>' + _escHtml(m) + '</li>'; }).join('');
  return '<div class="rec-list-title">' + _escHtml(title) + '</div>' +
         '<ul class="rec-list rec-list-' + kind + '">' + lis + '</ul>';
}

/**
 * Render the reconcile verdict object into #reconcileResult.
 *
 * @param {Object} v  Verdict dict from POST /api/v2/reconcile.
 */
function _recRenderVerdict(v) {
  var el = document.getElementById('reconcileResult');
  if (!el) { return; }
  var statusText = {
    clean:      '\u2705 Clean \u2014 all mapped columns match',
    advisories: '\u2139\ufe0f Advisories \u2014 compatible with informational notes',
    mismatch:   '\u26a0\ufe0f Type mismatch \u2014 one or more columns conflict',
    error:      '\u274c Error \u2014 missing table or required column'
  }[v.status] || v.status;

  var s = v.summary || {};
  var html = '<div class="rec-verdict-banner rec-verdict-' + _escHtml(v.status) + '">' +
             _escHtml(statusText) + '</div>';
  html += '<div class="rec-counts">' +
    '<span>Table: <b>' + _escHtml(v.table || '\u2014') + '</b></span>' +
    '<span>Adapter: <b>' + _escHtml(v.db_adapter || '\u2014') + '</b></span>' +
    '<span>Mapped: <b>' + (s.mapped_columns || 0) + '</b></span>' +
    '<span>DB columns: <b>' + (s.database_columns || 0) + '</b></span>' +
    '<span>Mismatches: <b>' + (s.mismatch_count || 0) + '</b></span>' +
    '<span>Advisories: <b>' + (s.advisory_count || 0) + '</b></span>' +
    '</div>';
  html += _recRenderList('Errors', v.errors, 'error');
  html += _recRenderList('Type mismatches', v.mismatches, 'mismatch');
  html += _recRenderList('Advisories', v.advisories, 'advisory');
  if (v.status === 'clean') {
    html += '<div class="rec-list-title">All field types reconcile cleanly.</div>';
  }
  el.innerHTML = html;
  el.style.display = '';
}

var _btnReconcile = document.getElementById('btnReconcile');
if (_btnReconcile) {
  _btnReconcile.addEventListener('click', async function() {
    var btn = this;
    var resultEl = document.getElementById('reconcileResult');
    var mapping  = (document.getElementById('reconcileMappingSelect') || {}).value || '';
    var table    = ((document.getElementById('reconcileTable')  || {}).value || '').trim();
    var schema   = ((document.getElementById('reconcileSchema') || {}).value || '').trim();

    if (!mapping) {
      if (resultEl) {
        resultEl.innerHTML = '<div class="rec-verdict-banner rec-verdict-error">Select a mapping first.</div>';
        resultEl.style.display = '';
      }
      return;
    }

    btn.disabled = true;
    var origText = btn.innerHTML;
    btn.innerHTML = '\u23f3 Reconciling\u2026';
    try {
      var body = { mapping: mapping };
      if (table)  { body.table  = table; }
      if (schema) { body.schema = schema; }
      var resp = await apiFetch('/api/v2/reconcile', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      var data = await resp.json();
      if (!resp.ok) {
        var msg = (data && data.detail) ? data.detail : ('HTTP ' + resp.status);
        if (resultEl) {
          resultEl.innerHTML = '<div class="rec-verdict-banner rec-verdict-error">' +
            _escHtml(String(msg)) + '</div>';
          resultEl.style.display = '';
        }
        return;
      }
      _recRenderVerdict(data);
    } catch (err) {
      if (resultEl) {
        resultEl.innerHTML = '<div class="rec-verdict-banner rec-verdict-error">' +
          _escHtml('Reconcile failed: ' + err) + '</div>';
        resultEl.style.display = '';
      }
    } finally {
      btn.disabled = false;
      btn.innerHTML = origText;
    }
  });
}

// ===========================================================================
// Source Editor tab (EE-S1 scaffold)
//
// Two operations:
//   1. loadOnboardingSources() \u2014 GET /api/v2/onboarding/sources and render
//      the committed source list as a table.
//   2. submitOnboardingPreview() \u2014 POST the selected .xlsx to
//      /api/v2/onboarding/preview and dump the JSON response as a <pre>.
//
// Live tree rendering + drift detection is EE-S2. The current scaffold is
// intentionally minimal: prove the round-trip works.
// ===========================================================================

/** Currently selected workbook File object for the preview form. */
var _seSelectedFile = null;

/**
 * Fetch the committed source list and render it into #seSourcesContent.
 *
 * Called whenever the Source Editor tab is activated, plus manually via
 * the Refresh button. Failures are surfaced as an error message in the
 * panel rather than throwing \u2014 the UI must stay usable when the server
 * is briefly unavailable.
 */
function loadOnboardingSources() {
  var box = document.getElementById('seSourcesContent');
  if (!box) return;
  box.innerHTML = '<p class="empty-msg">Loading sources&hellip;</p>';

  apiFetch('/api/v2/onboarding/sources')
    .then(function(r) {
      if (!r.ok) {
        throw new Error('HTTP ' + r.status + ' ' + r.statusText);
      }
      return r.json();
    })
    .then(function(data) {
      var sources = (data && data.sources) || [];
      if (!sources.length) {
        box.innerHTML =
          '<p class="empty-msg">No committed sources found under ' +
          '<code>config/e2e/sources/</code>.</p>';
        return;
      }
      var rows = sources.map(function(s) {
        var mtime = s.last_modified
          ? _escHtml(String(s.last_modified).slice(0, 19).replace('T', ' '))
          : '\u2014';
        return (
          '<tr>' +
            '<td class="se-sources-name">' + _escHtml(s.name) + '</td>' +
            '<td class="se-sources-count">' +
              _escHtml(String(s.input_files_count != null ? s.input_files_count : 0)) +
            '</td>' +
            '<td class="se-sources-count">' +
              _escHtml(String(s.output_files_count != null ? s.output_files_count : 0)) +
            '</td>' +
            '<td class="se-sources-mtime">' + mtime + '</td>' +
          '</tr>'
        );
      }).join('');
      box.innerHTML =
        '<table class="se-sources-table">' +
          '<thead><tr>' +
            '<th>Source</th>' +
            '<th>Inputs</th>' +
            '<th>Outputs</th>' +
            '<th>Last Modified (UTC)</th>' +
          '</tr></thead>' +
          '<tbody>' + rows + '</tbody>' +
        '</table>';
    })
    .catch(function(err) {
      box.innerHTML =
        '<p class="empty-msg" style="color:var(--err,#c00)">Failed to load sources: ' +
        _escHtml(err && err.message ? err.message : String(err)) + '</p>';
    });
}

/**
 * Wire up drag-and-drop + file-picker selection for the Source Editor
 * upload zone. Mirrors the Quick Test drop-zone wiring so the BA gets a
 * consistent UX.
 */
function _seInitDropZone() {
  var dz = document.getElementById('seDropZone');
  var input = document.getElementById('seFileInput');
  var submitBtn = document.getElementById('seSubmitBtn');
  var fileNameSpan = document.getElementById('seFileName');
  if (!dz || !input || !submitBtn) return;

  function _seAccept(file) {
    if (!file) return;
    if (!/\.xlsx$/i.test(file.name)) {
      _seSetStatus('Workbook must be an .xlsx file.', 'err');
      return;
    }
    _seSelectedFile = file;
    if (fileNameSpan) fileNameSpan.textContent = file.name;
    submitBtn.disabled = false;
    _seSetStatus('Ready to preview: ' + file.name, 'info');
  }

  dz.addEventListener('click', function() { input.click(); });
  dz.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); }
  });
  input.addEventListener('change', function(e) {
    if (e.target.files && e.target.files[0]) _seAccept(e.target.files[0]);
  });
  dz.addEventListener('dragover', function(e) {
    e.preventDefault(); dz.classList.add('dragover');
  });
  dz.addEventListener('dragleave', function() { dz.classList.remove('dragover'); });
  dz.addEventListener('drop', function(e) {
    e.preventDefault(); dz.classList.remove('dragover');
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]) {
      _seAccept(e.dataTransfer.files[0]);
    }
  });
}

/**
 * Update the Source Editor status message line.
 *
 * @param {string} msg - Text to display.
 * @param {string} kind - One of 'info', 'err', or 'ok' (drives CSS class).
 */
function _seSetStatus(msg, kind) {
  var el = document.getElementById('seStatusMsg');
  if (!el) return;
  el.textContent = msg;
  el.className = 'status-msg ' + (kind === 'err' ? 'err' : kind === 'ok' ? 'ok' : 'info');
}

/**
 * POST the selected workbook to /api/v2/onboarding/preview and render
 * the response as a collapsible artefact tree with per-artefact drift
 * status badges (EE-S2).
 *
 * The raw JSON response is preserved behind a <details> disclosure for
 * forensic debugging when the tree render itself fails.
 */
function submitOnboardingPreview() {
  if (!_seSelectedFile) {
    _seSetStatus('Select a workbook first.', 'err');
    return;
  }
  var submitBtn = document.getElementById('seSubmitBtn');
  var resultBox = document.getElementById('sePreviewResult');
  var jsonBox = document.getElementById('sePreviewJson');
  var treeBox = document.getElementById('sePreviewTree');
  if (!submitBtn || !resultBox || !jsonBox || !treeBox) return;

  submitBtn.disabled = true;
  _seSetStatus('Uploading and running dry-run\u2026', 'info');
  resultBox.style.display = 'none';

  var fd = new FormData();
  fd.append('file', _seSelectedFile, _seSelectedFile.name);

  apiFetch('/api/v2/onboarding/preview', {
    method: 'POST',
    body: fd
  })
    .then(function(r) {
      return r.text().then(function(text) {
        var parsed = null;
        try { parsed = text ? JSON.parse(text) : null; }
        catch (e) { parsed = null; }
        return { ok: r.ok, status: r.status, body: parsed, raw: text };
      });
    })
    .then(function(resp) {
      if (!resp.ok) {
        var detail = resp.body && resp.body.detail
          ? (typeof resp.body.detail === 'string' ? resp.body.detail : JSON.stringify(resp.body.detail))
          : (resp.raw || ('HTTP ' + resp.status));
        _seSetStatus('Preview failed: ' + detail, 'err');
        treeBox.innerHTML = '';
        jsonBox.textContent = resp.raw || '';
        resultBox.style.display = '';
        return;
      }
      var body = resp.body || {};
      var totalFiles = (body.summary && body.summary.total_files) || 0;
      var drift = (body.summary && body.summary.drift) || {};
      var driftLine = ' \u2014 ' + (drift.new || 0) + ' new, '
        + (drift.changed || 0) + ' changed, '
        + (drift.unchanged || 0) + ' unchanged';
      _seSetStatus(
        'Preview complete \u2014 ' + totalFiles + ' artefact(s)' + driftLine + '.',
        'ok'
      );
      // Cache the response payload globally so the artefact-content
      // modal can look up the emitted entry by path without re-fetching.
      window._seLastPreview = body;
      // EE-S3: stash the workbook hash so /artefact-content lookups
      // can resolve emitted text for the diff view without forcing a
      // workbook re-upload.
      window._seWorkbookHash = body.workbook_hash || null;
      try {
        renderOnboardingTree(body, treeBox);
      } catch (e) {
        treeBox.innerHTML =
          '<p class="empty-msg" style="color:var(--err,#c00)">Failed to ' +
          'render tree: ' + _escHtml(String(e && e.message || e)) +
          ' \u2014 see raw JSON below.</p>';
      }
      jsonBox.textContent = JSON.stringify(body, null, 2);
      resultBox.style.display = '';
      // EE-S3: a successful preview unlocks the commit buttons.
      _seEnableCommitButtons();
      _seSetCommitStatus(
        'Ready to commit \u2014 download the ZIP or open a pull request.',
        'info'
      );
    })
    .catch(function(err) {
      _seSetStatus('Network error: ' + (err && err.message ? err.message : String(err)), 'err');
    })
    .finally(function() {
      submitBtn.disabled = false;
    });
}

// ---------------------------------------------------------------------------
// EE-S2: artefact tree rendering, status badges, content viewer, unified diff
// ---------------------------------------------------------------------------

/** Canonical kind order for the tree sections (matches emitter order). */
var _SE_KIND_ORDER = [
  'source_yaml',
  'mapping_json',
  'rules_json',
  'reconciliation_yaml',
  'sql'
];

/** Human-readable kind labels for the section headers. */
var _SE_KIND_LABELS = {
  source_yaml: 'Source YAML',
  mapping_json: 'Mapping JSON',
  rules_json: 'Rules JSON',
  reconciliation_yaml: 'Reconciliation YAML',
  sql: 'Expected SQL'
};

/** Sections auto-collapse once they hit this artefact count. */
var _SE_COLLAPSE_THRESHOLD = 5;

/**
 * Build a status badge span. ``count`` may be passed to render a
 * "<count> <label>" prefix; when absent the badge is just the label.
 *
 * @param {string} status - One of 'new', 'changed', 'unchanged'.
 * @param {number} [count] - Optional count to prefix the badge.
 * @returns {string} The HTML snippet.
 */
function _seStatusBadge(status, count) {
  var className = 'se-badge se-badge-' + status;
  if (count === 0) className += ' se-badge-zero';
  var text = (typeof count === 'number')
    ? (count + ' ' + status)
    : status;
  return '<span class="' + className + '">' + _escHtml(text) + '</span>';
}

/**
 * Render the structured artefact tree into the supplied container.
 *
 * @param {Object} payload - The full preview-API response body.
 * @param {HTMLElement} container - DOM node to populate.
 */
function renderOnboardingTree(payload, container) {
  if (!payload || !container) return;
  container.innerHTML = '';

  var summary = payload.summary || {};
  var drift = summary.drift || { new: 0, changed: 0, unchanged: 0 };
  var sourceCode = payload.source_code || '(unknown)';

  // Top-level summary block: total files, total bytes, drift counts.
  var summaryDiv = document.createElement('div');
  summaryDiv.className = 'onboarding-tree-summary';
  summaryDiv.setAttribute('role', 'group');
  summaryDiv.setAttribute('aria-label', 'Preview summary');
  summaryDiv.innerHTML =
    '<span class="ot-summary-block">' +
      '<span class="ot-summary-label">Source</span>' +
      '<span class="ot-summary-value">' + _escHtml(sourceCode) + '</span>' +
    '</span>' +
    '<span class="ot-summary-divider" aria-hidden="true"></span>' +
    '<span class="ot-summary-block">' +
      '<span class="ot-summary-label">Files</span>' +
      '<span class="ot-summary-value">' + (summary.total_files || 0) + '</span>' +
    '</span>' +
    '<span class="ot-summary-block">' +
      '<span class="ot-summary-label">Bytes</span>' +
      '<span class="ot-summary-value">' +
        _seFormatBytes(summary.total_bytes || 0) +
      '</span>' +
    '</span>' +
    '<span class="ot-summary-divider" aria-hidden="true"></span>' +
    '<span class="ot-summary-block">' +
      _seStatusBadge('new', drift.new || 0) +
      _seStatusBadge('changed', drift.changed || 0) +
      _seStatusBadge('unchanged', drift.unchanged || 0) +
    '</span>';
  container.appendChild(summaryDiv);

  // Group artefacts by kind.
  var entries = payload.would_write || [];
  var byKind = {};
  _SE_KIND_ORDER.forEach(function(k) { byKind[k] = []; });
  entries.forEach(function(entry) {
    var k = entry && entry.kind;
    if (!byKind[k]) byKind[k] = [];
    byKind[k].push(entry);
  });

  // Render one collapsible section per kind that has entries.
  _SE_KIND_ORDER.forEach(function(kind) {
    var items = byKind[kind] || [];
    if (!items.length) return;
    container.appendChild(_seRenderKindSection(kind, items));
  });
}

/**
 * Build the <details> + <summary> + body for one kind section.
 *
 * @param {string} kind - The kind token (e.g. 'mapping_json').
 * @param {Array<Object>} items - The artefact entries in this kind.
 * @returns {HTMLDetailsElement}
 */
function _seRenderKindSection(kind, items) {
  var label = _SE_KIND_LABELS[kind] || kind;
  // Per-status counts within this section.
  var counts = { new: 0, changed: 0, unchanged: 0 };
  items.forEach(function(it) {
    var s = it && it.status;
    if (counts[s] != null) counts[s] += 1;
  });

  var details = document.createElement('details');
  details.className = 'onboarding-section onboarding-section-' + kind;
  // Auto-expand sections with < threshold artefacts; collapse big ones.
  if (items.length < _SE_COLLAPSE_THRESHOLD) details.open = true;

  var statusSummary = _seStatusBadge('new', counts.new) +
    _seStatusBadge('changed', counts.changed) +
    _seStatusBadge('unchanged', counts.unchanged);

  var summary = document.createElement('summary');
  summary.className = 'onboarding-section-header';
  summary.innerHTML =
    '<span class="ot-caret" aria-hidden="true">\u25b6</span>' +
    '<span class="ot-kind-label">' + _escHtml(label) + '</span>' +
    '<span class="ot-kind-count">(' + items.length + ')</span>' +
    '<span class="ot-kind-status-summary">' + statusSummary + '</span>';
  details.appendChild(summary);

  var body = document.createElement('div');
  body.className = 'onboarding-section-body';
  var rows = items.map(function(item) {
    var path = item.path || '';
    var bytesTxt = _seFormatBytes(item.bytes || 0);
    var status = item.status || 'unchanged';
    var driftReason = item.drift_reason
      ? '<span class="onboarding-drift-reason">' +
          _escHtml(String(item.drift_reason)) +
        '</span>'
      : '';
    // Path doubles as the lookup key for viewArtefactContent().
    var safePath = _escHtml(path);
    return (
      '<tr>' +
        '<td class="ot-path">' + safePath + driftReason + '</td>' +
        '<td class="ot-bytes">' + _escHtml(bytesTxt) + '</td>' +
        '<td class="ot-status">' + _seStatusBadge(status) + '</td>' +
        '<td class="ot-action">' +
          '<button type="button" class="ot-view-btn" ' +
            'onclick="viewArtefactContent(\'' +
              _seEscapeJsString(path) + '\')">View</button>' +
        '</td>' +
      '</tr>'
    );
  }).join('');
  body.innerHTML =
    '<table>' +
      '<thead><tr>' +
        '<th>Path</th>' +
        '<th>Size</th>' +
        '<th>Status</th>' +
        '<th></th>' +
      '</tr></thead>' +
      '<tbody>' + rows + '</tbody>' +
    '</table>';
  details.appendChild(body);
  return details;
}

/**
 * Escape a string for safe insertion into a single-quoted JS string
 * literal in inline ``onclick=''`` handlers.
 *
 * Less strict than _escHtml because the destination context is a JS
 * string, not an HTML text node. We still escape backslash + single
 * quote + line breaks so the resulting attribute value remains valid.
 *
 * @param {string} str
 * @returns {string}
 */
function _seEscapeJsString(str) {
  return String(str)
    .replace(/\\/g, '\\\\')
    .replace(/'/g, "\\'")
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\n/g, '\\n')
    .replace(/\r/g, '\\r');
}

/**
 * Format a byte count for human consumption (B / KB / MB).
 *
 * @param {number} bytes
 * @returns {string}
 */
function _seFormatBytes(bytes) {
  var n = Number(bytes) || 0;
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  return (n / (1024 * 1024)).toFixed(2) + ' MB';
}

/**
 * Open the artefact-content modal for the entry at the given path.
 *
 * For ``unchanged`` and ``new`` artefacts shows the emitted content
 * in a <pre>; for ``changed`` artefacts fetches the committed content
 * from the API and renders a unified diff against the emitted content.
 *
 * The emitted content is NOT carried in the preview-API response \u2014
 * EE-S2 doesn't return content payloads \u2014 so for any status we have
 * to ask the server. For unchanged/changed we hit
 * ``/api/v2/onboarding/committed-artefact?path=...`` (which serves the
 * verbatim on-disk file). For ``new`` artefacts there is no committed
 * file; we surface a placeholder noting "would be created" and rely
 * on the EE-S3 follow-up commit flow to render the emitted text.
 *
 * @param {string} path - The repo-relative artefact path.
 */
function viewArtefactContent(path) {
  var payload = window._seLastPreview || {};
  var entries = payload.would_write || [];
  var entry = null;
  for (var i = 0; i < entries.length; i++) {
    if (entries[i].path === path) { entry = entries[i]; break; }
  }
  if (!entry) {
    return;
  }

  var modal = document.getElementById('seArtefactModal');
  var title = document.getElementById('seArtefactModalTitle');
  var meta = document.getElementById('seArtefactModalMeta');
  var body = document.getElementById('seArtefactModalBody');
  if (!modal || !title || !meta || !body) return;

  title.textContent = path;
  meta.innerHTML =
    _seStatusBadge(entry.status || 'unchanged') +
    '<span>' + _escHtml(_seFormatBytes(entry.bytes || 0)) + '</span>' +
    '<span>kind: ' + _escHtml(entry.kind || '') + '</span>';

  // Show the modal up-front so the user gets immediate feedback while
  // the committed-artefact fetch resolves.
  modal.style.display = '';
  modal.setAttribute('aria-hidden', 'false');
  body.innerHTML =
    '<div class="se-diff-loading">Loading artefact content\u2026</div>';

  if (entry.status === 'new') {
    // EE-S3: surface the emitted content for ``new`` artefacts so the
    // BA can review what would be written to disk before committing.
    _seFetchEmittedContent(path).then(function(emitted) {
      body.innerHTML =
        '<div class="se-diff-empty"><em>This artefact does not yet ' +
        'exist in <code>config/</code>. The text below is what ' +
        'Valdo would write on commit.</em></div>' +
        '<pre>' + _escHtml(emitted || '') + '</pre>';
    }).catch(function(err) {
      body.innerHTML =
        '<div class="se-diff-empty" style="color:var(--err,#c00)">' +
        'Failed to load emitted content: ' +
        _escHtml(String(err && err.message || err)) + '</div>';
    });
    return;
  }

  // Both ``unchanged`` and ``changed`` artefacts have a committed
  // counterpart. EE-S3 fetches BOTH the committed (on-disk) and the
  // emitted (cached server-side via /artefact-content) content so the
  // unified-diff renderer can show the real change set, not just the
  // committed text twice.
  var fetchCommitted = apiFetch('/api/v2/onboarding/committed-artefact?path='
        + encodeURIComponent(path)).then(function(r) {
    if (!r.ok) throw new Error('committed: HTTP ' + r.status);
    return r.json();
  }).then(function(d) { return (d && d.content) || ''; });

  var fetchEmitted = _seFetchEmittedContent(path);

  Promise.all([fetchCommitted, fetchEmitted])
    .then(function(parts) {
      var committed = parts[0] || '';
      var emitted = parts[1] || '';
      if (entry.status === 'unchanged') {
        // For unchanged artefacts both sides are byte-equivalent under
        // the EE-S2 normalisation contract — show the committed text.
        body.innerHTML = '<pre>' + _escHtml(committed) + '</pre>';
        return;
      }
      // ``changed`` path: real unified diff (committed vs emitted),
      // closing the EE-S2 carve-out where the diff fed both sides the
      // committed file.
      var driftReason = entry.drift_reason || 'drift detected';
      body.innerHTML =
        '<div class="se-diff-empty"><strong>Drift reason:</strong> '
          + _escHtml(driftReason) + '</div>' +
        '<div class="se-diff">' +
          renderUnifiedDiff(committed, emitted,
            { header: 'Committed (-) vs emitted (+)' }) +
        '</div>';
    })
    .catch(function(err) {
      body.innerHTML =
        '<div class="se-diff-empty" style="color:var(--err,#c00)">' +
        'Failed to load artefact content: ' +
        _escHtml(String(err && err.message || err)) +
        '</div>';
    });
}

/**
 * Fetch the emitted text for an artefact path from the EE-S3
 * /artefact-content cache.
 *
 * Resolves to '' (and logs a warning to the console) when the
 * workbook hash is unknown — the View modal degrades gracefully to
 * showing only the committed side rather than failing the modal.
 *
 * @param {string} path - Repo-relative artefact path.
 * @returns {Promise<string>} The emitted UTF-8 text.
 */
function _seFetchEmittedContent(path) {
  var hash = window._seWorkbookHash;
  if (!hash) {
    return Promise.resolve('');
  }
  var url = '/api/v2/onboarding/artefact-content?workbook_hash='
    + encodeURIComponent(hash)
    + '&path=' + encodeURIComponent(path);
  return apiFetch(url).then(function(r) {
    if (!r.ok) throw new Error('emitted: HTTP ' + r.status);
    return r.json();
  }).then(function(d) {
    return (d && d.content) || '';
  });
}

/**
 * Close the artefact-content modal.
 */
function closeArtefactModal() {
  var modal = document.getElementById('seArtefactModal');
  if (!modal) return;
  modal.style.display = 'none';
  modal.setAttribute('aria-hidden', 'true');
}

/**
 * Render a minimal unified diff between two text blobs.
 *
 * Uses the longest-common-subsequence-style line-by-line approach with
 * a greedy fallback: we walk both line lists in lock-step, emitting
 * matching lines verbatim and pairing non-matching lines as
 * delete/insert. Sufficient for the EE-S2 "show me what would change"
 * UX; full Myers-diff fidelity is an EE-S3 polish goal.
 *
 * @param {string} oldText
 * @param {string} newText
 * @param {Object} [opts]
 * @param {string} [opts.header] - Optional header line.
 * @returns {string} HTML snippet.
 */
function renderUnifiedDiff(oldText, newText, opts) {
  opts = opts || {};
  var oldLines = String(oldText || '').split(/\r?\n/);
  var newLines = String(newText || '').split(/\r?\n/);
  var out = [];
  if (opts.header) {
    out.push('<span class="diff-line hunk">@@ ' +
      _escHtml(opts.header) + ' @@</span>');
  }

  // Compute a simple LCS table for line-level matching.
  var m = oldLines.length, n = newLines.length;
  // Cap the table size to keep things responsive in the browser; for
  // very large artefacts we fall back to a side-by-side dump.
  if (m * n > 200000) {
    return '<div class="se-diff-empty">Artefact too large for inline ' +
      'diff (' + m + ' vs ' + n + ' lines). Open the committed file ' +
      'in your editor and diff against the emitted artefact from ' +
      'the upcoming EE-S3 commit-flow preview.</div>';
  }
  var lcs = [];
  for (var i = 0; i <= m; i++) {
    lcs.push(new Array(n + 1).fill(0));
  }
  for (var i = m - 1; i >= 0; i--) {
    for (var j = n - 1; j >= 0; j--) {
      if (oldLines[i] === newLines[j]) {
        lcs[i][j] = lcs[i + 1][j + 1] + 1;
      } else {
        lcs[i][j] = Math.max(lcs[i + 1][j], lcs[i][j + 1]);
      }
    }
  }
  var i = 0, j = 0;
  while (i < m && j < n) {
    if (oldLines[i] === newLines[j]) {
      out.push('<span class="diff-line">  ' + _escHtml(oldLines[i]) + '</span>');
      i++; j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      out.push('<span class="diff-line del">- ' + _escHtml(oldLines[i]) + '</span>');
      i++;
    } else {
      out.push('<span class="diff-line add">+ ' + _escHtml(newLines[j]) + '</span>');
      j++;
    }
  }
  while (i < m) {
    out.push('<span class="diff-line del">- ' + _escHtml(oldLines[i]) + '</span>');
    i++;
  }
  while (j < n) {
    out.push('<span class="diff-line add">+ ' + _escHtml(newLines[j]) + '</span>');
    j++;
  }
  return out.join('');
}

// Initialise the drop-zone wiring once the page is interactive.
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', _seInitDropZone);
} else {
  _seInitDropZone();
}

// EE-S2: dismiss the artefact modal on Esc for keyboard users.
document.addEventListener('keydown', function(e) {
  if (e.key !== 'Escape') return;
  var modal = document.getElementById('seArtefactModal');
  if (modal && modal.style.display !== 'none') {
    closeArtefactModal();
  }
  var mrModal = document.getElementById('seOpenMrModal');
  if (mrModal && mrModal.style.display !== 'none') {
    closeOnboardingMrModal();
  }
});

// ===========================================================================
// EE-S3: Commit via ZIP / Open MR
//
// After the BA has previewed a workbook (renderOnboardingTree() has run),
// two buttons appear below the tree:
//
//   * Download ZIP — streams every emitted artefact back to the browser
//     as a zip attachment. Pure offline-review path, no server-side
//     git operations.
//   * Open MR — opens a modal collecting an MR title + description, then
//     hits /api/v2/onboarding/open-mr which branches + commits + pushes +
//     opens a PR via `gh pr create`. Gated by VALDO_UI_ENABLE_OPEN_MR
//     on the server side; when disabled the server returns 501 and the
//     UI surfaces an actionable hint.
// ===========================================================================

/**
 * Enable the EE-S3 commit buttons after a successful preview. Idempotent.
 */
function _seEnableCommitButtons() {
  var zipBtn = document.getElementById('seDownloadZipBtn');
  var mrBtn = document.getElementById('seOpenMrBtn');
  if (zipBtn) zipBtn.disabled = false;
  if (mrBtn) mrBtn.disabled = false;
}

/**
 * Update the EE-S3 commit-row status message.
 *
 * @param {string} msg
 * @param {string} kind - 'info' | 'ok' | 'err'
 */
function _seSetCommitStatus(msg, kind) {
  var el = document.getElementById('seCommitStatus');
  if (!el) return;
  el.textContent = msg;
  el.className = 'status-msg ' + (kind === 'err' ? 'err' : kind === 'ok' ? 'ok' : 'info');
}

/**
 * POST the selected workbook to /api/v2/onboarding/download-zip and
 * trigger a browser download of the resulting ZIP attachment.
 */
function downloadOnboardingZip() {
  if (!_seSelectedFile) {
    _seSetCommitStatus('Select and preview a workbook first.', 'err');
    return;
  }
  var btn = document.getElementById('seDownloadZipBtn');
  if (btn) btn.disabled = true;
  _seSetCommitStatus('Building ZIP…', 'info');

  var fd = new FormData();
  fd.append('file', _seSelectedFile, _seSelectedFile.name);

  apiFetch('/api/v2/onboarding/download-zip', {
    method: 'POST',
    body: fd
  })
    .then(function(r) {
      if (!r.ok) {
        return r.text().then(function(text) {
          var detail = text;
          try {
            var parsed = JSON.parse(text);
            if (parsed && parsed.detail) detail = parsed.detail;
          } catch (e) { /* fall through with raw text */ }
          throw new Error('HTTP ' + r.status + ': ' + detail);
        });
      }
      // Pull the filename out of the Content-Disposition header when
      // present so the saved file matches the server's view of it.
      var disposition = r.headers.get('Content-Disposition') || '';
      var match = disposition.match(/filename="([^"]+)"/);
      var filename = match ? match[1] : 'onboarding-artefacts.zip';
      return r.blob().then(function(blob) {
        return { blob: blob, filename: filename };
      });
    })
    .then(function(payload) {
      // Trigger the browser download via an anchor + Blob URL. We use
      // URL.createObjectURL so the ZIP stays in-memory; revoking the
      // URL on click avoids leaking the blob reference.
      var url = URL.createObjectURL(payload.blob);
      var a = document.createElement('a');
      a.href = url;
      a.download = payload.filename;
      document.body.appendChild(a);
      a.click();
      a.parentNode.removeChild(a);
      setTimeout(function() { URL.revokeObjectURL(url); }, 1000);
      _seSetCommitStatus('Downloaded: ' + payload.filename, 'ok');
    })
    .catch(function(err) {
      _seSetCommitStatus(
        'Download failed: ' + (err && err.message ? err.message : String(err)),
        'err'
      );
    })
    .finally(function() {
      if (btn) btn.disabled = false;
    });
}

/**
 * Open the EE-S3 Open MR modal. Clears any stale form state.
 */
function openOnboardingMrModal() {
  if (!_seSelectedFile) {
    _seSetCommitStatus('Select and preview a workbook first.', 'err');
    return;
  }
  var modal = document.getElementById('seOpenMrModal');
  if (!modal) return;
  // Pre-fill the title with a sensible default the BA can edit.
  var titleInput = document.getElementById('seMrTitle');
  var payload = window._seLastPreview || {};
  var sourceCode = payload.source_code || 'source';
  if (titleInput && !titleInput.value) {
    titleInput.value = 'feat(onboarding): add ' + sourceCode + ' source';
  }
  var resultBox = document.getElementById('seMrResult');
  if (resultBox) { resultBox.style.display = 'none'; resultBox.innerHTML = ''; }
  var statusEl = document.getElementById('seMrStatusMsg');
  if (statusEl) { statusEl.textContent = ''; statusEl.className = 'status-msg info'; }
  modal.style.display = '';
  modal.setAttribute('aria-hidden', 'false');
  if (titleInput) titleInput.focus();
}

/**
 * Close the Open MR modal.
 */
function closeOnboardingMrModal() {
  var modal = document.getElementById('seOpenMrModal');
  if (!modal) return;
  modal.style.display = 'none';
  modal.setAttribute('aria-hidden', 'true');
}

/**
 * POST the workbook + MR form to /api/v2/onboarding/open-mr.
 *
 * When the server returns 501 (feature-flag disabled) we surface a
 * friendly message and disable the Open MR button so the BA isn't
 * tempted to retry. When the server returns 200 we render the PR URL.
 */
function submitOnboardingMr() {
  if (!_seSelectedFile) {
    _seSetMrStatus('Select and preview a workbook first.', 'err');
    return;
  }
  var titleInput = document.getElementById('seMrTitle');
  var descInput = document.getElementById('seMrDescription');
  var branchInput = document.getElementById('seMrBranch');
  var submitBtn = document.getElementById('seMrSubmitBtn');
  if (!titleInput || !titleInput.value.trim()) {
    _seSetMrStatus('Title is required.', 'err');
    if (titleInput) titleInput.focus();
    return;
  }
  if (submitBtn) submitBtn.disabled = true;
  _seSetMrStatus('Creating branch, committing, pushing…', 'info');

  var fd = new FormData();
  fd.append('file', _seSelectedFile, _seSelectedFile.name);
  fd.append('mr_title', titleInput.value);
  fd.append('mr_description', descInput ? descInput.value : '');
  if (branchInput && branchInput.value) {
    fd.append('branch_name', branchInput.value);
  }

  apiFetch('/api/v2/onboarding/open-mr', {
    method: 'POST',
    body: fd
  })
    .then(function(r) {
      return r.text().then(function(text) {
        var parsed = null;
        try { parsed = text ? JSON.parse(text) : null; }
        catch (e) { parsed = null; }
        return { ok: r.ok, status: r.status, body: parsed, raw: text };
      });
    })
    .then(function(resp) {
      if (resp.status === 501) {
        // Feature-flag disabled — surface the helpful hint AND grey
        // out the button so the BA defaults to Download ZIP.
        var msg = (resp.body && resp.body.detail) ||
          'MR opening is disabled in this environment — use Download ZIP.';
        _seSetMrStatus(msg, 'err');
        var mrBtn = document.getElementById('seOpenMrBtn');
        if (mrBtn) {
          mrBtn.disabled = true;
          mrBtn.setAttribute('aria-disabled', 'true');
        }
        return;
      }
      if (!resp.ok) {
        var detail = (resp.body && resp.body.detail)
          ? (typeof resp.body.detail === 'string' ? resp.body.detail : JSON.stringify(resp.body.detail))
          : (resp.raw || ('HTTP ' + resp.status));
        _seSetMrStatus('Failed: ' + detail, 'err');
        return;
      }
      var data = resp.body || {};
      _seSetMrStatus('Pull request created.', 'ok');
      var resultBox = document.getElementById('seMrResult');
      if (resultBox) {
        var prUrl = data.pr_url || '';
        var branch = data.branch_name || '';
        var sha = data.commit_sha || '';
        resultBox.innerHTML =
          '<p><strong>Branch:</strong> <code>' + _escHtml(branch) + '</code></p>' +
          '<p><strong>Commit:</strong> <code>' + _escHtml(sha) + '</code></p>' +
          (prUrl
            ? '<p><strong>Pull request:</strong> <a href="' + _escHtml(prUrl) +
              '" target="_blank" rel="noopener noreferrer">' + _escHtml(prUrl) + '</a></p>'
            : '');
        resultBox.style.display = '';
      }
    })
    .catch(function(err) {
      _seSetMrStatus(
        'Network error: ' + (err && err.message ? err.message : String(err)),
        'err'
      );
    })
    .finally(function() {
      if (submitBtn) submitBtn.disabled = false;
    });
}

/**
 * Update the Open MR modal status line.
 *
 * @param {string} msg
 * @param {string} kind - 'info' | 'ok' | 'err'
 */
function _seSetMrStatus(msg, kind) {
  var el = document.getElementById('seMrStatusMsg');
  if (!el) return;
  el.textContent = msg;
  el.className = 'status-msg ' + (kind === 'err' ? 'err' : kind === 'ok' ? 'ok' : 'info');
}
