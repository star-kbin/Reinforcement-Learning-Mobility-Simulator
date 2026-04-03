"""Weighted reward calculation."""

from simulator.rewards.base import BaseRewardCalculator


class WeightedRewardCalculator(BaseRewardCalculator):
    def __init__(self, config: dict) -> None:
        self.weights = config["reward"]

    def compute(self, env: "MobilityEnv", step_distance: float, completed_count: int, cancelled_count: int) -> tuple[float, dict]:
        waiting_penalty = sum(
            1 for request in env.requests if request.status in {"waiting", "assigned"}
        ) * self.weights["wait_time"]
        distance_penalty = step_distance * self.weights["travel_distance"]
        completed_bonus = completed_count * self.weights["completed_request"]
        cancelled_penalty = cancelled_count * self.weights["cancelled_request"]

        reward = -waiting_penalty - distance_penalty + completed_bonus - cancelled_penalty
        breakdown = {
            "waiting_penalty": -waiting_penalty,
            "distance_penalty": -distance_penalty,
            "completed_bonus": completed_bonus,
            "cancelled_penalty": -cancelled_penalty,
        }
        return reward, breakdown
