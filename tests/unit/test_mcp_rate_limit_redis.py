"""Unit tests for the shared (Redis) MCP rate-limit backend (S17-1, #419).

These tests prove that :class:`src.mcp.rate_limit.RedisSlidingWindowBackend`
enforces the *same* allow/deny + ``retry_after`` contract as the in-memory
backend, and — the whole point of the story — that the cap holds **across**
two limiter instances when they share one backend (the multi-worker case
in-memory cannot satisfy because each process keeps its own counters).

To keep ``redis``/``fakeredis`` strictly optional, the tests inject a tiny
in-process fake that implements only the sorted-set + scripting commands
the backend uses (``eval``/``register_script`` over
``ZREMRANGEBYSCORE``/``ZCARD``/``ZADD``/``ZRANGE``/``EXPIRE``). The fake
mirrors a single-threaded Redis: ``eval`` runs the Lua-equivalent body
atomically in Python, so two "workers" sharing the fake see one shared
counter exactly as they would against a real Redis. When ``fakeredis`` is
installed it is exercised too (parametrised, skipped otherwise).
"""

from __future__ import annotations

import importlib.util
import math
from typing import Dict, List, Tuple

import pytest

_HAS_FAKEREDIS = importlib.util.find_spec("fakeredis") is not None

from src.mcp.rate_limit import (
    ENV_RATE_LIMIT_BACKEND,
    ENV_RATE_LIMIT_REDIS_URL,
    InMemorySlidingWindowBackend,
    RateLimiter,
    RedisSlidingWindowBackend,
)


class _FakeClock:
    """Deterministic monotonic clock — advances only when asked."""

    def __init__(self, start: float = 1_000.0) -> None:
        self._now = float(start)

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += float(seconds)


class _FakeRedis:
    """Minimal single-key-space fake Redis sorted-set client.

    Implements only what :class:`RedisSlidingWindowBackend` calls. Because
    Redis executes a single command/script at a time, modelling the store
    as a plain dict with synchronous methods faithfully reproduces the
    "one shared counter across N clients" behaviour the multi-worker test
    relies on.
    """

    def __init__(self) -> None:
        # key -> list of (score, member)
        self._sets: Dict[str, List[Tuple[float, str]]] = {}

    # --- sorted-set commands used by the backend's Lua-equivalent ---
    def _zremrangebyscore(self, key: str, lo: float, hi: float) -> None:
        members = self._sets.get(key)
        if not members:
            return
        self._sets[key] = [(s, m) for (s, m) in members if not (lo <= s <= hi)]

    def _zcard(self, key: str) -> int:
        return len(self._sets.get(key, []))

    def _zadd(self, key: str, score: float, member: str) -> None:
        self._sets.setdefault(key, []).append((float(score), member))

    def _zrange_first_score(self, key: str):
        members = self._sets.get(key)
        if not members:
            return None
        return min(s for (s, _m) in members)

    def _expire(self, key: str, _ttl: int) -> None:  # noqa: D401 - no-op in fake
        # TTL is irrelevant to the deterministic-clock tests; pruning by
        # score already bounds the set. Kept so the call signature matches.
        return None

    def register_script(self, _script: str):
        """Return a callable mirroring redis-py's Script object.

        The returned callable runs the same prune→count→conditional-add
        logic the real Lua script runs server-side, atomically (this fake
        is single-threaded), so behaviour is identical to a live Redis.
        """

        def _run(keys, args):  # redis-py Script: script(keys=[...], args=[...])
            key = keys[0]
            now = float(args[0])
            window = float(args[1])
            cap = int(args[2])
            member = args[3]
            cutoff = now - window
            # Prune everything strictly older than the window's left edge.
            self._zremrangebyscore(key, float("-inf"), cutoff)
            count = self._zcard(key)
            if cap <= 0 or count >= cap:
                oldest = self._zrange_first_score(key)
                if oldest is None:
                    seconds_until_free = window
                else:
                    seconds_until_free = (oldest + window) - now
                retry_after = max(1, int(math.ceil(seconds_until_free)))
                return [0, retry_after]
            self._zadd(key, now, member)
            self._expire(key, int(math.ceil(window)))
            return [1, 0]

        return _run


def _redis_backend(clock):
    """Build a Redis backend wired to the in-process fake client."""
    return RedisSlidingWindowBackend(
        client=_FakeRedis(), window_seconds=60, clock=clock
    )


# ---------------------------------------------------------------------------
# Backend parity — Redis backend must match the in-memory contract
# ---------------------------------------------------------------------------


class TestRedisBackendParity:
    def test_admits_up_to_cap_then_rejects(self):
        clock = _FakeClock()
        backend = _redis_backend(clock)
        for _ in range(3):
            d = backend.hit("k", cap=3)
            assert d.allowed is True
            assert d.retry_after == 0
        d = backend.hit("k", cap=3)
        assert d.allowed is False
        assert 0 < d.retry_after <= 60

    def test_slot_frees_when_window_slides(self):
        clock = _FakeClock()
        backend = _redis_backend(clock)
        for _ in range(3):
            assert backend.hit("k", cap=3).allowed is True
        assert backend.hit("k", cap=3).allowed is False
        clock.advance(61)
        assert backend.hit("k", cap=3).allowed is True

    def test_retry_after_counts_seconds_until_oldest_expires(self):
        clock = _FakeClock()
        backend = _redis_backend(clock)
        backend.hit("k", cap=1)
        clock.advance(10)
        d = backend.hit("k", cap=1)
        assert d.allowed is False
        assert d.retry_after == 50

    def test_keys_are_isolated(self):
        clock = _FakeClock()
        backend = _redis_backend(clock)
        for _ in range(3):
            assert backend.hit("a", cap=3).allowed is True
        assert backend.hit("a", cap=3).allowed is False
        assert backend.hit("b", cap=3).allowed is True

    def test_cap_zero_rejects_unconditionally(self):
        clock = _FakeClock()
        backend = _redis_backend(clock)
        assert backend.hit("k", cap=0).allowed is False

    def test_matches_inmemory_for_identical_sequence(self):
        # Drive the same hit/advance script through both backends and
        # assert byte-for-byte identical decisions.
        clock_a = _FakeClock()
        clock_b = _FakeClock()
        mem = InMemorySlidingWindowBackend(window_seconds=60, clock=clock_a)
        red = RedisSlidingWindowBackend(
            client=_FakeRedis(), window_seconds=60, clock=clock_b
        )
        script = [("hit", 4), ("hit", 4), ("advance", 30),
                  ("hit", 4), ("hit", 4), ("hit", 4),  # 4th in window -> deny
                  ("advance", 31), ("hit", 4)]
        for op, arg in script:
            if op == "advance":
                clock_a.advance(arg)
                clock_b.advance(arg)
                continue
            dm = mem.hit("same", cap=arg)
            dr = red.hit("same", cap=arg)
            assert dm.allowed == dr.allowed
            assert dm.retry_after == dr.retry_after


# ---------------------------------------------------------------------------
# Multi-worker simulation — the whole point of the story
# ---------------------------------------------------------------------------


class TestMultiWorkerSharedBackend:
    def test_cap_holds_across_two_limiters_sharing_one_backend(self):
        # 30/min per token, split across TWO limiter instances (two
        # gunicorn workers) that share ONE Redis backend. The 31st
        # COMBINED call must be denied — in-memory would let EACH do 30.
        clock = _FakeClock()
        shared = RedisSlidingWindowBackend(
            client=_FakeRedis(), window_seconds=60, clock=clock
        )
        worker_a = RateLimiter(
            per_token_cap=30, per_ip_cap=1000, run_status_cap=1000,
            backend=shared,
        )
        worker_b = RateLimiter(
            per_token_cap=30, per_ip_cap=1000, run_status_cap=1000,
            backend=shared,
        )

        allowed = 0
        # Alternate calls between the two workers, 15 each = 30 total.
        for i in range(30):
            worker = worker_a if i % 2 == 0 else worker_b
            d = worker.check_tool_call(
                token_id="shared-token", client_ip="5.5.5.5",
                tool_name="validate_file",
            )
            assert d.allowed is True
            allowed += 1
        assert allowed == 30

        # The 31st combined call — on EITHER worker — is denied.
        denied = worker_a.check_tool_call(
            token_id="shared-token", client_ip="5.5.5.5",
            tool_name="validate_file",
        )
        assert denied.allowed is False
        assert denied.scope == "per_token"
        assert denied.retry_after > 0

    def test_inmemory_would_NOT_hold_across_instances(self):
        # Contrast/control: two limiters with SEPARATE in-memory backends
        # each independently admit the full cap — demonstrating exactly the
        # bug the shared backend fixes. (Two backends, not one.)
        clock = _FakeClock()
        a = RateLimiter(per_token_cap=2, per_ip_cap=1000, run_status_cap=1000,
                        backend=InMemorySlidingWindowBackend(clock=clock))
        b = RateLimiter(per_token_cap=2, per_ip_cap=1000, run_status_cap=1000,
                        backend=InMemorySlidingWindowBackend(clock=clock))
        assert a.check_tool_call("t", "1.1.1.1", "validate_file").allowed
        assert a.check_tool_call("t", "1.1.1.1", "validate_file").allowed
        # 'a' is now at its cap, but 'b' (separate backend) still admits 2.
        assert b.check_tool_call("t", "1.1.1.1", "validate_file").allowed
        assert b.check_tool_call("t", "1.1.1.1", "validate_file").allowed


# ---------------------------------------------------------------------------
# from_env backend selection + fail-soft
# ---------------------------------------------------------------------------


class TestFromEnvBackendSelection:
    def test_defaults_to_memory(self, monkeypatch):
        monkeypatch.delenv(ENV_RATE_LIMIT_BACKEND, raising=False)
        limiter = RateLimiter.from_env(clock=_FakeClock())
        assert isinstance(limiter._backend, InMemorySlidingWindowBackend)

    def test_explicit_memory_selects_memory(self, monkeypatch):
        monkeypatch.setenv(ENV_RATE_LIMIT_BACKEND, "memory")
        limiter = RateLimiter.from_env(clock=_FakeClock())
        assert isinstance(limiter._backend, InMemorySlidingWindowBackend)

    def test_redis_selected_when_connector_succeeds(self, monkeypatch):
        # Inject a connector so no real redis package/server is needed.
        monkeypatch.setenv(ENV_RATE_LIMIT_BACKEND, "redis")
        monkeypatch.setenv(ENV_RATE_LIMIT_REDIS_URL, "redis://fake:6379/0")
        fake = _FakeRedis()

        def _connector(url):
            assert url == "redis://fake:6379/0"
            return fake

        limiter = RateLimiter.from_env(
            clock=_FakeClock(), redis_connector=_connector
        )
        assert isinstance(limiter._backend, RedisSlidingWindowBackend)

    def test_redis_unavailable_fails_soft_to_memory(self, monkeypatch, caplog):
        # The connector raises (package missing or connection refused) —
        # the limiter must fall back to in-memory and log a WARNING so the
        # server still boots with a visible degraded state.
        monkeypatch.setenv(ENV_RATE_LIMIT_BACKEND, "redis")
        monkeypatch.setenv(ENV_RATE_LIMIT_REDIS_URL, "redis://down:6379/0")

        def _connector(url):
            raise ConnectionError("redis unreachable")

        import logging

        with caplog.at_level(logging.WARNING):
            limiter = RateLimiter.from_env(
                clock=_FakeClock(), redis_connector=_connector
            )
        assert isinstance(limiter._backend, InMemorySlidingWindowBackend)
        assert any("rate-limit" in r.message.lower()
                   and "redis" in r.message.lower()
                   for r in caplog.records)


@pytest.mark.skipif(
    not _HAS_FAKEREDIS, reason="fakeredis (optional test dep) not installed"
)
class TestAgainstRealFakeredis:
    """Exercise the backend against the actual ``fakeredis`` client.

    Skipped automatically when ``fakeredis`` (optional test dep) is not
    installed, so the core suite stays green without it.
    """

    def test_cap_holds_with_fakeredis_client(self):
        import fakeredis

        clock = _FakeClock()
        client = fakeredis.FakeStrictRedis()
        # The backend uses server-side Lua scripting (EVAL). fakeredis only
        # supports it with the optional 'lua' extra (lupa). Skip cleanly
        # when that engine is absent rather than fail on an env detail.
        try:
            client.register_script("return 1")(keys=[], args=[])
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"fakeredis Lua engine unavailable: {exc}")
        backend = RedisSlidingWindowBackend(
            client=client, window_seconds=60, clock=clock
        )
        for _ in range(3):
            assert backend.hit("fr", cap=3).allowed is True
        d = backend.hit("fr", cap=3)
        assert d.allowed is False
        assert d.retry_after > 0
