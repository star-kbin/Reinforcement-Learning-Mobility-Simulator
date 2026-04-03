"""Metrics collection."""


class MetricsCollector:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.total_distance = 0.0
        self.completed_requests = 0
        self.cancelled_requests = 0

    def update(self, env: "MobilityEnv", step_distance: float, completed_count: int, cancelled_count: int) -> None:
        self.total_distance += step_distance
        self.completed_requests += completed_count
        self.cancelled_requests += cancelled_count

    def snapshot(self) -> dict:
        total = self.completed_requests + self.cancelled_requests
        success_rate = 0.0 if total == 0 else self.completed_requests / total
        return {
            "total_distance": self.total_distance,
            "completed_requests": self.completed_requests,
            "cancelled_requests": self.cancelled_requests,
            "success_rate": success_rate,
        }
