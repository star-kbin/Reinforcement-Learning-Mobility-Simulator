"""Vehicle entity."""

from copy import deepcopy
from datetime import datetime
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from simulator.entities.position import distance_between, extract_path_segment, interpolate_path, step_toward

if TYPE_CHECKING:
    from simulator.routing import BaseRoutingService, RoutePlan


@dataclass
class Vehicle:
    vehicle_id: str
    position: tuple[float, float]
    capacity: int
    speed: float
    available_until: int
    status: str = "idle"
    current_load: int = 0
    assigned_request_id: str | None = None
    target_position: tuple[float, float] | None = None
    distance_travelled: float = 0.0
    completed_request_ids: list[str] = field(default_factory=list)
    active_route_path: list[tuple[float, float]] = field(default_factory=list)
    active_route_distance: float = 0.0
    active_route_duration: float = 0.0
    active_route_elapsed: float = 0.0
    active_route_source: str | None = None
    active_route_metadata: dict = field(default_factory=dict)
    route_history_paths: list[list[tuple[float, float]]] = field(default_factory=list)
    route_history_sources: list[str] = field(default_factory=list)
    route_history_request_ids: list[str | None] = field(default_factory=list)
    route_history_stages: list[str] = field(default_factory=list)
    route_history_metadata: list[dict] = field(default_factory=list)
    actual_route_paths: list[list[tuple[float, float]]] = field(default_factory=list)
    actual_route_sources: list[str] = field(default_factory=list)
    actual_route_request_ids: list[str | None] = field(default_factory=list)
    actual_route_stages: list[str] = field(default_factory=list)

    def assign_request(self, request: "Request", route_plan: "RoutePlan | None" = None) -> None:
        self.assigned_request_id = request.request_id
        self.target_position = request.origin
        self.status = "to_pickup"
        self._set_route_plan(route_plan)

    def is_idle(self) -> bool:
        return self.status == "idle"

    def is_available(self, current_time: int) -> bool:
        return current_time < self.available_until

    def move_one_step(
        self,
        requests: list["Request"],
        current_time: int,
        time_step: int,
        routing_service: "BaseRoutingService | None" = None,
        reference_time: datetime | None = None,
        reroute_interval: int | None = None,
    ) -> float:
        if self.target_position is None:
            return 0.0
        step_end_time = current_time + time_step
        distance = self._advance_position(time_step)
        self.distance_travelled += distance

        assigned = next((request for request in requests if request.request_id == self.assigned_request_id), None)
        if assigned is None:
            return distance

        should_reroute = (
            routing_service is not None
            and reroute_interval is not None
            and reroute_interval > 0
            and self.target_position is not None
            and self.active_route_elapsed > 0
            and self.active_route_elapsed >= reroute_interval
            and self.position != self.target_position
        )
        if should_reroute:
            refreshed_route = routing_service.plan_route(
                self.position,
                self.target_position,
                self.speed,
                time_step,
                reference_time=reference_time,
            )
            self._set_route_plan(refreshed_route)

        if self.status == "to_pickup" and self.position == assigned.origin:
            self.current_load = 1
            assigned.mark_onboard(step_end_time)
            self.target_position = assigned.destination
            self.status = "onboard"
            next_route = None
            if routing_service is not None:
                next_route = routing_service.plan_route(
                    self.position,
                    assigned.destination,
                    self.speed,
                    time_step,
                    reference_time=reference_time,
                )
            self._set_route_plan(next_route)
        elif self.status == "onboard" and self.position == assigned.destination:
            assigned.mark_completed(step_end_time)
            self.current_load = 0
            self.completed_request_ids.append(assigned.request_id)
            self.assigned_request_id = None
            self.target_position = None
            self.status = "idle"
            self._clear_route()
        return distance

    def _advance_position(self, time_step: int) -> float:
        if self.target_position is None:
            return 0.0
        if self.active_route_path and self.active_route_duration > 0:
            previous_elapsed = self.active_route_elapsed
            self.active_route_elapsed = min(self.active_route_duration, self.active_route_elapsed + time_step)
            previous_fraction = previous_elapsed / self.active_route_duration
            current_fraction = self.active_route_elapsed / self.active_route_duration
            travelled_segment = extract_path_segment(self.active_route_path, previous_fraction, current_fraction)
            self.position = interpolate_path(self.active_route_path, current_fraction)
            if self.active_route_elapsed >= self.active_route_duration:
                self.position = self.target_position
                if travelled_segment[-1] != self.position:
                    travelled_segment.append(self.position)
            self._record_actual_path_points(travelled_segment)
            return self.active_route_distance * max(current_fraction - previous_fraction, 0.0)

        # 위경도 모드에서는 경로 탐색이 실패했을 때 목표까지 직선으로
        # 이동시키지 않습니다. 도로망이 없는 상태를 가짜 주행으로 표시하면
        # 지도와 거리/보상 지표 모두 왜곡됩니다. 격자 모드만 기존 이동 유지.
        is_grid = all(
            float(value).is_integer()
            for value in (*self.position, *self.target_position)
        )
        if not is_grid:
            return 0.0

        previous = self.position
        self.position = step_toward(self.position, self.target_position, self.speed)
        return float(distance_between(previous, self.position))

    def _set_route_plan(self, route_plan: "RoutePlan | None") -> None:
        if route_plan is None:
            self._clear_route()
            return
        path = list(route_plan.path)
        if not path:
            path = [self.position]
            if self.target_position is not None:
                path.append(self.target_position)
        if len(path) == 1 and self.target_position is not None and path[0] != self.target_position:
            path.append(self.target_position)
        self.active_route_path = [tuple(point) for point in path]
        self.active_route_distance = float(route_plan.total_distance)
        self.active_route_duration = float(route_plan.total_time)
        self.active_route_elapsed = 0.0
        self.active_route_source = route_plan.source
        self.active_route_metadata = deepcopy(route_plan.metadata)
        if len(self.active_route_path) > 1:
            self.route_history_paths.append(list(self.active_route_path))
            self.route_history_sources.append(route_plan.source)
            self.route_history_request_ids.append(self.assigned_request_id)
            self.route_history_stages.append(self.status)
            self.route_history_metadata.append(deepcopy(route_plan.metadata))
            self.actual_route_paths.append([tuple(self.active_route_path[0])])
            self.actual_route_sources.append(route_plan.source)
            self.actual_route_request_ids.append(self.assigned_request_id)
            self.actual_route_stages.append(self.status)

    def _clear_route(self) -> None:
        self.active_route_path = []
        self.active_route_distance = 0.0
        self.active_route_duration = 0.0
        self.active_route_elapsed = 0.0
        self.active_route_source = None
        self.active_route_metadata = {}

    def _record_actual_path_points(self, path_points: list[tuple[float, float]]) -> None:
        if not path_points:
            return
        normalized_points = [tuple(point) for point in path_points]
        if not self.actual_route_paths:
            self.actual_route_paths.append([normalized_points[0]])
            self.actual_route_sources.append(self.active_route_source or "unknown")

        actual_path = self.actual_route_paths[-1]
        for point in normalized_points:
            if not actual_path or actual_path[-1] != point:
                actual_path.append(point)

    def to_dict(self) -> dict:
        return {
            "vehicle_id": self.vehicle_id,
            "position": self.position,
            "capacity": self.capacity,
            "speed": self.speed,
            "available_until": self.available_until,
            "status": self.status,
            "current_load": self.current_load,
            "assigned_request_id": self.assigned_request_id,
            "target_position": self.target_position,
            "distance_travelled": self.distance_travelled,
            "completed_request_ids": list(self.completed_request_ids),
            "active_route_path": list(self.active_route_path),
            "active_route_distance": self.active_route_distance,
            "active_route_duration": self.active_route_duration,
            "active_route_elapsed": self.active_route_elapsed,
            "active_route_source": self.active_route_source,
            "active_route_metadata": deepcopy(self.active_route_metadata),
            "route_history_paths": [list(path) for path in self.route_history_paths],
            "route_history_sources": list(self.route_history_sources),
            "route_history_request_ids": list(self.route_history_request_ids),
            "route_history_stages": list(self.route_history_stages),
            "route_history_metadata": [deepcopy(metadata) for metadata in self.route_history_metadata],
            "actual_route_paths": [list(path) for path in self.actual_route_paths],
            "actual_route_sources": list(self.actual_route_sources),
            "actual_route_request_ids": list(self.actual_route_request_ids),
            "actual_route_stages": list(self.actual_route_stages),
        }
