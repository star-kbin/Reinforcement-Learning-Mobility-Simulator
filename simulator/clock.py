"""Clock utilities for step-based simulation."""


class SimulationClock:
    """Small wrapper around the current simulation time."""

    def __init__(self, start_time: int = 0) -> None:
        self.current_time = start_time

    def reset(self, start_time: int = 0) -> None:
        self.current_time = start_time

    def tick(self, delta: int = 1) -> int:
        self.current_time += delta
        return self.current_time
