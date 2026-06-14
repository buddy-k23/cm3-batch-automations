"""Unit tests for api_check test type — TestConfig fields and _run_api_check_test()."""

import pytest
from unittest.mock import MagicMock, patch
from pydantic import ValidationError

from src.contracts.test_suite import TestConfig


# ---------------------------------------------------------------------------
# TestConfig field acceptance tests
# ---------------------------------------------------------------------------

def test_testconfig_accepts_api_check_type():
    """TestConfig accepts type='api_check' without validation error."""
    config = TestConfig(
        name="my api check",
        type="api_check",
        url="http://example.com/health",
    )
    assert config.type == "api_check"


def test_testconfig_api_check_accepts_url():
    """TestConfig with api_check accepts 'url' field."""
    config = TestConfig(
        name="url test",
        type="api_check",
        url="http://internal-svc/health",
    )
    assert config.url == "http://internal-svc/health"


def test_testconfig_api_check_accepts_method():
    """TestConfig with api_check accepts 'method' field (GET/POST)."""
    get_config = TestConfig(name="get test", type="api_check", url="http://x", method="GET")
    post_config = TestConfig(name="post test", type="api_check", url="http://x", method="POST")
    assert get_config.method == "GET"
    assert post_config.method == "POST"


def test_testconfig_api_check_accepts_expected_status():
    """TestConfig with api_check accepts 'expected_status' field."""
    config = TestConfig(
        name="status test",
        type="api_check",
        url="http://x",
        expected_status=201,
    )
    assert config.expected_status == 201


def test_testconfig_api_check_accepts_response_contains():
    """TestConfig with api_check accepts 'response_contains' dict field."""
    config = TestConfig(
        name="response contains test",
        type="api_check",
        url="http://x",
        response_contains={"status": "ok"},
    )
    assert config.response_contains == {"status": "ok"}


def test_testconfig_api_check_accepts_body():
    """TestConfig with api_check accepts 'body' dict field."""
    config = TestConfig(
        name="body test",
        type="api_check",
        url="http://x",
        method="POST",
        body={"key": "value"},
    )
    assert config.body == {"key": "value"}


def test_testconfig_api_check_accepts_timeout_seconds():
    """TestConfig with api_check accepts 'timeout_seconds' int field."""
    config = TestConfig(
        name="timeout test",
        type="api_check",
        url="http://x",
        timeout_seconds=60,
    )
    assert config.timeout_seconds == 60


def test_testconfig_api_check_defaults():
    """TestConfig with api_check has sane defaults for optional fields."""
    config = TestConfig(name="defaults test", type="api_check", url="http://x")
    assert config.method == "GET"
    assert config.expected_status == 200
    assert config.timeout_seconds == 30
    assert config.body is None
    assert config.response_contains is None


def test_testconfig_rejects_unknown_type():
    """TestConfig rejects unknown type values."""
    with pytest.raises(ValidationError):
        TestConfig(name="bad type", type="unknown_type", url="http://x")


# ---------------------------------------------------------------------------
# _run_api_check_test() behavioural tests (mock httpx)
# ---------------------------------------------------------------------------

def _make_test(
    url="http://example.com/health",
    method="GET",
    expected_status=200,
    response_contains=None,
    body=None,
    timeout_seconds=30,
):
    """Helper to build a TestConfig for api_check tests."""
    return TestConfig(
        name="test check",
        type="api_check",
        url=url,
        method=method,
        expected_status=expected_status,
        response_contains=response_contains,
        body=body,
        timeout_seconds=timeout_seconds,
    )


def test_run_api_check_returns_pass_when_status_matches():
    """_run_api_check_test() returns PASS when status matches."""
    from src.commands.run_tests_command import _run_api_check_test

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {}

    with patch("httpx.get", return_value=mock_resp):
        result = _run_api_check_test(_make_test(expected_status=200), {})

    assert result["status"] == "PASS"
    assert result["errors"] == []


def test_run_api_check_returns_fail_when_status_mismatch():
    """_run_api_check_test() returns FAIL when status doesn't match."""
    from src.commands.run_tests_command import _run_api_check_test

    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.json.return_value = {}

    with patch("httpx.get", return_value=mock_resp):
        result = _run_api_check_test(_make_test(expected_status=200), {})

    assert result["status"] == "FAIL"
    assert any("500" in e for e in result["errors"])


def test_run_api_check_returns_fail_when_response_contains_key_missing():
    """_run_api_check_test() returns FAIL when response_contains key missing from body."""
    from src.commands.run_tests_command import _run_api_check_test

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"other_key": "value"}

    with patch("httpx.get", return_value=mock_resp):
        result = _run_api_check_test(
            _make_test(response_contains={"status": "ok"}), {}
        )

    assert result["status"] == "FAIL"
    assert any("status" in e for e in result["errors"])


def test_run_api_check_returns_pass_when_response_contains_all_match():
    """_run_api_check_test() returns PASS when response_contains all match."""
    from src.commands.run_tests_command import _run_api_check_test

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"status": "ok", "version": "1.0"}

    with patch("httpx.get", return_value=mock_resp):
        result = _run_api_check_test(
            _make_test(response_contains={"status": "ok"}), {}
        )

    assert result["status"] == "PASS"
    assert result["errors"] == []


def test_run_api_check_returns_error_when_connection_refused():
    """_run_api_check_test() returns ERROR when connection refused."""
    import httpx
    from src.commands.run_tests_command import _run_api_check_test

    with patch("httpx.get", side_effect=httpx.ConnectError("connection refused")):
        result = _run_api_check_test(_make_test(), {})

    assert result["status"] == "ERROR"
    assert any("Connection" in e or "connection" in e for e in result["errors"])


def test_run_api_check_post_method_uses_httpx_post():
    """_run_api_check_test() calls httpx.post for method=POST."""
    from src.commands.run_tests_command import _run_api_check_test

    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = {}

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        result = _run_api_check_test(
            _make_test(method="POST", expected_status=201, body={"key": "val"}), {}
        )

    mock_post.assert_called_once()
    assert result["status"] == "PASS"


def test_run_api_check_timeout_returns_error():
    """_run_api_check_test() returns ERROR when request times out."""
    import httpx
    from src.commands.run_tests_command import _run_api_check_test

    with patch("httpx.get", side_effect=httpx.TimeoutException("timed out")):
        result = _run_api_check_test(_make_test(timeout_seconds=5), {})

    assert result["status"] == "ERROR"
    assert any("timed out" in e.lower() or "timeout" in e.lower() for e in result["errors"])
