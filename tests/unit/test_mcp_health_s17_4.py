"""Unit tests for the MCP health probe service (S17-4, #432).

check_mcp_health is async; tests drive it with asyncio.run() against a fake
FastMCP-shaped object and monkeypatch the module-level liveness/resource
helpers to simulate each failure stage.
"""

import asyncio

import pytest

from src.mcp import health
from src.mcp.health import MCPHealthResult, check_mcp_health


class _FakeManager:
    def __init__(self, started=True, task_group=object()):
        self._has_started = started
        self._task_group = task_group


class _FakeServer:
    """Minimal FastMCP stand-in for the health probe."""

    def __init__(self, *, manager=None, tools=3, resources=2, prompts=1, read_ok=True):
        self._manager = manager if manager is not None else _FakeManager()
        self._tools = list(range(tools))
        self._resources = list(range(resources))
        self._prompts = list(range(prompts))
        self._read_ok = read_ok

    @property
    def session_manager(self):
        if self._manager is None:
            raise RuntimeError("not started")
        return self._manager

    async def read_resource(self, uri):
        if not self._read_ok:
            raise RuntimeError("handler broke")
        return ["content-part"]

    async def list_tools(self):
        return self._tools

    async def list_resources(self):
        return self._resources

    async def list_prompts(self):
        return self._prompts


class TestToPayload:
    def test_healthy_payload(self):
        r = MCPHealthResult(
            healthy=True,
            protocol_version="v1",
            tool_count=5,
            resource_count=2,
            prompt_count=1,
            uptime_seconds=10,
        )
        payload = r.to_payload()
        assert payload["status"] == "healthy"
        assert "failed_check" not in payload
        assert payload["tool_count"] == 5

    def test_unhealthy_payload(self):
        r = MCPHealthResult(
            healthy=False,
            protocol_version="v1",
            tool_count=0,
            resource_count=0,
            prompt_count=0,
            uptime_seconds=3,
            failed_check="registry",
            reason="boom",
        )
        payload = r.to_payload()
        assert payload["status"] == "unhealthy"
        assert payload["failed_check"] == "registry"
        assert payload["reason"] == "boom"


class TestSessionManagerLive:
    def test_live_when_started_with_task_group(self):
        assert health._session_manager_is_live(_FakeServer()) is True

    def test_not_live_when_not_started(self):
        srv = _FakeServer(manager=_FakeManager(started=False))
        assert health._session_manager_is_live(srv) is False

    def test_not_live_when_no_task_group(self):
        srv = _FakeServer(manager=_FakeManager(task_group=None))
        assert health._session_manager_is_live(srv) is False

    def test_not_live_when_accessor_raises(self):
        class _RaisingServer:
            @property
            def session_manager(self):
                raise RuntimeError("streamable_http_app() never called")

        assert health._session_manager_is_live(_RaisingServer()) is False


class TestReadViolationsResource:
    def test_empty_parts_raises(self):
        srv = _FakeServer()

        async def empty(uri):
            return []

        srv.read_resource = empty
        with pytest.raises(RuntimeError, match="no content parts"):
            asyncio.run(health._read_violations_resource(srv))

    def test_ok_does_not_raise(self):
        asyncio.run(health._read_violations_resource(_FakeServer()))


class TestCheckMcpHealth:
    def test_healthy(self):
        result = asyncio.run(check_mcp_health(_FakeServer(tools=4)))
        assert result.healthy is True
        assert result.tool_count == 4
        assert result.resource_count == 2
        assert result.failed_check is None

    def test_session_manager_failure(self):
        srv = _FakeServer(manager=_FakeManager(started=False))
        result = asyncio.run(check_mcp_health(srv))
        assert result.healthy is False
        assert result.failed_check == "session_manager"

    def test_resource_read_failure(self):
        srv = _FakeServer(read_ok=False)
        result = asyncio.run(check_mcp_health(srv))
        assert result.healthy is False
        assert result.failed_check == "resource_read"

    def test_registry_enumeration_failure(self, monkeypatch):
        srv = _FakeServer()

        async def boom():
            raise RuntimeError("registry down")

        srv.list_tools = boom
        result = asyncio.run(check_mcp_health(srv))
        assert result.healthy is False
        assert result.failed_check == "registry"

    def test_zero_tools_is_unhealthy(self):
        srv = _FakeServer(tools=0)
        result = asyncio.run(check_mcp_health(srv))
        assert result.healthy is False
        assert result.failed_check == "registry"
        assert "empty" in result.reason
