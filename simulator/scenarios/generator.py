"""Helpers for random scenario generation and experiment setup."""

from __future__ import annotations

import copy
import json
import math
import random
from typing import Any

from simulator.osm_routing import OSMRoutingService, OSMRoutingError


DEFAULT_LATLON_START_LABEL = "노량진역 앞 (서울특별시 동작구)"
DEFAULT_LATLON_START = (37.513620, 126.941570)
DEFAULT_LATLON_DESTINATION_FALLBACK = (37.512920, 126.942870)


def normalize_coordinate_pair(
    coord: Any,
    spatial_mode: str,
    fallback: tuple[float, float] | list[float],
) -> list[float]:
    fallback_pair = [float(fallback[0]), float(fallback[1])]
    if not isinstance(coord, (list, tuple)) or len(coord) < 2:
        return fallback_pair if spatial_mode == "latlon" else [int(fallback_pair[0]), int(fallback_pair[1])]
    if spatial_mode == "grid":
        return [int(round(float(coord[0]))), int(round(float(coord[1])))]
    return [float(coord[0]), float(coord[1])]


def ensure_vehicle_positions(
    config: dict,
    vehicle_count: int | None = None,
    spatial_mode: str | None = None,
) -> list[list[float]]:
    resolved_spatial_mode = spatial_mode or str(config.get("simulation", {}).get("space_mode", "grid"))
    resolved_vehicle_count = max(1, int(vehicle_count if vehicle_count is not None else config.get("vehicles", {}).get("count", 1)))
    default_position = [0, 0] if resolved_spatial_mode == "grid" else [DEFAULT_LATLON_START[0], DEFAULT_LATLON_START[1]]
    configured_positions = list(config.get("vehicles", {}).get("initial_positions", []))
    positions: list[list[float]] = []
    last_position = normalize_coordinate_pair(default_position, resolved_spatial_mode, default_position)
    for index in range(resolved_vehicle_count):
        candidate = configured_positions[index] if index < len(configured_positions) else last_position
        if resolved_spatial_mode == "latlon" and index >= len(configured_positions) and index > 0:
            service = OSMRoutingService(config.get("simulation", {}))
            pool = _accessible_road_nodes(service, positions, default_request_generation_radius("latlon"))
            candidate = random.Random(int(config.get("simulation", {}).get("random_seed", 42)) + index).choice(pool)
        normalized = normalize_coordinate_pair(candidate, resolved_spatial_mode, default_position)
        positions.append(normalized)
        last_position = normalized
    config.setdefault("vehicles", {})
    config["vehicles"]["count"] = resolved_vehicle_count
    config["vehicles"]["initial_positions"] = copy.deepcopy(positions)
    return positions


def request_generation_distance_unit(spatial_mode: str) -> str:
    return "m" if spatial_mode == "latlon" else "cell"


def default_request_generation_radius(spatial_mode: str) -> float:
    return 500.0 if spatial_mode == "latlon" else 3.0


def approx_point_distance(
    point_a: list[float] | tuple[float, float],
    point_b: list[float] | tuple[float, float],
    spatial_mode: str,
) -> float:
    if spatial_mode == "grid":
        return math.hypot(float(point_a[0]) - float(point_b[0]), float(point_a[1]) - float(point_b[1]))

    lat1, lon1 = float(point_a[0]), float(point_a[1])
    lat2, lon2 = float(point_b[0]), float(point_b[1])
    mean_lat = math.radians((lat1 + lat2) / 2.0)
    meters_per_lat = 111_320.0
    meters_per_lon = max(1.0, 111_320.0 * math.cos(mean_lat))
    delta_lat = (lat1 - lat2) * meters_per_lat
    delta_lon = (lon1 - lon2) * meters_per_lon
    return math.hypot(delta_lat, delta_lon)


def sample_point_near_anchor(
    anchor: list[float] | tuple[float, float],
    radius: float,
    spatial_mode: str,
    rng: random.Random,
    config: dict,
) -> list[float]:
    sample_radius = radius * math.sqrt(rng.random()) if radius > 0 else 0.0
    angle = rng.random() * 2.0 * math.pi
    if spatial_mode == "grid":
        grid_width = max(1, int(config.get("simulation", {}).get("grid_width", 10)))
        grid_height = max(1, int(config.get("simulation", {}).get("grid_height", 10)))
        candidate_x = int(round(float(anchor[0]) + sample_radius * math.cos(angle)))
        candidate_y = int(round(float(anchor[1]) + sample_radius * math.sin(angle)))
        return [
            max(0, min(grid_width - 1, candidate_x)),
            max(0, min(grid_height - 1, candidate_y)),
        ]

    anchor_lat = float(anchor[0])
    meters_per_lat = 111_320.0
    meters_per_lon = max(1.0, 111_320.0 * math.cos(math.radians(anchor_lat)))
    delta_lat = (sample_radius * math.cos(angle)) / meters_per_lat
    delta_lon = (sample_radius * math.sin(angle)) / meters_per_lon
    return [anchor_lat + delta_lat, float(anchor[1]) + delta_lon]


def find_non_overlapping_point(
    anchors: list[list[float]],
    used_points: list[list[float]],
    radius: float,
    min_gap: float,
    spatial_mode: str,
    rng: random.Random,
    config: dict,
) -> list[float]:
    normalized_anchors = anchors or [[0.0, 0.0] if spatial_mode == "latlon" else [0, 0]]
    for _ in range(240):
        anchor = rng.choice(normalized_anchors)
        candidate = sample_point_near_anchor(anchor, radius, spatial_mode, rng, config)
        if all(approx_point_distance(candidate, existing, spatial_mode) >= min_gap for existing in used_points):
            return candidate

    fallback_anchor = normalized_anchors[len(used_points) % len(normalized_anchors)]
    if spatial_mode == "grid":
        fallback = [int(fallback_anchor[0]) + len(used_points) + 1, int(fallback_anchor[1]) + len(used_points) + 1]
        return sample_point_near_anchor(fallback, 0.0, spatial_mode, rng, config)
    return [
        float(fallback_anchor[0]) + (len(used_points) + 1) * 0.00045,
        float(fallback_anchor[1]) + (len(used_points) + 1) * 0.00045,
    ]


def random_wait_time_bounds(max_time: int, time_step: int) -> tuple[int, int]:
    min_wait = max(1, time_step, int(max_time * 0.1))
    max_wait = max(min_wait, min(max_time, max(min_wait + time_step, int(max_time * 0.45))))
    return min_wait, max_wait


def _accessible_road_nodes(service: OSMRoutingService, anchors: list[list[float]], radius: float) -> list[list[float]]:
    if not math.isfinite(radius) or radius < 0:
        raise OSMRoutingError("생성 반경은 0 이상의 유한한 수여야 합니다.")
    indices = [service.nearest_node(anchor)[0] for anchor in anchors]
    labels = service.graph["components"]
    component = labels[indices[0]]
    if any(labels[index] != component for index in indices):
        raise OSMRoutingError("차량 시작점들이 서로 왕복 가능한 OSM 도로망에 있지 않습니다. 시작 위치를 수정해 주세요.")
    pool = [list(service.graph["nodes"][index]) for index in service.graph["snap_nodes"]
            if labels[index] == component and any(
                approx_point_distance(service.graph["nodes"][index], anchor, "latlon") <= radius for anchor in anchors)]
    if len(pool) < 2:
        raise OSMRoutingError("생성 반경 안에 서로 접근 가능한 도로 지점이 부족합니다. 반경을 늘리거나 시작 위치를 수정해 주세요.")
    return pool


def _generate_road_requests(vehicle_positions, request_count, config, seed, radius, fixed_max_wait_time):
    service = OSMRoutingService(config.get("simulation", {}))
    anchors = vehicle_positions or [list(DEFAULT_LATLON_START)]
    pool = _accessible_road_nodes(service, anchors, radius)
    rng = random.Random(seed)
    max_time = max(1, int(config.get("simulation", {}).get("max_time", 1)))
    time_step = max(1, int(config.get("simulation", {}).get("time_step", 1)))
    wait_min, wait_max = random_wait_time_bounds(max_time, time_step)
    created_times = sorted(rng.sample(range(max_time), request_count)) if request_count <= max_time else sorted(
        rng.randrange(max_time) for _ in range(request_count))
    used = []
    requests = []
    min_gap = max(35., radius * .18)
    for index in range(request_count):
        # Crowding may relax spacing, but never road access or the generation radius.
        origins = [p for p in pool if all(approx_point_distance(p, old, "latlon") >= min_gap for old in used)]
        origin = rng.choice(origins or pool)
        destinations = [p for p in pool if p != origin]
        spaced = [p for p in destinations if approx_point_distance(p, origin, "latlon") >= max(60., min_gap * 1.5)
                  and all(approx_point_distance(p, old, "latlon") >= min_gap for old in used)]
        destination = rng.choice(spaced or destinations)
        # Verify directed routes as well as the shared-component guarantee.
        service.plan_route(origin, destination, 1, time_step)
        for anchor in anchors:
            service.plan_route(anchor, origin, 1, time_step)
            service.plan_route(destination, anchor, 1, time_step)
        used.extend([origin, destination])
        road_snap = {role: {"requested": list(point), "snapped": list(point), "distance_meters": 0.,
                            "status": "on_road", "strategy": "osm_accessible_node"}
                     for role, point in (("origin", origin), ("destination", destination))}
        requests.append({"request_id": f"R{index+1}", "created_at": created_times[index],
                         "origin": list(origin), "destination": list(destination),
                         "max_wait_time": int(fixed_max_wait_time) if fixed_max_wait_time is not None else rng.randint(wait_min, wait_max),
                         "metadata": {"generated": True, "anchor_vehicle_index": index % len(anchors) + 1,
                                      "generation_seed": seed, "generation_radius": radius,
                                      "road_snap": road_snap, "route_accessibility_validated": True,
                                      "spacing_relaxed": not origins or not spaced}})
    return requests


def generate_random_requests(
    vehicle_positions: list[list[float]],
    request_count: int,
    spatial_mode: str,
    config: dict,
    seed: int,
    radius: float,
    fixed_max_wait_time: int | None = None,
) -> list[dict]:
    if request_count <= 0:
        return []
    if spatial_mode == "latlon":
        return _generate_road_requests(vehicle_positions, request_count, config, seed, radius, fixed_max_wait_time)
    rng = random.Random(seed)
    normalized_vehicle_positions = [
        normalize_coordinate_pair(
            position,
            spatial_mode,
            (0, 0) if spatial_mode == "grid" else DEFAULT_LATLON_START,
        )
        for position in (
            vehicle_positions
            or [[0, 0] if spatial_mode == "grid" else [DEFAULT_LATLON_START[0], DEFAULT_LATLON_START[1]]]
        )
    ]
    max_time = max(1, int(config.get("simulation", {}).get("max_time", 1)))
    time_step = max(1, int(config.get("simulation", {}).get("time_step", 1)))
    wait_min, wait_max = random_wait_time_bounds(max_time, time_step)
    min_gap = max(1.0, radius * 0.18) if spatial_mode == "grid" else max(35.0, radius * 0.18)
    trip_gap = max(min_gap * 1.5, 2.0 if spatial_mode == "grid" else 60.0)

    if request_count <= max_time:
        created_times = sorted(rng.sample(range(max_time), request_count))
    else:
        created_times = sorted(rng.randint(0, max_time - 1) for _ in range(request_count))

    used_points: list[list[float]] = []
    requests: list[dict] = []
    for index in range(request_count):
        origin = find_non_overlapping_point(normalized_vehicle_positions, used_points, radius, min_gap, spatial_mode, rng, config)
        destination = find_non_overlapping_point(normalized_vehicle_positions, [*used_points, origin], radius, trip_gap, spatial_mode, rng, config)
        used_points.extend([origin, destination])
        max_wait_time = (
            int(fixed_max_wait_time)
            if fixed_max_wait_time is not None
            else rng.randint(wait_min, wait_max)
        )
        request_payload = {
            "request_id": f"R{index + 1}",
            "created_at": int(created_times[index]),
            "origin": origin if spatial_mode == "latlon" else [int(origin[0]), int(origin[1])],
            "destination": destination if spatial_mode == "latlon" else [int(destination[0]), int(destination[1])],
            "max_wait_time": int(max_wait_time),
            "metadata": {
                "generated": True,
                "anchor_vehicle_index": int(index % max(1, len(normalized_vehicle_positions))) + 1,
                "generation_seed": int(seed),
                "generation_radius": float(radius),
            },
        }
        requests.append(request_payload)

    return requests


def build_random_request_generation_signature(
    vehicle_positions: list[list[float]],
    request_count: int,
    spatial_mode: str,
    radius: float,
    seed: int,
    config: dict,
) -> str:
    return json.dumps(
        {
            "vehicle_positions": vehicle_positions,
            "request_count": int(request_count),
            "spatial_mode": spatial_mode,
            "radius": float(radius),
            "seed": int(seed),
            "max_time": int(config.get("simulation", {}).get("max_time", 0)),
            "time_step": int(config.get("simulation", {}).get("time_step", 1)),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
