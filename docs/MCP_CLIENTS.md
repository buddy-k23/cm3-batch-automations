# MCP Clients — Connecting Agents to Valdo

Valdo's Model Context Protocol (MCP) server (see
[`docs/MCP_SERVER.md`](MCP_SERVER.md)) exposes 21 tools, 7 resources, and
4 prompts. This document is the **client-side index**: it lists the
agent platforms that have been integration-tested against Valdo and
links to the per-client setup guides.

For the server-side reference (tool catalogue, auth modes, run state
persistence, table shape) see [`docs/MCP_SERVER.md`](MCP_SERVER.md).

---

## Supported clients

| Client            | Setup guide                                                                  | Transport                | Auth                         | Status                           |
|-------------------|------------------------------------------------------------------------------|--------------------------|------------------------------|----------------------------------|
| Claude Desktop    | (TODO — Sprint 7) See [`docs/MCP_SERVER.md`](MCP_SERVER.md) for ad-hoc config | stdio (via local proxy)  | `~/.valdo/mcp-token` bearer  | Works; setup guide pending       |
| VSCode + Copilot Chat | [`mcp-clients/vscode/vscode-setup.md`](mcp-clients/vscode/vscode-setup.md) | Streamable HTTP          | `~/.valdo/mcp-token` bearer  | Works; setup guide shipped (S6-5) |
| GitLab Duo Chat   | [`mcp-clients/gitlab-duo/gitlab-duo-setup.md`](mcp-clients/gitlab-duo/gitlab-duo-setup.md) | HTTP — Custom Tool fallback today, native MCP when available | `X-API-Key` (custom-tool) / Bearer (native) | Works via fallback; native MCP path documented |

---

## Capability matrix

Which Valdo MCP tools work in each client (as of June 2026):

| Tool                          | Surface     | Claude Desktop | VSCode + Copilot | GitLab Duo (native MCP) | GitLab Duo (Custom Tool fallback) |
|-------------------------------|-------------|:--------------:|:----------------:|:-----------------------:|:---------------------------------:|
| `list_sources`                | read-only   | Yes            | Yes              | Yes                     | Yes (via `/api/v2/onboarding/sources`) |
| `get_source_spec`             | read-only   | Yes            | Yes              | Yes                     | Yes (via `/committed-artefact`)        |
| `list_recent_runs`            | read-only   | Yes            | Yes              | Yes                     | Yes (via `/api/v2/runs/recent`)        |
| `validate_file`               | action      | Yes            | Yes              | Yes                     | Possible (multi-part awkward)          |
| `get_run_status`              | action      | Yes            | Yes              | Yes                     | Yes (via `/runs/{id}`)                 |
| `get_violations`              | action      | Yes            | Yes              | Yes                     | Yes (via `/runs/{id}/violations`)      |
| `infer_mapping_from_sample`   | onboarding  | Yes            | Yes              | Yes                     | Possible (large payload — prefer native) |
| `upload_workbook_as_spec`     | onboarding  | Yes            | Yes              | Yes                     | Possible (multi-part awkward)          |
| `onboard_source_dry_run`      | onboarding  | Yes            | Yes              | Yes                     | Yes (via dedicated Custom Tool)        |
| MCP resources (`runs/recent`) | resource    | Yes            | Yes              | Yes                     | Not available                          |
| MCP prompts (4 prompts)       | prompt      | Yes            | Yes              | Yes                     | Not available (use Duo's prompt library) |

**Notes on the matrix:**

- **GitLab Duo fallback path** uses Valdo's existing `/api/v2/`
  REST endpoints (the EE-S3 surface). It is read-friendly but the
  write-side tools (`validate_file` upload, `upload_workbook_as_spec`)
  require multi-part form posts that are awkward to model as Duo
  Custom Tool webhooks. For the write flow, BAs should drive Valdo
  from VSCode or Claude Desktop where native MCP is in place.
- **Claude Desktop** runs Valdo over **stdio** via `valdo mcp-serve`
  (the local-proxy pattern), not Streamable HTTP. The token is still
  read from `~/.valdo/mcp-token`.
- **VSCode** runs against the **Streamable HTTP** transport at
  `/mcp/`. The same endpoint serves all HTTP clients.

---

## Authentication summary

All three clients ultimately authenticate against the same backend:

| Auth mode            | When used                                                  | How to mint                                                     |
|----------------------|-----------------------------------------------------------|-----------------------------------------------------------------|
| Bearer token (HMAC)  | Claude Desktop, VSCode, native Duo MCP                    | `valdo mcp-login` → writes `~/.valdo/mcp-token` (0600 perms)    |
| `X-API-Key`          | Duo Custom Tool fallback, service-to-service automation   | Server admin runs `valdo create-api-key --role ...`             |
| Dev-mode bypass      | LOCAL dev only — `VALDO_MCP_AUTH=dev` **+** `VALDO_ALLOW_DEV_AUTH=1` | Two server-side env vars; no client credential needed |

The token TTL is configurable; the default is 8 hours, max 24 hours.
See [`src/mcp/auth.py`](../src/mcp/auth.py) for the TTL clamp constants.

> **Dev-mode bypass is a strict, two-key opt-in (S13.5-3, #409).** The
> zero-credential `admin` bypass requires BOTH `VALDO_MCP_AUTH=dev` AND a
> truthy `VALDO_ALLOW_DEV_AUTH` (`1`/`true`/`yes`/`on`). Setting
> `VALDO_MCP_AUTH=dev` alone does NOT bypass auth — the server warns once and
> falls through to the production auth chain. This keeps a copied
> `.env.example` (which ships `VALDO_MCP_AUTH=` empty) safe by default. Never
> set `VALDO_ALLOW_DEV_AUTH` in INT/prod.

---

## What to read next

- New to MCP? Start with the upstream spec:
  <https://modelcontextprotocol.io/specification>
- Setting up a single client? Jump to the per-client guide:
  - [`mcp-clients/vscode/vscode-setup.md`](mcp-clients/vscode/vscode-setup.md)
  - [`mcp-clients/gitlab-duo/gitlab-duo-setup.md`](mcp-clients/gitlab-duo/gitlab-duo-setup.md)
- Operating the server? See [`docs/MCP_SERVER.md`](MCP_SERVER.md).
- Smoke-testing the server end-to-end? See
  [`tests/manual/TEST_PLAN.md`](../tests/manual/TEST_PLAN.md) Scenario 5
  (curl-driven) and Scenario 7 (VSCode-driven).

---

## Versioning and freshness

The MCP ecosystem moves fast. Each client guide carries an explicit
**"As of <date>"** banner at the top. When you revisit this doc, check
the banners — if the dated state is older than ~3 months, double-check
against the upstream client's current docs before walking a BA through
the steps.
