"""``valdo mcp-login`` — mint a stdio MCP bearer token (EF-S7).

The CLI subcommand prompts the operator for their LDAP username +
password, POSTs to ``POST /api/v2/mcp/login`` on the configured Valdo
server, and writes the returned signed token to ``~/.valdo/mcp-token``
with strict 0o600 permissions. Subsequent stdio MCP sessions
(``valdo mcp-serve`` and any agent that launches a stdio MCP transport)
read that file and use it to authenticate.

Usage::

    valdo mcp-login
    valdo mcp-login --server https://valdo.bank.internal
    valdo mcp-login --ttl-hours 8

The handler is split out of :mod:`src.main` (per the repo's "thin
registration layer" convention) so the CLI surface stays under 1000
lines and the network call is unit-testable.

SECURITY POSTURE
----------------
* Password is read via ``click.prompt(hide_input=True)`` so the value
  never appears on the terminal or in shell history.
* TLS verification is enabled by default. ``--insecure`` disables it
  only when explicitly requested (e.g. self-signed dev certs); the flag
  prints a banner so the operator notices.
* The HTTP response is logged at INFO level WITHOUT the token field —
  only ``expires_at`` and ``role`` surface in stdout.
* The token file is written via :func:`src.mcp.auth.write_token_file`,
  which enforces 0o700 on the parent directory and 0o600 on the file.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional

import click

from src.mcp.auth import (
    DEFAULT_TOKEN_TTL_HOURS,
    MAX_TOKEN_TTL_HOURS,
    resolve_token_path,
    write_token_file,
)

logger = logging.getLogger(__name__)


@click.command("mcp-login")
@click.option(
    "--server",
    default="http://localhost:8000",
    show_default=True,
    help="Valdo API base URL (e.g. https://valdo.bank.internal).",
)
@click.option(
    "--ttl-hours",
    "ttl_hours",
    default=DEFAULT_TOKEN_TTL_HOURS,
    show_default=True,
    type=click.IntRange(min=1, max=MAX_TOKEN_TTL_HOURS),
    help=f"Token TTL in hours (max {MAX_TOKEN_TTL_HOURS}).",
)
@click.option(
    "--username",
    default=None,
    help="LDAP username. Prompts if omitted.",
)
@click.option(
    "--token-path",
    "token_path",
    default=None,
    type=click.Path(),
    help="Override the token output path (default: ~/.valdo/mcp-token).",
)
@click.option(
    "--insecure/--secure",
    default=False,
    help="Disable TLS certificate verification (dev only).",
)
def mcp_login(
    server: str,
    ttl_hours: int,
    username: Optional[str],
    token_path: Optional[str],
    insecure: bool,
) -> None:
    """Mint a stdio MCP bearer token via LDAP credentials.

    The token is written to ``~/.valdo/mcp-token`` (or ``--token-path``)
    with 0600 permissions so only the current user can read it.
    """
    # ``requests`` is the standard HTTP client across the rest of the
    # repo (see src/services/downloader_service.py etc.) so we use it
    # here for consistency and to keep urllib3 retry/verify semantics
    # uniform.
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
        username = click.prompt("LDAP username", type=str)
    password = click.prompt("LDAP password", hide_input=True, type=str)

    url = server.rstrip("/") + "/api/v2/mcp/login"
    payload = {
        "username": username,
        "password": password,
        "ttl_hours": ttl_hours,
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
            click.style("Invalid credentials. Token NOT minted.", fg="red"),
            err=True,
        )
        sys.exit(3)
    if response.status_code == 503:
        click.echo(
            click.style(
                "Server reported LDAP or token-signing is unavailable. "
                "Contact your Valdo administrator.",
                fg="red",
            ),
            err=True,
        )
        sys.exit(4)
    if response.status_code >= 400:
        # Surface enough detail that an operator can diagnose, but never
        # echo the password back.
        click.echo(
            click.style(
                f"Login failed with HTTP {response.status_code}: {response.text[:500]}",
                fg="red",
            ),
            err=True,
        )
        sys.exit(5)

    try:
        body = response.json()
    except json.JSONDecodeError as exc:
        click.echo(
            click.style(f"Server returned non-JSON response: {exc}", fg="red"),
            err=True,
        )
        sys.exit(6)

    token = body.get("token")
    if not isinstance(token, dict):
        click.echo(
            click.style("Server response missing 'token' field.", fg="red"),
            err=True,
        )
        sys.exit(7)

    target = Path(token_path).expanduser() if token_path else resolve_token_path()
    written = write_token_file(token, target=target)

    expires_at = body.get("expires_at")
    role = body.get("role", "?")
    principal_dn = body.get("principal_dn", "?")
    click.echo(click.style("MCP login successful.", fg="green"))
    click.echo(f"  Token written: {written}")
    click.echo(f"  Principal:     {principal_dn}")
    click.echo(f"  Role:          {role}")
    click.echo(f"  Expires at:    {expires_at} (epoch seconds)")
    click.echo("Use stdio MCP transport now (or pass the token via Authorization: Bearer).")
