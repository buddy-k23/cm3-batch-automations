"""Patch the qtMrYamlInput to be visually prominent."""
from pathlib import Path

f = Path("src/reports/static/ui.html")
content = f.read_text(encoding="utf-8")

OLD = '''      <input type="file" id="qtMrYamlInput" accept=".yaml,.yml"
             style="font-size:13px;color:var(--text-primary);width:100%;"
             aria-label="Multi-record YAML config file">
      <div style="font-size:11px;color:var(--text-secondary);margin-top:4px;">
        Upload a multi-record YAML config to validate header/detail/trailer files.
        Generate one in the <a href="#" onclick="switchTab('mapping');return false;" style="color:var(--accent);">Mapping Generator</a> tab.
      </div>'''

NEW = '''      <input type="file" id="qtMrYamlInput" accept=".yaml,.yml"
             style="display:none;" aria-label="Multi-record YAML config file">
      <label for="qtMrYamlInput" id="qtMrYamlLabel"
             style="display:flex;align-items:center;gap:10px;padding:10px 14px;
                    border:2px dashed var(--border);border-radius:10px;cursor:pointer;
                    background:var(--surface);transition:border-color .15s;"
             onmouseover="this.style.borderColor='var(--accent)'"
             onmouseout="this.style.borderColor='var(--border)'">
        <svg aria-hidden="true" focusable="false" width="18" height="18" fill="none"
             stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"
             style="flex-shrink:0;color:var(--accent)">
          <path stroke-linecap="round" stroke-linejoin="round"
            d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0
               0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5
               3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0
               .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504
               1.125-1.125V11.25a9 9 0 00-9-9z"/>
        </svg>
        <span id="qtMrYamlLabelText" style="font-size:13px;color:var(--text-secondary);">
          Click to select a <strong style="color:var(--text-primary);">.yaml</strong>
          umbrella config&hellip;
        </span>
      </label>
      <div style="font-size:11px;color:var(--text-secondary);margin-top:4px;">
        Select <code style="font-size:11px;background:var(--surface);padding:1px 5px;
        border-radius:4px;border:1px solid var(--border);">config/mappings/SHAW_TRANERT.yaml</code>
        or any umbrella YAML. Overrides the mapping dropdown above.
      </div>'''

if OLD in content:
    content = content.replace(OLD, NEW)
    f.write_text(content, encoding="utf-8")
    print("Patched successfully.")
else:
    print("ERROR: old string not found — check whitespace/encoding.")
