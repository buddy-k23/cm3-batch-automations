"""IP whitelist middleware for enterprise VDI environments."""
import ipaddress
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


class IPWhitelistMiddleware(BaseHTTPMiddleware):
    """Middleware that restricts access to configured IP ranges.

    When ip_whitelist is empty or not configured, all IPs are allowed
    (backwards compatible default-open behaviour).

    Args:
        app: The ASGI application.
        whitelist: List of IP addresses and/or CIDR ranges as strings.
            E.g. ["10.0.0.0/8", "192.168.1.50"].
        trust_proxy: If True, use X-Forwarded-For header as client IP
            (first entry). Default False.
    """

    def __init__(
        self,
        app,
        whitelist: list = None,
        trust_proxy: bool = False,
        trusted_proxies: list = None,
    ):
        """Initialise the middleware, parsing whitelist entries into network objects.

        Args:
            app: The ASGI application to wrap.
            whitelist: List of IP addresses and/or CIDR ranges as strings.
                Invalid entries are logged as warnings and skipped.
            trust_proxy: If True, walk the X-Forwarded-For chain rightward,
                skipping any IPs in ``trusted_proxies``, to derive the
                untrusted client IP.
            trusted_proxies: List of IP addresses or CIDR ranges that are
                allowed to appear as proxy hops in X-Forwarded-For. The
                middleware skips these entries when walking the chain.
                Required when ``trust_proxy`` is True \u2014 without it the
                rightmost-untrusted strategy collapses to the direct peer.
        """
        super().__init__(app)
        self.trust_proxy = trust_proxy
        self._networks = []
        for entry in (whitelist or []):
            try:
                self._networks.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                logger.warning("Invalid IP whitelist entry ignored: %s", entry)
        self._trusted_proxies = []
        for entry in (trusted_proxies or []):
            try:
                self._trusted_proxies.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                logger.warning("Invalid trusted_proxies entry ignored: %s", entry)

    def _is_trusted_proxy(self, ip_str: str) -> bool:
        """Return True when ``ip_str`` is in the configured trusted-proxies list.

        Args:
            ip_str: An IP address string parsed from an X-Forwarded-For
                entry or the direct peer.

        Returns:
            True if the address parses cleanly and falls within any
            configured trusted-proxy network, otherwise False.
        """
        if not self._trusted_proxies:
            return False
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        return any(addr in net for net in self._trusted_proxies)

    def _is_allowed(self, client_ip: str) -> bool:
        """Return True if client_ip is in the whitelist (or whitelist is empty).

        Args:
            client_ip: The IP address string to check.

        Returns:
            True if the IP is permitted or no whitelist is configured;
            False if the IP is blocked.
        """
        if not self._networks:
            return True
        try:
            addr = ipaddress.ip_address(client_ip)
        except ValueError:
            return False
        return any(addr in net for net in self._networks)

    async def dispatch(self, request: Request, call_next):
        """Check client IP against the whitelist before forwarding the request.

        Args:
            request: The incoming HTTP request.
            call_next: The next middleware/route handler in the chain.

        Returns:
            A 403 JSONResponse if the client IP is not permitted, otherwise
            the response from the downstream handler.
        """
        direct_peer = request.client.host if request.client else ""
        if self.trust_proxy:
            # SECURITY: The leftmost X-Forwarded-For entry is attacker-
            # controllable. Walk the chain right-to-left, skipping any
            # configured trusted proxies, and use the first untrusted IP
            # we encounter. The chain is XFF-entries followed by the
            # direct peer (rightmost). When no proxies are trusted, this
            # collapses to the direct peer \u2014 still safer than the
            # leftmost-XFF behaviour.
            forwarded = request.headers.get("X-Forwarded-For", "")
            xff_chain = [x.strip() for x in forwarded.split(",") if x.strip()]
            chain = xff_chain + ([direct_peer] if direct_peer else [])
            client_ip = direct_peer
            for candidate in reversed(chain):
                if self._is_trusted_proxy(candidate):
                    continue
                client_ip = candidate
                break
        else:
            client_ip = direct_peer

        if not self._is_allowed(client_ip):
            logger.warning("Blocked request from %s — not in IP whitelist", client_ip)
            return JSONResponse(
                status_code=403,
                content={"error": "Forbidden", "detail": "Your IP address is not permitted."},
            )
        return await call_next(request)
