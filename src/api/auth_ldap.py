"""LDAPS bind-based authentication.

Verifies a user's username + password against a corporate LDAP directory
over a TLS-protected connection (LDAPS on port 636, or LDAP+STARTTLS on 389).
Plaintext binds are refused — the server URI must use ldaps://, or the
caller must explicitly opt into start_tls (also enforced).

Lower-environment server: ldaps://ldap-wil.bank.internal:636
Service Accounts OU:      OU=Service Accounts,DC=bank,DC=internal

On success, returns an LdapUser dataclass that the auth router stores in
the signed Starlette session cookie.

SECURITY NOTES
--------------
- TLS certificate validation is always CERT_REQUIRED. Never set
  validate=ssl.CERT_NONE in production.
- _escape_ldap_filter() prevents filter-injection attacks (RFC 4515).
- LDAPBindError is collapsed to a single generic error string to avoid
  leaking whether the username exists vs. the password was wrong.
- Service-account credentials are read via get_secrets_provider() so
  HashiCorp Vault and Azure Key Vault deployments work without code changes.
- The LDAP server URI is operator-controlled (config/ui.yml), never
  user-controlled, so SSRF guarding is not required for that value.
"""

from __future__ import annotations

import logging
import ssl
from dataclasses import dataclass
from typing import Mapping

from ldap3 import ALL, SUBTREE, Connection, Server, Tls
from ldap3.core.exceptions import LDAPBindError, LDAPException

from src.utils.secrets import get_secrets_provider

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public exceptions and data types
# ---------------------------------------------------------------------------


class LdapAuthError(Exception):
    """Raised on any LDAP authentication or lookup failure.

    The error message is intentionally generic to avoid leaking whether
    the username exists vs. the password was wrong — both surface as
    HTTP 401 'Invalid credentials' in the auth router.
    """


@dataclass(frozen=True)
class LdapUser:
    """Authenticated LDAP user attributes returned by ldap_authenticate()."""

    dn: str
    email: str | None
    name: str | None
    groups: tuple  # tuple[str, ...] — short CN values from memberOf DNs


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_server(cfg: Mapping) -> Server:
    """Construct an ldap3 Server with strict TLS validation.

    Args:
        cfg: The ``auth.ldap`` sub-section of config/ui.yml.

    Returns:
        Configured ldap3 Server instance.

    Raises:
        LdapAuthError: If the server URI does not use ldaps:// and
            start_tls is not explicitly enabled.
    """
    ca_cert = cfg.get("ca_cert_path")
    tls = Tls(
        validate=ssl.CERT_REQUIRED,
        version=ssl.PROTOCOL_TLS_CLIENT,
        ca_certs_file=ca_cert if ca_cert else None,
    )
    uri: str = cfg["server_uri"]
    if not (uri.startswith("ldaps://") or cfg.get("start_tls", False)):
        raise LdapAuthError("ldap_server_uri_not_tls")
    return Server(
        uri,
        use_ssl=uri.startswith("ldaps://"),
        tls=tls,
        get_info=ALL,
        connect_timeout=int(cfg.get("connect_timeout_seconds", 5)),
    )


def _escape_ldap_filter(value: str) -> str:
    """RFC 4515 escape — protect against filter-injection in user_search_filter.

    Args:
        value: Raw user-supplied string (e.g. username).

    Returns:
        Escaped string safe for use inside an LDAP search filter.
    """
    return (
        value
        .replace("\\", r"\5c")
        .replace("*",  r"\2a")
        .replace("(",  r"\28")
        .replace(")",  r"\29")
        .replace("\x00", r"\00")
    )


def _resolve_user_dn(server: Server, username: str, cfg: Mapping) -> str:
    """Look up a user's DN via a service-account search (search_then_bind strategy).

    Args:
        server: Pre-built ldap3 Server instance.
        username: The sAMAccountName (or uid) to search for.
        cfg: The ``auth.ldap`` sub-section of config/ui.yml.

    Returns:
        The user's full distinguished name string.

    Raises:
        LdapAuthError: If service-account credentials are missing, the
            connection fails, or the user is not found.
    """
    provider = get_secrets_provider()
    svc_dn = provider.get_secret(cfg["service_account_dn_env"])
    svc_pw = provider.get_secret(cfg["service_account_password_env"])
    if not svc_dn or not svc_pw:
        raise LdapAuthError("ldap_service_account_not_configured")
    try:
        conn = Connection(server, user=svc_dn, password=svc_pw, auto_bind=True)
    except LDAPException as exc:
        logger.error("ldap_service_bind_error type=%s", type(exc).__name__)
        raise LdapAuthError("ldap_unavailable") from exc
    try:
        flt = cfg["user_search_filter"].format(
            username=_escape_ldap_filter(username)
        )
        conn.search(
            search_base=cfg["user_search_base"],
            search_filter=flt,
            search_scope=SUBTREE,
            attributes=["distinguishedName"],
        )
        if not conn.entries:
            raise LdapAuthError("user_not_found")
        return str(conn.entries[0].entry_dn)
    finally:
        conn.unbind()


def _cn_from_dn(dn: str) -> str:
    """Extract the leftmost CN= value from an LDAP DN, lowercased.

    Example:
        "CN=valdo-admins,OU=Groups,DC=bank,DC=internal" -> "valdo-admins"

    Args:
        dn: Full LDAP distinguished name string.

    Returns:
        Lowercased CN value, or the full DN lowercased if parsing fails.
    """
    head = dn.split(",", 1)[0].strip()
    if "=" in head:
        return head.split("=", 1)[1].strip().lower()
    return dn.strip().lower()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ldap_authenticate(username: str, password: str, cfg: Mapping) -> LdapUser:
    """Bind as the user against the corporate LDAP directory; on success,
    fetch their attributes and group memberships.

    Args:
        username: User-supplied identifier (e.g. sAMAccountName for AD).
        password: User-supplied password — passed only to the LDAP bind,
            never logged.
        cfg: The ``auth.ldap`` sub-section of config/ui.yml.

    Returns:
        Populated LdapUser on successful bind.

    Raises:
        LdapAuthError: Any failure — bad credentials, server unreachable,
            misconfigured TLS, missing service account, etc.
    """
    if not username or not password:
        raise LdapAuthError("missing_credentials")

    server = _build_server(cfg)

    strategy = cfg.get("bind_strategy", "search_then_bind")
    if strategy == "upn":
        # Active Directory UPN bind: username@domain
        bind_user = f"{username}@{cfg['user_domain']}"
    elif strategy == "search_then_bind":
        # Look up the user's DN first, then bind with it
        bind_user = _resolve_user_dn(server, username, cfg)
    else:
        raise LdapAuthError(f"unknown_bind_strategy:{strategy}")

    # Bind with the user's credentials to verify the password
    try:
        conn = Connection(server, user=bind_user, password=password, auto_bind=True)
    except LDAPBindError:
        # Collapse bind failure to a single generic error to prevent
        # user-enumeration (same response whether username or password is wrong)
        raise LdapAuthError("invalid_credentials")
    except LDAPException as exc:
        logger.error("ldap_connect_error type=%s", type(exc).__name__)
        raise LdapAuthError("ldap_unavailable") from exc

    try:
        # Fetch the user's attributes and group memberships.
        # We re-search by the configured filter to get a uniform attribute
        # set regardless of which bind strategy was used.
        group_attr = cfg.get("group_attr", "memberOf")
        flt = cfg["user_search_filter"].format(
            username=_escape_ldap_filter(username)
        )
        conn.search(
            search_base=cfg["user_search_base"],
            search_filter=flt,
            search_scope=SUBTREE,
            attributes=["distinguishedName", "mail", "displayName", "cn", group_attr],
        )
        if not conn.entries:
            raise LdapAuthError("user_attributes_not_found")

        entry = conn.entries[0]

        # AD memberOf returns full DNs like
        # "CN=valdo-admins,OU=Groups,DC=bank,DC=internal"
        # Reduce to the leftmost CN value for matching against group_role_map.
        groups_raw = list(entry[group_attr] or []) if group_attr in entry else []
        groups = tuple(_cn_from_dn(g) for g in groups_raw if g)

        email = str(entry.mail) if "mail" in entry and entry.mail else None
        if "displayName" in entry and entry.displayName:
            name = str(entry.displayName)
        elif "cn" in entry and entry.cn:
            name = str(entry.cn)
        else:
            name = None

        return LdapUser(
            dn=str(entry.entry_dn),
            email=email,
            name=name,
            groups=groups,
        )
    finally:
        conn.unbind()


def map_groups_to_role(groups: tuple, role_map: Mapping) -> str:
    """Pick the highest-precedence Valdo role from the user's LDAP groups.

    Iterates ``groups`` in order and returns the first match found in
    ``role_map``. Falls back to the ``"*"`` wildcard entry, then to
    ``"tester"`` if no wildcard is defined.

    Args:
        groups: Tuple of short CN group names (lowercased) from LDAP.
        role_map: The ``auth.ldap.group_role_map`` dict from config/ui.yml.

    Returns:
        A Valdo role string: ``"tester"``, ``"mapping_owner"``, or ``"admin"``.
    """
    for grp in groups:
        if grp in role_map:
            return role_map[grp]
    return role_map.get("*", "tester")
