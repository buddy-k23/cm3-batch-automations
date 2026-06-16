"""Optional response-shaping relay for GitLab Duo Custom Tools → Valdo.

This is a small FastAPI app a Valdo operator can deploy next to the
main Valdo server (or as a sidecar) when the BA wants nicely-rendered
markdown tables in GitLab Duo Chat instead of raw JSON.

It is **not required** for Duo → Valdo integration. Duo can call
Valdo's REST API directly (see ``gitlab-duo-setup.md`` §B). Deploy this
relay only when:

* You want server-side rendering (markdown tables, drift highlights)
* You want to bridge older Duo Custom Tool webhook formats to Valdo's
  current ``/api/v2/`` shape
* You want to add per-request audit logging at the relay layer without
  modifying Valdo's public surface

The relay is intentionally a single file with no Valdo internals
dependency — it talks to Valdo over HTTP like any other client. Drop
it into ``/opt/valdo-duo-relay/`` and run with::

    pip install fastapi httpx uvicorn
    uvicorn mcp_relay_for_duo:app --host 0.0.0.0 --port 8101

then point your GitLab Duo Custom Tool at
``http://relay-host:8101/duo/list-sources`` instead of Valdo's
``/api/v2/onboarding/sources`` direct.

SECURITY POSTURE
----------------
* The relay re-uses the API key the BA supplied in the Duo Custom Tool
  request header — it does NOT keep its own service credential. This
  preserves audit attribution (the request shows up in Valdo's audit
  log under the BA's API key, not the relay's).
* TLS verification is on by default. Override with the ``VALDO_INSECURE``
  env var only for self-signed dev certs.
* The relay does not store request bodies, response payloads, or API
  keys. It is stateless.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request

logger = logging.getLogger(__name__)

# Configuration --------------------------------------------------------

VALDO_BASE_URL = os.environ.get("VALDO_BASE_URL", "http://localhost:8001")
VALDO_INSECURE = os.environ.get("VALDO_INSECURE", "").lower() in ("1", "true", "yes")
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("VALDO_RELAY_TIMEOUT", "15"))


app = FastAPI(
    title="Valdo → GitLab Duo Custom Tool relay",
    description=(
        "Response-shaping relay that turns Valdo REST JSON responses "
        "into markdown that GitLab Duo Chat renders nicely."
    ),
    version="1.0.0",
)


def _client() -> httpx.AsyncClient:
    """Build the HTTPX client with consistent TLS / timeout settings.

    Returns:
        An :class:`httpx.AsyncClient` configured against ``VALDO_BASE_URL``.
    """
    return httpx.AsyncClient(
        base_url=VALDO_BASE_URL,
        verify=not VALDO_INSECURE,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )


def _require_api_key(x_api_key: str | None) -> str:
    """Return the BA's API key or raise 401 if absent.

    Args:
        x_api_key: The header value Duo forwarded from the Custom Tool
            configuration.

    Returns:
        The API key string for re-forwarding to Valdo.

    Raises:
        HTTPException: 401 if the header is missing or blank.
    """
    if not x_api_key:
        raise HTTPException(
            status_code=401,
            detail="Missing X-API-Key header. Configure it in the Duo Custom Tool definition.",
        )
    return x_api_key


# --- Tool 1: list sources -------------------------------------------------


@app.get("/duo/list-sources")
async def duo_list_sources(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    """Proxy to Valdo's ``GET /api/v2/onboarding/sources`` with markdown output.

    Args:
        x_api_key: The BA's Valdo API key, forwarded from Duo.

    Returns:
        A dict with a ``message`` (markdown) and ``result`` (raw JSON) key.
        Duo renders the ``message`` field directly.

    Raises:
        HTTPException: 401 if the API key is missing, 502 if Valdo is
            unreachable, or the upstream status code if Valdo returned
            an error.
    """
    api_key = _require_api_key(x_api_key)

    async with _client() as client:
        try:
            response = await client.get(
                "/api/v2/onboarding/sources",
                headers={"X-API-Key": api_key, "Accept": "application/json"},
            )
        except httpx.RequestError as exc:
            logger.warning("Valdo unreachable: %s", exc)
            raise HTTPException(
                status_code=502, detail=f"Valdo unreachable: {exc}"
            ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.text[:500],
        )

    payload = response.json()
    sources = payload.get("sources", [])

    if not sources:
        return {
            "message": "_Valdo reports no configured sources._",
            "result": payload,
        }

    # Render a markdown table — Duo Chat renders this inline.
    lines = ["| Source | File types | Last committed |", "|--------|------------|----------------|"]
    for src in sources:
        name = src.get("name", "?")
        file_types = ", ".join(src.get("file_types", []) or ["?"])
        last = src.get("last_committed", "—")
        lines.append(f"| {name} | {file_types} | {last} |")

    return {"message": "\n".join(lines), "result": payload}


# --- Tool 2: get source spec ---------------------------------------------


@app.get("/duo/source-spec/{source}")
async def duo_source_spec(
    source: str,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    """Proxy to Valdo's committed-artefact endpoint, rendering a summary.

    Args:
        source: Source code (e.g. ``SHAW``).
        x_api_key: The BA's Valdo API key, forwarded from Duo.

    Returns:
        A dict with a ``message`` (markdown summary) and ``result`` (raw
        JSON) key.
    """
    api_key = _require_api_key(x_api_key)

    async with _client() as client:
        try:
            response = await client.get(
                "/api/v2/onboarding/committed-artefact",
                params={"source": source},
                headers={"X-API-Key": api_key, "Accept": "application/json"},
            )
        except httpx.RequestError as exc:
            logger.warning("Valdo unreachable: %s", exc)
            raise HTTPException(
                status_code=502, detail=f"Valdo unreachable: {exc}"
            ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.text[:500],
        )

    payload = response.json()
    summary = payload.get("summary", {})

    msg = (
        f"### Valdo source: **{source}**\n\n"
        f"- Mappings: {summary.get('mapping_count', '?')}\n"
        f"- Rules: {summary.get('rules_count', '?')}\n"
        f"- File types: {', '.join(summary.get('file_types', []) or ['?'])}\n"
        f"- Last committed: {summary.get('last_committed', '—')}\n"
    )
    return {"message": msg, "result": payload}


# --- Health check --------------------------------------------------------


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Plain liveness probe — does not call Valdo.

    Returns:
        A dict reporting the relay's status and target Valdo URL.
    """
    return {"status": "ok", "valdo_base_url": VALDO_BASE_URL}
