"""Wait for the FastAPI server to be ready before running E2E tests.

Polls GET /api/v1/system/health up to MAX_ATTEMPTS times with SLEEP_S
seconds between attempts. Exits 0 when the server responds with a
success status; exits 1 if the server is not ready in time.
"""
from __future__ import annotations

import os
import sys
import time

import httpx

# Default to https; override with VALDO_HEALTH_URL for plain-HTTP deployments.
HEALTH_URL = os.getenv(
    "VALDO_HEALTH_URL", "https://127.0.0.1:8000/api/v1/system/health"
)
# When the server uses a self-signed cert (tls.strategy: self_signed),
# certificate verification will fail. Set VALDO_VERIFY_TLS=1 once the cert
# is trusted by the local store.
VERIFY_TLS = os.getenv("VALDO_VERIFY_TLS", "0").lower() in ("1", "true", "yes")
MAX_ATTEMPTS = 30
SLEEP_S = 0.5


def wait_for_server() -> int:
    """Poll the health endpoint until the server is ready.

    Returns:
        0 if the server became ready within the timeout, 1 otherwise.
    """
    last_exc: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            r = httpx.get(HEALTH_URL, timeout=2, verify=VERIFY_TLS)
            if r.is_success:
                print("Server ready")
                return 0
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
        time.sleep(SLEEP_S)
    print(f"Server did not start in time after {MAX_ATTEMPTS} attempts: {last_exc}")
    return 1


if __name__ == "__main__":
    sys.exit(wait_for_server())
