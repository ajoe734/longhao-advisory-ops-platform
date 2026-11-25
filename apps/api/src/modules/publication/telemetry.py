from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class MetricSnapshot:
    request_count: dict[tuple[str, str], int]
    latency_seconds: dict[str, tuple[float, ...]]
    dependency_failures: dict[str, int]


class M08Telemetry:
    """Low-cardinality module metrics; customer and tenant IDs are never labels."""

    def __init__(self) -> None:
        self._requests: dict[tuple[str, str], int] = defaultdict(int)
        self._latencies: dict[str, list[float]] = defaultdict(list)
        self._dependencies: dict[str, int] = defaultdict(int)
        self._lock = Lock()

    def observe_request(self, operation: str, result_class: str, duration_seconds: float) -> None:
        with self._lock:
            self._requests[(operation, result_class)] += 1
            self._latencies[operation].append(duration_seconds)

    def dependency_failure(self, dependency: str) -> None:
        with self._lock:
            self._dependencies[dependency] += 1

    def snapshot(self) -> MetricSnapshot:
        with self._lock:
            return MetricSnapshot(
                dict(self._requests),
                {key: tuple(values) for key, values in self._latencies.items()},
                dict(self._dependencies),
            )
