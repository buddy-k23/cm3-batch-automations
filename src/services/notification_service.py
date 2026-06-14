"""Notification service for suite test results (issue #35).

Provides email (SMTP) and webhook (Teams/Slack) notification channels.
All external calls handle errors gracefully — a notification failure never
crashes the suite runner.

Environment variables for email:

* ``SMTP_HOST`` — SMTP server hostname (e.g. ``smtp.example.com``).
* ``SMTP_PORT`` — SMTP server port (default ``587``).
* ``SMTP_FROM`` — Sender email address.
* ``SMTP_USER`` — SMTP authentication username (optional).
* ``SMTP_PASSWORD`` — SMTP authentication password (optional).
"""
from __future__ import annotations

import json
import logging
import os
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

import requests

from src.pipeline.suite_config import NotificationTarget, NotificationsConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


def send_email(to: List[str], subject: str, body: str) -> bool:
    """Send a plain-text email via SMTP using environment variable config.

    Args:
        to: List of recipient email addresses.
        subject: Email subject line.
        body: Plain-text email body.

    Returns:
        True if the email was sent successfully, False otherwise.
    """
    host = os.environ.get("SMTP_HOST", "")
    port = int(os.environ.get("SMTP_PORT", "587"))
    from_addr = os.environ.get("SMTP_FROM", "")
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")

    if not host or not from_addr:
        logger.warning("SMTP_HOST or SMTP_FROM not configured — skipping email notification")
        return False

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to)

    try:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.ehlo()
            if port != 25:
                server.starttls()
                server.ehlo()
            if user and password:
                server.login(user, password)
            server.sendmail(from_addr, to, msg.as_string())
        logger.info("Email notification sent to %s", to)
        return True
    except Exception:  # noqa: BLE001
        logger.warning("Failed to send email notification to %s", to, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


def send_teams_webhook(url: str, payload: Dict[str, Any]) -> bool:
    """POST a message card to a Microsoft Teams incoming webhook.

    Args:
        url: The Teams incoming webhook URL.
        payload: Dict conforming to the Teams MessageCard schema.

    Returns:
        True if the webhook accepted the payload, False otherwise.
    """
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        logger.info("Teams webhook notification sent")
        return True
    except Exception:  # noqa: BLE001
        logger.warning("Failed to send Teams webhook notification", exc_info=True)
        return False


def send_slack_webhook(url: str, payload: Dict[str, Any]) -> bool:
    """POST a message payload to a Slack incoming webhook.

    Args:
        url: The Slack incoming webhook URL.
        payload: Dict with at least a ``text`` key (Slack message format).

    Returns:
        True if the webhook accepted the payload, False otherwise.
    """
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        logger.info("Slack webhook notification sent")
        return True
    except Exception:  # noqa: BLE001
        logger.warning("Failed to send Slack webhook notification", exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------


def build_plain_text_body(suite_name: str, result: Dict[str, Any]) -> str:
    """Build a plain-text summary of suite results for email notifications.

    Args:
        suite_name: Human-readable suite name.
        result: Suite result dict as returned by
            :func:`~src.services.scheduler_service.run_suite_by_name`.

    Returns:
        Formatted multi-line string summarising the run.
    """
    now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    status = result.get("status", "unknown").upper()
    run_id = result.get("run_id", "n/a")
    step_results = result.get("step_results", [])

    lines = [
        f"Suite: {suite_name}",
        f"Date:  {now_str}",
        f"Run ID: {run_id}",
        f"Status: {status}",
        "",
        "Step Results:",
        "-" * 50,
    ]

    for step in step_results:
        name = step.get("name", "?")
        step_status = step.get("status", "unknown").upper()
        errors = step.get("error_count", 0)
        rows = step.get("total_rows", 0)
        detail = step.get("detail", "")
        line = f"  {step_status:<10} {name} — {rows} rows, {errors} error(s)"
        if detail:
            line += f" [{detail}]"
        lines.append(line)

    lines.append("-" * 50)
    return "\n".join(lines)


def build_teams_payload(suite_name: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """Build a Teams MessageCard payload from suite results.

    Args:
        suite_name: Human-readable suite name.
        result: Suite result dict.

    Returns:
        Dict conforming to the Teams MessageCard schema.
    """
    status = result.get("status", "unknown").upper()
    color = "00cc00" if status == "PASSED" else "cc0000"
    body = build_plain_text_body(suite_name, result)

    return {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": color,
        "summary": f"Suite '{suite_name}' — {status}",
        "sections": [
            {
                "activityTitle": f"Valdo Suite Result: {suite_name}",
                "activitySubtitle": status,
                "text": f"```\n{body}\n```",
                "markdown": True,
            }
        ],
    }


def build_slack_payload(suite_name: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """Build a Slack incoming-webhook payload from suite results.

    Args:
        suite_name: Human-readable suite name.
        result: Suite result dict.

    Returns:
        Dict with ``text`` key suitable for Slack's incoming webhook API.
    """
    status = result.get("status", "unknown").upper()
    emoji = ":white_check_mark:" if status == "PASSED" else ":x:"
    body = build_plain_text_body(suite_name, result)

    return {
        "text": f"{emoji} *Suite '{suite_name}' — {status}*\n```\n{body}\n```",
    }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def notify_suite_result(
    suite_name: str,
    result: Dict[str, Any],
    notifications_config: Optional[NotificationsConfig],
) -> None:
    """Send notifications for a suite result based on the suite's config.

    Determines whether the result warrants notification (``on_success`` or
    ``on_failure``) and dispatches to the appropriate channels.  All errors
    are caught and logged — this function never raises.

    Args:
        suite_name: Suite name used in notification messages.
        result: Suite result dict (must contain a ``status`` key).
        notifications_config: Optional notification configuration from the
            suite definition.  When ``None``, no notifications are sent.
    """
    if notifications_config is None:
        return

    status = result.get("status", "unknown")
    is_success = status == "passed"

    targets: List[NotificationTarget] = []
    if is_success and notifications_config.on_success:
        targets = notifications_config.on_success
    elif not is_success and notifications_config.on_failure:
        targets = notifications_config.on_failure

    for target in targets:
        try:
            _dispatch_target(suite_name, result, target)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Notification dispatch failed for target type='%s'",
                target.type,
                exc_info=True,
            )


def _dispatch_target(
    suite_name: str,
    result: Dict[str, Any],
    target: NotificationTarget,
) -> None:
    """Route a single notification target to the correct sender.

    Args:
        suite_name: Suite name for the notification content.
        result: Suite result dict.
        target: The notification target describing type and destination.
    """
    if target.type == "email":
        recipients = target.to if target.to else []
        if not recipients:
            logger.warning("Email notification target has no recipients — skipping")
            return
        subject = f"Valdo Suite '{suite_name}' — {result.get('status', 'unknown').upper()}"
        body = build_plain_text_body(suite_name, result)
        send_email(recipients, subject, body)

    elif target.type == "teams":
        if not target.url:
            logger.warning("Teams notification target has no URL — skipping")
            return
        payload = build_teams_payload(suite_name, result)
        send_teams_webhook(target.url, payload)

    elif target.type == "slack":
        if not target.url:
            logger.warning("Slack notification target has no URL — skipping")
            return
        payload = build_slack_payload(suite_name, result)
        send_slack_webhook(target.url, payload)

    else:
        logger.warning("Unknown notification target type: '%s'", target.type)
