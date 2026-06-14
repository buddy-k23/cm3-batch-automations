import os

from fastapi.testclient import TestClient

os.environ.setdefault("API_KEYS", "test-key:admin")

from src.api.main import app
from src.services.metrics_registry import METRICS


client = TestClient(app)
AUTH_HEADERS = {"X-API-Key": "test-key"}


def test_metrics_and_slo_endpoints():
    # Reset singleton state so assertions are deterministic regardless of test order
    with METRICS._lock:
        METRICS._counters.clear()
        METRICS._latencies_ms.clear()
    METRICS.incr("tasks.submitted", amount=20)
    METRICS.incr("tasks.failed", amount=2)
    METRICS.observe_latency("compare.async", 6000)

    m = client.get("/api/v1/system/metrics", headers=AUTH_HEADERS)
    assert m.status_code == 200
    metrics = m.json()
    assert "counters" in metrics

    s = client.get("/api/v1/system/slo-alerts", headers=AUTH_HEADERS)
    assert s.status_code == 200
    alerts = s.json()["alerts"]
    names = {a["name"] for a in alerts}
    assert "task_failure_rate" in names
    assert "compare_async_p95_latency" in names
