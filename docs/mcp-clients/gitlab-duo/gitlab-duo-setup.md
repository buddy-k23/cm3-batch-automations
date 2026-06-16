# Valdo + GitLab Duo Chat — setup

> **As of June 2026.** GitLab Duo Chat's native Model Context Protocol
> (MCP) tool registration is still emerging. The first
> tech-previews shipped in GitLab 17.x but coverage is partial and the
> registration surface is in flux. On most enterprise GitLab installs
> the BA-facing experience today is via **Duo Custom Tools** — a webhook
> that proxies HTTP requests to an internal service. This guide
> documents both paths and biases toward the fallback path because that
> is what works reliably on the GitLab versions BAs are most likely to
> be running.
>
> When you revisit this doc, check the **dated state** line above. If
> GitLab Duo has since shipped first-class MCP support, prefer Path A
> (native) and treat Path B (custom-tool fallback) as legacy.

---

## Path A — Native MCP tool registration (when supported)

GitLab Duo's native MCP support, where it exists, plugs an MCP server
URL into the Duo Chat configuration so the agent can call
`tools/list` / `tools/call` directly against Valdo. On builds that
support it the registration surface is in **Settings → GitLab Duo →
External tools → Model Context Protocol** (exact path varies by
GitLab version).

### Configuration sketch

If your GitLab Duo build exposes the MCP registration UI, supply the
following:

| Field                  | Value                                                |
|------------------------|------------------------------------------------------|
| Server name            | `valdo`                                              |
| Transport              | Streamable HTTP                                      |
| URL                    | `https://valdo.bank.internal/mcp/`                   |
| Auth scheme            | Bearer                                               |
| Token                  | The token value from `~/.valdo/mcp-token` (mint via `valdo mcp-login`) |
| Accept header          | `application/json,text/event-stream`                 |

The same 9 tools that VSCode sees (see
[`docs/MCP_CLIENTS.md`](../../MCP_CLIENTS.md) capability matrix) become
available in Duo Chat.

### Why this section is a stub

The exact registration UI is still moving between GitLab releases. We
deliberately do not paste a screenshot or step-by-step click-path here
because it would go stale before the next sprint. Consult the latest
GitLab Duo docs at <https://docs.gitlab.com/ee/user/gitlab_duo_chat/>
for the current registration surface. If it works, you're done; if
not, fall through to Path B.

---

## Path B — Duo Custom Tool fallback (current state for most BAs)

The reliable BA-facing path today is to register a **Duo Custom Tool**
that calls Valdo's existing `/api/v2/` REST endpoints (the EE-S3
surface). Duo Chat treats the Custom Tool as a webhook: the BA asks a
question, Duo calls the webhook, and Duo renders the response in chat.

This path is **read-only by default** — it surfaces the `list_sources`
and `get_source_spec` equivalents (Valdo's `GET /api/v2/onboarding/sources`
and `GET /api/v2/onboarding/committed-artefact`) without exposing the
write tools (`validate_file`, `upload_workbook_as_spec`,
`onboard_source_dry_run`). If your tester role requires the write tools
from Duo, register a second Custom Tool per write endpoint, gated by
the same `X-API-Key` scope the Web UI uses.

### B.1 — Mint an API key for Duo

Duo Custom Tools authenticate with a service-to-service header, not a
per-user LDAP token. Issue a dedicated API key for the Duo integration:

1. Server admin runs (on the Valdo server):

   ```bash
   valdo create-api-key \
     --name "gitlab-duo-readonly" \
     --role tester \
     --scope "onboarding:read"
   ```

2. Capture the resulting key — you only see it once.

3. Store it in GitLab's CI/CD variables (Project → Settings → CI/CD →
   Variables) as `VALDO_API_KEY`, **masked** and **protected**.

### B.2 — Register the Custom Tool in GitLab Duo

Go to: **Settings → GitLab Duo → Custom tools → Add tool**.

| Field              | Value                                                                                                      |
|--------------------|------------------------------------------------------------------------------------------------------------|
| Tool name          | `valdo-list-sources`                                                                                       |
| Description        | List all data sources configured in Valdo. Use when the user asks about available sources or onboardings. |
| Endpoint URL       | `https://valdo.bank.internal/api/v2/onboarding/sources`                                                    |
| HTTP method        | `GET`                                                                                                      |
| Headers            | `X-API-Key: $VALDO_API_KEY` <br> `Accept: application/json`                                                |
| Input schema       | `{}` (no input — read-only listing)                                                                        |
| Response renderer  | Markdown — see B.3 below                                                                                   |

Repeat with one tool per Valdo endpoint you want to expose. The minimum
useful set:

| Custom Tool name        | Valdo endpoint                                       | Use case                                |
|-------------------------|------------------------------------------------------|-----------------------------------------|
| `valdo-list-sources`    | `GET /api/v2/onboarding/sources`                     | "What sources does Valdo know about?"   |
| `valdo-get-source-spec` | `GET /api/v2/onboarding/committed-artefact?source=$source` | "Show me the SHAW spec"            |
| `valdo-list-recent-runs`| `GET /api/v2/runs/recent?limit=10`                   | "What did Valdo validate today?"        |

### B.3 — Response rendering

Duo expects the Custom Tool's response to be either plain text,
markdown, or a JSON object with a top-level `message` or `result` key.
Valdo's `/api/v2/onboarding/sources` returns:

```json
{
  "sources": [
    {"name": "SHAW", "file_types": ["TRANERT"], "last_committed": "2026-06-10T14:22:01Z"},
    {"name": "SRC_A", "file_types": ["ATOCTRAN"], "last_committed": "2026-05-30T09:15:44Z"}
  ]
}
```

Duo's default JSON renderer will pretty-print this. If you want a
nicer experience, drop in the optional relay script
[`mcp_relay_for_duo.py`](mcp_relay_for_duo.py) — it accepts the same
Custom Tool callout, calls the Valdo API, and returns a markdown table:

```markdown
| Source | File types | Last committed       |
|--------|------------|----------------------|
| SHAW   | TRANERT    | 2026-06-10 14:22 UTC |
| SRC_A  | ATOCTRAN   | 2026-05-30 09:15 UTC |
```

The relay is **optional** — Duo can talk to Valdo directly. Use the
relay only if you want server-side response shaping (markdown tables,
violation summaries, drift highlights) without modifying Valdo's
public API.

### B.4 — Verify

In a GitLab issue or merge request, open the Duo Chat panel and ask:

> What data sources does Valdo know about?

Expected: Duo calls the `valdo-list-sources` Custom Tool, renders a
list with at least `SHAW`.

If Duo says "I don't have access to a tool that can answer that",
check:

1. The Custom Tool's description triggered Duo's tool router. Try
   re-asking with explicit phrasing: "Use the valdo-list-sources tool."
2. The `VALDO_API_KEY` CI variable is set at the project level (or
   group level — Duo runs scoped to the project context).
3. The endpoint URL is reachable from GitLab's egress. Curl-test from
   a runner if needed.

---

## Limitations of Path B vs Path A

| Capability                       | Path A (native MCP) | Path B (custom tool fallback) |
|----------------------------------|---------------------|-------------------------------|
| `list_sources`                   | Yes                 | Yes (via `/api/v2/onboarding/sources`) |
| `get_source_spec`                | Yes                 | Yes (via `/api/v2/onboarding/committed-artefact`) |
| `list_recent_runs`               | Yes                 | Yes (via `/api/v2/runs/recent`)        |
| `validate_file`                  | Yes                 | Possible — needs a write-scoped API key + Custom Tool with file payload |
| `get_run_status`                 | Yes                 | Yes (via `/api/v2/runs/{run_id}`)      |
| `get_violations`                 | Yes                 | Yes (via `/api/v2/runs/{run_id}/violations`) |
| `infer_mapping_from_sample`      | Yes                 | Possible — large payload, recommend native path instead |
| `upload_workbook_as_spec`        | Yes                 | Possible — multi-part upload via Custom Tool is awkward |
| `onboard_source_dry_run`         | Yes                 | Possible — read-only, returns a `would_write` list |
| MCP resources (`runs/recent`, `prompts/list`) | Yes    | Not available — no equivalent REST surface |
| MCP prompts (3 guided BA prompts)             | Yes    | Not available — Duo's own prompt library is the substitute |

In short: Path B covers the BA's day-to-day "show me what's there"
workflow. For the write-side onboarding flow (upload workbook →
dry-run → commit) the BA should still drive Valdo from VSCode or
Claude Desktop, where native MCP is in place today.

---

## See also

- [`docs/MCP_CLIENTS.md`](../../MCP_CLIENTS.md) — overview of all MCP
  clients
- [`docs/MCP_SERVER.md`](../../MCP_SERVER.md) — server-side reference
- [`docs/mcp-clients/vscode/vscode-setup.md`](../vscode/vscode-setup.md)
  — VSCode equivalent of this guide
- [`mcp_relay_for_duo.py`](mcp_relay_for_duo.py) — optional response
  shaping relay
- Upstream GitLab Duo docs —
  <https://docs.gitlab.com/ee/user/gitlab_duo_chat/>
