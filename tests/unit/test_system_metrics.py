from fastapi.testclient import TestClient

from src.api.main import app
from src.services.metrics_registry import METRICS


client = TestClient(app)


def test_metrics_and_slo_endpoints():
    METRICS.incr("tasks.submitted", amount=20)
    METRICS.incr("tasks.failed", amount=2)
    METRICS.observe_latency("compare.async", 6000)

    m = client.get("/api/v1/system/metrics")
    assert m.status_code == 200
    metrics = m.json()
    assert "counters" in metrics

    s = client.get("/api/v1/system/slo-alerts")
    assert s.status_code == 200
    alerts = s.json()["alerts"]
    names = {a["name"] for a in alerts}
    assert "task_failure_rate" in names
    assert "compare_async_p95_latency" in names
