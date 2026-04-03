"""Constraint checking."""

from simulator.action_space import Action


class ConstraintChecker:
    def is_action_valid(self, env: "MobilityEnv", action: Action) -> bool:
        if action.action_type == "WAIT":
            return True
        if action.action_type != "ASSIGN":
            return False

        vehicle = next((v for v in env.vehicles if v.vehicle_id == action.vehicle_id), None)
        request = next((r for r in env.requests if r.request_id == action.request_id), None)
        if vehicle is None or request is None:
            return False
        if not vehicle.is_idle() or not vehicle.is_available(env.current_time):
            return False
        if request.status != "waiting":
            return False
        if request.waiting_time(env.current_time) > request.max_wait_time:
            return False
        return True
