"""``valdo audit-verify <path>`` — verify audit-log tamper-evidence (S13.5-1, #408).

Thin CLI wrapper around :func:`src.utils.audit_logger.verify_audit_log`.  The
verification logic lives in the utils layer (per the repo's "thin registration
layer" convention); this module only parses arguments, reports the outcome, and
sets the process exit code.

Usage::

    valdo audit-verify logs/audit.jsonl
    valdo audit-verify logs/audit.jsonl --key "$VALDO_AUDIT_HMAC_KEY"

The command exits non-zero when the chain is broken, so it is safe to wire
into a cron / SRE integrity-monitoring job.  When ``--key`` is omitted the
``VALDO_AUDIT_HMAC_KEY`` environment value (via the secrets provider) is used
for HMAC records; unkeyed (SHA-256) records are verified without a key.
"""

from __future__ import annotations

import logging
import sys
from typing import Optional

import click

from src.utils.audit_logger import verify_audit_log

logger = logging.getLogger(__name__)


@click.command("audit-verify")
@click.argument("path")
@click.option(
    "--key",
    default=None,
    help=(
        "HMAC key for verifying hmac-sha256 records. Defaults to "
        "VALDO_AUDIT_HMAC_KEY via the secrets provider."
    ),
)
def audit_verify(path: str, key: Optional[str]) -> None:
    """Verify the integrity chain of a JSONL audit log at *path*.

    Walks the log checking sequence continuity, prev_hash linkage, and the
    per-record MAC.  Prints a human-readable result and exits non-zero on the
    first broken link.

    Args:
        path: Filesystem path to the JSONL audit log.
        key: Optional HMAC key for hmac-sha256 records.
    """
    result = verify_audit_log(path, key=key)

    if result.ok:
        click.echo(
            click.style(
                f"OK — audit chain intact ({result.records_checked} records).",
                fg="green",
            )
        )
        return

    click.echo(
        click.style(
            f"FAILED — audit chain broken after {result.records_checked} "
            f"record(s).",
            fg="red",
        ),
        err=True,
    )
    if result.bad_seq is not None:
        click.echo(click.style(f"  First bad seq: {result.bad_seq}", fg="red"), err=True)
    if result.error:
        click.echo(click.style(f"  Detail: {result.error}", fg="red"), err=True)
    sys.exit(1)
