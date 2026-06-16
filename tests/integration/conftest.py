"""Shared fixtures for MCP/API integration tests.

S13.5-3 (#409) made the zero-credential MCP dev-auth bypass a strict
opt-in: ``VALDO_MCP_AUTH=dev`` now ALSO requires a truthy
``VALDO_ALLOW_DEV_AUTH``. The MCP integration suites set
``VALDO_MCP_AUTH=dev`` per test to exercise tools without minting a token,
so we enable the opt-in process-wide here (autouse) — this is exactly what
a local developer does (see docs/MCP_CLIENTS.md and PRODUCTION_DEPLOYMENT.md).

The opt-in alone never bypasses anything: it is inert unless a test also
sets ``VALDO_MCP_AUTH=dev``. Tests that exercise the PRODUCTION auth chain
delete ``VALDO_MCP_AUTH`` (e.g. ``test_mcp_production_mode_rejects_*``);
those remain fully gated because the bypass requires the sentinel too.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _enable_dev_auth_opt_in(monkeypatch):
    """Opt in to the MCP dev-auth bypass for the integration suite.

    Mirrors the documented local-dev setup (VALDO_MCP_AUTH=dev +
    VALDO_ALLOW_DEV_AUTH=1). Applied via monkeypatch so it is torn down
    after every test and never leaks into other suites.
    """
    monkeypatch.setenv("VALDO_ALLOW_DEV_AUTH", "1")
