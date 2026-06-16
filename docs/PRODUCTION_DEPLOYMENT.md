# Valdo MCP — Production Deployment Runbook

**Audience:** SRE / platform / security engineers deploying the Valdo MCP
server into the bank's INT region (and later prod) behind the edge.

**Scope:** This runbook is the single operational source of truth for
hardening and running the Valdo MCP server in a bank network. It is built up
across Sprint 9 — each story appends its own section. Work top-to-bottom for
a first deploy; jump to a section for a targeted change.

**End state (Sprint 9 goal):** an SRE can follow this document from a bare
RHEL host to a TLS-terminated `https://valdo.bank.internal/mcp/initialize`
handshake, with rate limiting, a token-revocation lever, an LB health probe,
and validations decoupled from the request path.

> Replace every `valdo.bank.internal` / cert path / IP in this runbook with
> your environment's real values. Nothing host-specific is hardcoded in the
> shipped artefacts (Architecture Principle #5).

## Architecture at a glance

```
   Agent / BA client (Mac or VDI)
            │  HTTPS
            ▼
   ┌─────────────────────────┐   :443 TLS termination, security headers,
   │  nginx 1.20+ (RHEL)     │   gzip, X-Forwarded-* injection
   │  /etc/nginx/conf.d/     │
   │      valdo.conf         │
   └───────────┬─────────────┘
               │  HTTP, loopback, X-Forwarded-For = real client
               ▼
   ┌─────────────────────────┐   uvicorn/gunicorn → src.api.main:app
   │  Valdo app 127.0.0.1:8000│   ProxyHeadersMiddleware recovers real IP
   │  (systemd: valdo.service)│   MCP sub-app mounted at /mcp
   └───────────┬─────────────┘
               │  JDBC / oracledb thin
               ▼
        Oracle (run history, etc.)
```

---

## TLS + nginx (S9-1)

This section takes you from zero to a valid TLS handshake at
`https://<FQDN>/mcp/initialize`.

### 0. Prerequisites

- RHEL 8 or 9 host, the Valdo RPM installed (`valdo.service` present), app
  reachable on loopback (`curl -s http://127.0.0.1:8000/api/v1/system/health`).
- nginx 1.20+ installed: `sudo dnf install nginx && nginx -v`.
- SELinux: allow nginx to make outbound (proxy) connections:
  `sudo setsebool -P httpd_can_network_connect 1`.
- firewalld: open the edge ports:
  `sudo firewall-cmd --permanent --add-service=https --add-service=http && sudo firewall-cmd --reload`.

### 1. Bind the app to loopback and trust the proxy

The app must listen only where nginx can reach it, and must trust nginx's
forwarded headers so audit logs record the **real client IP**, not nginx's.

In `/etc/valdo/.env` (read by `valdo.service`):

```bash
# App bind — loopback only; nginx is the sole ingress.
VALDO_HOST=127.0.0.1
VALDO_PORT=8000

# Trust the local nginx as a forwarding proxy. Comma-separated proxy
# IPs/CIDRs, or "*" ONLY if nothing but nginx can reach the app port.
# Single-host topology → loopback is correct and is also the default.
VALDO_MCP_TRUSTED_PROXIES=127.0.0.1

# Hostnames the MCP transport will accept (DNS-rebinding protection).
# Comma-separated; must include your public FQDN.
VALDO_MCP_ALLOWED_HOSTS=valdo.bank.internal

# MCP auth (EF-S7) — token signing key + LDAP must be configured for real
# auth. Leave VALDO_MCP_AUTH unset/empty in prod (signed-token auth).
VALDO_MCP_TOKEN_SIGNING_KEY=<32+ random bytes; rotate to revoke all tokens>
```

> **Never enable the dev-auth bypass in INT/prod (S13.5-3, #409).** The
> zero-credential `admin` bypass now requires BOTH `VALDO_MCP_AUTH=dev` AND
> `VALDO_ALLOW_DEV_AUTH=1`. `VALDO_MCP_AUTH=dev` on its own does NOT bypass
> auth — the server logs a one-time `mcp_dev_auth_misconfigured` warning and
> falls through to the production auth chain (fail-closed). Do **not** set
> `VALDO_ALLOW_DEV_AUTH` anywhere other than a developer's local machine.
> A `.env` copied from `.env.example` ships with `VALDO_MCP_AUTH=` empty and
> is therefore never auth-bypassed out of the box.

> **Why this matters:** FastAPI sees nginx as the TCP peer. uvicorn's
> `ProxyHeadersMiddleware` (wired in `src/api/main.py`) rewrites
> `request.client.host` from `X-Forwarded-For` **only when the peer is in
> `VALDO_MCP_TRUSTED_PROXIES`**. This is what makes the MCP token-mint audit
> event (`mcp_login_success` / `mcp_login_failure`, `client_ip` field) record
> the agent's real IP. Leave the trust list tight — an over-broad list lets a
> caller spoof its IP via a forged header.

Restart: `sudo systemctl restart valdo`.

### 2. Request a TLS certificate from the bank PKI team

Production certs come from the bank's internal PKI/CA — **Valdo does not mint
them**. Hand off:

1. Generate a CSR + private key on the host (key never leaves the box):

   ```bash
   sudo mkdir -p /etc/pki/tls/private /etc/pki/tls/certs
   sudo openssl req -new -newkey rsa:2048 -nodes \
     -keyout /etc/pki/tls/private/valdo.key \
     -out /tmp/valdo.csr \
     -subj "/CN=valdo.bank.internal/O=YourBank/OU=Platform"
   sudo chmod 600 /etc/pki/tls/private/valdo.key
   ```

2. Submit `/tmp/valdo.csr` to the PKI team via their cert-request process.
   Request: server-auth EKU, SAN = your FQDN, 1-year validity (or per bank
   policy).
3. They return a **leaf cert** and the **intermediate chain**. Concatenate
   leaf + intermediates (leaf first) into the cert file nginx serves:

   ```bash
   sudo bash -c 'cat valdo-leaf.crt valdo-intermediates.crt > /etc/pki/tls/certs/valdo.crt'
   sudo chmod 644 /etc/pki/tls/certs/valdo.crt
   ```

### 3. INT-region self-signed path (test before real certs land)

The PKI handoff has lead time. To validate the **whole nginx+app path** in
INT before real certs arrive, use a self-signed cert. This is for INT only —
clients must explicitly trust or `-k`-skip it; never use self-signed in prod.

```bash
sudo openssl req -x509 -newkey rsa:2048 -nodes -days 90 \
  -keyout /etc/pki/tls/private/valdo.key \
  -out /etc/pki/tls/certs/valdo.crt \
  -subj "/CN=valdo.bank.internal" \
  -addext "subjectAltName=DNS:valdo.bank.internal"
sudo chmod 600 /etc/pki/tls/private/valdo.key
```

> Valdo also ships a config-driven self-signed strategy
> (`config/ui.yml` → `tls.strategy: self_signed`, `src/services/tls_service.py`)
> for the app's own direct-TLS mode. In the nginx-terminated topology TLS
> lives at nginx, so the self-signed cert above is what you point nginx at.

### 4. Install and activate the nginx config

The RPM ships the sample to `/etc/nginx/conf.d/valdo.conf.sample` (it does
**not** auto-activate, so it can't clobber a hand-tuned conf).

```bash
sudo cp /etc/nginx/conf.d/valdo.conf.sample /etc/nginx/conf.d/valdo.conf
sudo vi /etc/nginx/conf.d/valdo.conf
#   - set server_name to your FQDN (both server blocks)
#   - set ssl_certificate / ssl_certificate_key paths
#   - confirm the upstream address (default 127.0.0.1:8000)
#   - on nginx 1.20–1.24: replace `http2 on;` with `listen 443 ssl http2;`
```

Validate and reload:

```bash
sudo nginx -t            # MUST print "syntax is ok" + "test is successful"
sudo systemctl enable --now nginx
sudo systemctl reload nginx
```

The shipped config provides: TLS 1.2/1.3 termination, HTTP→HTTPS 308
redirect, `X-Forwarded-Proto` / `X-Forwarded-Host` / `X-Forwarded-For` /
`X-Real-IP` injection, WebSocket/stream upgrade headers for MCP
Streamable-HTTP, gzip for JSON, and security headers (HSTS,
X-Content-Type-Options, X-Frame-Options, Referrer-Policy).

### 5. Validate the end-to-end TLS handshake

```bash
# (a) Certificate served + chain valid (drop --insecure once real certs land)
openssl s_client -connect valdo.bank.internal:443 -servername valdo.bank.internal </dev/null 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates

# (b) HTTP redirects to HTTPS
curl -sI http://valdo.bank.internal/ | grep -i location   # → https://...

# (c) Security headers present
curl -sI https://valdo.bank.internal/healthz | grep -iE 'strict-transport|x-content-type|x-frame'

# (d) THE acceptance check — a real MCP initialize handshake over TLS.
#     Use --insecure ONLY for the INT self-signed phase.
curl -sk https://valdo.bank.internal/mcp/initialize \
  -H "Authorization: Bearer $(base64 < ~/.valdo/mcp-token | tr -d '\n')" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
       "params":{"protocolVersion":"2024-11-05","capabilities":{},
                 "clientInfo":{"name":"curl","version":"0"}}}'
# Expect a JSON-RPC result with serverInfo.name == "valdo".
```

If `initialize` returns 401, the token/auth chain is the issue (see
EF-S7 / `docs/MCP_SERVER.md`); if it returns a 421/400 about host, add the
FQDN to `VALDO_MCP_ALLOWED_HOSTS` and restart the app.

### 6. Confirm the real client IP reaches the audit trail

```bash
# After a successful `valdo mcp-login` through nginx:
sudo tail -n 20 /var/log/valdo/audit.jsonl | grep mcp_login_success
# The "client_ip" field must be the AGENT's IP, not 127.0.0.1 / nginx.
```

If `client_ip` shows the proxy address, `VALDO_MCP_TRUSTED_PROXIES` does not
include the nginx hop — fix and restart the app.

### 7. Certificate renewal

**Manual (bank PKI, the default path):** Certs expire (typically annually).
~30 days before `notAfter` (from step 5a), repeat step 2 to obtain a renewed
cert, drop the new chain over `/etc/pki/tls/certs/valdo.crt` (and key if
re-keyed), then:

```bash
sudo nginx -t && sudo systemctl reload nginx   # reload — no downtime
```

Set a calendar reminder, or monitor expiry:

```bash
echo | openssl s_client -connect valdo.bank.internal:443 2>/dev/null \
  | openssl x509 -noout -enddate
```

**Automated (certbot / ACME, only if the bank runs an internal ACME CA):**
The shipped config already serves `/.well-known/acme-challenge/` from
`/var/lib/nginx/acme` for http-01. If an internal ACME endpoint exists:

```bash
sudo dnf install certbot python3-certbot-nginx
sudo certbot certonly --webroot -w /var/lib/nginx/acme \
  -d valdo.bank.internal --server https://acme.bank.internal/directory
# certbot installs a systemd timer that renews + reloads nginx automatically.
sudo systemctl list-timers | grep certbot
```

Point `ssl_certificate` / `ssl_certificate_key` at the certbot live paths
(`/etc/letsencrypt/live/<FQDN>/fullchain.pem` and `privkey.pem`) and add a
`--deploy-hook "systemctl reload nginx"`.

### Troubleshooting (TLS + nginx)

| Symptom | Likely cause | Fix |
|---|---|---|
| `nginx -t` fails on `$connection_upgrade` | duplicate `map` from another conf | delete the `map` block in `valdo.conf` (a shared snippet already defines it) |
| `502 Bad Gateway` | app down / SELinux blocking proxy | `systemctl status valdo`; `setsebool -P httpd_can_network_connect 1` |
| `client_ip` = 127.0.0.1 in audit | proxy not trusted | add nginx hop to `VALDO_MCP_TRUSTED_PROXIES`, restart app |
| `421`/host error on `initialize` | DNS-rebinding protection | add FQDN to `VALDO_MCP_ALLOWED_HOSTS`, restart app |
| `SSL_ERROR` / cert untrusted | self-signed in INT | expected — use `-k`, or import the INT CA into the client trust store |

---

## Health probe (S9-2)

`GET /mcp/health` is the **MCP-aware** load-balancer / readiness probe. The
FastAPI process can be alive (`/api/v1/system/health` returns 200) while the
MCP surface is wedged — the session manager never entered its run loop, a
resource handler started raising, or the tool registry regressed to empty. A
load balancer polling process-liveness alone would keep routing BA agents at a
broken transport. `/mcp/health` closes that gap.

**What it checks (in-process, NO DB round-trip):**

1. **Session-manager liveness** — confirms the FastMCP Streamable-HTTP session
   manager has entered `run()`. This is the in-process analogue of a
   successful `initialize` handshake; a dead manager means every `/mcp/`
   JSON-RPC call would fail.
2. **Resource read** — actually reads the `taxonomy://violations` resource
   through the registered handler, proving the resource path serves.
3. **Registry enumeration** — counts the registered tools, resources, and
   prompts from the live registry (no hardcoded expected literal — the counts
   move every sprint).

**No auth required.** The route is registered *outside* the MCP token-auth
gate (`MCPAuthMiddleware` short-circuits the `/mcp/health` path before any
credential check), because load balancers do not authenticate. Every other
`/mcp/` path still requires a session cookie, `X-API-Key`, or bearer token.

**Latency target: <100 ms.** Every check is in-process and touches only the
FastMCP registries and the engine-introspection taxonomy — there is
deliberately no DB query — so the endpoint stays cheap to poll frequently.

**Success — HTTP 200:**

```json
{
  "status": "healthy",
  "mcp_protocol_version": "2025-11-25",
  "tool_count": 10,
  "resource_count": 4,
  "prompt_count": 4,
  "uptime_seconds": 137
}
```

`mcp_protocol_version` tracks the installed `mcp` library; `*_count` fields
are the live registry sizes; `uptime_seconds` is whole seconds since the MCP
server module came up (correlate against a deploy/restart event).

**Failure — HTTP 503:** any failed internal check collapses to a structured
body the operator (and the failing load-balancer access-log line) can read
without a token:

```json
{
  "status": "unhealthy",
  "failed_check": "session_manager",
  "reason": "MCP session manager is not running — the Streamable-HTTP transport cannot complete an initialize handshake.",
  "mcp_protocol_version": "2025-11-25",
  "tool_count": 0,
  "resource_count": 0,
  "prompt_count": 0,
  "uptime_seconds": 5
}
```

`failed_check` is one of `session_manager` | `resource_read` | `registry`. The
`reason` is operator-facing and never echoes a raw stack trace.

**Load-balancer / nginx probe config:** the shipped `packaging/nginx/valdo.conf`
proxies `location = /healthz` to `http://valdo_mcp/mcp/health` (access logging
off). Point your LB / Kubernetes readiness probe at `/healthz` (or directly at
`/mcp/health`):

```nginx
location = /healthz {
    proxy_pass       http://valdo_mcp/mcp/health;
    proxy_set_header Host $host;
    access_log       off;
}
```

| Probe response | LB action |
|---|---|
| `200` | Node in service — route traffic |
| `503` | Drain this node — MCP surface degraded |
| no response / timeout | Node down — drain |

For Kubernetes, use it as a `readinessProbe` (poll every 5–10 s; the <100 ms
budget keeps the overhead negligible):

```yaml
readinessProbe:
  httpGet:
    path: /mcp/health
    port: 8000
  periodSeconds: 10
  timeoutSeconds: 1
  failureThreshold: 3
```

---

## Rate limiting (S9-3)

The `/mcp/` JSON-RPC surface is protected by an in-process **sliding-window**
rate limiter (`src/mcp/rate_limit.py`), wired into `MCPAuthMiddleware` so it
runs **after** auth resolves the caller's identity. This means per-token
limiting keys on the authenticated principal, and per-IP limiting uses the
**proxy-corrected client IP** recovered by the S9-1 forwarded-headers wiring
(`VALDO_MCP_TRUSTED_PROXIES`) — not the nginx hop.

**What is limited — only billable tool calls.** The limiter meters
`tools/call` requests. Three classes of request are **exempt**:

- **Resource reads** (`resources/read` — `taxonomy://…`, `templates://etl/…`,
  `formats://supported`): cheap and idempotent, never throttled.
- **Handshake / discovery** (`initialize`, `tools/list`, `resources/list`,
  `prompts/list`, `ping`): protocol overhead, never throttled.
- A body the limiter cannot parse as JSON-RPC: fails open (auth already gated
  the request; the transport rejects the bad body itself).

### Caps and env vars

| Budget | Default | Env var | Rationale |
|---|---|---|---|
| Per-token tool calls / min | **30** | `VALDO_MCP_RATE_LIMIT_PER_MINUTE` | One agent's normal working rate. |
| Per-IP tool calls / min | **60** | `VALDO_MCP_RATE_LIMIT_PER_IP_PER_MINUTE` | Defence-in-depth vs **token theft** — a stolen token used from one box still hits this wall; a thief rotating tokens from one IP is caught here. Wider than per-token (multiple legit agents may share an egress IP). |
| Per-token `get_run_status` polls / min | **240** | `VALDO_MCP_RATE_LIMIT_RUN_STATUS_PER_MINUTE` | **Elevated** — BAs poll long-running validations; the normal 30/min cap would self-DOS them. ~one poll every 250 ms. Metered on a **separate counter** so polling never erodes the normal tool budget (and vice-versa). |

Each cap falls back to its default when the env var is unset, empty,
non-numeric, or non-positive — a misconfigured value **cannot silently
disable** throttling (Architecture Principle #5: config from settings, with a
safe default). The rolling window is 60 s.

Set the caps in `/etc/valdo/.env` (read by `valdo.service`) and restart:

```bash
# MCP rate limiting (S9-3). Defaults shown; tune per environment.
VALDO_MCP_RATE_LIMIT_PER_MINUTE=30
VALDO_MCP_RATE_LIMIT_PER_IP_PER_MINUTE=60
VALDO_MCP_RATE_LIMIT_RUN_STATUS_PER_MINUTE=240
```

### 429 / Retry-After semantics

When a call exceeds a budget the middleware short-circuits — the transport
never sees the over-limit call — and returns:

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 12
Content-Type: application/json

{"error": "Rate limit exceeded", "scope": "per_token", "retry_after_seconds": 12}
```

- **`Retry-After`** is whole seconds until a slot frees — i.e. until the
  *oldest* in-window hit ages out of the 60 s window. It is always ≥ 1 and
  ≤ 60. A well-behaved client backs off for that long before retrying.
- **`scope`** names the binding budget (`per_token` or `per_ip`) so an
  operator can tell a single hot token from a hot source IP. The per-token
  check runs first, so a single misbehaving token is attributed to
  `per_token` rather than being masked by the wider IP budget; a
  token-throttled call does **not** also burn the caller's IP allowance.
- Each throttle event is logged at WARNING: `mcp_rate_limited scope=… tool=…
  user=… retry_after=…s` — correlate against the nginx access log to
  identify the offending agent.

### State model — in-memory now, Redis-pluggable later

Counters live in **process memory** (`InMemorySlidingWindowBackend`, a deque
of hit timestamps per key behind a `threading.Lock`). This is sufficient for
the single-host INT pilot. The backend sits behind a one-method
`RateLimitBackend` protocol, so a Redis (or other shared-store) backend can
drop in for a **multi-node** deployment without touching the limiter facade
or the middleware. **Redis is NOT a dependency today** — in-memory is the
only implementation shipped; distributed coordination is a deliberate
scale-out follow-up (see Sprint 9 kickoff, "Out of scope").

> **Multi-worker caveat:** with gunicorn running N workers, each worker holds
> its own in-memory window, so the *effective* cap is up to N × the
> configured value. For the INT pilot run a single worker, or set the env
> caps to `configured / N`. The Redis backend removes this caveat — that is
> its primary motivation for a scale-out deployment.

The window clock is injectable in `rate_limit.py` (`clock` callable) purely
for deterministic unit tests; production uses `time.monotonic`.

---

## Token revocation (S9-4)

EF-S7 MCP tokens are HMAC-signed and self-contained. Before S9-4 the only
lever to kill a leaked token was rotating `VALDO_MCP_TOKEN_SIGNING_KEY` —
which invalidates **every** outstanding token, innocent ones included. S9-4
adds a **per-token revocation blocklist** so a single compromised token can be
revoked without disrupting the fleet.

### How it works

Each minted token now carries an opaque **`jti`** (16 random bytes,
hex-encoded — a JWT-style token id) bound into the HMAC signature. To revoke a
token an admin submits its `jti` to `POST /api/v2/mcp/revoke`; the `jti` is
written to the Oracle table **`MCP_REVOKED_TOKENS`** (created by Alembic
migration `0005`). Token verification (`src/mcp/auth.py`) consults the
blocklist **after** signature + expiry validation and rejects any token whose
`jti` is listed.

### The `jti` grace window (zero-downtime rollout)

Tokens minted **before** S9-4 have no `jti`. To avoid breaking agents holding
those tokens at deploy time, a no-`jti` token still validates during a
**24-hour grace window**, then is rejected (forcing a `valdo mcp-login` that
mints a revocable, `jti`-bearing token).

The grace boundary is a single **epoch cutoff**: a no-`jti` token is accepted
iff its `issued_at` is *strictly before* the cutoff. Resolution order:

1. **`VALDO_MCP_JTI_GRACE_UNTIL`** (epoch seconds) — a deploy-time hard cutoff
   you can pin. Set it to "now + grace" at rollout to make the boundary
   explicit and auditable, e.g.:
   ```bash
   # Reject no-jti tokens 24h after this deploy:
   echo "VALDO_MCP_JTI_GRACE_UNTIL=$(($(date +%s) + 86400))" | sudo tee -a /etc/valdo/.env
   sudo systemctl restart valdo
   ```
2. **Unset** → a rolling default of **process-start + 24h**, computed once when
   the app starts. A fresh deploy thus honours the no-`jti` tokens already in
   the wild for 24h, then rejects them. (A malformed value falls back to this
   safe default — it cannot silently disable the gate.)

> `jti`-bearing tokens are never subject to the grace gate; only the absence of
> a `jti` triggers it.

### The revoke endpoint — `POST /api/v2/mcp/revoke`

Admin-only. The caller authenticates with **their own** LDAP credentials in the
request body (the endpoint is not API-key-gated); their LDAP groups are mapped
to a Valdo role and the request is **rejected with 403** unless that role is
`admin` — i.e. membership in the `valdo-admins` group per
`auth.ldap.group_role_map`. Bad credentials → 401; LDAP/table unavailable →
503.

```bash
curl -sk https://valdo.bank.internal/api/v2/mcp/revoke \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","password":"<admin-pw>",
       "token_id":"<the-jti-to-revoke>","reason":"laptop stolen — INC-12345"}'
# → {"revoked": true, "token_id": "<jti>", "revoked_by": "CN=alice,..."}
```

Every attempt is audited (`mcp_revoke_success` / `mcp_revoke_forbidden` /
`mcp_revoke_failure`) with the principal DN, client IP, `jti`, and reason — SOX
attribution for who revoked what and why.

### The `valdo mcp-revoke` CLI (SRE incident response)

For runbook / incident use, the same revocation is available from the CLI:

```bash
valdo mcp-revoke <token_id> --reason "laptop stolen — INC-12345"
valdo mcp-revoke <token_id> --server https://valdo.bank.internal --reason "leak"
```

It prompts for the operator's admin LDAP credentials (password is never echoed
or logged), POSTs to `/api/v2/mcp/revoke`, and exits non-zero on failure
(`3`=bad creds, `4`=not an admin/403, `5`=server/LDAP/table unavailable). The
`token_id` is the `jti` from the target token's payload (the `jti` field in
`~/.valdo/mcp-token`, or recovered from logs/audit).

### Cache behaviour and latency (the <1ms hot path)

The auth path checks the blocklist on **every** MCP call, so it is backed by an
**in-memory cache with a 60-second TTL** (`src/mcp/revocation.py`):

- A lookup within the TTL is answered from an in-process `set` — **no DB
  round-trip**, sub-millisecond.
- A lookup after the TTL reloads the whole blocklist with one `SELECT jti`.
- A revocation issued on a node **invalidates that node's cache immediately**,
  so the revoking node enforces it at once; **other nodes/workers converge
  within the TTL (≤ 60s)**. Plan incident response around this bound — for an
  instant fleet-wide kill, rotating the signing key remains the nuclear option.
- **Fail-soft:** if the database is unreachable at lookup time the cache
  retains its last snapshot (a already-cached revoked `jti` stays revoked) and
  logs a WARNING rather than failing auth closed — signature + expiry still
  gate the request. If the `MCP_REVOKED_TOKENS` table is absent entirely
  (migration not yet applied) the blocklist is simply empty and revocation is a
  no-op until `0005` is applied.

### Incident-response flow

1. **Identify** the compromised token's `jti` (from the agent's
   `~/.valdo/mcp-token`, or the `mcp_login_success` audit event's correlation).
2. **Revoke** via `valdo mcp-revoke <jti> --reason "<incident ref>"` (or the
   endpoint). The revoking node enforces immediately.
3. **Confirm** within 60s the token fails on all nodes (subsequent MCP calls
   from it 401). Check the `mcp_revoke_success` audit event landed.
4. **Re-issue** a fresh token to the legitimate user via `valdo mcp-login`.
5. **Escalation:** if many tokens are compromised at once, rotate
   `VALDO_MCP_TOKEN_SIGNING_KEY` (kills all tokens instantly, no TTL wait) and
   force a fleet-wide re-login.

### Migration

Apply the blocklist table before enabling the feature:

```bash
cd /opt/valdo && valdo db-migrate --revision head   # applies 0005_mcp_revoked_tokens
```

`MCP_REVOKED_TOKENS`: `jti` (PK), `reason`, `revoked_by`, `revoked_at`. The
migration is idempotent and cross-dialect (Oracle / PostgreSQL / SQLite).

---

## Background job worker (S9-5 seam, S10-1 runtime)

Per **ADR 0021** (`docs/adr/0021-mcp-background-jobs.md`, Option A), long
validations are decoupled from the MCP request path: instead of running the
engine synchronously inside the `validate_file` MCP call (which ties up a
gunicorn worker for the duration of a 10M-row file), `validate_file` writes a
durable `queued` row to `APP_MCP_RUN_REGISTRY` and returns within 100ms. A
separate **`valdo run-job-worker`** process claims that row out of band and
runs the validation. No new runtime dependency — the queue is the database
table the run registry already persists.

> **S9-5 shipped the seam** (`valdo run-job-worker --once` + the `claim_next`
> atomic dequeue + the async feature flag). **S10-1 shipped the production
> runtime**: the continuous poll loop with capped backoff, in-flight
> heartbeats, graceful `SIGTERM`/`SIGINT` shutdown, and the stuck-`running`
> reaper (Alembic 0006 adds `last_heartbeat_at` + `attempt_count`). **S10-2
> ships the cutover**: the RPM-packaged `valdo-run-job-worker.service` unit,
> the `MCP_WORKERS` liveness marker (Alembic 0007), and **flips the async flag
> ON by default** — made safe by a synchronous fallback (below). See below.

### The async feature flag (`VALDO_MCP_ASYNC_VALIDATE`)

**Default flipped ON in S10-2 (#397).** `validate_file` always writes the
durable `queued` row first, then chooses its execution path based on whether a
worker is live:

| Value | Behaviour |
|---|---|
| unset / `1` / `true` / `yes` / `on` (**default**) | **Liveness-aware.** If a `run-job-worker` has heartbeated within the liveness window, `validate_file` **enqueues only** and returns within ~100ms (the worker runs it out of band). If **no** worker is live, `validate_file` falls back to a **synchronous inline run** so the call always completes — nothing is stranded in `queued`. |
| `0` / `false` / `no` / `off` | **Always synchronous inline** (legacy behaviour), regardless of worker presence. |

This makes the ON-by-default flip safe in environments with no worker (local
dev, a host where the worker unit is stopped): the call self-heals to inline.
**Deploy order no longer strands runs** — but for the fast async path to engage
you must have the `valdo-run-job-worker` unit running (it ships in the RPM and
is enabled in `%post`; start it after configuring `/etc/valdo/.env`).

### Worker liveness marker (`MCP_WORKERS`, Alembic 0007)

The liveness signal lives in a **dedicated** `MCP_WORKERS` table — **not** a
sentinel row in `APP_MCP_RUN_REGISTRY` (a SOX-audited table of *runs*, kept
free of non-run rows). Each worker upserts a row (`worker_id`, `host`,
`started_at`, `last_heartbeat_at`) on start and on every poll iteration.
`validate_file` answers "is ANY worker draining the queue?" with a single
`SELECT ... WHERE last_heartbeat_at >= now - window` (any live worker → enqueue).

| Var | Default | Meaning |
|---|---|---|
| `VALDO_MCP_WORKER_LIVENESS_SECONDS` | `60` | Liveness window. A worker counts as live if it heartbeated within this many seconds. Comfortably larger than the worker's default `--poll-interval` (2s) so a busy/looping worker never flickers "dead" between iterations. |

The table also enables future worker observability (count / which-host).

### End-to-end async flow (enqueue → drain → status)

1. Agent calls `validate_file(source, file_path)`. The MCP server writes a
   `queued` row to `APP_MCP_RUN_REGISTRY`, checks `MCP_WORKERS` for a live
   worker, sees one, and returns `{run_id, started_at}` within ~100ms.
2. `valdo-run-job-worker` polls, `claim_next()` atomically flips the row
   `queued → running` (guarded `UPDATE`, so two workers never grab it), and
   runs the engine while a daemon thread heartbeats the run every poll.
3. On completion the worker writes the terminal `completed`/`failed` record.
4. The agent polls `get_run_status` (sees `queued` → `running` → terminal) and
   pages `get_violations`.

**10M-row flow.** A multi-hour 10M-row validation is exactly why this exists:
the engine runs on the worker (not tying up a gunicorn request), and the
in-flight heartbeat thread keeps `last_heartbeat_at` fresh so the reaper never
falsely reclaims it. If the worker crashes mid-run, the row stops heartbeating
and the reaper requeues it (`attempt_count++`) for another worker.

### Draining the queue

```bash
# Continuous mode (production default): poll, claim, run, reap, back off.
# Heartbeats keep in-flight runs fresh; SIGTERM/SIGINT stop gracefully.
cd /opt/valdo && valdo run-job-worker \
  --poll-interval 2 --reap-multiple 10

# One drain-and-exit cycle — claim a single queued job, run it, exit.
# Use for cron / manual backlog draining / CI.
cd /opt/valdo && valdo run-job-worker --once
```

**Knobs:**

| Flag | Default | Meaning |
|---|---|---|
| `--poll-interval` | `2.0` | Seconds to sleep when the queue is empty; also the in-flight heartbeat cadence. |
| `--max-runs` | `0` | Stop after N jobs (`0` = unbounded). Useful for drain-then-recycle. |
| `--reap-multiple` | `10` | Reaper threshold = `poll-interval * reap-multiple`. A `running` row whose heartbeat is older than this is reclaimed. Set so the threshold comfortably exceeds the worst-case validation time (≈2×+). |

A cron entry (`* * * * * cd /opt/valdo && valdo run-job-worker --once`) remains
a valid lightweight stop-gap; the continuous unit below is the production form.

### Heartbeat + stuck-run reaper

While a job runs, the worker spawns a daemon **heartbeat thread** that calls
`registry.heartbeat(run_id)` every `--poll-interval` seconds until the job
returns. This means even a multi-hour 10M-row validation keeps a fresh
`last_heartbeat_at` and is **never** falsely reclaimed by another worker.

On every idle poll the worker runs the **reaper**: any `running` row whose
`last_heartbeat_at` is older than `poll-interval * reap-multiple` (or whose
heartbeat is NULL and whose `started_at` is that old — a worker that died
before its first beat) is reset to `queued` and its `attempt_count` is
incremented, so the backlog self-heals after a worker crash. No manual SQL
reset is needed anymore.

### systemd unit (separate from the gunicorn service)

The worker runs as its **own** systemd unit, distinct from `valdo.service`
(the gunicorn MCP server). **S10-2 ships this unit in the RPM** as
`valdo-run-job-worker.service` (enabled in `%post`, disabled/stopped in
`%preun`); start it with `sudo systemctl start valdo-run-job-worker` once
`/etc/valdo/.env` is configured. The reference unit body below matches the
packaged one (the `--once` cron form needs no long-running unit):

```ini
[Unit]
Description=Valdo MCP background validation worker
After=network.target

[Service]
Type=simple
User=valdo
WorkingDirectory=/opt/valdo
EnvironmentFile=/etc/valdo/valdo.env
ExecStart=/opt/valdo/.venv/bin/valdo run-job-worker --poll-interval 2 --reap-multiple 10
# systemd sends SIGTERM on stop; the worker finishes its in-flight job and
# exits 0. TimeoutStopSec MUST exceed the worst-case single-file validation
# time so a graceful stop is never SIGKILL'd mid-run (which would strand a row
# in 'running' until the reaper reclaims it).
TimeoutStopSec=120
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

> **S10-2 (#397) ships this unit in the RPM** (`valdo-run-job-worker.service`);
> the body above is the reference for a manual install and matches the packaged
> unit (`After=network.target valdo.service`, `Restart=on-failure`,
> `NoNewPrivileges`/`PrivateTmp`, `TimeoutStopSec=120`).

### Lifecycle guarantees

- **Restart-pickup (works today).** `queued` rows are durable in the database
  backend. A worker, host, or deploy restart leaves the row `queued`; the next
  `run-job-worker --once` (or future poll) claims it. **No work is lost on
  deploy/rotation.**
- **Atomic claim (works today).** `run-job-worker` claims a job via the
  registry's guarded `UPDATE ... SET status='running' WHERE status='queued'`,
  so two workers never grab the same job — a different worker either claims a
  different `queued` row or gets nothing.
- **Graceful shutdown (works today).** The continuous worker traps
  `SIGTERM`/`SIGINT`, finishes its current run, claims no new work, and exits
  0. Set `TimeoutStopSec` above the worst-case validation time.
- **Heartbeat keeps long jobs alive (works today).** A daemon thread
  heartbeats the in-flight run every `--poll-interval` seconds, so even
  multi-hour validations are never reaped while genuinely running.
- **Stuck-`running` reaper (works today).** If a worker dies mid-run its row
  stops heartbeating; once `last_heartbeat_at` is older than
  `poll-interval * reap-multiple` the reaper resets it to `queued` and
  increments `attempt_count`. The backlog self-heals — no manual SQL reset
  required. (A manual reset is still valid as a last resort:
  `UPDATE APP_MCP_RUN_REGISTRY SET status='queued' WHERE run_id=:id AND status='running';`)

---

## Change log of this runbook

| Sprint story | Section added |
|---|---|
| S9-1 (#387) | Architecture overview, TLS + nginx, forwarded-IP wiring |
| S9-2 (#390) | Health probe — `/mcp/health` schema, 503 semantics, LB/K8s probe config |
| S9-3 (#388) | Rate limiting — per-token/per-IP caps, env vars, 429/Retry-After, resource-exempt + get_run_status-elevated rules, in-memory-vs-Redis |
| S9-4 (#389) | Token revocation — `jti` format + 24h grace window, `POST /api/v2/mcp/revoke` (admin-only), `valdo mcp-revoke` CLI, 60s-TTL blocklist cache, `MCP_REVOKED_TOKENS` (Alembic 0005), incident-response flow |
| S9-5 (#391) | Background job worker (skeleton) — ADR 0021 run-registry-as-queue, `VALDO_MCP_ASYNC_VALIDATE` flag (default off), `valdo run-job-worker --once`, separate systemd unit, atomic `claim_next`, restart-pickup, manual stuck-run reset (reaper is fast-follow) |
| S10-1 (#397) | Background-worker **runtime** — continuous poll loop (`--poll-interval`/`--max-runs`/`--reap-multiple`) with capped backoff, in-flight heartbeat thread, graceful `SIGTERM`/`SIGINT` shutdown, stuck-`running` reaper (`last_heartbeat_at`/`attempt_count`, Alembic 0006). Async flag still off (S10-2 flips it); RPM unit deferred to S10-2 |
| S10-2 (#397) | Background-worker **cutover** — async flag flipped **ON by default** with a live-worker-aware synchronous fallback; `MCP_WORKERS` liveness marker (Alembic 0007); RPM-packaged `valdo-run-job-worker.service`; multi-worker no-double-claim proven under concurrency. Sections: async flag (revised), worker liveness, end-to-end async flow (+10M-row) |
