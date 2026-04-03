"""Base reward calculator."""

from abc import ABC, abstractmethod


class BaseRewardCalculator(ABC):
    @abstractmethod
    def compute(self, env: "MobilityEnv", step_distance: float, completed_count: int, cancelled_count: int) -> tuple[float, dict]:
        raise NotImplementedError
