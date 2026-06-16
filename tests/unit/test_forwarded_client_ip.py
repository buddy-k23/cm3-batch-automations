"""S9-1 (#387) — FastAPI honours X-Forwarded-* behind the nginx reverse proxy.

When Valdo runs behind nginx (TLS termination + proxy_pass), the TCP peer
FastAPI sees is nginx, not the real client. The MCP token-signing audit
trail and any IP-based logging MUST record the *real* client IP carried in
``X-Forwarded-For`` — otherwise every audit event would attribute actions
to the proxy's loopback/internal address, defeating SOX traceability.

These tests assert that uvicorn's ``ProxyHeadersMiddleware`` (wired in
:mod:`src.api.main`, trust list configurable via
``VALDO_MCP_TRUSTED_PROXIES``) rewrites ``request.client.host`` from the
forwarded header so handlers and the audit path see the forwarded IP, not
the direct peer.

Before the S9-1 change these tests FAIL: with no proxy-header middleware,
``request.client.host`` reflects the TestClient peer ("testclient"), never
the ``X-Forwarded-For`` value.
"""

from __future__ import annotations

import importlib

from fastapi import Request
from fastapi.testclient import TestClient


def _build_app(monkeypatch, trusted_proxies: str):
    """Reload src.api.main with the proxy-trust env var applied.

    ``ProxyHeadersMiddleware`` reads its trust list at app-construction time,
    so the module must be re-imported after the env var is set for the test
    to exercise the configured posture rather than a cached app instance.
    """
    monkeypatch.setenv("VALDO_MCP_TRUSTED_PROXIES", trusted_proxies)
    # A signing key so the auth.enabled startup gate (config/ui.yml) passes.
    monkeypatch.setenv("VALDO_SESSION_SIGNING_KEY", "test-key-forwarded-ip-000000")
    import src.api.main as main_mod

    importlib.reload(main_mod)
    return main_mod


def test_forwarded_for_is_seen_by_handler(monkeypatch):
    """A request carrying X-Forwarded-For reaches a handler whose
    ``request.client.host`` equals the forwarded IP, not the proxy peer.

    We register a throwaway probe route on the reloaded app that echoes the
    client host FastAPI resolved, then assert it matches the forwarded IP.
    """
    main_mod = _build_app(monkeypatch, trusted_proxies="*")
    app = main_mod.app

    @app.get("/__test__/whoami")
    async def _whoami(request: Request):
        return {"client_host": request.client.host if request.client else None}

    client = TestClient(app)
    resp = client.get(
        "/__test__/whoami",
        headers={"X-Forwarded-For": "203.0.113.77", "X-Forwarded-Proto": "https"},
    )

    assert resp.status_code == 200
    assert resp.json()["client_host"] == "203.0.113.77"


def test_untrusted_proxy_does_not_spoof_client_ip(monkeypatch):
    """When the peer is NOT in VALDO_MCP_TRUSTED_PROXIES, X-Forwarded-For is
    ignored and the direct peer wins — an attacker cannot spoof their IP.

    With the trust list pinned to a host the TestClient peer never matches,
    the forwarded header must be ignored and the resolved client host must
    NOT be the spoofed value.
    """
    main_mod = _build_app(monkeypatch, trusted_proxies="10.255.255.1")
    app = main_mod.app

    @app.get("/__test__/whoami2")
    async def _whoami2(request: Request):
        return {"client_host": request.client.host if request.client else None}

    client = TestClient(app)
    resp = client.get(
        "/__test__/whoami2",
        headers={"X-Forwarded-For": "203.0.113.99"},
    )

    assert resp.status_code == 200
    # The spoofed forwarded value must NOT be trusted from an untrusted peer.
    assert resp.json()["client_host"] != "203.0.113.99"
