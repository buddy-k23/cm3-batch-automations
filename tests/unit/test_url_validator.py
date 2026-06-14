"""Tests for ``src.api.security.url_validator.assert_safe_callback_url``.

Covers issue #9 fix 9-D: SSRF defence for the webhook callback URL.
"""

from __future__ import annotations

import socket

import pytest

from src.api.security.url_validator import assert_safe_callback_url


def _stub_getaddrinfo(addresses):
    """Build a fake ``socket.getaddrinfo`` result list.

    Each entry mimics CPython's tuple shape:
    ``(family, type, proto, canonname, sockaddr)`` where ``sockaddr`` is
    ``(ip_string, port)`` for IPv4 or ``(ip_string, port, flowinfo, scopeid)``
    for IPv6.
    """
    out = []
    for addr in addresses:
        if ":" in addr:
            sockaddr = (addr, 0, 0, 0)
            family = socket.AF_INET6
        else:
            sockaddr = (addr, 0)
            family = socket.AF_INET
        out.append((family, socket.SOCK_STREAM, 0, "", sockaddr))
    return out


class TestSchemeValidation:
    @pytest.mark.parametrize("url", [
        "ftp://example.com/x",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "gopher://internal/",
        "data:text/plain,hello",
    ])
    def test_disallowed_schemes_rejected(self, url):
        with pytest.raises(ValueError, match="scheme"):
            assert_safe_callback_url(url)

    def test_http_accepted(self, monkeypatch):
        monkeypatch.setattr(
            "src.api.security.url_validator.socket.getaddrinfo",
            lambda host, port: _stub_getaddrinfo(["8.8.8.8"]),
        )
        assert_safe_callback_url("http://example.com/hook")  # no exception

    def test_https_accepted(self, monkeypatch):
        monkeypatch.setattr(
            "src.api.security.url_validator.socket.getaddrinfo",
            lambda host, port: _stub_getaddrinfo(["8.8.8.8"]),
        )
        assert_safe_callback_url("https://example.com/hook")


class TestMissingHost:
    def test_empty_url_rejected(self):
        with pytest.raises(ValueError, match="empty"):
            assert_safe_callback_url("")

    def test_missing_hostname_rejected(self):
        with pytest.raises(ValueError, match="hostname"):
            assert_safe_callback_url("http:///path")


class TestAllowlist:
    def test_host_in_allowlist_accepted(self, monkeypatch):
        monkeypatch.setattr(
            "src.api.security.url_validator.socket.getaddrinfo",
            lambda host, port: _stub_getaddrinfo(["8.8.8.8"]),
        )
        assert_safe_callback_url(
            "https://hooks.example.com/x",
            allowlist=["hooks.example.com"],
        )

    def test_host_not_in_allowlist_rejected(self):
        with pytest.raises(ValueError, match="allowlist"):
            assert_safe_callback_url(
                "https://attacker.example.org/x",
                allowlist=["hooks.example.com"],
            )

    def test_empty_allowlist_falls_back_to_dns_check(self, monkeypatch):
        # Empty/None allowlist must NOT block public hosts \u2014 it is treated
        # as "no allowlist configured".
        monkeypatch.setattr(
            "src.api.security.url_validator.socket.getaddrinfo",
            lambda host, port: _stub_getaddrinfo(["8.8.8.8"]),
        )
        assert_safe_callback_url("https://hooks.example.com/x", allowlist=None)
        assert_safe_callback_url("https://hooks.example.com/x", allowlist=[])


class TestPrivateAddressBlocking:
    @pytest.mark.parametrize("private_ip", [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.1",
        "192.168.0.5",
        "169.254.169.254",  # cloud metadata service
        "::1",
        "fe80::1",
    ])
    def test_dns_resolved_private_addresses_rejected(self, monkeypatch, private_ip):
        monkeypatch.setattr(
            "src.api.security.url_validator.socket.getaddrinfo",
            lambda host, port: _stub_getaddrinfo([private_ip]),
        )
        with pytest.raises(ValueError, match="blocked"):
            assert_safe_callback_url("https://innocent.example.com/x")

    def test_literal_private_ip_rejected(self):
        with pytest.raises(ValueError, match="blocked"):
            assert_safe_callback_url("http://169.254.169.254/latest/meta-data/")

    def test_literal_public_ip_accepted(self):
        # 8.8.8.8 is public \u2014 no DNS lookup is needed for literal IPs.
        assert_safe_callback_url("https://8.8.8.8/hook")

    def test_dns_failure_rejected(self, monkeypatch):
        def boom(host, port):
            raise socket.gaierror("no such host")
        monkeypatch.setattr(
            "src.api.security.url_validator.socket.getaddrinfo", boom
        )
        with pytest.raises(ValueError, match="DNS lookup failed"):
            assert_safe_callback_url("https://nonexistent.invalid/x")

    def test_mixed_addresses_any_private_rejected(self, monkeypatch):
        """If a host resolves to BOTH a public and a private IP, reject."""
        monkeypatch.setattr(
            "src.api.security.url_validator.socket.getaddrinfo",
            lambda host, port: _stub_getaddrinfo(["8.8.8.8", "10.0.0.5"]),
        )
        with pytest.raises(ValueError, match="blocked"):
            assert_safe_callback_url("https://dns-rebind.example.com/x")
