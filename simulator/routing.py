"""Routing services for simulated travel."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import csv
import heapq
import json
import math
import os
from pathlib import Path
from urllib.parse import urlencode
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from simulator.entities.position import distance_between
from simulator.local_its_defaults import DEFAULT_LOCAL_ITS_AVERAGE_SPEED_KPH

try:
    import numpy as np
except Exception:  # pragma: no cover - optional dependency fallback
    np = None


TMAP_LIVE_ROUTES_URL = (
    "https://apis.openapi.sk.com/tmap/routes?version=1"
    "&format=json&sort=index&totalValue=2"
)
DEFAULT_LOCAL_ITS_GRAPH_DATASET = (
    Path(__file__).resolve().parents[2] / "TESTAM" / "etc1" / "kor_nodelink_info_2023_utf8.csv"
)
DEFAULT_LOCAL_ITS_GEOMETRY_DATASET = (
    Path(__file__).resolve().parents[2] / "TESTAM" / "etc1" / "kor_linkID_info_20250124_utf.csv"
)
DEFAULT_LOCAL_ITS_NODE_DATASET = (
    Path(__file__).resolve().parents[2] / "TESTAM" / "etc1" / "seoul_nodeID_info.csv"
)


def _geo_distance_meters(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = math.radians(float(a[0])), math.radians(float(a[1]))
    lat2, lon2 = math.radians(float(b[0])), math.radians(float(b[1]))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    x = dlon * math.cos((lat1 + lat2) / 2.0)
    y = dlat
    return 6371000.0 * math.sqrt(x * x + y * y)


def _epsg3857_to_wgs84(point: tuple[float, float]) -> tuple[float, float]:
    x, y = float(point[0]), float(point[1])
    lon = (x / 20037508.34) * 180.0
    lat = (y / 20037508.34) * 180.0
    lat = 180.0 / math.pi * (2.0 * math.atan(math.exp(lat * math.pi / 180.0)) - math.pi / 2.0)
    return (lat, lon)


def _dedupe_path(path: list[tuple[float, float]]) -> list[tuple[float, float]]:
    deduped: list[tuple[float, float]] = []
    for point in path:
        normalized = (float(point[0]), float(point[1]))
        if not deduped or deduped[-1] != normalized:
            deduped.append(normalized)
    return deduped


def _polyline_distance_meters(path: list[tuple[float, float]]) -> float:
    if len(path) < 2:
        return 0.0
    return float(sum(_geo_distance_meters(a, b) for a, b in zip(path, path[1:])))


def _parse_linestring_wkt(geometry_wkt: str) -> list[tuple[float, float]]:
    text = str(geometry_wkt or "").strip()
    if not text:
        return []
    upper = text.upper()
    if not upper.startswith("LINESTRING"):
        return []
    start = text.find("(")
    end = text.rfind(")")
    if start < 0 or end <= start:
        return []
    coordinates: list[tuple[float, float]] = []
    for chunk in text[start + 1:end].split(","):
        parts = chunk.strip().split()
        if len(parts) < 2:
            continue
        try:
            x = float(parts[0])
            y = float(parts[1])
        except ValueError:
            continue
        coordinates.append((x, y))
    return coordinates


def _projected_endpoint_distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.dist((float(a[0]), float(a[1])), (float(b[0]), float(b[1])))


def _normalize_hourly_speed_profile(profile: Any, fallback_speed_kph: float) -> list[float]:
    fallback = max(float(fallback_speed_kph), 1.0)
    if isinstance(profile, dict):
        normalized = [fallback] * 24
        for hour in range(24):
            raw_value = profile.get(str(hour), profile.get(hour, fallback))
            try:
                normalized[hour] = max(float(raw_value), 1.0)
            except (TypeError, ValueError):
                normalized[hour] = fallback
        return normalized

    if isinstance(profile, (list, tuple)):
        normalized = []
        for hour in range(24):
            raw_value = profile[hour] if hour < len(profile) else fallback
            try:
                normalized.append(max(float(raw_value), 1.0))
            except (TypeError, ValueError):
                normalized.append(fallback)
        return normalized

    return [fallback] * 24


class LocalITSAffineTransformer:
    _cache: dict[tuple[str, str], "LocalITSAffineTransformer | None"] = {}

    def __init__(self, lon_coefficients: tuple[float, float, float], lat_coefficients: tuple[float, float, float]) -> None:
        self.lon_coefficients = lon_coefficients
        self.lat_coefficients = lat_coefficients

    @classmethod
    def load(
        cls,
        dataset_path: str,
        node_dataset_path: str,
        required_node_ids: set[str],
    ) -> "LocalITSAffineTransformer | None":
        cache_key = (dataset_path, node_dataset_path)
        cached = cls._cache.get(cache_key)
        if cached is not None:
            return cached

        if np is None:
            cls._cache[cache_key] = None
            return None

        node_path = Path(node_dataset_path)
        if not node_path.exists():
            cls._cache[cache_key] = None
            return None

        control_pairs: list[tuple[float, float, float, float]] = []
        with node_path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                try:
                    tm_x = float(row["TM_X"])
                    tm_y = float(row["TM_Y"]) + 100_000.0
                    lon = float(row["X"])
                    lat = float(row["Y"])
                except (KeyError, TypeError, ValueError):
                    continue
                control_pairs.append((tm_x, tm_y, lon, lat))

        if len(control_pairs) < 3:
            cls._cache[cache_key] = None
            return None

        matrix = np.array([[x, y, 1.0] for x, y, _, _ in control_pairs], dtype=float)
        longitudes = np.array([lon for _, _, lon, _ in control_pairs], dtype=float)
        latitudes = np.array([lat for _, _, _, lat in control_pairs], dtype=float)
        lon_coeff = tuple(float(value) for value in np.linalg.lstsq(matrix, longitudes, rcond=None)[0])
        lat_coeff = tuple(float(value) for value in np.linalg.lstsq(matrix, latitudes, rcond=None)[0])
        transformer = cls(lon_coeff, lat_coeff)
        cls._cache[cache_key] = transformer
        return transformer

    def to_wgs84(self, point: tuple[float, float]) -> tuple[float, float]:
        x = float(point[0])
        y = float(point[1])
        lon = self.lon_coefficients[0] * x + self.lon_coefficients[1] * y + self.lon_coefficients[2]
        lat = self.lat_coefficients[0] * x + self.lat_coefficients[1] * y + self.lat_coefficients[2]
        return (lat, lon)


class LocalITSNodeRepository:
    _cache: dict[str, dict[str, tuple[float, float]]] = {}

    @classmethod
    def load(cls, dataset_path: str) -> dict[str, tuple[float, float]]:
        cached = cls._cache.get(dataset_path)
        if cached is not None:
            return cached

        path = Path(dataset_path)
        if not path.exists():
            cls._cache[dataset_path] = {}
            return {}

        coordinates_by_node: dict[str, tuple[float, float]] = {}
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                node_id = str(row.get("NODE_ID") or row.get("\ufeffNODE_ID") or "").strip()
                if not node_id:
                    continue
                try:
                    lon = float(row["X"])
                    lat = float(row["Y"])
                except (KeyError, TypeError, ValueError):
                    continue
                coordinates_by_node[node_id] = (lat, lon)

        cls._cache[dataset_path] = coordinates_by_node
        return coordinates_by_node


class LocalITSGeometryRepository:
    _cache: dict[tuple[str, str, str], dict[str, list[tuple[float, float]]]] = {}

    def __init__(
        self,
        dataset_path: str,
        geometry_dataset_path: str,
        node_dataset_path: str,
        link_records: list[dict[str, Any]],
    ) -> None:
        self.dataset_path = dataset_path
        self.geometry_dataset_path = geometry_dataset_path
        self.node_dataset_path = node_dataset_path
        self.link_records = link_records
        self.geometries_by_link = self._load(dataset_path, geometry_dataset_path, node_dataset_path, link_records)

    @classmethod
    def _load(
        cls,
        dataset_path: str,
        geometry_dataset_path: str,
        node_dataset_path: str,
        link_records: list[dict[str, Any]],
    ) -> dict[str, list[tuple[float, float]]]:
        cache_key = (dataset_path, geometry_dataset_path, node_dataset_path)
        cached = cls._cache.get(cache_key)
        if cached is not None:
            return cached

        geometry_path = Path(geometry_dataset_path)
        if not geometry_path.exists():
            cls._cache[cache_key] = {}
            return {}

        required_link_ids = {str(record["link_id"]) for record in link_records}
        required_node_ids = {
            str(record["from_node"])
            for record in link_records
        } | {
            str(record["to_node"])
            for record in link_records
        }
        transformer = LocalITSAffineTransformer.load(dataset_path, node_dataset_path, required_node_ids)
        if transformer is None:
            cls._cache[cache_key] = {}
            return {}

        exact_rows: dict[str, list[dict[str, Any]]] = {}
        split_rows: dict[str, list[dict[str, Any]]] = {}
        with geometry_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                link_id = str(row.get("LINK_ID") or "").strip()
                history_ref = str(row.get("HISTREMARK") or "").strip()
                target_link_id = ""
                if history_ref and history_ref in required_link_ids:
                    target_link_id = history_ref
                elif link_id in required_link_ids:
                    target_link_id = link_id
                if not target_link_id:
                    continue
                projected_path = _parse_linestring_wkt(str(row.get("geometry") or ""))
                if len(projected_path) < 2:
                    continue
                transformed_path = _dedupe_path([transformer.to_wgs84(point) for point in projected_path])
                if len(transformed_path) < 2:
                    continue
                row_payload = {
                    "link_id": link_id,
                    "history_ref": history_ref,
                    "from_node": str(row.get("F_NODE") or "").strip(),
                    "to_node": str(row.get("T_NODE") or "").strip(),
                    "projected_path": projected_path,
                    "path": transformed_path,
                }
                if history_ref and history_ref in required_link_ids:
                    split_rows.setdefault(history_ref, []).append(row_payload)
                elif link_id in required_link_ids:
                    exact_rows.setdefault(link_id, []).append(row_payload)

        geometries: dict[str, list[tuple[float, float]]] = {}
        for record in link_records:
            link_id = str(record["link_id"])
            start = tuple(record["start"])
            end = tuple(record["end"])
            if split_rows.get(link_id):
                path = cls._assemble_split_link_geometry(split_rows[link_id], start, end)
            elif exact_rows.get(link_id):
                path = cls._pick_exact_link_geometry(exact_rows[link_id], start, end)
            else:
                path = []
            if not path:
                continue
            geometries[link_id] = cls._merge_original_endpoints(path, start, end)

        cls._cache[cache_key] = geometries
        return geometries

    @classmethod
    def _pick_exact_link_geometry(
        cls,
        rows: list[dict[str, Any]],
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> list[tuple[float, float]]:
        best_path: list[tuple[float, float]] = []
        best_score = float("inf")
        for row in rows:
            path = cls._orient_path(row["path"], start, end)
            if len(path) < 2:
                continue
            score = _geo_distance_meters(start, path[0]) + _geo_distance_meters(end, path[-1])
            if score < best_score:
                best_score = score
                best_path = path
        return best_path

    @classmethod
    def _assemble_split_link_geometry(
        cls,
        rows: list[dict[str, Any]],
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> list[tuple[float, float]]:
        if len(rows) == 1:
            return cls._orient_path(rows[0]["path"], start, end)

        outgoing: dict[str, list[dict[str, Any]]] = {}
        indegree: dict[str, int] = {}
        outdegree: dict[str, int] = {}
        for row in rows:
            from_node = str(row["from_node"])
            to_node = str(row["to_node"])
            outgoing.setdefault(from_node, []).append(row)
            indegree[to_node] = indegree.get(to_node, 0) + 1
            outdegree[from_node] = outdegree.get(from_node, 0) + 1
            indegree.setdefault(from_node, indegree.get(from_node, 0))
            outdegree.setdefault(to_node, outdegree.get(to_node, 0))

        def _endpoint_distance(row: dict[str, Any], reference: tuple[float, float], use_start: bool) -> float:
            point = row["path"][0] if use_start else row["path"][-1]
            return _geo_distance_meters(reference, point)

        start_candidates = [
            node_id
            for node_id in outgoing.keys()
            if outdegree.get(node_id, 0) > indegree.get(node_id, 0)
        ] or list(outgoing.keys())
        start_node = min(
            start_candidates,
            key=lambda node_id: min(
                (_endpoint_distance(row, start, True) for row in outgoing.get(node_id, [])),
                default=float("inf"),
            ),
        )

        ordered_rows: list[dict[str, Any]] = []
        used_links: set[str] = set()
        current_node = start_node
        while True:
            candidates = [
                row
                for row in outgoing.get(current_node, [])
                if str(row["link_id"]) not in used_links
            ]
            if not candidates:
                break
            chosen = min(
                candidates,
                key=lambda row: (
                    _endpoint_distance(row, end, False),
                    _polyline_distance_meters(row["path"]),
                ),
            )
            ordered_rows.append(chosen)
            used_links.add(str(chosen["link_id"]))
            current_node = str(chosen["to_node"])

        if len(ordered_rows) != len(rows):
            remaining = [row for row in rows if str(row["link_id"]) not in used_links]
            remaining.sort(key=lambda row: _endpoint_distance(row, end, False))
            ordered_rows.extend(remaining)

        merged: list[tuple[float, float]] = []
        for row in ordered_rows:
            segment_path = list(row["path"])
            if not merged:
                merged.extend(segment_path)
                continue
            if _geo_distance_meters(merged[-1], segment_path[0]) <= _geo_distance_meters(merged[-1], segment_path[-1]):
                oriented = segment_path
            else:
                oriented = list(reversed(segment_path))
            if merged[-1] == oriented[0]:
                merged.extend(oriented[1:])
            else:
                merged.extend(oriented)
        return cls._orient_path(merged, start, end)

    @staticmethod
    def _orient_path(
        path: list[tuple[float, float]],
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> list[tuple[float, float]]:
        if len(path) < 2:
            return list(path)
        forward_score = _geo_distance_meters(start, path[0]) + _geo_distance_meters(end, path[-1])
        reverse_score = _geo_distance_meters(start, path[-1]) + _geo_distance_meters(end, path[0])
        oriented = path if forward_score <= reverse_score else list(reversed(path))
        return _dedupe_path(oriented)

    @staticmethod
    def _merge_original_endpoints(
        path: list[tuple[float, float]],
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> list[tuple[float, float]]:
        merged = list(path)
        if not merged:
            return [tuple(start), tuple(end)]
        if _geo_distance_meters(start, merged[0]) <= 15.0:
            merged[0] = tuple(start)
        else:
            merged.insert(0, tuple(start))
        if _geo_distance_meters(end, merged[-1]) <= 15.0:
            merged[-1] = tuple(end)
        else:
            merged.append(tuple(end))
        return _dedupe_path(merged)


class NearestRoadSnapper:
    _cache: dict[str, list[tuple[float, float]]] = {}

    def __init__(self, dataset_path: str, max_distance_meters: float = 400.0) -> None:
        self.dataset_path = dataset_path
        self.max_distance_meters = float(max_distance_meters)
        self.points = self._load_points(dataset_path)

    @classmethod
    def _load_points(cls, dataset_path: str) -> list[tuple[float, float]]:
        cached = cls._cache.get(dataset_path)
        if cached is not None:
            return cached

        path = Path(dataset_path)
        if not path.exists():
            cls._cache[dataset_path] = []
            return []

        points: list[tuple[float, float]] = []
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            fieldnames = [field or "" for field in (reader.fieldnames or [])]
            explicit_pairs = []
            if {"F_Y", "F_X", "T_Y", "T_X"}.issubset(set(fieldnames)):
                explicit_pairs = [("F_Y", "F_X"), ("T_Y", "T_X")]
            for row in reader:
                if explicit_pairs:
                    for lat_key, lon_key in explicit_pairs:
                        try:
                            lat = float(row[lat_key])
                            lon = float(row[lon_key])
                        except (TypeError, ValueError, KeyError):
                            continue
                        points.append((lat, lon))
                    continue
                lat = None
                lon = None
                for key, value in row.items():
                    if value in (None, ""):
                        continue
                    lowered = key.lower()
                    try:
                        numeric = float(value)
                    except ValueError:
                        continue
                    if lat is None and ("lat" in lowered or "위도" in key):
                        lat = numeric
                    elif lon is None and ("lon" in lowered or "경도" in key):
                        lon = numeric
                if lat is None and lon is None:
                    try:
                        candidate_lon = float(row.get("X", ""))
                        candidate_lat = float(row.get("Y", ""))
                    except (TypeError, ValueError):
                        candidate_lon = None
                        candidate_lat = None
                    if (
                        candidate_lat is not None
                        and candidate_lon is not None
                        and -90.0 <= candidate_lat <= 90.0
                        and -180.0 <= candidate_lon <= 180.0
                    ):
                        lat = candidate_lat
                        lon = candidate_lon
                if lat is not None and lon is not None:
                    points.append((lat, lon))

        deduped = list(dict.fromkeys((round(lat, 7), round(lon, 7)) for lat, lon in points))
        cls._cache[dataset_path] = deduped
        return deduped

    def snap(self, point: tuple[float, float]) -> tuple[tuple[float, float], dict[str, Any]]:
        if not self.points:
            return point, {"status": "unavailable"}

        nearest = min(
            self.points,
            key=lambda candidate: _geo_distance_meters(point, candidate),
        )
        distance_meters = _geo_distance_meters(point, nearest)
        metadata = {
            "status": "snapped" if distance_meters <= self.max_distance_meters else "too_far",
            "original": [float(point[0]), float(point[1])],
            "snapped": [float(nearest[0]), float(nearest[1])],
            "distance_meters": round(distance_meters, 2),
            "dataset_path": self.dataset_path,
        }
        if distance_meters > self.max_distance_meters:
            return point, metadata
        return nearest, metadata


@dataclass
class RoutePlan:
    path: list[tuple[float, float]]
    total_distance: float
    total_time: float
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseRoutingService:
    def plan_route(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        speed: float,
        time_step: int,
        reference_time: datetime | None = None,
    ) -> RoutePlan:
        raise NotImplementedError


class StraightLineRoutingService(BaseRoutingService):
    def __init__(self, provider_name: str = "simple") -> None:
        self.provider_name = provider_name

    def plan_route(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        speed: float,
        time_step: int,
        reference_time: datetime | None = None,
    ) -> RoutePlan:
        distance = float(distance_between(start, end))
        per_step_speed = max(float(speed), 1e-9)
        total_time = 0.0 if distance == 0 else max((distance / per_step_speed) * time_step, float(time_step))
        return RoutePlan(
            path=[tuple(start), tuple(end)],
            total_distance=distance,
            total_time=total_time,
            source=self.provider_name,
        )


class LocalITSRoadGraph:
    _cache: dict[
        str,
        tuple[
            dict[str, tuple[float, float]],
            dict[str, list[tuple[str, float]]],
            dict[str, list[tuple[str, float]]],
            dict[tuple[str, str], list[tuple[float, float]]],
        ],
    ] = {}

    def __init__(self, dataset_path: str, geometry_dataset_path: str = "", node_dataset_path: str = "") -> None:
        self.dataset_path = dataset_path
        self.geometry_dataset_path = geometry_dataset_path
        self.node_dataset_path = node_dataset_path
        self.nodes, self.adjacency, self.undirected_adjacency, self.edge_geometries = self._load_graph(
            dataset_path,
            geometry_dataset_path,
            node_dataset_path,
        )

    @classmethod
    def _load_graph(
        cls,
        dataset_path: str,
        geometry_dataset_path: str,
        node_dataset_path: str,
    ) -> tuple[
        dict[str, tuple[float, float]],
        dict[str, list[tuple[str, float]]],
        dict[str, list[tuple[str, float]]],
        dict[tuple[str, str], list[tuple[float, float]]],
    ]:
        cache_key = json.dumps(
            {
                "dataset_path": dataset_path,
                "geometry_dataset_path": geometry_dataset_path,
                "node_dataset_path": node_dataset_path,
            },
            sort_keys=True,
        )
        cached = cls._cache.get(cache_key)
        if cached is not None:
            return cached

        path = Path(dataset_path)
        if not path.exists():
            empty = ({}, {}, {}, {})
            cls._cache[cache_key] = empty
            return empty

        nodes: dict[str, tuple[float, float]] = {}
        adjacency: dict[str, list[tuple[str, float]]] = {}
        undirected: dict[str, list[tuple[str, float]]] = {}
        link_records: list[dict[str, Any]] = []
        node_lookup = LocalITSNodeRepository.load(node_dataset_path) if node_dataset_path else {}
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            fieldnames = set(reader.fieldnames or [])
            has_embedded_coordinates = {"F_X", "F_Y", "T_X", "T_Y"}.issubset(fieldnames)
            for row in reader:
                try:
                    link_id = str(row["LINK_ID"]).strip()
                    from_node = str(row["F_NODE"]).strip()
                    to_node = str(row["T_NODE"]).strip()
                    road_length_m = float(row.get("ROAD_len(m)", 0.0) or 0.0)
                except (KeyError, TypeError, ValueError):
                    continue

                if not from_node or not to_node:
                    continue

                if has_embedded_coordinates:
                    try:
                        from_lon = float(row["F_X"])
                        from_lat = float(row["F_Y"])
                        to_lon = float(row["T_X"])
                        to_lat = float(row["T_Y"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    start_position = (from_lat, from_lon)
                    end_position = (to_lat, to_lon)
                else:
                    start_position = node_lookup.get(from_node)
                    end_position = node_lookup.get(to_node)
                    if start_position is None or end_position is None:
                        continue
                    from_lat, from_lon = start_position
                    to_lat, to_lon = end_position

                if road_length_m <= 0:
                    road_length_m = max(_geo_distance_meters((from_lat, from_lon), (to_lat, to_lon)), 1.0)

                nodes[from_node] = (from_lat, from_lon)
                nodes[to_node] = (to_lat, to_lon)
                adjacency.setdefault(from_node, []).append((to_node, road_length_m))
                undirected.setdefault(from_node, []).append((to_node, road_length_m))
                undirected.setdefault(to_node, []).append((from_node, road_length_m))
                link_records.append(
                    {
                        "link_id": link_id,
                        "from_node": from_node,
                        "to_node": to_node,
                        "start": start_position,
                        "end": end_position,
                        "road_length_m": road_length_m,
                    }
                )

        geometry_repository = LocalITSGeometryRepository(
            dataset_path,
            geometry_dataset_path,
            node_dataset_path,
            link_records,
        )
        edge_geometries: dict[tuple[str, str], list[tuple[float, float]]] = {}
        for record in link_records:
            geometry_path = geometry_repository.geometries_by_link.get(str(record["link_id"]))
            if geometry_path:
                edge_geometries[(str(record["from_node"]), str(record["to_node"]))] = list(geometry_path)

        cached_value = (nodes, adjacency, undirected, edge_geometries)
        cls._cache[cache_key] = cached_value
        return cached_value

    def nearest_node(self, point: tuple[float, float]) -> tuple[str, tuple[float, float], float]:
        if not self.nodes:
            raise ValueError("Local ITS graph does not contain any nodes.")
        nearest_node_id, nearest_point = min(
            self.nodes.items(),
            key=lambda item: _geo_distance_meters(point, item[1]),
        )
        return nearest_node_id, nearest_point, _geo_distance_meters(point, nearest_point)

    def shortest_path(
        self,
        start_node: str,
        end_node: str,
        use_undirected: bool = False,
    ) -> tuple[list[str] | None, float]:
        adjacency = self.undirected_adjacency if use_undirected else self.adjacency
        if start_node == end_node:
            return [start_node], 0.0
        if start_node not in adjacency:
            return None, 0.0

        heap: list[tuple[float, str]] = [(0.0, start_node)]
        distances: dict[str, float] = {start_node: 0.0}
        previous: dict[str, str] = {}
        visited: set[str] = set()

        while heap:
            current_distance, node = heapq.heappop(heap)
            if node in visited:
                continue
            visited.add(node)
            if node == end_node:
                break
            for neighbor, edge_weight in adjacency.get(node, []):
                new_distance = current_distance + float(edge_weight)
                if new_distance >= distances.get(neighbor, float("inf")):
                    continue
                distances[neighbor] = new_distance
                previous[neighbor] = node
                heapq.heappush(heap, (new_distance, neighbor))

        if end_node not in distances:
            return None, 0.0

        path = [end_node]
        while path[-1] != start_node:
            parent = previous.get(path[-1])
            if parent is None:
                return None, 0.0
            path.append(parent)
        path.reverse()
        return path, distances[end_node]

    def build_path_coordinates(
        self,
        node_path: list[str],
        use_undirected: bool = False,
    ) -> list[tuple[float, float]]:
        if not node_path:
            return []
        if len(node_path) == 1:
            return [self.nodes[node_path[0]]]

        merged: list[tuple[float, float]] = []
        for start_node, end_node in zip(node_path, node_path[1:]):
            segment = self.edge_geometries.get((start_node, end_node))
            if segment is None and use_undirected:
                reverse_segment = self.edge_geometries.get((end_node, start_node))
                if reverse_segment is not None:
                    segment = list(reversed(reverse_segment))
            if segment is None:
                segment = [self.nodes[start_node], self.nodes[end_node]]
            if not merged:
                merged.extend(segment)
            elif merged[-1] == segment[0]:
                merged.extend(segment[1:])
            elif merged[-1] == segment[-1]:
                reversed_segment = list(reversed(segment))
                merged.extend(reversed_segment[1:])
            else:
                merged.extend(segment)
        return _dedupe_path(merged)


class LocalITSRoutingService(BaseRoutingService):
    def __init__(
        self,
        simulation_cfg: dict[str, Any],
        dataset_path: str,
        geometry_dataset_path: str = "",
        node_dataset_path: str = "",
        average_speed_kph: float = DEFAULT_LOCAL_ITS_AVERAGE_SPEED_KPH,
        allow_bidirectional_fallback: bool = True,
    ) -> None:
        self.dataset_path = dataset_path
        self.geometry_dataset_path = geometry_dataset_path
        self.node_dataset_path = node_dataset_path
        self.average_speed_kph = max(float(average_speed_kph), 1.0)
        self.use_hourly_speed_profile = bool(simulation_cfg.get("local_its_use_hourly_speed_profile", False))
        self.hourly_speed_profile = _normalize_hourly_speed_profile(
            simulation_cfg.get("local_its_hourly_speed_kph", []),
            self.average_speed_kph,
        )
        self.allow_bidirectional_fallback = bool(allow_bidirectional_fallback)
        self.graph = LocalITSRoadGraph(dataset_path, geometry_dataset_path=geometry_dataset_path, node_dataset_path=node_dataset_path)
        self.fallback = StraightLineRoutingService(provider_name="local_its_fallback")
        self.cache: dict[tuple[float, float, float, float, str], RoutePlan] = {}

    def _cache_key(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        reference_time: datetime | None,
    ) -> tuple[float, float, float, float, str]:
        return (
            round(start[0], 6),
            round(start[1], 6),
            round(end[0], 6),
            round(end[1], 6),
            "" if reference_time is None else reference_time.strftime("%Y-%m-%dT%H:%M"),
        )

    def plan_route(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        speed: float,
        time_step: int,
        reference_time: datetime | None = None,
    ) -> RoutePlan:
        key = self._cache_key(start, end, reference_time)
        if key in self.cache:
            return self.cache[key]

        if start == end:
            applied_speed_kph, speed_hour = self._resolve_speed_kph(reference_time)
            plan = RoutePlan(
                path=[tuple(start)],
                total_distance=0.0,
                total_time=0.0,
                source="local_its",
                metadata={
                    "dataset_path": self.dataset_path,
                    "average_speed_kph": applied_speed_kph,
                    "base_average_speed_kph": self.average_speed_kph,
                    "use_hourly_speed_profile": self.use_hourly_speed_profile,
                    "speed_profile_hour": speed_hour,
                    "coordinate_mode": "same_point",
                },
            )
            self.cache[key] = plan
            return plan

        try:
            plan = self._build_network_plan(start, end, time_step, reference_time=reference_time)
        except ValueError as exc:
            plan = self.fallback.plan_route(start, end, speed, time_step, reference_time=reference_time)
            plan.metadata["fallback_reason"] = "local_its_route_failed"
            plan.metadata["dataset_path"] = self.dataset_path
            plan.metadata["error"] = str(exc)
        self.cache[key] = plan
        return plan

    def _build_network_plan(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        time_step: int,
        reference_time: datetime | None = None,
    ) -> RoutePlan:
        if not self.graph.nodes:
            raise ValueError("Local ITS graph is empty.")

        start_node, start_snapped, start_snap_m = self.graph.nearest_node(start)
        end_node, end_snapped, end_snap_m = self.graph.nearest_node(end)
        node_path, network_distance_m = self.graph.shortest_path(start_node, end_node, use_undirected=False)
        used_bidirectional_fallback = False
        if node_path is None and self.allow_bidirectional_fallback:
            node_path, network_distance_m = self.graph.shortest_path(start_node, end_node, use_undirected=True)
            used_bidirectional_fallback = True
        if node_path is None:
            raise ValueError("Local ITS graph could not connect the snapped nodes.")

        coordinate_path = self.graph.build_path_coordinates(node_path, use_undirected=used_bidirectional_fallback)
        merged_path = self._merge_endpoints(coordinate_path, start, end)
        connector_distance_m = 0.0
        if start != start_snapped:
            connector_distance_m += _geo_distance_meters(start, start_snapped)
        if end != end_snapped:
            connector_distance_m += _geo_distance_meters(end_snapped, end)
        total_distance_m = float(network_distance_m) + connector_distance_m
        applied_speed_kph, speed_hour = self._resolve_speed_kph(reference_time)
        total_time_seconds = max(total_distance_m / max(applied_speed_kph * 1000.0 / 3600.0, 1e-6), float(time_step))

        coordinate_distance = float(sum(distance_between(a, b) for a, b in zip(merged_path, merged_path[1:])))
        return RoutePlan(
            path=merged_path,
            total_distance=coordinate_distance,
            total_time=total_time_seconds,
            source="local_its",
            metadata={
                "dataset_path": self.dataset_path,
                "geometry_dataset_path": self.geometry_dataset_path,
                "node_dataset_path": self.node_dataset_path,
                "average_speed_kph": applied_speed_kph,
                "base_average_speed_kph": self.average_speed_kph,
                "use_hourly_speed_profile": self.use_hourly_speed_profile,
                "speed_profile_hour": speed_hour,
                "coordinate_mode": "local_its_network",
                "network_distance_meters": round(total_distance_m, 2),
                "node_path_length": len(node_path),
                "path_point_count": len(merged_path),
                "used_bidirectional_fallback": used_bidirectional_fallback,
                "snap": {
                    "start": {
                        "node_id": start_node,
                        "distance_meters": round(start_snap_m, 2),
                        "snapped": [float(start_snapped[0]), float(start_snapped[1])],
                    },
                    "end": {
                        "node_id": end_node,
                        "distance_meters": round(end_snap_m, 2),
                        "snapped": [float(end_snapped[0]), float(end_snapped[1])],
                    },
                },
            },
        )

    def _resolve_speed_kph(self, reference_time: datetime | None) -> tuple[float, int | None]:
        if not self.use_hourly_speed_profile or reference_time is None:
            return self.average_speed_kph, None
        hour = int(reference_time.hour) % 24
        return float(self.hourly_speed_profile[hour]), hour

    def _merge_endpoints(
        self,
        path: list[tuple[float, float]],
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> list[tuple[float, float]]:
        merged = [tuple(point) for point in path] if path else [tuple(start), tuple(end)]
        if not merged:
            merged = [tuple(start), tuple(end)]
        if merged[0] != tuple(start):
            merged.insert(0, tuple(start))
        if merged[-1] != tuple(end):
            merged.append(tuple(end))
        deduped: list[tuple[float, float]] = []
        for point in merged:
            if not deduped or deduped[-1] != point:
                deduped.append(point)
        return deduped


class TmapRoutingService(BaseRoutingService):
    def __init__(
        self,
        app_key: str,
        simulation_cfg: dict[str, Any],
        timeout: int = 10,
        road_snap_dataset: str | None = None,
        road_snap_max_distance_meters: float = 400.0,
    ) -> None:
        self.app_key = app_key
        self.timeout = timeout
        self.use_reference_time_routing = bool(
            simulation_cfg.get("use_prediction_traffic", bool(simulation_cfg.get("window_start")))
        )
        self.fallback = StraightLineRoutingService(provider_name="simple_fallback")
        self.cache: dict[tuple[float, float, float, float, str], RoutePlan] = {}
        self.road_snap_dataset = (road_snap_dataset or "").strip()
        self.road_snap_max_distance_meters = float(road_snap_max_distance_meters)
        self.snapper = None
        if self.road_snap_dataset:
            self.snapper = NearestRoadSnapper(
                self.road_snap_dataset,
                max_distance_meters=self.road_snap_max_distance_meters,
            )

    def _serialize_reference_time(self, reference_time: datetime | None) -> str | None:
        if reference_time is None:
            return None
        return reference_time.isoformat(timespec="seconds")

    def _format_gps_time(self, reference_time: datetime | None) -> str | None:
        if reference_time is None:
            return None
        return reference_time.strftime("%Y%m%d%H%M%S")

    def _cache_key(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        reference_time: datetime | None,
    ) -> tuple[float, float, float, float, str]:
        return (
            round(start[0], 6),
            round(start[1], 6),
            round(end[0], 6),
            round(end[1], 6),
            "" if reference_time is None else reference_time.strftime("%Y-%m-%dT%H:%M"),
        )

    def plan_route(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        speed: float,
        time_step: int,
        reference_time: datetime | None = None,
    ) -> RoutePlan:
        key = self._cache_key(start, end, reference_time)
        if key in self.cache:
            return self.cache[key]

        if start == end:
            traffic_mode = "live_gpstime" if self.use_reference_time_routing and reference_time is not None else "live"
            plan = RoutePlan(
                path=[tuple(start)],
                total_distance=0.0,
                total_time=0.0,
                source=f"tmap_{traffic_mode}",
                metadata={
                    "traffic_mode": traffic_mode,
                    "reference_time": self._serialize_reference_time(reference_time),
                },
            )
            self.cache[key] = plan
            return plan

        request_mode = "live_gpstime" if self.use_reference_time_routing and reference_time is not None else "live"
        fallback_reason = None
        upstream_errors: list[str] = []
        coordinate_variants = self._build_coordinate_variants(start, end)
        response_coord_types = ["EPSG3857", "WGS84GEO"]

        attempt_modes = [request_mode]
        if request_mode != "live":
            attempt_modes.append("live")

        for mode in attempt_modes:
            for variant in coordinate_variants:
                for response_coord_type in response_coord_types:
                    route_start = variant["route_start"]
                    route_end = variant["route_end"]
                    try:
                        if mode == "live_gpstime":
                            parsed, transport = self._request_live_route_with_time(
                                route_start,
                                route_end,
                                reference_time,
                                response_coord_type=response_coord_type,
                            )
                        else:
                            parsed, transport = self._request_live_route(
                                route_start,
                                route_end,
                                response_coord_type=response_coord_type,
                            )
                        plan = self._parse_route_response(
                            parsed,
                            route_start,
                            route_end,
                            speed,
                            time_step,
                            response_coord_type=response_coord_type,
                        )
                        plan = self._merge_variant_path(plan, start, end, speed, time_step)
                        plan.source = f"tmap_{mode}"
                        plan.metadata["traffic_mode"] = mode
                        plan.metadata["reference_time"] = self._serialize_reference_time(reference_time)
                        plan.metadata["request_transport"] = transport
                        plan.metadata["requested_start"] = [float(start[0]), float(start[1])]
                        plan.metadata["requested_end"] = [float(end[0]), float(end[1])]
                        plan.metadata["routed_start"] = [float(route_start[0]), float(route_start[1])]
                        plan.metadata["routed_end"] = [float(route_end[0]), float(route_end[1])]
                        plan.metadata["coordinate_mode"] = variant["coordinate_mode"]
                        plan.metadata["response_coord_type"] = response_coord_type
                        if variant.get("road_snap"):
                            plan.metadata["road_snap"] = variant["road_snap"]
                        if fallback_reason is not None:
                            plan.metadata["fallback_reason"] = fallback_reason
                        if upstream_errors:
                            plan.metadata["upstream_errors"] = list(upstream_errors)
                        self.cache[key] = plan
                        return plan
                    except (URLError, TimeoutError, ValueError, KeyError, json.JSONDecodeError) as exc:
                        label = f"{mode}:{variant['coordinate_mode']}:{response_coord_type}"
                        upstream_errors.append(f"{label}:{exc.__class__.__name__}:{exc}")
                        if mode == "live_gpstime":
                            fallback_reason = "live_gpstime_request_failed"
                            continue

        plan = self.fallback.plan_route(start, end, speed, time_step, reference_time=reference_time)
        plan.metadata["traffic_mode"] = "fallback"
        plan.metadata["reference_time"] = self._serialize_reference_time(reference_time)
        plan.metadata["fallback_reason"] = "tmap_request_failed"
        plan.metadata["requested_start"] = [float(start[0]), float(start[1])]
        plan.metadata["requested_end"] = [float(end[0]), float(end[1])]
        plan.metadata["attempt_modes"] = list(attempt_modes)
        if upstream_errors:
            plan.metadata["upstream_errors"] = list(upstream_errors)
        self.cache[key] = plan
        return plan

    def _build_coordinate_variants(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> list[dict[str, Any]]:
        variants = [
            {
                "coordinate_mode": "direct",
                "route_start": start,
                "route_end": end,
            }
        ]
        if self.snapper is None:
            return variants

        snapped_start, start_meta = self.snapper.snap(start)
        snapped_end, end_meta = self.snapper.snap(end)
        if snapped_start == start and snapped_end == end:
            return variants

        variants.append(
            {
                "coordinate_mode": "road_snapped",
                "route_start": snapped_start,
                "route_end": snapped_end,
                "road_snap": {
                    "start": start_meta,
                    "end": end_meta,
                },
            }
        )
        return variants

    def _merge_variant_path(
        self,
        plan: RoutePlan,
        original_start: tuple[float, float],
        original_end: tuple[float, float],
        speed: float,
        time_step: int,
    ) -> RoutePlan:
        merged_path = [tuple(point) for point in plan.path]
        connector_distance = 0.0
        if merged_path and merged_path[0] != tuple(original_start):
            connector_distance += float(distance_between(original_start, merged_path[0]))
            merged_path.insert(0, tuple(original_start))
        if merged_path and merged_path[-1] != tuple(original_end):
            connector_distance += float(distance_between(merged_path[-1], original_end))
            merged_path.append(tuple(original_end))

        if connector_distance <= 0.0:
            return plan

        per_step_speed = max(float(speed), 1e-9)
        connector_time = max((connector_distance / per_step_speed) * time_step, float(time_step))
        return RoutePlan(
            path=merged_path,
            total_distance=float(plan.total_distance) + connector_distance,
            total_time=float(plan.total_time) + connector_time,
            source=plan.source,
            metadata=dict(plan.metadata),
        )

    def _http_error_message(self, exc: HTTPError) -> str:
        detail = ""
        try:
            raw = exc.read().decode("utf-8")
        except Exception:
            raw = ""
        if raw:
            try:
                parsed = json.loads(raw)
                detail = json.dumps(parsed, ensure_ascii=False)
            except json.JSONDecodeError:
                detail = raw.strip()
        if detail:
            return f"{exc.code} {exc.reason}: {detail}"
        return f"{exc.code} {exc.reason}"

    def _request_json_body(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "appKey": self.app_key,
            },
            method="POST",
        )
        with urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _request_form_body(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        form_payload = {"appKey": self.app_key}
        for key, value in payload.items():
            form_payload[key] = str(value)
        request = Request(
            url,
            data=urlencode(form_payload).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            },
            method="POST",
        )
        with urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _request_route_payload(self, url: str, payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
        errors: list[str] = []
        for transport_name, request_fn in (
            ("json_header", self._request_json_body),
            ("form_body", self._request_form_body),
        ):
            try:
                return request_fn(url, payload), transport_name
            except HTTPError as exc:
                errors.append(f"{transport_name}:HTTPError:{self._http_error_message(exc)}")
            except (URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
                errors.append(f"{transport_name}:{exc.__class__.__name__}:{exc}")
        raise ValueError(" ; ".join(errors))

    def _request_live_route(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        response_coord_type: str = "WGS84GEO",
    ) -> tuple[dict[str, Any], str]:
        payload = {
            "startX": start[1],
            "startY": start[0],
            "endX": end[1],
            "endY": end[0],
            "startName": "start",
            "endName": "end",
            "reqCoordType": "WGS84GEO",
            "resCoordType": response_coord_type,
            "searchOption": 0,
            "directionOption": 0,
            "roadType": 32,
            "tollgateFareOption": 16,
            "carType": 1,
            "trafficInfo": "Y",
        }
        return self._request_route_payload(TMAP_LIVE_ROUTES_URL, payload)

    def _request_live_route_with_time(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        reference_time: datetime,
        response_coord_type: str = "WGS84GEO",
    ) -> tuple[dict[str, Any], str]:
        payload = {
            "startX": start[1],
            "startY": start[0],
            "endX": end[1],
            "endY": end[0],
            "startName": "start",
            "endName": "end",
            "reqCoordType": "WGS84GEO",
            "resCoordType": response_coord_type,
            "searchOption": 0,
            "directionOption": 0,
            "roadType": 32,
            "tollgateFareOption": 16,
            "carType": 1,
            "gpsTime": self._format_gps_time(reference_time),
            "trafficInfo": "Y",
        }
        return self._request_route_payload(TMAP_LIVE_ROUTES_URL, payload)

    def _parse_route_response(
        self,
        payload: dict[str, Any],
        start: tuple[float, float],
        end: tuple[float, float],
        speed: float,
        time_step: int,
        response_coord_type: str = "WGS84GEO",
    ) -> RoutePlan:
        features = payload.get("features", [])
        if not features:
            raise ValueError("TMAP response did not contain route features.")

        path: list[tuple[float, float]] = []
        point_path: list[tuple[float, float]] = []
        total_distance = None
        total_time = None
        geometry_types: set[str] = set()
        candidate_features = list(self._iter_feature_candidates(features))
        for feature in candidate_features:
            properties = feature.get("properties", {})
            if total_distance is None and properties.get("totalDistance") is not None:
                total_distance = float(properties["totalDistance"])
            if total_time is None and properties.get("totalTime") is not None:
                total_time = float(properties["totalTime"])

            geometry = feature.get("geometry", {})
            geometry_type = str(geometry.get("type", "Unknown"))
            geometry_types.add(geometry_type)
            coordinates = geometry.get("coordinates", [])
            if geometry_type == "LineString":
                self._append_line_coordinates(path, coordinates, response_coord_type)
            elif geometry_type == "MultiLineString":
                for line in coordinates:
                    self._append_line_coordinates(path, line, response_coord_type)
            elif geometry_type == "Point":
                point = self._normalize_coordinate(coordinates, response_coord_type)
                if point is not None and (not point_path or point_path[-1] != point):
                    point_path.append(point)

        if not path:
            if len(point_path) >= 3:
                path = point_path
            else:
                geometry_summary = ",".join(sorted(geometry_types)) or "none"
                payload_keys = sorted(payload.keys()) if isinstance(payload, dict) else []
                first_feature = candidate_features[0] if candidate_features else (features[0] if features else {})
                first_feature_keys = sorted(first_feature.keys()) if isinstance(first_feature, dict) else []
                first_properties = first_feature.get("properties", {}) if isinstance(first_feature, dict) else {}
                first_property_keys = sorted(first_properties.keys()) if isinstance(first_properties, dict) else []
                description = None
                if isinstance(first_properties, dict):
                    description = (
                        first_properties.get("description")
                        or first_properties.get("message")
                        or first_properties.get("errorMessage")
                    )
                raise ValueError(
                    "TMAP response did not contain LineString geometry. "
                    f"geometry_types={geometry_summary}, point_features={len(point_path)}, "
                    f"feature_count={len(features)}, candidate_features={len(candidate_features)}, "
                    f"payload_keys={payload_keys}, first_feature_keys={first_feature_keys}, "
                    f"first_property_keys={first_property_keys}, description={description!r}"
                )

        if total_distance is None:
            total_distance = float(sum(distance_between(a, b) for a, b in zip(path, path[1:])))
        if total_time is None:
            fallback = self.fallback.plan_route(start, end, speed, time_step)
            total_time = fallback.total_time

        return RoutePlan(
            path=path,
            total_distance=total_distance,
            total_time=max(float(total_time), 0.0),
            source="tmap_live",
            metadata={
                "feature_count": len(features),
                "candidate_feature_count": len(candidate_features),
                "geometry_types": sorted(geometry_types),
                "response_coord_type": response_coord_type,
            },
        )

    def _iter_feature_candidates(self, value: Any) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []

        def visit(node: Any) -> None:
            if isinstance(node, dict):
                if "geometry" in node or "properties" in node:
                    candidates.append(node)
                nested_features = node.get("features")
                if isinstance(nested_features, list):
                    for item in nested_features:
                        visit(item)
                for key, nested in node.items():
                    if key == "features":
                        continue
                    if isinstance(nested, (dict, list)):
                        visit(nested)
            elif isinstance(node, list):
                for item in node:
                    visit(item)

        visit(value)
        deduped: list[dict[str, Any]] = []
        seen: set[int] = set()
        for candidate in candidates:
            identifier = id(candidate)
            if identifier in seen:
                continue
            seen.add(identifier)
            deduped.append(candidate)
        return deduped

    def _append_line_coordinates(
        self,
        path: list[tuple[float, float]],
        coordinates: list[Any],
        response_coord_type: str,
    ) -> None:
        for coord in coordinates:
            point = self._normalize_coordinate(coord, response_coord_type)
            if point is None:
                continue
            if not path or path[-1] != point:
                path.append(point)

    def _normalize_coordinate(
        self,
        coordinate: Any,
        response_coord_type: str,
    ) -> tuple[float, float] | None:
        if not isinstance(coordinate, (list, tuple)) or len(coordinate) < 2:
            return None
        first = float(coordinate[0])
        second = float(coordinate[1])
        if response_coord_type == "EPSG3857":
            return _epsg3857_to_wgs84((first, second))
        return (float(second), float(first))


def build_routing_service(config: dict) -> BaseRoutingService | None:
    simulation_cfg = config.get("simulation", {})
    space_mode = simulation_cfg.get("space_mode", "grid")
    provider = simulation_cfg.get("routing_provider", "simple")
    if space_mode == "grid":
        return None

    tmap_key = (
        config.get("integrations", {}).get("tmap_app_key")
        or os.environ.get("TMAP_APP_KEY")
        or ""
    ).strip()
    configured_dataset = str(simulation_cfg.get("road_snap_dataset", "")).strip()
    if configured_dataset:
        road_snap_dataset = configured_dataset
    else:
        default_dataset = DEFAULT_LOCAL_ITS_NODE_DATASET
        road_snap_dataset = str(default_dataset) if default_dataset.exists() else ""
    road_snap_max_distance_meters = float(simulation_cfg.get("road_snap_max_distance_meters", 400.0))
    configured_local_its_dataset = str(simulation_cfg.get("local_its_dataset", "")).strip()
    local_its_dataset = configured_local_its_dataset or (
        str(DEFAULT_LOCAL_ITS_GRAPH_DATASET) if DEFAULT_LOCAL_ITS_GRAPH_DATASET.exists() else road_snap_dataset
    )
    configured_geometry_dataset = str(simulation_cfg.get("local_its_geometry_dataset", "")).strip()
    local_its_geometry_dataset = configured_geometry_dataset or (
        str(DEFAULT_LOCAL_ITS_GEOMETRY_DATASET) if DEFAULT_LOCAL_ITS_GEOMETRY_DATASET.exists() else ""
    )
    configured_node_dataset = str(simulation_cfg.get("local_its_node_dataset", "")).strip()
    local_its_node_dataset = configured_node_dataset or (
        str(DEFAULT_LOCAL_ITS_NODE_DATASET) if DEFAULT_LOCAL_ITS_NODE_DATASET.exists() else ""
    )
    if provider == "tmap" and tmap_key:
        return TmapRoutingService(
            tmap_key,
            simulation_cfg,
            road_snap_dataset=road_snap_dataset,
            road_snap_max_distance_meters=road_snap_max_distance_meters,
        )
    if provider == "local_its" and local_its_dataset:
        return LocalITSRoutingService(
            simulation_cfg,
            dataset_path=local_its_dataset,
            geometry_dataset_path=local_its_geometry_dataset,
            node_dataset_path=local_its_node_dataset,
            average_speed_kph=float(simulation_cfg.get("local_its_average_speed_kph", DEFAULT_LOCAL_ITS_AVERAGE_SPEED_KPH)),
            allow_bidirectional_fallback=bool(simulation_cfg.get("local_its_allow_bidirectional_fallback", True)),
        )
    return StraightLineRoutingService(provider_name="simple")
