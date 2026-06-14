"""Unit tests for IPWhitelistMiddleware.

Since the middleware now uses a rightmost-untrusted XFF strategy (issue #10
fix), tests must declare the test-client peer as a trusted proxy so the XFF
entry is honoured. We override Starlette's default ``request.client.host``
of ``"testclient"`` to a real IP via the ``TestClient(client=...)``
parameter, then list that IP in ``trusted_proxies``.

Tests that verify the *no-whitelist* (open) path use the raw testclient host,
which is always allowed when the whitelist is empty.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.api.middleware.ip_whitelist import IPWhitelistMiddleware

# Synthetic IP we make Starlette report as the test-client peer, plus a
# trusted-proxies list that contains it. Together these let an XFF header
# be honoured by the rightmost-untrusted algorithm.
_PEER_IP = "203.0.113.10"  # TEST-NET-3, reserved for documentation
_TRUSTED_PROXIES = [_PEER_IP]


def make_app(whitelist, trust_proxy=False, trusted_proxies=None):
    """Create a minimal FastAPI app with IPWhitelistMiddleware attached.

    Args:
        whitelist: List of IP addresses and/or CIDR ranges to allow.
        trust_proxy: If True, use X-Forwarded-For header as client IP.
        trusted_proxies: Optional list of trusted-proxy networks. When
            ``trust_proxy=True`` and ``trusted_proxies`` is omitted, the
            synthetic ``_PEER_IP`` is declared trusted so XFF entries
            sent by the test client are honoured.

    Returns:
        FastAPI application with the middleware and a /ping route.
    """
    if trust_proxy and trusted_proxies is None:
        trusted_proxies = _TRUSTED_PROXIES
    app = FastAPI()
    app.add_middleware(
        IPWhitelistMiddleware,
        whitelist=whitelist,
        trust_proxy=trust_proxy,
        trusted_proxies=trusted_proxies,
    )

    @app.get("/ping")
    def ping():
        return {"ok": True}

    return app


def make_client(app):
    """Create a TestClient whose reported peer is ``_PEER_IP`` not 'testclient'."""
    return TestClient(app, client=(_PEER_IP, 12345))


def test_empty_whitelist_allows_all():
    """No whitelist configured — every IP (including 'testclient') is allowed."""
    app = make_app(whitelist=[])
    client = TestClient(app)
    response = client.get("/ping")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_exact_ip_match_allowed():
    """An IP that exactly matches a whitelist entry is allowed (200)."""
    app = make_app(whitelist=["192.168.1.50"], trust_proxy=True)
    client = make_client(app)
    response = client.get("/ping", headers={"X-Forwarded-For": "192.168.1.50"})
    assert response.status_code == 200


def test_exact_ip_not_in_whitelist_denied():
    """An IP not in the whitelist is blocked (403)."""
    app = make_app(whitelist=["10.0.0.1"], trust_proxy=True)
    client = make_client(app)
    response = client.get("/ping", headers={"X-Forwarded-For": "192.168.1.99"})
    assert response.status_code == 403


def test_cidr_range_match_allowed():
    """An IP within a CIDR range whitelist entry is allowed (200)."""
    app = make_app(whitelist=["10.0.0.0/8"], trust_proxy=True)
    client = make_client(app)
    response = client.get("/ping", headers={"X-Forwarded-For": "10.20.30.40"})
    assert response.status_code == 200


def test_cidr_range_no_match_denied():
    """An IP outside all CIDR ranges is blocked (403)."""
    app = make_app(whitelist=["10.0.0.0/8"], trust_proxy=True)
    client = make_client(app)
    response = client.get("/ping", headers={"X-Forwarded-For": "172.16.0.1"})
    assert response.status_code == 403


def test_multiple_ranges():
    """IPs from each of multiple ranges are allowed; outside all ranges is denied."""
    app = make_app(whitelist=["10.0.0.0/8", "192.168.0.0/16"], trust_proxy=True)
    client = make_client(app)

    # IP in first range
    response = client.get("/ping", headers={"X-Forwarded-For": "10.0.0.5"})
    assert response.status_code == 200

    # IP in second range
    response2 = client.get("/ping", headers={"X-Forwarded-For": "192.168.5.1"})
    assert response2.status_code == 200

    # IP outside both ranges
    response3 = client.get("/ping", headers={"X-Forwarded-For": "172.16.0.1"})
    assert response3.status_code == 403


def test_trust_proxy_x_forwarded_for():
    """When trust_proxy=True, X-Forwarded-For header is used as client IP."""
    # Whitelist only 192.168.1.50; provide matching header
    app = make_app(whitelist=["192.168.1.50"], trust_proxy=True)
    client = make_client(app)
    response = client.get("/ping", headers={"X-Forwarded-For": "192.168.1.50"})
    assert response.status_code == 200


def test_trust_proxy_false_ignores_header():
    """When trust_proxy=False, X-Forwarded-For header is NOT used.

    Even if the header contains a whitelisted IP, the actual client host
    ('testclient', which is not a valid IP) is evaluated instead and blocked.
    """
    app = make_app(whitelist=["192.168.1.50"], trust_proxy=False)
    client = TestClient(app)
    # Header says whitelisted IP, but trust_proxy=False so it is ignored.
    # The real client host 'testclient' is not a valid IP → denied.
    response = client.get("/ping", headers={"X-Forwarded-For": "192.168.1.50"})
    assert response.status_code == 403


def test_invalid_whitelist_entry_ignored():
    """A non-IP string in the whitelist is silently skipped; valid entries still apply."""
    # "not-an-ip" should be logged as a warning and ignored.
    # The remaining valid entry "192.168.1.10" still applies.
    app = make_app(whitelist=["not-an-ip", "192.168.1.10"], trust_proxy=True)
    client = make_client(app)
    # IP matching the valid entry → allowed
    response = client.get("/ping", headers={"X-Forwarded-For": "192.168.1.10"})
    assert response.status_code == 200
    # IP not matching → denied (middleware is still functional)
    response2 = client.get("/ping", headers={"X-Forwarded-For": "10.0.0.1"})
    assert response2.status_code == 403


def test_403_response_body():
    """Blocked response must contain 'error' and 'detail' keys."""
    app = make_app(whitelist=["10.0.0.1"], trust_proxy=True)
    client = make_client(app)
    response = client.get("/ping", headers={"X-Forwarded-For": "9.9.9.9"})
    assert response.status_code == 403
    body = response.json()
    assert "error" in body
    assert "detail" in body


# ---------------------------------------------------------------------------
# Issue #10 — rightmost-untrusted XFF strategy regression tests
# ---------------------------------------------------------------------------


def test_spoofed_leftmost_xff_is_ignored_when_no_trusted_proxies():
    """Without trusted_proxies, an XFF entry is NOT honoured \u2014 the direct
    peer is used instead.

    Previously the leftmost XFF entry was trusted blindly; an attacker
    sending ``X-Forwarded-For: 10.0.0.1`` could trivially join a 10/8
    whitelist. Now, the rightmost-untrusted entry (which is the direct
    peer when no proxies are trusted) is used.
    """
    # Whitelist allows the spoofed IP but NOT the test client's real peer.
    app = make_app(
        whitelist=["10.0.0.0/8"],
        trust_proxy=True,
        trusted_proxies=[],  # explicitly empty
    )
    client = make_client(app)
    # Spoofed XFF claims 10.0.0.1 \u2014 but the real peer is _PEER_IP
    # (203.0.113.10) which is NOT in 10.0.0.0/8.
    response = client.get("/ping", headers={"X-Forwarded-For": "10.0.0.1"})
    assert response.status_code == 403


def test_rightmost_untrusted_xff_used_when_proxy_is_trusted():
    """When the direct peer is in trusted_proxies, the rightmost-untrusted
    XFF entry is used \u2014 spoofed leftmost entries are ignored.
    """
    # Two-hop chain: client(spoofed_leftmost) -> real_client -> trusted_proxy -> us
    # XFF chain header: "spoofed, real_client" + direct_peer trusted_proxy
    app = make_app(
        whitelist=["198.51.100.50"],  # only the real client is whitelisted
        trust_proxy=True,
        trusted_proxies=[_PEER_IP],
    )
    client = make_client(app)
    response = client.get(
        "/ping",
        headers={"X-Forwarded-For": "10.0.0.1, 198.51.100.50"},
    )
    assert response.status_code == 200


def test_rightmost_untrusted_skips_multiple_trusted_hops():
    """Multiple trusted-proxy hops are skipped; first untrusted entry wins."""
    app = make_app(
        whitelist=["198.51.100.50"],
        trust_proxy=True,
        trusted_proxies=[_PEER_IP, "192.0.2.0/24"],
    )
    client = make_client(app)
    # XFF: attacker, real-client, trusted-edge -> direct peer is also trusted
    response = client.get(
        "/ping",
        headers={"X-Forwarded-For": "10.0.0.1, 198.51.100.50, 192.0.2.7"},
    )
    assert response.status_code == 200


def test_invalid_trusted_proxies_entry_logged_and_ignored():
    """A non-IP string in trusted_proxies is dropped without crashing."""
    app = make_app(
        whitelist=["198.51.100.50"],
        trust_proxy=True,
        trusted_proxies=["garbage", _PEER_IP],
    )
    client = make_client(app)
    response = client.get(
        "/ping",
        headers={"X-Forwarded-For": "198.51.100.50"},
    )
    assert response.status_code == 200
