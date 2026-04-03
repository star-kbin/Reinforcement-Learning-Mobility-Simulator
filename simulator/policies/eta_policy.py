"""ETA baseline policy."""

from simulator.action_space import Action
from simulator.entities.position import distance_between
from simulator.policies.base import BasePolicy


class ETAPolicy(BasePolicy):
    def select_action(self, state: dict, valid_actions: list[Action], info: dict | None = None) -> Action:
        best_action = Action(action_type="WAIT")
        best_eta = None

        vehicles = {vehicle["vehicle_id"]: vehicle for vehicle in state["vehicles"]}
        requests = {request["request_id"]: request for request in state["requests"] if request["status"] == "waiting"}

        for action in valid_actions:
            if action.action_type != "ASSIGN":
                continue
            vehicle = vehicles[action.vehicle_id]
            request = requests[action.request_id]
            speed = max(float(vehicle["speed"]), 1.0)
            eta = distance_between(tuple(vehicle["position"]), tuple(request["origin"])) / speed
            if best_eta is None or eta < best_eta:
                best_eta = eta
                best_action = action

        return best_action

    def get_name(self) -> str:
        return "eta"
