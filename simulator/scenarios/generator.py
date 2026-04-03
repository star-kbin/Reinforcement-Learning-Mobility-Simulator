"""Helpers for random scenario generation and experiment setup."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import random
from typing import Any

from simulator.routing import (
    DEFAULT_LOCAL_ITS_GEOMETRY_DATASET,
    DEFAULT_LOCAL_ITS_GRAPH_DATASET,
    DEFAULT_LOCAL_ITS_NODE_DATASET,
    LocalITSRoadGraph,
)


DEFAULT_LATLON_START = (37.4915254, 127.0310762)
DEFAULT_LATLON_DESTINATION_FALLBACK = (37.4922254, 127.0323762)


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


def _resolve_request_snap_graph(config: dict, spatial_mode: str) -> LocalITSRoadGraph | None:
    if spatial_mode != "latlon":
        return None

    simulation_cfg = config.get("simulation", {})
    if not bool(simulation_cfg.get("snap_generated_requests_to_road_network", True)):
        return None

    configured_local_its_dataset = str(simulation_cfg.get("local_its_dataset", "")).strip()
    dataset_path = configured_local_its_dataset or (
        str(DEFAULT_LOCAL_ITS_GRAPH_DATASET) if Path(DEFAULT_LOCAL_ITS_GRAPH_DATASET).exists() else ""
    )
    if not dataset_path or not Path(dataset_path).exists():
        return None

    configured_geometry_dataset = str(simulation_cfg.get("local_its_geometry_dataset", "")).strip()
    geometry_dataset_path = configured_geometry_dataset or (
        str(DEFAULT_LOCAL_ITS_GEOMETRY_DATASET) if Path(DEFAULT_LOCAL_ITS_GEOMETRY_DATASET).exists() else ""
    )
    configured_node_dataset = str(simulation_cfg.get("local_its_node_dataset", "")).strip()
    node_dataset_path = configured_node_dataset or (
        str(DEFAULT_LOCAL_ITS_NODE_DATASET) if Path(DEFAULT_LOCAL_ITS_NODE_DATASET).exists() else ""
    )
    try:
        return LocalITSRoadGraph(
            dataset_path,
            geometry_dataset_path=geometry_dataset_path,
            node_dataset_path=node_dataset_path,
        )
    except Exception:
        return None


def _snap_point_to_road_network(
    candidate: list[float],
    road_graph: LocalITSRoadGraph | None,
    max_snap_distance_meters: float,
) -> tuple[list[float] | None, dict[str, Any]]:
    if road_graph is None:
        return list(candidate), {}

    _, snapped_point, distance_meters = road_graph.nearest_node((float(candidate[0]), float(candidate[1])))
    metadata = {
        "requested": [float(candidate[0]), float(candidate[1])],
        "snapped": [float(snapped_point[0]), float(snapped_point[1])],
        "distance_meters": round(distance_meters, 2),
        "status": "snapped" if distance_meters <= max_snap_distance_meters else "too_far",
        "strategy": "local_its_nearest_node",
    }
    if distance_meters > max_snap_distance_meters:
        return None, metadata
    return [float(snapped_point[0]), float(snapped_point[1])], metadata


def generate_random_requests(
    vehicle_positions: list[list[float]],
    request_count: int,
    spatial_mode: str,
    config: dict,
    seed: int,
    radius: float,
    fixed_max_wait_time: int | None = None,
) -> list[dict]:
    rng = random.Random(seed)
    road_graph = _resolve_request_snap_graph(config, spatial_mode)
    snap_max_distance_meters = float(config.get("simulation", {}).get("road_snap_max_distance_meters", 400.0))
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
        origin = None
        origin_snap_meta: dict[str, Any] = {}
        for _ in range(240):
            origin_candidate = find_non_overlapping_point(
                normalized_vehicle_positions,
                used_points,
                radius,
                min_gap,
                spatial_mode,
                rng,
                config,
            )
            snapped_origin, origin_snap_meta = _snap_point_to_road_network(
                origin_candidate,
                road_graph,
                snap_max_distance_meters,
            )
            if snapped_origin is None:
                continue
            if all(approx_point_distance(snapped_origin, existing, spatial_mode) >= min_gap for existing in used_points):
                origin = snapped_origin
                break
        if origin is None:
            fallback_origin = find_non_overlapping_point(
                normalized_vehicle_positions,
                used_points,
                radius,
                min_gap,
                spatial_mode,
                rng,
                config,
            )
            snapped_origin, origin_snap_meta = _snap_point_to_road_network(
                fallback_origin,
                road_graph,
                max(snap_max_distance_meters, 10_000.0),
            )
            origin = snapped_origin or fallback_origin

        destination = None
        destination_snap_meta: dict[str, Any] = {}
        destination_used_points = [*used_points, origin]
        for _ in range(240):
            destination_candidate = find_non_overlapping_point(
                normalized_vehicle_positions,
                destination_used_points,
                radius,
                trip_gap,
                spatial_mode,
                rng,
                config,
            )
            snapped_destination, destination_snap_meta = _snap_point_to_road_network(
                destination_candidate,
                road_graph,
                snap_max_distance_meters,
            )
            if snapped_destination is None:
                continue
            if (
                approx_point_distance(origin, snapped_destination, spatial_mode) >= trip_gap
                and all(
                    approx_point_distance(snapped_destination, existing, spatial_mode) >= min_gap
                    for existing in destination_used_points
                )
            ):
                destination = snapped_destination
                break
        if destination is None:
            fallback_destination = find_non_overlapping_point(
                normalized_vehicle_positions,
                destination_used_points,
                radius,
                trip_gap,
                spatial_mode,
                rng,
                config,
            )
            snapped_destination, destination_snap_meta = _snap_point_to_road_network(
                fallback_destination,
                road_graph,
                max(snap_max_distance_meters, 10_000.0),
            )
            destination = snapped_destination or fallback_destination

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
        if origin_snap_meta or destination_snap_meta:
            request_payload["metadata"]["road_snap"] = {
                "origin": origin_snap_meta,
                "destination": destination_snap_meta,
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
