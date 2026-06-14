"""Unit tests for the notification service (issue #35).

Covers:
- Email formatting and SMTP dispatch
- Teams/Slack webhook payload generation and dispatch
- Graceful error handling (failures logged, not raised)
- notify_suite_result routing to correct channels
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch, ANY

import pytest

from src.pipeline.suite_config import NotificationTarget, NotificationsConfig
from src.services.notification_service import (
    build_plain_text_body,
    build_slack_payload,
    build_teams_payload,
    notify_suite_result,
    send_email,
    send_slack_webhook,
    send_teams_webhook,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_PASSED_RESULT = {
    "suite_name": "daily-validation",
    "run_id": "abc-123",
    "status": "passed",
    "message": "",
    "step_results": [
        {
            "name": "Validate customer file",
            "type": "validate",
            "status": "passed",
            "error_count": 0,
            "total_rows": 100,
            "detail": "",
        },
    ],
}

_FAILED_RESULT = {
    "suite_name": "daily-validation",
    "run_id": "def-456",
    "status": "failed",
    "message": "",
    "step_results": [
        {
            "name": "Validate customer file",
            "type": "validate",
            "status": "passed",
            "error_count": 0,
            "total_rows": 100,
            "detail": "",
        },
        {
            "name": "Validate transaction file",
            "type": "validate",
            "status": "failed",
            "error_count": 3,
            "total_rows": 200,
            "detail": "3 validation error(s)",
        },
    ],
}


# ---------------------------------------------------------------------------
# Plain text body
# ---------------------------------------------------------------------------


class TestBuildPlainTextBody:
    """Tests for the plain-text email body builder."""

    def test_contains_suite_name(self):
        body = build_plain_text_body("daily-validation", _PASSED_RESULT)
        assert "daily-validation" in body

    def test_contains_status_passed(self):
        body = build_plain_text_body("daily-validation", _PASSED_RESULT)
        assert "PASSED" in body

    def test_contains_status_failed(self):
        body = build_plain_text_body("daily-validation", _FAILED_RESULT)
        assert "FAILED" in body

    def test_contains_run_id(self):
        body = build_plain_text_body("daily-validation", _PASSED_RESULT)
        assert "abc-123" in body

    def test_contains_step_details(self):
        body = build_plain_text_body("daily-validation", _FAILED_RESULT)
        assert "Validate customer file" in body
        assert "Validate transaction file" in body
        assert "3 validation error(s)" in body

    def test_step_rows_and_errors(self):
        body = build_plain_text_body("daily-validation", _FAILED_RESULT)
        assert "200 rows" in body
        assert "3 error(s)" in body


# ---------------------------------------------------------------------------
# Teams payload
# ---------------------------------------------------------------------------


class TestBuildTeamsPayload:
    """Tests for the Teams MessageCard payload builder."""

    def test_message_card_schema(self):
        payload = build_teams_payload("my-suite", _PASSED_RESULT)
        assert payload["@type"] == "MessageCard"
        assert "@context" in payload

    def test_passed_colour_green(self):
        payload = build_teams_payload("my-suite", _PASSED_RESULT)
        assert payload["themeColor"] == "00cc00"

    def test_failed_colour_red(self):
        payload = build_teams_payload("my-suite", _FAILED_RESULT)
        assert payload["themeColor"] == "cc0000"

    def test_summary_contains_suite_name(self):
        payload = build_teams_payload("my-suite", _PASSED_RESULT)
        assert "my-suite" in payload["summary"]

    def test_section_body_present(self):
        payload = build_teams_payload("my-suite", _PASSED_RESULT)
        sections = payload.get("sections", [])
        assert len(sections) == 1
        assert "my-suite" in sections[0]["text"]


# ---------------------------------------------------------------------------
# Slack payload
# ---------------------------------------------------------------------------


class TestBuildSlackPayload:
    """Tests for the Slack webhook payload builder."""

    def test_text_key_present(self):
        payload = build_slack_payload("my-suite", _PASSED_RESULT)
        assert "text" in payload

    def test_passed_has_checkmark(self):
        payload = build_slack_payload("my-suite", _PASSED_RESULT)
        assert ":white_check_mark:" in payload["text"]

    def test_failed_has_x(self):
        payload = build_slack_payload("my-suite", _FAILED_RESULT)
        assert ":x:" in payload["text"]

    def test_contains_suite_name(self):
        payload = build_slack_payload("my-suite", _PASSED_RESULT)
        assert "my-suite" in payload["text"]


# ---------------------------------------------------------------------------
# send_email
# ---------------------------------------------------------------------------


class TestSendEmail:
    """Tests for SMTP email sending."""

    @patch.dict("os.environ", {
        "SMTP_HOST": "smtp.test.com",
        "SMTP_PORT": "587",
        "SMTP_FROM": "ci@example.com",
        "SMTP_USER": "user",
        "SMTP_PASSWORD": "pass",
    })
    @patch("src.services.notification_service.smtplib.SMTP")
    def test_sends_email_successfully(self, mock_smtp_cls):
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        result = send_email(["dev@test.com"], "Test Subject", "Test body")

        assert result is True
        mock_smtp_cls.assert_called_once_with("smtp.test.com", 587, timeout=30)

    @patch.dict("os.environ", {"SMTP_HOST": "", "SMTP_FROM": ""}, clear=False)
    def test_returns_false_when_not_configured(self):
        result = send_email(["dev@test.com"], "Subject", "Body")
        assert result is False

    @patch.dict("os.environ", {
        "SMTP_HOST": "smtp.test.com",
        "SMTP_PORT": "587",
        "SMTP_FROM": "ci@example.com",
    })
    @patch("src.services.notification_service.smtplib.SMTP")
    def test_catches_smtp_error_gracefully(self, mock_smtp_cls):
        mock_smtp_cls.side_effect = Exception("Connection refused")

        result = send_email(["dev@test.com"], "Subject", "Body")

        assert result is False  # no exception raised


# ---------------------------------------------------------------------------
# send_teams_webhook
# ---------------------------------------------------------------------------


class TestSendTeamsWebhook:
    """Tests for Teams webhook sending."""

    @patch("src.services.notification_service.requests.post")
    def test_sends_successfully(self, mock_post):
        mock_post.return_value.raise_for_status = MagicMock()

        result = send_teams_webhook("https://teams.hook/123", {"key": "value"})

        assert result is True
        mock_post.assert_called_once_with(
            "https://teams.hook/123", json={"key": "value"}, timeout=15
        )

    @patch("src.services.notification_service.requests.post")
    def test_catches_error_gracefully(self, mock_post):
        mock_post.side_effect = Exception("Timeout")

        result = send_teams_webhook("https://teams.hook/123", {})

        assert result is False


# ---------------------------------------------------------------------------
# send_slack_webhook
# ---------------------------------------------------------------------------


class TestSendSlackWebhook:
    """Tests for Slack webhook sending."""

    @patch("src.services.notification_service.requests.post")
    def test_sends_successfully(self, mock_post):
        mock_post.return_value.raise_for_status = MagicMock()

        result = send_slack_webhook("https://hooks.slack.com/xxx", {"text": "hi"})

        assert result is True

    @patch("src.services.notification_service.requests.post")
    def test_catches_error_gracefully(self, mock_post):
        mock_post.side_effect = Exception("Timeout")

        result = send_slack_webhook("https://hooks.slack.com/xxx", {"text": "hi"})

        assert result is False


# ---------------------------------------------------------------------------
# notify_suite_result — routing
# ---------------------------------------------------------------------------


class TestNotifySuiteResult:
    """Tests for the orchestration function."""

    def test_noop_when_config_is_none(self):
        # Should not raise.
        notify_suite_result("suite", _PASSED_RESULT, None)

    @patch("src.services.notification_service.send_email")
    def test_routes_email_on_failure(self, mock_email):
        mock_email.return_value = True
        config = NotificationsConfig(
            on_failure=[NotificationTarget(type="email", to=["dev@test.com"])],
            on_success=[],
        )

        notify_suite_result("suite", _FAILED_RESULT, config)

        mock_email.assert_called_once()
        args = mock_email.call_args
        assert args[0][0] == ["dev@test.com"]
        assert "FAILED" in args[0][1]  # subject

    @patch("src.services.notification_service.send_email")
    def test_does_not_email_on_success_when_only_on_failure(self, mock_email):
        config = NotificationsConfig(
            on_failure=[NotificationTarget(type="email", to=["dev@test.com"])],
            on_success=[],
        )

        notify_suite_result("suite", _PASSED_RESULT, config)

        mock_email.assert_not_called()

    @patch("src.services.notification_service.send_email")
    def test_routes_email_on_success(self, mock_email):
        mock_email.return_value = True
        config = NotificationsConfig(
            on_failure=[],
            on_success=[NotificationTarget(type="email", to=["qa@test.com"])],
        )

        notify_suite_result("suite", _PASSED_RESULT, config)

        mock_email.assert_called_once()

    @patch("src.services.notification_service.send_teams_webhook")
    def test_routes_teams_on_failure(self, mock_teams):
        mock_teams.return_value = True
        config = NotificationsConfig(
            on_failure=[NotificationTarget(type="teams", url="https://teams.hook/1")],
        )

        notify_suite_result("suite", _FAILED_RESULT, config)

        mock_teams.assert_called_once()
        call_url = mock_teams.call_args[0][0]
        assert call_url == "https://teams.hook/1"

    @patch("src.services.notification_service.send_slack_webhook")
    def test_routes_slack_on_failure(self, mock_slack):
        mock_slack.return_value = True
        config = NotificationsConfig(
            on_failure=[NotificationTarget(type="slack", url="https://hooks.slack.com/x")],
        )

        notify_suite_result("suite", _FAILED_RESULT, config)

        mock_slack.assert_called_once()

    @patch("src.services.notification_service.send_email")
    @patch("src.services.notification_service.send_teams_webhook")
    def test_multiple_targets_on_failure(self, mock_teams, mock_email):
        mock_email.return_value = True
        mock_teams.return_value = True
        config = NotificationsConfig(
            on_failure=[
                NotificationTarget(type="email", to=["dev@test.com"]),
                NotificationTarget(type="teams", url="https://teams.hook/1"),
            ],
        )

        notify_suite_result("suite", _FAILED_RESULT, config)

        mock_email.assert_called_once()
        mock_teams.assert_called_once()

    @patch("src.services.notification_service.send_email")
    def test_dispatch_error_does_not_raise(self, mock_email):
        mock_email.side_effect = RuntimeError("unexpected")
        config = NotificationsConfig(
            on_failure=[NotificationTarget(type="email", to=["dev@test.com"])],
        )

        # Must not raise.
        notify_suite_result("suite", _FAILED_RESULT, config)

    def test_empty_targets_is_noop(self):
        config = NotificationsConfig(on_failure=[], on_success=[])
        # Should not raise.
        notify_suite_result("suite", _FAILED_RESULT, config)
        notify_suite_result("suite", _PASSED_RESULT, config)
