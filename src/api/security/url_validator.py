"""URL validation helpers used to defend against SSRF attacks.

The :func:`assert_safe_callback_url` helper is used to validate user-supplied
URLs before the application makes outbound HTTP requests (e.g. webhook
callbacks). It enforces:

* Scheme allow-list (``http``/``https`` only by default).
* Optional host allow-list \u2014 when configured, only those exact hostnames
  are permitted.
* DNS resolution of the hostname, rejecting any address that resolves into
  a private, loopback, link-local, or other reserved range. This blocks
  cloud-metadata exfiltration (``169.254.169.254``), internal-network
  scanning (``10.0.0.0/8``), and localhost pivots.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Iterable, Optional
from urllib.parse import urlparse

# Networks that must not be reachable via callbacks. These cover loopback,
# RFC1918 private ranges, link-local (incl. AWS/GCP/Azure metadata service at
# 169.254.169.254), unique-local IPv6, and IPv6 link-local.
_BLOCKED_NETWORKS = [
    ipaddress.ip_network(c)
    for c in (
        "127.0.0.0/8",       # IPv4 loopback
        "10.0.0.0/8",        # RFC1918 private
        "172.16.0.0/12",     # RFC1918 private
        "192.168.0.0/16",    # RFC1918 private
        "169.254.0.0/16",    # link-local (incl. cloud metadata)
        "0.0.0.0/8",         # "this network"
        "100.64.0.0/10",     # carrier-grade NAT
        "::1/128",           # IPv6 loopback
        "fc00::/7",          # IPv6 unique local
        "fe80::/10",         # IPv6 link-local
    )
]

_ALLOWED_SCHEMES = frozenset({"http", "https"})


def _is_blocked_address(addr: ipaddress._BaseAddress) -> bool:
    """Return True when *addr* falls inside any blocked network or is otherwise unsafe.

    Args:
        addr: A parsed :class:`ipaddress.IPv4Address` or
            :class:`ipaddress.IPv6Address`.

    Returns:
        True if the address is private, loopback, link-local, multicast,
        unspecified, or reserved \u2014 in which case it must not be reached
        via a user-supplied callback URL.
    """
    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_unspecified
        or addr.is_reserved
    ):
        return True
    return any(addr in net for net in _BLOCKED_NETWORKS)


def assert_safe_callback_url(
    url: str,
    allowlist: Optional[Iterable[str]] = None,
) -> None:
    """Validate that *url* is safe to use as an outbound HTTP callback target.

    Args:
        url: The candidate URL to validate.
        allowlist: Optional iterable of permitted hostnames. When supplied
            and non-empty, the parsed hostname must match one of these
            entries exactly (case-insensitive).

    Raises:
        ValueError: If the URL is malformed, uses a disallowed scheme,
            lacks a hostname, fails DNS resolution, resolves to a blocked
            address range, or is not present in *allowlist* when one is
            configured.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Callback URL is empty")

    parsed = urlparse(url.strip())
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError(
            f"Disallowed scheme {scheme!r}; only http/https are permitted"
        )

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValueError("Callback URL is missing a hostname")

    if allowlist:
        normalised_allow = {h.lower() for h in allowlist if h}
        if normalised_allow and hostname not in normalised_allow:
            raise ValueError(
                f"Host {hostname!r} is not in the configured callback allowlist"
            )

    # If the host is already a literal IP, validate it directly without DNS.
    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        if _is_blocked_address(literal_ip):
            raise ValueError(
                f"Callback host resolves to a blocked address range: {literal_ip}"
            )
        return

    # Resolve and validate every address the hostname maps to. We must reject
    # if *any* of them is unsafe \u2014 a hostile DNS server could otherwise
    # return a public IP at validation time and a private IP at fetch time.
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise ValueError(f"DNS lookup failed for {hostname!r}: {exc}") from exc

    seen_any = False
    for _family, _type, _proto, _canon, sockaddr in infos:
        ip_str = sockaddr[0]
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            # Skip entries we cannot interpret (e.g. unusual scopes).
            continue
        seen_any = True
        if _is_blocked_address(addr):
            raise ValueError(
                f"Callback host {hostname!r} resolves to a blocked address: {addr}"
            )

    if not seen_any:
        raise ValueError(f"Could not resolve any address for host {hostname!r}")
