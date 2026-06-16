"""``valdo mcp-revoke`` — revoke an MCP token by its jti (S9-4, #389).

SRE incident-response lever: when a token leaks, an administrator runs
this command to add the token's ``jti`` to the server-side revocation
blocklist (``MCP_REVOKED_TOKENS``). Subsequent MCP calls presenting that
token fail within the cache TTL (60s) — without rotating the signing key
and thus without disrupting every other agent.

Usage::

    valdo mcp-revoke <token_id> --reason "laptop stolen — INC-12345"
    valdo mcp-revoke <token_id> --server https://valdo.bank.internal --reason "leak"

The command POSTs to ``POST /api/v2/mcp/revoke`` on the configured Valdo
server. That endpoint is **admin-only** (LDAPS group ``valdo-admins``), so
the operator is prompted for their own LDAP credentials; a non-admin is
rejected with HTTP 403.

The handler is split out of :mod:`src.main` (per the repo's "thin
registration layer" convention) so the CLI surface stays small and the
network call is unit-testable. Mirrors :mod:`src.commands.mcp_login`.

SECURITY POSTURE
----------------
* Password is read via ``click.prompt(hide_input=True)`` — never echoed
  to the terminal or shell history.
* TLS verification is on by default; ``--insecure`` disables it only when
  explicitly requested (self-signed dev/INT certs) and prints a banner.
* The ``token_id`` (jti) is opaque and safe to log; the password is never
  logged.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Optional

import click

logger = logging.getLogger(__name__)


@click.command("mcp-revoke")
@click.argument("token_id")
@click.option(
    "--reason",
    required=True,
    help="Why the token is being revoked (incident ref, 'laptop stolen', etc.).",
)
@click.option(
    "--server",
    default="http://localhost:8000",
    show_default=True,
    help="Valdo API base URL (e.g. https://valdo.bank.internal).",
)
@click.option(
    "--username",
    default=None,
    help="Admin LDAP username. Prompts if omitted.",
)
@click.option(
    "--insecure/--secure",
    default=False,
    help="Disable TLS certificate verification (dev only).",
)
def mcp_revoke(
    token_id: str,
    reason: str,
    server: str,
    username: Optional[str],
    insecure: bool,
) -> None:
    """Revoke an MCP token by its jti (token_id) via the admin endpoint.

    Prompts for admin LDAP credentials, POSTs the revocation to the Valdo
    server, and reports the outcome. Exits non-zero on any failure so the
    command is safe to use in an incident-response runbook / script.
    """
    try:
        import requests
    except ImportError as exc:  # pragma: no cover — requests is a base dep
        click.echo(click.style(f"Missing dependency: {exc}", fg="red"), err=True)
        sys.exit(1)

    if insecure:
        click.echo(
            click.style(
                "WARNING: TLS verification disabled (--insecure). "
                "Never use this against a production Valdo server.",
                fg="yellow",
            ),
            err=True,
        )

    if not username:
        username = click.prompt("Admin LDAP username", type=str)
    password = click.prompt("Admin LDAP password", hide_input=True, type=str)

    url = server.rstrip("/") + "/api/v2/mcp/revoke"
    payload = {
        "username": username,
        "password": password,
        "token_id": token_id,
        "reason": reason,
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=15,
            verify=not insecure,
        )
    except requests.RequestException as exc:
        click.echo(
            click.style(f"Could not reach {url}: {exc}", fg="red"),
            err=True,
        )
        sys.exit(2)

    if response.status_code == 401:
        click.echo(
            click.style("Invalid credentials. Token NOT revoked.", fg="red"),
            err=True,
        )
        sys.exit(3)
    if response.status_code == 403:
        click.echo(
            click.style(
                "Forbidden — your account is not in 'valdo-admins'. "
                "Token NOT revoked.",
                fg="red",
            ),
            err=True,
        )
        sys.exit(4)
    if response.status_code == 503:
        click.echo(
            click.style(
                "Server reported LDAP or the revocation blocklist is "
                "unavailable. Token NOT revoked. Contact your Valdo "
                "administrator.",
                fg="red",
            ),
            err=True,
        )
        sys.exit(5)
    if response.status_code >= 400:
        click.echo(
            click.style(
                f"Revoke failed with HTTP {response.status_code}: "
                f"{response.text[:500]}",
                fg="red",
            ),
            err=True,
        )
        sys.exit(6)

    try:
        body = response.json()
    except json.JSONDecodeError as exc:
        click.echo(
            click.style(f"Server returned non-JSON response: {exc}", fg="red"),
            err=True,
        )
        sys.exit(7)

    revoked_by = body.get("revoked_by", "?")
    click.echo(click.style("MCP token revoked.", fg="green"))
    click.echo(f"  Token id (jti): {token_id}")
    click.echo(f"  Revoked by:     {revoked_by}")
    click.echo(f"  Reason:         {reason}")
    click.echo(
        "The revoking node enforces this immediately; other nodes "
        "converge within the 60s cache TTL."
    )
