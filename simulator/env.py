"""Main step-based mobility environment."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

from simulator.action_space import Action, ActionSpace
from simulator.clock import SimulationClock
from simulator.constraints.base import ConstraintChecker
from simulator.entities.request import Request
from simulator.entities.vehicle import Vehicle
from simulator.models.metrics import MetricsCollector
from simulator.models.result import EpisodeResult
from simulator.rewards.weighted_reward import WeightedRewardCalculator
from simulator.routing import build_routing_service
from simulator.scenarios.loader import build_requests, load_config, load_scenario
from simulator.state_encoder import StateEncoder

KST = timezone(timedelta(hours=9))


class MobilityEnv:
    """Minimal environment for baseline and RL integration."""

    def __init__(self, config: dict, scenario: dict) -> None:
        self.base_config = deepcopy(config)
        self.base_scenario = deepcopy(scenario)
        self.clock = SimulationClock()
        self.action_space = ActionSpace()
        self.state_encoder = StateEncoder()
        self.constraint_checker = ConstraintChecker()
        self.reward_calculator = WeightedRewardCalculator(config)
        self.metrics = MetricsCollector()
        self.reset()

    @classmethod
    def from_files(cls, config_path: str, scenario_path: str) -> "MobilityEnv":
        return cls(load_config(config_path), load_scenario(scenario_path))

    def reset(self) -> dict:
        self.config = deepcopy(self.base_config)
        self.scenario = deepcopy(self.base_scenario)
        self.routing_service = build_routing_service(self.config)
        self.simulation_start_time = self._parse_simulation_start_time()
        self.clock.reset(0)
        self.current_time = 0
        self.done = False
        self.step_logs: list[dict] = []
        self.timeline: list[dict] = []
        self.vehicles = self._build_vehicles()
        self.requests = build_requests(self.scenario)
        self.metrics.reset()
        self._activate_pending_requests()
        self.timeline.append(self._build_snapshot(stage="initial", action=None, reward=0.0, info={}))
        return self.get_state()

    def _build_vehicles(self) -> list[Vehicle]:
        positions = self.config["vehicles"]["initial_positions"]
        vehicle_cfg = self.config["vehicles"]
        vehicles: list[Vehicle] = []
        for index, position in enumerate(positions, start=1):
            vehicles.append(
                Vehicle(
                    vehicle_id=f"V{index}",
                    position=tuple(position),
                    capacity=vehicle_cfg["capacity"],
                    speed=vehicle_cfg["speed"],
                    available_until=vehicle_cfg["available_time"],
                )
            )
        return vehicles

    def get_state(self) -> dict:
        return self.state_encoder.encode(self)

    def get_valid_actions(self) -> list[Action]:
        return self.action_space.build_actions(self)

    def is_done(self) -> bool:
        return self.done

    def render(self) -> None:
        print(self.get_state())

    def step(self, action: Action) -> tuple[dict, float, bool, dict]:
        if self.done:
            raise RuntimeError("Episode is already finished.")

        self._activate_pending_requests()
        if not self.constraint_checker.is_action_valid(self, action):
            action = Action(action_type="WAIT")

        self._apply_action(action)
        step_distance = self._move_vehicles()
        completed_now, cancelled_now = self._update_requests()

        reward, breakdown = self.reward_calculator.compute(
            env=self,
            step_distance=step_distance,
            completed_count=completed_now,
            cancelled_count=cancelled_now,
        )
        self.metrics.update(self, step_distance, completed_now, cancelled_now)

        self.clock.tick(self.config["simulation"]["time_step"])
        self.current_time = self.clock.current_time
        self._activate_pending_requests()
        self.done = self._check_done()

        info = {
            "valid_action_count": len(self.get_valid_actions()),
            "completed_requests": completed_now,
            "cancelled_requests": cancelled_now,
            "step_metrics": self.metrics.snapshot(),
            "step_distance": step_distance,
            "reward_breakdown": breakdown,
        }
        self.step_logs.append(
            {
                "time": self.current_time,
                "action": action.__dict__,
                "reward": reward,
                "reward_breakdown": breakdown,
                "info": info,
            }
        )
        self.timeline.append(self._build_snapshot(stage="post_step", action=action, reward=reward, info=info))
        return self.get_state(), reward, self.done, info

    def _apply_action(self, action: Action) -> None:
        if action.action_type != "ASSIGN":
            return

        vehicle = self._get_vehicle(action.vehicle_id)
        request = self._get_request(action.request_id)
        if vehicle is None or request is None:
            return

        route_plan = None
        if self.routing_service is not None:
            route_plan = self.routing_service.plan_route(
                vehicle.position,
                request.origin,
                vehicle.speed,
                int(self.config["simulation"]["time_step"]),
                reference_time=self._get_reference_time(),
            )
        vehicle.assign_request(request, route_plan=route_plan)
        request.assign(vehicle.vehicle_id, self.current_time)

    def _activate_pending_requests(self) -> None:
        for request in self.requests:
            if request.status == "pending" and request.created_at <= self.current_time:
                request.status = "waiting"

    def _move_vehicles(self) -> float:
        total_distance = 0.0
        reroute_interval = self.config["simulation"].get("traffic_update_interval")
        time_step = int(self.config["simulation"]["time_step"])
        reference_time = self._get_reference_time(offset_seconds=time_step)
        for vehicle in self.vehicles:
            total_distance += vehicle.move_one_step(
                self.requests,
                self.current_time,
                time_step,
                self.routing_service,
                reference_time=reference_time,
                reroute_interval=None if reroute_interval is None else int(reroute_interval),
            )
        return total_distance

    def _update_requests(self) -> tuple[int, int]:
        completed_now = 0
        cancelled_now = 0
        for request in self.requests:
            if request.status == "waiting" and request.waiting_time(self.current_time) > request.max_wait_time:
                request.mark_cancelled(self.current_time)
                cancelled_now += 1
            elif request.status == "completed" and not request.logged_complete:
                request.logged_complete = True
                completed_now += 1
        return completed_now, cancelled_now

    def _check_done(self) -> bool:
        if self.current_time >= self.config["simulation"]["max_time"]:
            return True
        unfinished = [r for r in self.requests if r.status not in {"completed", "cancelled"}]
        return len(unfinished) == 0

    def _build_snapshot(self, stage: str, action: Action | None, reward: float, info: dict) -> dict:
        reference_time = self._get_reference_time()
        return {
            "time": self.current_time,
            "absolute_time": None if reference_time is None else reference_time.isoformat(sep=" ", timespec="seconds"),
            "stage": stage,
            "action": None if action is None else action.__dict__,
            "reward": reward,
            "metrics": self.get_metrics(),
            "vehicles": [vehicle.to_dict() for vehicle in self.vehicles],
            "requests": [request.to_dict(current_time=self.current_time) for request in self.requests],
            "info": {
                **info,
                "absolute_time": None if reference_time is None else reference_time.isoformat(sep=" ", timespec="seconds"),
                "simulation_start_time": (
                    None
                    if self.simulation_start_time is None
                    else self.simulation_start_time.isoformat(sep=" ", timespec="seconds")
                ),
                "routing_provider": None if self.routing_service is None else self.routing_service.__class__.__name__,
                "traffic_reference_mode": self._get_traffic_reference_mode(),
            },
        }

    def _get_traffic_reference_mode(self) -> str | None:
        return "osm_static_network" if self.routing_service is not None else None

    def _get_reference_time(self, offset_seconds: int = 0) -> datetime | None:
        if self.simulation_start_time is None:
            return None
        return self.simulation_start_time + timedelta(seconds=self.current_time + offset_seconds)

    def _parse_simulation_start_time(self) -> datetime | None:
        simulation_cfg = self.config.get("simulation", {})
        raw_value = str(simulation_cfg.get("window_start", "")).strip()
        if not raw_value:
            return None

        parsed = self._try_parse_datetime(raw_value)
        if parsed is None:
            return None
        if parsed.tzinfo is not None:
            return parsed.astimezone(KST)
        return parsed.replace(tzinfo=KST)

    def _try_parse_datetime(self, value: str) -> datetime | None:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass

        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        return None

    def _get_vehicle(self, vehicle_id: str | None) -> Vehicle | None:
        return next((vehicle for vehicle in self.vehicles if vehicle.vehicle_id == vehicle_id), None)

    def _get_request(self, request_id: str | None) -> Request | None:
        return next((request for request in self.requests if request.request_id == request_id), None)

    def get_metrics(self) -> dict:
        return self.metrics.snapshot()

    def get_result(self, policy_name: str = "unknown") -> EpisodeResult:
        return EpisodeResult(
            policy_name=policy_name,
            scenario_name=self.scenario.get("scenario_name", "unknown"),
            total_reward=sum(log["reward"] for log in self.step_logs),
            metrics=self.get_metrics(),
            vehicle_logs=[vehicle.to_dict() for vehicle in self.vehicles],
            request_logs=[request.to_dict(current_time=self.current_time) for request in self.requests],
            step_logs=self.step_logs,
            timeline=self.timeline,
        )
