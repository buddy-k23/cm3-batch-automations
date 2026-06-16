"""Unit tests for the MCP sliding-window rate limiter (S9-3, #388).

The limiter in :mod:`src.mcp.rate_limit` is a pure, in-process
sliding-window counter behind a small pluggable backend interface (so a
Redis backend can drop in later) with an **injectable clock** so these
tests are deterministic and never sleep on the wall clock.

Coverage maps to the #388 acceptance criteria:

* :class:`TestSlidingWindowBackend` — the raw counter primitive: a cap of
  ``N`` admits ``N`` hits in a window and rejects the ``N+1``-th with a
  positive ``retry_after``; the slot frees once the window slides past
  the oldest hit. Per-key isolation is asserted here.
* :class:`TestRateLimiter` — the env-configured facade: per-token vs
  per-IP caps are independent, the ``get_run_status`` elevated cap is
  honoured, and resource reads are classified as exempt.
* :class:`TestRequestClassification` — the JSON-RPC body classifier that
  the middleware uses to decide *what* to rate-limit (tool call vs
  resource read vs handshake) and *which* tool was invoked.

The integration test that fires 31 calls in one second against the live
``/mcp/`` route and asserts the 31st returns ``429`` with a
``Retry-After`` header lives in
``tests/integration/test_mcp_rate_limit.py``.
"""

from __future__ import annotations

import pytest

from src.mcp.rate_limit import (
    DEFAULT_PER_IP_PER_MINUTE,
    DEFAULT_PER_TOKEN_PER_MINUTE,
    DEFAULT_RUN_STATUS_PER_MINUTE,
    ENV_PER_IP_PER_MINUTE,
    ENV_PER_TOKEN_PER_MINUTE,
    ENV_RUN_STATUS_PER_MINUTE,
    InMemorySlidingWindowBackend,
    RateLimitDecision,
    RateLimiter,
    classify_request,
    token_identity,
)


class _FakeClock:
    """Deterministic monotonic clock for the limiter under test.

    Starts at an arbitrary epoch and only moves when a test calls
    :meth:`advance`. Passed to the limiter as its ``clock`` callable so
    no test ever sleeps on real time.
    """

    def __init__(self, start: float = 1_000.0) -> None:
        self._now = float(start)

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += float(seconds)


# ---------------------------------------------------------------------------
# Sliding-window backend primitive
# ---------------------------------------------------------------------------


class TestSlidingWindowBackend:
    def test_admits_up_to_cap_then_rejects(self):
        clock = _FakeClock()
        backend = InMemorySlidingWindowBackend(window_seconds=60, clock=clock)

        # First three hits at cap=3 are admitted.
        for _ in range(3):
            decision = backend.hit("k", cap=3)
            assert decision.allowed is True
            assert decision.retry_after == 0

        # The fourth within the same window is rejected.
        decision = backend.hit("k", cap=3)
        assert decision.allowed is False
        assert decision.retry_after > 0
        assert decision.retry_after <= 60

    def test_slot_frees_when_window_slides(self):
        clock = _FakeClock()
        backend = InMemorySlidingWindowBackend(window_seconds=60, clock=clock)

        for _ in range(3):
            assert backend.hit("k", cap=3).allowed is True
        assert backend.hit("k", cap=3).allowed is False

        # Slide the window just past the oldest hit — one slot frees.
        clock.advance(61)
        assert backend.hit("k", cap=3).allowed is True

    def test_retry_after_counts_seconds_until_oldest_expires(self):
        clock = _FakeClock()
        backend = InMemorySlidingWindowBackend(window_seconds=60, clock=clock)

        backend.hit("k", cap=1)  # window now holds one hit at t=1000
        clock.advance(10)  # 10s elapsed
        decision = backend.hit("k", cap=1)
        assert decision.allowed is False
        # Oldest hit expires 60s after it landed → 50s remain.
        assert decision.retry_after == 50

    def test_keys_are_isolated(self):
        clock = _FakeClock()
        backend = InMemorySlidingWindowBackend(window_seconds=60, clock=clock)

        for _ in range(3):
            assert backend.hit("a", cap=3).allowed is True
        # "a" is now full, but "b" has its own independent window.
        assert backend.hit("a", cap=3).allowed is False
        assert backend.hit("b", cap=3).allowed is True


# ---------------------------------------------------------------------------
# Env-configured RateLimiter facade
# ---------------------------------------------------------------------------


class TestRateLimiter:
    def test_defaults_match_documented_caps(self, monkeypatch):
        monkeypatch.delenv(ENV_PER_TOKEN_PER_MINUTE, raising=False)
        monkeypatch.delenv(ENV_PER_IP_PER_MINUTE, raising=False)
        monkeypatch.delenv(ENV_RUN_STATUS_PER_MINUTE, raising=False)
        limiter = RateLimiter.from_env(clock=_FakeClock())
        assert limiter.per_token_cap == DEFAULT_PER_TOKEN_PER_MINUTE == 30
        assert limiter.per_ip_cap == DEFAULT_PER_IP_PER_MINUTE == 60
        assert (
            limiter.run_status_cap
            == DEFAULT_RUN_STATUS_PER_MINUTE
            == 240
        )

    def test_env_overrides_caps(self, monkeypatch):
        monkeypatch.setenv(ENV_PER_TOKEN_PER_MINUTE, "5")
        monkeypatch.setenv(ENV_PER_IP_PER_MINUTE, "7")
        monkeypatch.setenv(ENV_RUN_STATUS_PER_MINUTE, "9")
        limiter = RateLimiter.from_env(clock=_FakeClock())
        assert limiter.per_token_cap == 5
        assert limiter.per_ip_cap == 7
        assert limiter.run_status_cap == 9

    def test_per_token_limit_is_independent_of_per_ip(self, monkeypatch):
        # Per-token cap is tighter than per-IP; a single token should hit
        # the token wall first without exhausting the (wider) IP budget.
        monkeypatch.setenv(ENV_PER_TOKEN_PER_MINUTE, "2")
        monkeypatch.setenv(ENV_PER_IP_PER_MINUTE, "100")
        limiter = RateLimiter.from_env(clock=_FakeClock())

        assert limiter.check_tool_call(token_id="t1", client_ip="1.1.1.1",
                                       tool_name="validate_file").allowed
        assert limiter.check_tool_call(token_id="t1", client_ip="1.1.1.1",
                                       tool_name="validate_file").allowed
        denied = limiter.check_tool_call(token_id="t1", client_ip="1.1.1.1",
                                         tool_name="validate_file")
        assert denied.allowed is False
        assert denied.scope == "per_token"
        assert denied.retry_after > 0

    def test_per_ip_limit_catches_token_rotation(self, monkeypatch):
        # Defence-in-depth: a thief rotating tokens from one IP still hits
        # the per-IP wall even though each token is under its own cap.
        monkeypatch.setenv(ENV_PER_TOKEN_PER_MINUTE, "100")
        monkeypatch.setenv(ENV_PER_IP_PER_MINUTE, "2")
        limiter = RateLimiter.from_env(clock=_FakeClock())

        assert limiter.check_tool_call(token_id="ta", client_ip="9.9.9.9",
                                       tool_name="validate_file").allowed
        assert limiter.check_tool_call(token_id="tb", client_ip="9.9.9.9",
                                       tool_name="validate_file").allowed
        denied = limiter.check_tool_call(token_id="tc", client_ip="9.9.9.9",
                                         tool_name="validate_file")
        assert denied.allowed is False
        assert denied.scope == "per_ip"

    def test_get_run_status_uses_elevated_cap(self, monkeypatch):
        # Normal tools capped at 2/min; get_run_status capped at 5/min.
        # The 3rd-5th get_run_status polls must still pass.
        monkeypatch.setenv(ENV_PER_TOKEN_PER_MINUTE, "2")
        monkeypatch.setenv(ENV_PER_IP_PER_MINUTE, "1000")
        monkeypatch.setenv(ENV_RUN_STATUS_PER_MINUTE, "5")
        limiter = RateLimiter.from_env(clock=_FakeClock())

        for _ in range(5):
            d = limiter.check_tool_call(token_id="poller", client_ip="2.2.2.2",
                                        tool_name="get_run_status")
            assert d.allowed is True, d
        # 6th poll exceeds even the elevated cap.
        assert limiter.check_tool_call(token_id="poller", client_ip="2.2.2.2",
                                       tool_name="get_run_status").allowed is False

    def test_get_run_status_isolated_from_normal_tool_budget(self, monkeypatch):
        # A poller hammering get_run_status must NOT consume the normal
        # tool budget for the same token (separate elevated counter).
        monkeypatch.setenv(ENV_PER_TOKEN_PER_MINUTE, "2")
        monkeypatch.setenv(ENV_PER_IP_PER_MINUTE, "1000")
        monkeypatch.setenv(ENV_RUN_STATUS_PER_MINUTE, "10")
        limiter = RateLimiter.from_env(clock=_FakeClock())

        for _ in range(5):
            assert limiter.check_tool_call(token_id="p", client_ip="3.3.3.3",
                                           tool_name="get_run_status").allowed
        # The normal tool budget is untouched.
        assert limiter.check_tool_call(token_id="p", client_ip="3.3.3.3",
                                       tool_name="validate_file").allowed
        assert limiter.check_tool_call(token_id="p", client_ip="3.3.3.3",
                                       tool_name="validate_file").allowed
        assert limiter.check_tool_call(token_id="p", client_ip="3.3.3.3",
                                       tool_name="validate_file").allowed is False


# ---------------------------------------------------------------------------
# Request classification
# ---------------------------------------------------------------------------


class TestRequestClassification:
    def test_tool_call_is_rate_limited(self):
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "validate_file", "arguments": {}}}
        cls = classify_request(body)
        assert cls.is_tool_call is True
        assert cls.tool_name == "validate_file"

    def test_resource_read_is_exempt(self):
        body = {"jsonrpc": "2.0", "id": 1, "method": "resources/read",
                "params": {"uri": "taxonomy://violations"}}
        cls = classify_request(body)
        assert cls.is_tool_call is False
        assert cls.tool_name is None

    def test_handshake_methods_are_exempt(self):
        for method in ("initialize", "tools/list", "resources/list",
                       "prompts/list", "ping"):
            body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": {}}
            assert classify_request(body).is_tool_call is False

    def test_get_run_status_classified_by_tool_name(self):
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "get_run_status",
                           "arguments": {"run_id": "abc"}}}
        cls = classify_request(body)
        assert cls.is_tool_call is True
        assert cls.tool_name == "get_run_status"

    def test_malformed_body_is_treated_as_exempt(self):
        # A non-dict / missing-method body must not crash the classifier;
        # it is treated as "not a tool call" so the limiter fails open on
        # parse (auth has already gated the request).
        assert classify_request(None).is_tool_call is False
        assert classify_request([]).is_tool_call is False
        assert classify_request({"no": "method"}).is_tool_call is False


def test_decision_dataclass_shape():
    # Lightweight guard on the public RateLimitDecision contract the
    # middleware depends on (allowed, retry_after, scope).
    d = RateLimitDecision(allowed=True, retry_after=0, scope=None)
    assert d.allowed is True
    assert d.retry_after == 0
    assert d.scope is None


# ---------------------------------------------------------------------------
# S17-2 (#420): per-token bucket keyed on jti
# ---------------------------------------------------------------------------


class _Principal:
    """Minimal stand-in for MCPPrincipal — duck-typed for token_identity."""

    def __init__(self, user, auth_kind, jti=None):
        self.user = user
        self.auth_kind = auth_kind
        self.jti = jti


class TestTokenIdentityKeyedOnJti:
    def test_uses_jti_when_present(self):
        p = _Principal(user="jsmith", auth_kind="token", jti="a" * 32)
        assert token_identity(p) == f"jti:{'a' * 32}"

    def test_falls_back_to_auth_kind_user_without_jti(self):
        # Legacy/dev tokens (no jti) keep the old composite key.
        p = _Principal(user="jsmith", auth_kind="token", jti=None)
        assert token_identity(p) == "token:jsmith"

    def test_api_key_principal_without_jti_uses_fallback(self):
        p = _Principal(user="apikey:abc123", auth_kind="api_key", jti=None)
        assert token_identity(p) == "api_key:apikey:abc123"

    def test_none_principal_is_anonymous(self):
        assert token_identity(None) == "anonymous"

    def test_two_sessions_same_user_distinct_jti_get_separate_buckets(self):
        # The whole point of S17-2: two live sessions for ONE user no longer
        # share a per-token bucket — each jti is its own unit of identity.
        clock = _FakeClock()
        limiter = RateLimiter(
            per_token_cap=2, per_ip_cap=1000, run_status_cap=1000,
            clock=clock,
        )
        sess_a = _Principal("jsmith", "token", jti="1" * 32)
        sess_b = _Principal("jsmith", "token", jti="2" * 32)

        # Session A burns its full per-token budget.
        assert limiter.check_tool_call(token_identity(sess_a), "9.9.9.9", "validate_file").allowed
        assert limiter.check_tool_call(token_identity(sess_a), "9.9.9.9", "validate_file").allowed
        a_denied = limiter.check_tool_call(token_identity(sess_a), "9.9.9.9", "validate_file")
        assert a_denied.allowed is False
        assert a_denied.scope == "per_token"

        # Session B (same user, different jti) still has its OWN budget.
        assert limiter.check_tool_call(token_identity(sess_b), "9.9.9.9", "validate_file").allowed

    def test_same_jti_shares_one_bucket(self):
        clock = _FakeClock()
        limiter = RateLimiter(
            per_token_cap=2, per_ip_cap=1000, run_status_cap=1000,
            clock=clock,
        )
        p = _Principal("jsmith", "token", jti="3" * 32)
        key = token_identity(p)
        assert limiter.check_tool_call(key, "9.9.9.9", "validate_file").allowed
        assert limiter.check_tool_call(key, "9.9.9.9", "validate_file").allowed
        # Same jti → same bucket → 3rd call denied.
        assert limiter.check_tool_call(key, "9.9.9.9", "validate_file").allowed is False
