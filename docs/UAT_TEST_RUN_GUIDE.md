# Valdo — BA & QA Test-Run Guide (MCP-based UAT)

A step-by-step playbook for a Business Analyst (BA) and a QA engineer to test
Valdo **through its MCP server** — the agentic interface Valdo exposes to LLM
clients (VS Code + GitLab Duo / Copilot Chat, Claude Desktop, etc.). Both lanes
drive Valdo over MCP; UI/CLI checks are kept as a secondary cross-check.

- **Hosting model:** local per-person (each runs Valdo on their own laptop; SQLite, no external DB).
- **Primary interface under test:** the MCP server at `http://localhost:8000/mcp/`.
- **Time:** BA ≈ 45 min · QA ≈ 1.5–2 hrs.
- **File findings:** GitHub issues at `github.com/buddy-k23/valdo` (tracker is at zero open — findings stand out). Template at the end.

---

## 0. Prerequisites

- macOS / Linux / WSL, **Python 3.11+**, `git`. No Docker/Oracle needed (SQLite).
- **BA:** VS Code with an MCP-capable agent chat (GitLab Duo or GitHub Copilot Chat). The team's path is **GitLab Duo agentic chat in VS Code** (no GitLab API access needed — MCP is the bridge).
- **QA:** the above is fine, plus a terminal for deterministic tool calls.

## 1. Setup (both lanes) — bring up Valdo + the MCP server

```bash
git clone https://github.com/buddy-k23/valdo.git && cd valdo
git checkout valdo-version-v4
bash scripts/valdo-setup.sh        # venv + deps + .env (SQLite) + migrations + smoke
source .venv/bin/activate
valdo serve                        # serves the API + UI + the MCP server at /mcp/
```

The MCP server is now live at **`http://localhost:8000/mcp/`**. Sanity check (new terminal):

```bash
curl -s http://localhost:8000/mcp/health     # -> {"status":"healthy","tool_count":...}
```

### 1a. Authenticate the MCP client (pick one)

- **Token (recommended, prod-like):** `valdo mcp-login` mints an HMAC bearer token to `~/.valdo/mcp-token` (LDAP-backed). Point the client at it (see the VS Code config below).
- **Local dev bypass (no LDAP):** start the server with **both** env vars set —
  `VALDO_MCP_AUTH=dev VALDO_ALLOW_DEV_AUTH=1 valdo serve`. (Setting only
  `VALDO_MCP_AUTH=dev` does **not** bypass — that safety check is itself a QA test, see §3d.)

### 1b. Connect the VS Code agent

Drop the repo's `docs/mcp-clients/vscode/vscode-mcp-config.json` into your
workspace as `.vscode/mcp.json` (point the URL at `http://localhost:8000/mcp/`),
then open the Duo/Copilot chat and confirm Valdo's tools appear. Full steps:
`docs/mcp-clients/vscode/vscode-setup.md` and `docs/MCP_CLIENTS.md`.

---

## 2. BA lane — drive Valdo conversationally over MCP

The BA tests whether the **natural-language → MCP-tool** path actually works for
their job. In the VS Code agent chat, ask Valdo to do each of these and judge
whether it picks the right tool and gives a useful answer:

1. **Discover** — "What ETL templates does Valdo offer?" (→ `etl-templates-list`) and "What file formats are supported?" (→ `formats-supported`). Expect fixed-width, CSV/TSV/pipe, JSON (NDJSON), XML.
2. **Pick a shape** — "I have a newline-delimited JSON file of customer records — which template should I use?" (→ `pick_etl_shape` → should recommend `json_single_record`).
3. **Validate** — "Validate `templates/etl/json_single_record_sample/input.ndjson` with the json_single_record mapping and rules." (→ `validate_file` → returns a run id) then "Show me the violations for that run." (→ `get_run_status` / `get_violations` → 4 errors on records 6/8/9/10).
4. **Reconcile** — "Reconcile the json_single_record mapping against table CUSTOMER." (→ `reconcile_mapping` → field-level verdict).
5. **Compare** — "Compare these two CSVs and tell me the differences." (→ `compare_two_files`).
6. **★ Onboard a real source** — "Here's my spec workbook — onboard it." (→ `onboard_new_source` / `upload_workbook_as_spec` / `onboard_source_dry_run`). Use a **real spec** you own. *This is the true BA test:* can you go from your workbook to a working mapping by chatting with Valdo?

What to judge: does the agent route to the right tool? Are the responses
understandable to a BA (not raw JSON dumps)? Did `pick_etl_shape` guide you
correctly? Did onboarding produce a usable mapping?

---

## 3. QA lane — verify the MCP surface deterministically

### 3a. Tool / resource / prompt coverage

Drive each MCP capability and confirm correct behavior. You can use the agent
chat, **or** call the transport directly for repeatable results. A minimal
deterministic caller (no chat client needed):

```python
# mcp_call.py — initialize then call one tool/resource over MCP Streamable-HTTP
import requests, json, sys
BASE="http://localhost:8000/mcp/"
H={"Accept":"application/json, text/event-stream","Content-Type":"application/json"}
# (dev-bypass server: no auth header; token mode: add {"Authorization": f"Bearer {open(...).read()}"})
def rpc(method, params, i):
    r=requests.post(BASE, headers=H, json={"jsonrpc":"2.0","id":i,"method":method,"params":params})
    t=r.text
    if "text/event-stream" in r.headers.get("content-type",""):
        t=[l[5:].strip() for l in t.splitlines() if l.startswith("data:")][0]
    return json.loads(t)
rpc("initialize", {"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"qa","version":"1"}}, 1)
# If the server returns an `Mcp-Session-Id` header on initialize, echo it on the
# next calls (add it to H). The authoritative deterministic check is the MCP
# integration suite — see §3e — if this ad-hoc caller fights the handshake.
print(json.dumps(rpc(sys.argv[1], json.loads(sys.argv[2]), 2), indent=2))
# e.g.: python mcp_call.py tools/list '{}'
#       python mcp_call.py resources/read '{"uri":"templates://etl/list"}'
#       python mcp_call.py tools/call '{"name":"reconcile_mapping","arguments":{"mapping":"...","table":"CUSTOMER"}}'
```

Verify, per tool/resource:
- `tools/list` lists the expected tools (validate_file, get_run_status, get_violations, compare_two_files, reconcile_mapping, pick_etl_shape, onboard_new_source, list_sources, …).
- `resources/read` on `templates://etl/list` lists all shapes incl. `json_single_record` and `xml_single_record`; `formats://supported` enumerates the formats; the taxonomy resources return rule/violation catalogues.
- `validate_file` on the JSON/XML samples → a run id; `get_violations` → **4 errors** (matches the CLI/UI result and the sample `expected_report.json`).
- `reconcile_mapping` → a correct field-level verdict on SQLite.
- `pick_etl_shape` recommends the right template for a described file.

### 3b. Async validation flow (the worker path)

`validate_file` is async-by-default with a live-worker-aware fallback. Test both:
- **No worker running:** call `validate_file` → it completes inline (fallback), `get_run_status` shows completed. (No stranded run.)
- **With a worker:** in another terminal `valdo run-job-worker`, then `validate_file` → returns fast (queued), poll `get_run_status` → queued → running → completed; `get_violations` returns the results.

### 3c. Error / edge handling over MCP

- `validate_file` on a missing/garbage path → a clean MCP error, not a 500 stack.
- A plain `.json` array (not NDJSON) → "convert to NDJSON first" guidance.
- An XML file with a `<!DOCTYPE>` → rejected (hardened parser; XXE defense).

### 3d. Auth / rate-limit / revocation (banking-critical)

- **`/mcp/health` needs no token** (it's a load-balancer probe) — confirm it returns 200 with no auth.
- **Auth required otherwise:** with auth on (no dev bypass) and no token, a tool call → **401**.
- **Dev-bypass safety:** start the server with `VALDO_MCP_AUTH=dev` but WITHOUT `VALDO_ALLOW_DEV_AUTH` → confirm it does **not** bypass (server warns; calls still need auth).
- **Rate limit:** fire >30 `tools/call` in a minute on one token → expect **429 + Retry-After** (per-token cap).
- **Revocation:** mint a token (`valdo mcp-login`), use it, then `valdo mcp-revoke <jti>` → subsequent calls with that token are rejected.

### 3e. Regression gate

```bash
python3 -m pytest tests/unit/ tests/integration/test_mcp_*.py -q   # expect 0 failed (MCP integration tests cover the surface)
```

---

## 4. Secondary cross-check (UI/CLI) — optional, ~15 min

Confirm the MCP results agree with the other surfaces: open `http://localhost:8000/ui`
(Quick Test validate + DB Compare reconcile panel), and run the same sample via CLI
(`valdo validate --file … --mapping … --rules …`). The violation counts should match
what MCP returned and the sample's committed `expected_report.json`.

---

## 5. UAT checklist (tick as you go)

**Setup**
- [ ] `valdo-setup.sh` clean; `valdo serve` up; `curl /mcp/health` healthy
- [ ] MCP client authenticated (token via `mcp-login`, or dev-bypass with BOTH env vars)
- [ ] VS Code agent chat shows Valdo's tools

**BA (over MCP)**
- [ ] Discovered templates + formats by asking the agent
- [ ] `pick_etl_shape` recommended the right template
- [ ] Validated a sample and saw the violations conversationally
- [ ] Reconciled a mapping; compared two files
- [ ] Onboarded a **real spec** workbook → usable mapping

**QA (over MCP)**
- [ ] `tools/list` + key `resources/read` return expected content (incl. json/xml templates)
- [ ] `validate_file` → `get_violations` = 4 on JSON & XML samples (matches expected_report.json)
- [ ] `reconcile_mapping` + `compare_two_files` correct on SQLite
- [ ] Async flow: inline fallback (no worker) AND queued→done (with `run-job-worker`)
- [ ] Error/edge cases return clean MCP errors (bad path, plain-JSON-array, DOCTYPE-XML)
- [ ] `/mcp/health` no-auth 200; missing-token 401; dev-bypass needs BOTH env vars
- [ ] Rate limit → 429 + Retry-After; revoked token rejected
- [ ] `pytest tests/unit/ tests/integration/test_mcp_*.py` = 0 failed

**Sign-off**
- [ ] BA: ____________________ date: ______   QA: ____________________ date: ______

---

## 6. Findings report template (one per finding → a GitHub issue)

```
Title: [UAT-MCP][BA|QA] <one-line summary>

Environment: valdo-setup.sh local (SQLite), valdo-version-v4 @ <git rev-parse --short HEAD>
Interface: MCP (tool/resource/prompt: <name>) | client: VS Code Duo | raw JSON-RPC
Severity: blocker | major | minor | cosmetic

Steps:
1. <agent prompt OR the mcp_call.py invocation>
2. ...

Expected: <what the tool should return — cite the sample's expected_report.json if relevant>
Actual:   <what came back; paste the MCP response / error>
Notes:    <spec ref, auth mode, worker on/off, etc.>
```

---

## Reference docs

- `docs/MCP_SERVER.md` — the MCP surface: tools, resources, prompts, auth, rate-limit, health.
- `docs/MCP_CLIENTS.md` + `docs/mcp-clients/vscode/` — connecting VS Code / GitLab Duo + token minting.
- `templates/etl/*_README.md` — per-shape worked examples (the deterministic expected outcomes).
- `docs/USAGE_AND_OPERATIONS_GUIDE.md` — full CLI/UI/feature reference (secondary cross-check).
- `demo/reconcile_e2e/` — a runnable end-to-end reconcile demo (mcp-valdo + Playwright) you can mirror.
