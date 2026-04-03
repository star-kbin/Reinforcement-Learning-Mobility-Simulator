"""Episode result model."""

from dataclasses import asdict, dataclass


@dataclass
class EpisodeResult:
    policy_name: str
    scenario_name: str
    total_reward: float
    metrics: dict
    vehicle_logs: list[dict]
    request_logs: list[dict]
    step_logs: list[dict]
    timeline: list[dict]

    def to_dict(self) -> dict:
        return asdict(self)
