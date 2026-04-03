"""Action-space helpers."""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Action:
    action_type: str
    vehicle_id: Optional[str] = None
    request_id: Optional[str] = None
    target_position: Optional[tuple[int, int]] = None


class ActionSpace:
    """Builds valid actions for the current state."""

    def build_actions(self, env: "MobilityEnv") -> list[Action]:
        actions: list[Action] = [Action(action_type="WAIT")]
        waiting_requests = [r for r in env.requests if r.status == "waiting"]
        idle_vehicles = [v for v in env.vehicles if v.is_idle() and v.is_available(env.current_time)]

        for vehicle in idle_vehicles:
            for request in waiting_requests:
                actions.append(
                    Action(
                        action_type="ASSIGN",
                        vehicle_id=vehicle.vehicle_id,
                        request_id=request.request_id,
                    )
                )
        return actions

    def build_mask(self, env: "MobilityEnv") -> list[int]:
        return [1] * len(self.build_actions(env))
