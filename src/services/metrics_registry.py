from __future__ import annotations

from collections import defaultdict
from statistics import mean
from threading import Lock


class MetricsRegistry:
    """In-memory metrics registry with basic SLO alert evaluation."""

    def __init__(self):
        self._lock = Lock()
        self._counters: dict[str, int] = defaultdict(int)
        self._latencies_ms: dict[str, list[float]] = defaultdict(list)

    def incr(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] += amount

    def observe_latency(self, name: str, value_ms: float) -> None:
        with self._lock:
            self._latencies_ms[name].append(value_ms)

    def snapshot(self) -> dict:
        with self._lock:
            latency = {
                key: {
                    "count": len(values),
                    "avg_ms": round(mean(values), 2) if values else 0.0,
                    "p95_ms": round(sorted(values)[int(len(values) * 0.95) - 1], 2) if values else 0.0,
                }
                for key, values in self._latencies_ms.items()
            }
            return {
                "counters": dict(self._counters),
                "latencies": latency,
            }

    def slo_alerts(self) -> list[dict]:
        snap = self.snapshot()
        alerts: list[dict] = []

        task_failures = snap["counters"].get("tasks.failed", 0)
        task_total = snap["counters"].get("tasks.submitted", 0)
        if task_total >= 10:
            failure_rate = task_failures / task_total
            if failure_rate > 0.05:
                alerts.append({"name": "task_failure_rate", "severity": "high", "value": failure_rate, "threshold": 0.05})

        compare_latency = snap["latencies"].get("compare.async", {}).get("p95_ms", 0)
        if compare_latency and compare_latency > 5000:
            alerts.append({"name": "compare_async_p95_latency", "severity": "medium", "value_ms": compare_latency, "threshold_ms": 5000})

        return alerts


METRICS = MetricsRegistry()
