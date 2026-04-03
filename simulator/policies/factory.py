"""Policy factory helpers."""

from simulator.policies.eta_policy import ETAPolicy
from simulator.policies.nearest_vehicle_policy import NearestVehiclePolicy
from simulator.policies.random_policy import RandomPolicy


def build_policy(name: str):
    if name == "random":
        return RandomPolicy()
    if name == "nearest":
        return NearestVehiclePolicy()
    if name == "eta":
        return ETAPolicy()
    raise ValueError(f"Unsupported policy: {name}")
