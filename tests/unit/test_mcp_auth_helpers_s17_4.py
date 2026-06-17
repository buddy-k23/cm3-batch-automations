"""Unit tests for MCP auth token + header helpers (S17-4, #432).

Exercises the pure crypto path (mint/verify round-trip, tamper/expiry/grace
gates), the bearer encode/decode pair, the DN short-name helper, and the
per-credential check helpers (_check_api_key_header / _check_bearer_token /
_check_session_cookie) with a lightweight fake Request.
"""

import time
from unittest import mock

import pytest

from src.mcp import auth
from src.mcp.auth import (
    MCPPrincipal,
    TokenError,
    encode_bearer,
    mint_token,
    verify_token,
)

_KEY_ENV = auth.TOKEN_SIGNING_KEY_ENV_VAR


@pytest.fixture
def signing_key(monkeypatch):
    monkeypatch.setenv(_KEY_ENV, "unit-test-signing-key")
    return "unit-test-signing-key"


class _FakeRequest:
    """Minimal Starlette-Request stand-in for the header check helpers."""

    def __init__(self, headers=None):
        self.headers = headers or {}


class TestSigningKey:
    def test_unconfigured_raises(self, monkeypatch):
        monkeypatch.delenv(_KEY_ENV, raising=False)
        with pytest.raises(TokenError, match="not configured"):
            auth._signing_key()

    def test_configured_returns_bytes(self, signing_key):
        assert auth._signing_key() == b"unit-test-signing-key"


class TestMintVerifyRoundTrip:
    def test_round_trip(self, signing_key):
        token = mint_token("alice", "cn=alice,dc=x", "admin", ttl_hours=2)
        assert token["user"] == "alice"
        assert token["jti"]
        verified = verify_token(token)
        assert verified.user == "alice"
        assert verified.role == "admin"
        assert verified.jti == token["jti"]

    def test_ttl_clamped_low(self, signing_key):
        token = mint_token("a", "", "tester", ttl_hours=0)
        # clamped to >= 1 hour
        assert token["expires_at"] - token["issued_at"] == 3600

    def test_ttl_clamped_high(self, signing_key):
        token = mint_token("a", "", "tester", ttl_hours=10_000)
        max_secs = auth.MAX_TOKEN_TTL_HOURS * 3600
        assert token["expires_at"] - token["issued_at"] == max_secs


class TestVerifyTokenGates:
    def test_missing_fields(self, signing_key):
        with pytest.raises(TokenError, match="missing required fields"):
            verify_token({"user": "a"})

    def test_non_integer_timestamps(self, signing_key):
        token = mint_token("a", "", "tester")
        token["issued_at"] = "not-int"
        with pytest.raises(TokenError, match="not integers"):
            verify_token(token)

    def test_tampered_signature(self, signing_key):
        token = mint_token("a", "", "tester")
        token["role"] = "admin"  # role changed but signature not re-computed
        with pytest.raises(TokenError, match="signature does not match"):
            verify_token(token)

    def test_expired(self, signing_key):
        token = mint_token("a", "", "tester")
        # rewrite expiry into the past and re-sign with the helper's payload
        import hashlib
        import hmac

        token["issued_at"] = int(time.time()) - 7200
        token["expires_at"] = int(time.time()) - 3600
        token["signature"] = hmac.new(
            auth._signing_key(),
            auth._signature_payload(
                token["user"],
                token["principal_dn"],
                token["role"],
                token["issued_at"],
                token["expires_at"],
                token["jti"],
            ),
            hashlib.sha256,
        ).hexdigest()
        with pytest.raises(TokenError, match="expired"):
            verify_token(token)

    def test_revoked_jti_rejected(self, signing_key):
        token = mint_token("a", "", "tester")
        with mock.patch.object(auth, "_jti_is_revoked", return_value=True):
            with pytest.raises(TokenError, match="revoked"):
                verify_token(token)


class TestJtiIsRevokedFailSoft:
    def test_lookup_error_treated_not_revoked(self, signing_key):
        with mock.patch(
            "src.mcp.revocation.get_revocation_cache",
            side_effect=RuntimeError("db down"),
        ):
            assert auth._jti_is_revoked("deadbeef") is False


class TestBearerEncodeDecode:
    def test_round_trip(self):
        payload = {"user": "a", "role": "admin"}
        encoded = encode_bearer(payload)
        assert "=" not in encoded  # padding stripped
        assert auth._decode_bearer(encoded) == payload

    def test_raw_json_accepted(self):
        assert auth._decode_bearer('{"user": "a"}') == {"user": "a"}

    def test_raw_json_invalid_raises(self):
        with pytest.raises(TokenError, match="not valid JSON"):
            auth._decode_bearer("{not json")

    def test_garbage_base64_raises(self):
        with pytest.raises(TokenError, match="could not be decoded"):
            auth._decode_bearer("!!!not-base64!!!")


class TestShortUserFromDn:
    def test_extracts_cn(self):
        assert auth._short_user_from_dn("cn=alice,dc=x,dc=y") == "alice"

    def test_empty_dn(self):
        assert auth._short_user_from_dn("") == ""

    def test_no_equals(self):
        assert auth._short_user_from_dn("plainname") == "plainname"


class TestCheckApiKeyHeader:
    def test_missing_header_returns_none(self):
        assert auth._check_api_key_header(_FakeRequest()) is None

    def test_valid_key_returns_principal(self):
        req = _FakeRequest({"x-api-key": "secretkey123"})
        with mock.patch("src.api.auth._parse_api_keys", return_value={"secretkey123": "admin"}):
            principal = auth._check_api_key_header(req)
        assert isinstance(principal, MCPPrincipal)
        assert principal.role == "admin"
        assert principal.auth_kind == "api_key"

    def test_unknown_key_returns_none(self):
        req = _FakeRequest({"x-api-key": "nope"})
        with mock.patch("src.api.auth._parse_api_keys", return_value={}):
            assert auth._check_api_key_header(req) is None


class TestCheckBearerToken:
    def test_missing_header_returns_none(self):
        assert auth._check_bearer_token(_FakeRequest()) is None

    def test_non_bearer_returns_none(self):
        assert auth._check_bearer_token(_FakeRequest({"authorization": "Basic x"})) is None

    def test_valid_bearer_returns_principal(self, signing_key):
        token = mint_token("bob", "cn=bob", "tester")
        bearer = encode_bearer(token)
        req = _FakeRequest({"authorization": f"Bearer {bearer}"})
        principal = auth._check_bearer_token(req)
        assert principal.user == "bob"
        assert principal.auth_kind == "token"
        assert principal.jti == token["jti"]

    def test_bad_token_returns_none(self, signing_key):
        req = _FakeRequest({"authorization": "Bearer !!!garbage!!!"})
        assert auth._check_bearer_token(req) is None


class TestCheckSessionCookie:
    def test_no_session_returns_none(self):
        with mock.patch("src.api.auth._try_resolve_session_cookie", return_value=None):
            assert auth._check_session_cookie(_FakeRequest()) is None

    def test_session_translated_to_principal(self):
        ctx = mock.Mock(subject="cn=carol,dc=x", role="mapping_owner")
        ctx.name = "carol"  # `name` is a reserved Mock kwarg; set it explicitly
        with mock.patch("src.api.auth._try_resolve_session_cookie", return_value=ctx):
            principal = auth._check_session_cookie(_FakeRequest())
        assert principal.user == "carol"
        assert principal.role == "mapping_owner"
        assert principal.auth_kind == "session"
