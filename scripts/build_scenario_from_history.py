#!/usr/bin/env python3
"""Build a simulator scenario from historical OD records."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta
import math
from pathlib import Path
import sys

import yaml

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from simulator.local_its_defaults import (
    DEFAULT_LOCAL_ITS_AVERAGE_SPEED_KPH,
    DEFAULT_LOCAL_ITS_HOURLY_SPEED_KPH,
)


TIME_FORMAT_COMPACT = "%Y%m%d%H%M%S"
TIME_FORMAT_DASHED = "%Y-%m-%d %H:%M:%S"


def parse_timestamp(value: str) -> datetime:
    stripped = value.strip()
    if len(stripped) == 14 and stripped.isdigit():
        return datetime.strptime(stripped, TIME_FORMAT_COMPACT)
    return datetime.strptime(stripped, TIME_FORMAT_DASHED)


def geo_distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.dist(a, b)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Historical OD CSV file")
    parser.add_argument("--output", required=True, help="Output scenario YAML path")
    parser.add_argument("--config-output", default=None, help="Optional config YAML path")
    parser.add_argument("--limit", type=int, default=100, help="Maximum number of requests to export")
    parser.add_argument("--vehicle-count", type=int, default=2, help="Number of vehicles for the generated config")
    parser.add_argument("--max-wait-time", type=int, default=600, help="Default max wait time in seconds")
    parser.add_argument("--window-minutes", type=int, default=60, help="Slice duration from the first request time")
    parser.add_argument(
        "--start-time",
        default=None,
        help="Optional absolute start time. Supports YYYYmmddHHMMSS or YYYY-MM-DD HH:MM:SS",
    )
    parser.add_argument("--time-step", type=int, default=30, help="Simulation time step in seconds for lat/lon mode")
    return parser


def select_rows(path: Path, start_time: datetime | None, window_minutes: int, limit: int) -> tuple[list[dict], datetime]:
    selected: list[dict] = []
    window_start: datetime | None = start_time
    window_end: datetime | None = None

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if not row.get("승차일시") or not row.get("하차일시"):
                continue

            pickup_time = parse_timestamp(row["승차일시"])
            if window_start is None:
                window_start = pickup_time
                window_end = window_start + timedelta(minutes=window_minutes)
            elif window_end is None:
                window_end = window_start + timedelta(minutes=window_minutes)

            if pickup_time < window_start:
                continue
            if pickup_time >= window_end:
                if selected:
                    break
                continue

            selected.append(row)
            if len(selected) >= limit:
                break

    if window_start is None:
        raise ValueError("No valid rows with pickup and dropoff times were found.")
    if not selected:
        raise ValueError("No rows matched the requested time window.")
    return selected, window_start


def build_requests(rows: list[dict], window_start: datetime, max_wait_time: int) -> list[dict]:
    requests: list[dict] = []
    for index, row in enumerate(rows, start=1):
        pickup_time = parse_timestamp(row["승차일시"])
        dropoff_time = parse_timestamp(row["하차일시"])
        origin = (float(row["승차정류장위도"]), float(row["승차정류장경도"]))
        destination = (float(row["하차정류장위도"]), float(row["하차정류장경도"]))
        trip_seconds = max(int((dropoff_time - pickup_time).total_seconds()), 1)
        requests.append(
            {
                "request_id": f"R{index}",
                "created_at": int((pickup_time - window_start).total_seconds()),
                "origin": [round(origin[0], 7), round(origin[1], 7)],
                "destination": [round(destination[0], 7), round(destination[1], 7)],
                "max_wait_time": max_wait_time,
                "metadata": {
                    "pickup_at": pickup_time.strftime(TIME_FORMAT_DASHED),
                    "dropoff_at": dropoff_time.strftime(TIME_FORMAT_DASHED),
                    "pickup_station_id": row.get("승차정류장ID"),
                    "dropoff_station_id": row.get("하차정류장ID"),
                    "trip_duration_seconds": trip_seconds,
                },
            }
        )
    return requests


def estimate_vehicle_positions(rows: list[dict], vehicle_count: int) -> list[list[float]]:
    if not rows:
        return [[37.505, 126.95] for _ in range(vehicle_count)]
    first_origin = [
        round(float(rows[0]["승차정류장위도"]), 7),
        round(float(rows[0]["승차정류장경도"]), 7),
    ]
    return [list(first_origin) for _ in range(vehicle_count)]


def estimate_speed(requests: list[dict], time_step: int) -> float:
    speeds: list[float] = []
    for request in requests:
        origin = tuple(request["origin"])
        destination = tuple(request["destination"])
        trip_seconds = max(int(request["metadata"]["trip_duration_seconds"]), 1)
        distance = geo_distance(origin, destination)
        per_step = distance / max(trip_seconds / time_step, 1.0)
        if per_step > 0:
            speeds.append(per_step)
    if not speeds:
        return 0.0003
    speeds.sort()
    return round(speeds[len(speeds) // 2], 6)


def build_config(requests: list[dict], rows: list[dict], vehicle_count: int, time_step: int, window_start: datetime, window_minutes: int) -> dict:
    max_created_at = max(int(request["created_at"]) for request in requests)
    max_trip = max(int(request["metadata"]["trip_duration_seconds"]) for request in requests)
    return {
        "simulation": {
            "space_mode": "latlon",
            "routing_provider": "osm",
            "max_time": max_created_at + max_trip + (15 * 60),
            "time_step": time_step,
            "traffic_update_interval": max(300, time_step),
            "use_prediction_traffic": True,
            "random_seed": 42,
            "window_start": window_start.strftime(TIME_FORMAT_DASHED),
            "window_minutes": window_minutes,
            "demand_window_minutes": window_minutes,
            "road_snap_max_distance_meters": 400.0,
            "road_snap_dataset": "",
            "local_its_dataset": "",
            "local_its_average_speed_kph": DEFAULT_LOCAL_ITS_AVERAGE_SPEED_KPH,
            "local_its_use_hourly_speed_profile": False,
            "local_its_hourly_speed_kph": list(DEFAULT_LOCAL_ITS_HOURLY_SPEED_KPH),
            "local_its_allow_bidirectional_fallback": True,
        },
        "vehicles": {
            "count": vehicle_count,
            "capacity": 1,
            "speed": estimate_speed(requests, time_step),
            "available_time": max_created_at + max_trip + (15 * 60),
            "initial_positions": estimate_vehicle_positions(rows, vehicle_count),
        },
        "reward": {
            "wait_time": 1.0,
            "travel_distance": 0.2,
            "completed_request": 3.0,
            "violation": 5.0,
            "cancelled_request": 4.0,
        },
        "options": {
            "allow_reposition": True,
            "max_visible_requests": 10,
        },
        "visualization": {
            "map_provider": "openstreetmap",
        },
    }


def main() -> None:
    args = build_parser().parse_args()
    input_path = Path(args.input)
    start_time = parse_timestamp(args.start_time) if args.start_time else None
    selected_rows, window_start = select_rows(input_path, start_time, args.window_minutes, args.limit)
    requests = build_requests(selected_rows, window_start, args.max_wait_time)

    scenario = {
        "scenario_name": f"{input_path.stem}_latlon_sample",
        "metadata": {
            "coordinate_system": "latlon",
            "source_file": str(input_path),
            "window_start": window_start.strftime(TIME_FORMAT_DASHED),
            "window_minutes": args.window_minutes,
            "request_count": len(requests),
        },
        "requests": requests,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(scenario, handle, allow_unicode=True, sort_keys=False)

    if args.config_output:
        config = build_config(requests, selected_rows, args.vehicle_count, args.time_step, window_start, args.window_minutes)
        config_path = Path(args.config_output)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with config_path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(config, handle, allow_unicode=True, sort_keys=False)

    print(f"Wrote scenario to {output_path}")
    if args.config_output:
        print(f"Wrote config to {args.config_output}")


if __name__ == "__main__":
    main()
