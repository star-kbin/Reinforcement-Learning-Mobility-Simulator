"""Encodes object state into a policy-friendly representation."""


class StateEncoder:
    """Returns a simple dict state for MVP policies."""

    def encode(self, env: "MobilityEnv") -> dict:
        return {
            "current_time": env.current_time,
            "vehicles": [vehicle.to_dict() for vehicle in env.vehicles],
            "requests": [request.to_dict(current_time=env.current_time) for request in env.requests],
        }
