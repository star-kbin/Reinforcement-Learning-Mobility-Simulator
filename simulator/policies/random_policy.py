"""Random baseline policy."""

import random

from simulator.action_space import Action
from simulator.policies.base import BasePolicy


class RandomPolicy(BasePolicy):
    def __init__(self, seed: int = 42) -> None:
        self._rng = random.Random(seed)

    def select_action(self, state: dict, valid_actions: list[Action], info: dict | None = None) -> Action:
        return self._rng.choice(valid_actions)

    def get_name(self) -> str:
        return "random"
