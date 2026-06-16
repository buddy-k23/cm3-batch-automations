# Valdo MCP — VSCode setup

> **As of June 2026.** VSCode's MCP support landed in stable in early 2026
> via GitHub Copilot Chat 1.x. The config schema below tracks the
> `.vscode/mcp.json` shape current as of this writing. If you are on a
> newer Copilot Chat build and the steps below don't match the UI exactly,
> consult the upstream MCP spec at
> <https://modelcontextprotocol.io/specification> and the Copilot
> customization docs.

This guide walks a BA on a fresh Mac or Windows laptop through
connecting VSCode to a running Valdo MCP server end-to-end. After
following it you'll be able to invoke `list_sources` (and the other
eight Valdo tools) directly from VSCode Chat.

---

## What you need before you start

| Item                  | How to get it                                                                 |
|-----------------------|-------------------------------------------------------------------------------|
| VSCode 1.90+          | <https://code.visualstudio.com/download>                                      |
| GitHub Copilot Chat   | Marketplace extension, free with a Copilot subscription                       |
| Valdo CLI installed   | `pip install valdo` (or the RPM/PEX bundle — see [`docs/INSTALL.md`](../../INSTALL.md)) |
| Reachable Valdo server | The INT URL (default `http://localhost:8001`) or your prod URL              |
| LDAP credentials      | Your corporate username + password — same one you use for the Web UI         |

> The reference extension this guide was written against is **GitHub
> Copilot Chat 1.x**. Any other MCP-aware extension that reads
> `.vscode/mcp.json` (e.g. Continue.dev, Cline) should work with the same
> config file, possibly with minor schema tweaks. If you use a different
> extension, see its docs for where to drop the config.

---

## Step 1 — Install GitHub Copilot Chat

1. Open VSCode.
2. Open the Extensions view (`Cmd/Ctrl+Shift+X`).
3. Search for **GitHub Copilot Chat**.
4. Click **Install**.
5. Sign in to GitHub when prompted (the same account that has your
   Copilot subscription).

Verify it's working: open the Chat panel (`Cmd/Ctrl+Alt+I`) and ask
"hello" — you should get a reply.

---

## Step 2 — Mint your Valdo MCP token

Valdo issues short-lived signed bearer tokens (default 8h TTL, max 24h)
backed by your corporate LDAP credentials. The CLI subcommand
`valdo mcp-login` handles this:

```bash
# Against local INT (the default)
valdo mcp-login

# Against a remote RHEL host (the typical BA flow)
valdo mcp-login --server https://valdo.bank.internal

# Custom TTL (default is 8 hours)
valdo mcp-login --ttl-hours 12
```

You'll be prompted for your LDAP username and password. On success the
CLI writes `~/.valdo/mcp-token` with strict `0600` permissions and
prints something like:

```
MCP login successful.
  Token written: /Users/<you>/.valdo/mcp-token
  Principal:     uid=<you>,ou=people,dc=bank,dc=internal
  Role:          tester
  Expires at:    1718500000 (epoch seconds)
```

For the full CLI reference (including `--insecure`, `--token-path`,
and the error model) see
[`src/commands/mcp_login.py`](../../../src/commands/mcp_login.py).

> **Dev-mode shortcut.** If the Valdo server is running with
> `VALDO_MCP_AUTH=dev` (local INT only — see
> [`docs/MCP_SERVER.md`](../../MCP_SERVER.md)) you can skip Step 2
> entirely. The Authorization header is ignored in dev mode, but
> setting it anyway does no harm.

---

## Step 3 — Drop the MCP config into your workspace

Copy [`vscode-mcp-config.json`](vscode-mcp-config.json) into the workspace
where you want to use Valdo Chat:

```bash
mkdir -p .vscode
cp /path/to/this/repo/docs/mcp-clients/vscode/vscode-mcp-config.json .vscode/mcp.json
```

The file is safe to commit — it contains no secrets. Bearer tokens are
read from `~/.valdo/mcp-token` at session start via the `${file:...}`
substitution.

### 3a — Point at a different Valdo server (optional)

The default URL in the shipped config is `http://localhost:8001/mcp/`.
Two ways to target a different Valdo:

**Option 1 — edit the URL directly.** Open `.vscode/mcp.json` and
replace the `url` value with your target, e.g.
`"url": "https://valdo.bank.internal/mcp/"`. Commit the edit if you
want the rest of the team to inherit it.

**Option 2 — env-var indirection.** Set `VALDO_MCP_URL` in your shell
and change the config to read it:

```jsonc
"url": "${env:VALDO_MCP_URL}"
```

Then export the URL before launching VSCode:

```bash
# macOS / Linux
launchctl setenv VALDO_MCP_URL https://valdo.bank.internal/mcp/
# Then restart VSCode (full quit, not just reload).
```

```powershell
# Windows PowerShell — persists for new sessions
[Environment]::SetEnvironmentVariable("VALDO_MCP_URL", "https://valdo.bank.internal/mcp/", "User")
# Then restart VSCode.
```

Use option 1 for shared/checked-in configs and option 2 when each
developer needs their own target host.

### 3b — Inline-token fallback (if `${file:}` isn't supported by your Copilot Chat build)

Older Copilot Chat builds did not support reading the token from a file.
For those builds, replace the `headers` block in `.vscode/mcp.json` with:

```json
"headers": {
  "Authorization": "Bearer ${env:VALDO_MCP_TOKEN}",
  "Accept": "application/json,text/event-stream"
}
```

…and export the token value into your shell before launching VSCode:

```bash
# Extract the token value field from ~/.valdo/mcp-token (it's JSON)
export VALDO_MCP_TOKEN=$(python -c "import json,pathlib; print(json.loads(pathlib.Path.home().joinpath('.valdo/mcp-token').read_text())['token']['value'])")
```

This is less convenient (the value lives in your shell env and you must
re-export after every `mcp-login`) but works on every Copilot Chat
version that supports env-var substitution at all.

---

## Step 4 — Reload VSCode

The MCP server config is read at workspace startup. After dropping
`mcp.json` in place:

1. Quit VSCode (`Cmd/Ctrl+Q`).
2. Reopen the workspace.

Or use the **Developer: Reload Window** command from the command palette
(`Cmd/Ctrl+Shift+P`).

---

## Step 5 — Verify the connection

1. Open the Chat panel (`Cmd/Ctrl+Alt+I`).
2. Switch to **Agent mode** (Copilot Chat 1.0+ — look for the mode picker
   at the top of the chat panel).
3. Ask:

   > List the data sources configured in Valdo.

   Copilot should visibly call the `list_sources` MCP tool. The tool
   call shows up in the chat transcript with a collapsed JSON request
   and response. Expand it and you should see at least one source —
   typically `SHAW` if you're against the INT instance — in the response.

4. (Optional, more thorough) Ask:

   > Use Valdo to fetch the spec for the SHAW source.

   This invokes `get_source_spec("SHAW")` and returns the source's
   mappings + rules summary.

If both of those succeed, you're done. Hand the workspace to your team.

---

## Troubleshooting

### "Token has expired. Run 'valdo mcp-login'"

The token TTL is 8 hours by default, max 24 hours. Re-mint:

```bash
valdo mcp-login
```

The new token overwrites `~/.valdo/mcp-token`. Reload the VSCode window
(`Cmd/Ctrl+Shift+P → Developer: Reload Window`) so the new token gets
picked up.

### HTTP 401 from VSCode Chat

Most common causes, in order:

1. **Token file has wrong permissions.** The Valdo server refuses
   tokens read from world-readable files. Fix:

   ```bash
   chmod 600 ~/.valdo/mcp-token
   ```

2. **Token expired.** See above — re-run `valdo mcp-login`.

3. **Token signed with a different key than the server uses.** This
   happens when the `VALDO_MCP_TOKEN_SIGNING_KEY` was rotated server-side
   after you minted your token. Re-mint.

4. **`${file:}` substitution silently failed.** Check the VSCode
   Output panel → select "MCP" or "GitHub Copilot Chat" from the
   dropdown. You should see the substituted URL and (redacted) header.
   If the Authorization header is literally `Bearer ${file:...}`,
   your Copilot Chat build doesn't support the substitution — use the
   env-var fallback in §3b.

### "Tool list is empty" or no tools show up in Chat

1. **Server URL is wrong / unreachable.** Curl-test it:

   ```bash
   curl -sS -X POST http://localhost:8001/mcp/ \
     -H "Content-Type: application/json" \
     -H "Accept: application/json,text/event-stream" \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
   ```

   You should get a JSON-RPC response listing 9 tools. If you get
   `Connection refused`, the Valdo server isn't running on that host /
   port. If you get HTTP 401, the dev-mode auth bypass isn't enabled and
   you do need the bearer token.

2. **VSCode silently failed to load `mcp.json`.** Check the Output
   panel → "MCP" channel for parse errors. JSON comment lines (the
   `_comment_*` keys) are valid JSON but VSCode may warn about them in
   strict mode — they're advisory only, the file still loads.

### "Dev mode" works locally but production doesn't

Production deployments **always** enforce bearer token auth. The
`VALDO_MCP_AUTH=dev` bypass is INT-only. For prod:

1. Make sure you ran `valdo mcp-login --server https://<prod-host>`
   (not the local default).
2. Make sure your LDAP user has at least the `tester` role
   (group→role mapping is in [`docs/MCP_SERVER.md`](../../MCP_SERVER.md)).

---

## See also

- [`docs/MCP_CLIENTS.md`](../../MCP_CLIENTS.md) — overview of all MCP
  clients (Claude Desktop / VSCode / GitLab Duo) and the capability
  matrix
- [`docs/MCP_SERVER.md`](../../MCP_SERVER.md) — server-side reference
  (tool catalogue, auth modes, run state persistence)
- [`docs/mcp-clients/gitlab-duo/gitlab-duo-setup.md`](../gitlab-duo/gitlab-duo-setup.md)
  — the equivalent guide for GitLab Duo Chat
- [`src/commands/mcp_login.py`](../../../src/commands/mcp_login.py) — the
  `valdo mcp-login` CLI source (full flag reference)
- Upstream MCP spec — <https://modelcontextprotocol.io/specification>
