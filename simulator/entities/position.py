"""Position helpers for grid and latitude/longitude scenarios."""

from __future__ import annotations

import math

Position = tuple[float, float]


def _is_integral_point(point: Position) -> bool:
    return all(float(value).is_integer() for value in point)


def distance_between(a: Position, b: Position) -> float:
    """Use Manhattan distance for grid points and Euclidean distance otherwise."""
    if _is_integral_point(a) and _is_integral_point(b):
        return abs(a[0] - b[0]) + abs(a[1] - b[1])
    return math.dist(a, b)


def manhattan_distance(a: Position, b: Position) -> float:
    """Backward-compatible distance helper used by baseline policies."""
    return distance_between(a, b)


def step_toward(current: Position, target: Position, speed: float = 1.0) -> Position:
    if current == target:
        return current

    # Keep the original axis-aligned movement for integer grid scenarios.
    if _is_integral_point(current) and _is_integral_point(target):
        x, y = int(current[0]), int(current[1])
        target_x, target_y = int(target[0]), int(target[1])
        steps = max(1, int(speed))
        for _ in range(steps):
            if x != target_x:
                x += 1 if target_x > x else -1
            elif y != target_y:
                y += 1 if target_y > y else -1
        return (float(x), float(y))

    remaining = distance_between(current, target)
    if remaining <= 0:
        return target

    max_step = max(float(speed), 0.0)
    if remaining <= max_step:
        return target

    ratio = max_step / remaining
    return (
        current[0] + (target[0] - current[0]) * ratio,
        current[1] + (target[1] - current[1]) * ratio,
    )


def interpolate_path(path: list[Position], fraction: float) -> Position:
    """Return a point at the given progress fraction along a polyline."""
    if not path:
        raise ValueError("Path must contain at least one point.")
    if len(path) == 1:
        return path[0]

    clamped = min(max(fraction, 0.0), 1.0)
    if clamped <= 0.0:
        return path[0]
    if clamped >= 1.0:
        return path[-1]

    segment_lengths = [distance_between(start, end) for start, end in zip(path, path[1:])]
    total_length = sum(segment_lengths)
    if total_length <= 0:
        return path[-1]

    target_distance = total_length * clamped
    traversed = 0.0
    for index, segment_length in enumerate(segment_lengths):
        if traversed + segment_length >= target_distance and segment_length > 0:
            segment_fraction = (target_distance - traversed) / segment_length
            start = path[index]
            end = path[index + 1]
            return (
                start[0] + (end[0] - start[0]) * segment_fraction,
                start[1] + (end[1] - start[1]) * segment_fraction,
            )
        traversed += segment_length
    return path[-1]


def extract_path_segment(path: list[Position], start_fraction: float, end_fraction: float) -> list[Position]:
    """Return the polyline segment between two progress fractions."""
    if not path:
        raise ValueError("Path must contain at least one point.")
    if len(path) == 1:
        return [path[0]]

    start = min(max(start_fraction, 0.0), 1.0)
    end = min(max(end_fraction, 0.0), 1.0)
    if end < start:
        start, end = end, start
    if math.isclose(start, end):
        return [interpolate_path(path, end)]

    segment_lengths = [distance_between(a, b) for a, b in zip(path, path[1:])]
    total_length = sum(segment_lengths)
    if total_length <= 0:
        return [path[0], path[-1]]

    start_distance = total_length * start
    end_distance = total_length * end
    traversed = 0.0
    points: list[Position] = []

    for index, segment_length in enumerate(segment_lengths):
        segment_start = traversed
        segment_end = traversed + segment_length
        overlap_start = max(start_distance, segment_start)
        overlap_end = min(end_distance, segment_end)
        if overlap_end < overlap_start or segment_length <= 0:
            traversed = segment_end
            continue

        start_ratio = (overlap_start - segment_start) / segment_length
        end_ratio = (overlap_end - segment_start) / segment_length
        start_point = (
            path[index][0] + (path[index + 1][0] - path[index][0]) * start_ratio,
            path[index][1] + (path[index + 1][1] - path[index][1]) * start_ratio,
        )
        end_point = (
            path[index][0] + (path[index + 1][0] - path[index][0]) * end_ratio,
            path[index][1] + (path[index + 1][1] - path[index][1]) * end_ratio,
        )

        if not points or points[-1] != start_point:
            points.append(start_point)
        if points[-1] != end_point:
            points.append(end_point)
        traversed = segment_end

    if not points:
        return [interpolate_path(path, start), interpolate_path(path, end)]
    return points
