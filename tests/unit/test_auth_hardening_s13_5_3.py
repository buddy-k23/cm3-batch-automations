"""Auth-hardening regression tests (S13.5-3, #409).

Locks in three security fixes:

* **Dev-auth requires an explicit opt-in.** ``VALDO_MCP_AUTH=dev`` alone no
  longer bypasses auth — it must be paired with ``VALDO_ALLOW_DEV_AUTH=1``.
  A copied ``.env.example`` is therefore NOT auth-bypassed, while a
  developer can still opt in deliberately.
* **Constant-time API-key comparison.** The dict lookup
  (``keys.get(x_api_key)``) is replaced with an ``hmac.compare_digest``
  scan over the configured keys that preserves the ``key:role`` mapping
  and role resolution.
* **No functional secrets in ``.env.example``.** The sample signing keys
  are non-functional placeholders.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.mcp import auth as mcp_auth


# ---------------------------------------------------------------------------
# Dev-auth opt-in (src.mcp.auth.is_dev_auth_enabled)
# ---------------------------------------------------------------------------


class TestDevAuthOptIn:
    """``VALDO_MCP_AUTH=dev`` only bypasses auth WITH the explicit opt-in."""

    def test_dev_without_opt_in_is_not_bypassed(self, monkeypatch):
        """``VALDO_MCP_AUTH=dev`` alone does NOT enable the dev bypass."""
        monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
        monkeypatch.delenv("VALDO_ALLOW_DEV_AUTH", raising=False)
        assert mcp_auth.is_dev_auth_enabled() is False

    def test_dev_with_opt_in_is_enabled(self, monkeypatch):
        """``VALDO_MCP_AUTH=dev`` + ``VALDO_ALLOW_DEV_AUTH=1`` enables it."""
        monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
        monkeypatch.setenv("VALDO_ALLOW_DEV_AUTH", "1")
        assert mcp_auth.is_dev_auth_enabled() is True

    @pytest.mark.parametrize("truthy", ["1", "true", "True", "yes", "on"])
    def test_opt_in_accepts_common_truthy_values(self, monkeypatch, truthy):
        """The opt-in accepts the usual truthy spellings."""
        monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
        monkeypatch.setenv("VALDO_ALLOW_DEV_AUTH", truthy)
        assert mcp_auth.is_dev_auth_enabled() is True

    @pytest.mark.parametrize("falsy", ["0", "false", "no", "off", ""])
    def test_opt_in_rejects_falsy_values(self, monkeypatch, falsy):
        """A falsy opt-in keeps the dev bypass disabled."""
        monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
        monkeypatch.setenv("VALDO_ALLOW_DEV_AUTH", falsy)
        assert mcp_auth.is_dev_auth_enabled() is False

    def test_opt_in_without_dev_mode_does_nothing(self, monkeypatch):
        """The opt-in is inert unless ``VALDO_MCP_AUTH=dev`` is also set."""
        monkeypatch.delenv("VALDO_MCP_AUTH", raising=False)
        monkeypatch.setenv("VALDO_ALLOW_DEV_AUTH", "1")
        assert mcp_auth.is_dev_auth_enabled() is False

    def test_non_dev_auth_value_is_not_bypassed(self, monkeypatch):
        """A non-``dev`` mode value never enables the bypass."""
        monkeypatch.setenv("VALDO_MCP_AUTH", "prod")
        monkeypatch.setenv("VALDO_ALLOW_DEV_AUTH", "1")
        assert mcp_auth.is_dev_auth_enabled() is False


# ---------------------------------------------------------------------------
# Constant-time API-key comparison (src.api.auth)
# ---------------------------------------------------------------------------


class TestConstantTimeApiKeyCompare:
    """The API-key check uses compare_digest, not a dict lookup."""

    def test_resolve_role_matches_valid_key(self, monkeypatch):
        """A configured key resolves to its mapped role."""
        from src.api import auth as api_auth

        monkeypatch.setenv("API_KEYS", "alpha:admin,beta:tester")
        keys = api_auth._parse_api_keys()
        assert api_auth._resolve_api_key_role("alpha", keys) == "admin"
        assert api_auth._resolve_api_key_role("beta", keys) == "tester"

    def test_resolve_role_rejects_invalid_key(self, monkeypatch):
        """An unconfigured key resolves to None (rejected)."""
        from src.api import auth as api_auth

        monkeypatch.setenv("API_KEYS", "alpha:admin")
        keys = api_auth._parse_api_keys()
        assert api_auth._resolve_api_key_role("not-a-key", keys) is None

    def test_multi_key_role_resolution_preserved(self, monkeypatch):
        """Each key in a multi-key config resolves to its own role."""
        from src.api import auth as api_auth

        monkeypatch.setenv(
            "API_KEYS", "k1:admin,k2:mapping_owner,k3:tester,k4"
        )
        keys = api_auth._parse_api_keys()
        assert api_auth._resolve_api_key_role("k1", keys) == "admin"
        assert api_auth._resolve_api_key_role("k2", keys) == "mapping_owner"
        assert api_auth._resolve_api_key_role("k3", keys) == "tester"
        # Bare key without a role suffix defaults to "tester".
        assert api_auth._resolve_api_key_role("k4", keys) == "tester"

    def test_no_dict_lookup_in_source(self):
        """The timing-attackable ``keys.get(x_api_key)`` lookup is gone."""
        src = Path(mcp_auth.__file__).parent.parent / "api" / "auth.py"
        text = src.read_text(encoding="utf-8")
        assert "keys.get(x_api_key)" not in text
        assert "compare_digest" in text


# ---------------------------------------------------------------------------
# .env.example ships no functional secrets
# ---------------------------------------------------------------------------


class TestEnvExampleNoFunctionalSecrets:
    """A fresh ``.env`` from the sample must not be auth-bypassed or seeded."""

    def _env_example(self) -> str:
        root = Path(mcp_auth.__file__).parent.parent.parent
        return (root / ".env.example").read_text(encoding="utf-8")

    def test_mcp_auth_not_dev_by_default(self):
        """The active (uncommented) ``VALDO_MCP_AUTH`` line is empty, not ``dev``.

        Doc comments may still mention the ``VALDO_MCP_AUTH=dev`` opt-in, so
        we only inspect the non-comment assignment lines.
        """
        active = [
            line.strip()
            for line in self._env_example().splitlines()
            if line.strip().startswith("VALDO_MCP_AUTH=")
        ]
        assert active == ["VALDO_MCP_AUTH="], active

    def test_signing_keys_are_placeholders(self):
        """Sample signing keys are clearly non-functional placeholders."""
        text = self._env_example()
        # The old functional-looking dev secrets must be gone.
        assert "dev-mcp-signing-key-change-me" not in text
        assert "change-me-to-a-32-byte-random-hex-string" not in text
        # The placeholder convention is present for both signing keys.
        assert text.count("<CHANGE_ME_") >= 2
