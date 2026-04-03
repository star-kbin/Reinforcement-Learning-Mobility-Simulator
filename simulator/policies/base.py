"""Base policy interface."""

from abc import ABC, abstractmethod

from simulator.action_space import Action


class BasePolicy(ABC):
    def reset(self) -> None:
        return None

    @abstractmethod
    def select_action(self, state: dict, valid_actions: list[Action], info: dict | None = None) -> Action:
        raise NotImplementedError

    @abstractmethod
    def get_name(self) -> str:
        raise NotImplementedError
