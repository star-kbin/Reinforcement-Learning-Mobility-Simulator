#!/usr/bin/env python3
"""Streamlit dashboard for the RL mobility simulator."""

from __future__ import annotations

import copy
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import random
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import uuid

import streamlit as st
import streamlit.components.v1 as components
from typing import Any, Callable

ROOT_DIR = Path(__file__).resolve().parents[1]
CONDITION_PRESET_DIR = ROOT_DIR / "results" / "condition_presets"
ENVIRONMENT_PRESET_DIR = ROOT_DIR / "results" / "environment_presets"
DEFAULT_LATLON_START_LABEL = "서울특별시 강남구 강남대로 318"
DEFAULT_LATLON_START = (37.4915254, 127.0310762)
DEFAULT_LATLON_DESTINATION_FALLBACK = (37.4922254, 127.0323762)
DEFAULT_LATLON_REQUEST_COUNT = 4
DASHBOARD_AUTOPLAY_SPEED_OPTIONS = [1, 2, 4, 8, 10, 50, 100]
DASHBOARD_AUTOPLAY_BASE_SECONDS = 10
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from simulator.env import MobilityEnv
from simulator.evaluation.runner import run_episode
from simulator.local_its_defaults import (
    DEFAULT_LOCAL_ITS_AVERAGE_SPEED_KPH,
    DEFAULT_LOCAL_ITS_HOURLY_SPEED_KPH,
    DEFAULT_LOCAL_ITS_SPEED_PROFILE_SOURCE,
)
from simulator.policies.factory import build_policy
from simulator.scenarios.generator import (
    ensure_vehicle_positions as shared_ensure_vehicle_positions,
    generate_random_requests as shared_generate_random_requests,
)
from simulator.scenarios.loader import load_config, load_scenario

KST = timezone(timedelta(hours=9))




def _current_day_start_text() -> str:
    now_local = datetime.now(timezone.utc).astimezone(KST)
    return now_local.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def _parse_display_datetime(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def _format_display_datetime(value: object) -> str:
    parsed = _parse_display_datetime(value)
    if parsed is None:
        return "-"
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _preferred_config_index(config_files: list[Path]) -> int:
    preferred_names = ["dongjak_history_latlon.yaml", "base_latlon.yaml"]
    for preferred_name in preferred_names:
        for index, path in enumerate(config_files):
            if path.name == preferred_name:
                return index
    for index, path in enumerate(config_files):
        if "latlon" in path.name.lower():
            return index
    return 0


def _preferred_scenario_index(scenario_files: list[Path]) -> int:
    preferred_names = {"scenario_dongjak_history.yaml"}
    for index, path in enumerate(scenario_files):
        if path.name in preferred_names:
            return index
    for index, path in enumerate(scenario_files):
        if "history" in path.name.lower():
            return index
    return 0


def _provider_label(provider: str) -> str:
    labels = {
        "local_its": "OSM 도로망 (이전 설정 자동 전환)",
        "osm": "OSM 도로망",
        "simple": "직선 기준",
    }
    return labels.get(provider, provider)


def _policy_label(policy: str) -> str:
    return {"nearest": "가까운 차량 우선", "eta": "도착시간 우선", "random": "무작위 배차"}.get(policy, policy)


def _map_provider_label(provider: str) -> str:
    labels = {

        "openstreetmap": "OpenStreetMap",
        "coordinate": "경량 좌표 맵",
        "pydeck": "PyDeck 오픈 지도",
    }
    return labels.get(provider, provider)


def _render_section_description(help_text: str) -> None:
    st.caption(help_text)


def _set_widget_default(key: str, value: object) -> None:
    if key not in st.session_state:
        st.session_state[key] = value


def _default_local_its_hourly_speed_profile(base_speed_kph: float) -> list[float]:
    _ = base_speed_kph
    return list(DEFAULT_LOCAL_ITS_HOURLY_SPEED_KPH)


def _normalize_local_its_hourly_speed_profile(profile: object, fallback_speed_kph: float) -> list[float]:
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
        normalized: list[float] = []
        for hour in range(24):
            raw_value = profile[hour] if hour < len(profile) else fallback
            try:
                normalized.append(max(float(raw_value), 1.0))
            except (TypeError, ValueError):
                normalized.append(fallback)
        return normalized
    return _default_local_its_hourly_speed_profile(fallback)


def _sync_local_its_speed_profile_state(source_signature: str, config: dict) -> None:
    simulation_cfg = config.setdefault("simulation", {})
    base_speed_kph = float(simulation_cfg.get("local_its_average_speed_kph", DEFAULT_LOCAL_ITS_AVERAGE_SPEED_KPH))
    normalized_profile = _normalize_local_its_hourly_speed_profile(
        simulation_cfg.get("local_its_hourly_speed_kph", []),
        base_speed_kph,
    )
    sync_signature = json.dumps(
        {
            "source": source_signature,
            "average_speed_kph": round(base_speed_kph, 3),
            "use_hourly_speed_profile": bool(simulation_cfg.get("local_its_use_hourly_speed_profile", False)),
            "hourly_speed_kph": [round(float(speed_kph), 3) for speed_kph in normalized_profile],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    widget_namespace = hashlib.sha1(sync_signature.encode("utf-8")).hexdigest()[:12]
    if (
        st.session_state.get("dashboard_local_its_speed_profile_source") == sync_signature
        and st.session_state.get("dashboard_local_its_hourly_widget_namespace") == widget_namespace
    ):
        return
    st.session_state["local_its_use_hourly_speed_profile"] = bool(
        simulation_cfg.get("local_its_use_hourly_speed_profile", False)
    )
    st.session_state["dashboard_local_its_speed_profile_source"] = sync_signature
    st.session_state["dashboard_local_its_hourly_widget_namespace"] = widget_namespace
    st.session_state["dashboard_local_its_hourly_default_profile"] = list(normalized_profile)


def render_compact_metric_cards(items: list[tuple[str, str]], columns_per_row: int) -> None:
    if not items:
        return
    for start in range(0, len(items), columns_per_row):
        row_items = items[start:start + columns_per_row]
        columns = st.columns(len(row_items))
        for column, (label, value) in zip(columns, row_items):
            with column:
                st.markdown(
                    (
                        '<div style="border:1px solid #e2e8f0;border-radius:12px;'
                        'padding:10px 12px;background:#ffffff;min-height:72px;'
                        'margin-bottom:8px;">'
                        f'<div style="font-size:0.72rem;color:#64748b;font-weight:600;line-height:1.2;">{label}</div>'
                        f'<div style="margin-top:6px;font-size:1.02rem;color:#0f172a;font-weight:800;line-height:1.25;">{value}</div>'
                        '</div>'
                    ),
                    unsafe_allow_html=True,
                )


def render_widget_alignment_spacer(height_px: int = 28) -> None:
    st.markdown(
        f'<div style="height:{int(height_px)}px;"></div>',
        unsafe_allow_html=True,
    )


def render_editor_metric_cards(items: list[tuple[str, str]]) -> None:
    if not items:
        return
    columns = st.columns(len(items))
    for column, (label, value) in zip(columns, items):
        with column:
            st.markdown(
                (
                    '<div style="border:1px solid #e2e8f0;border-radius:10px;'
                    'padding:8px 10px;background:#ffffff;min-height:58px;'
                    'margin-bottom:4px;">'
                    f'<div style="font-size:0.68rem;color:#64748b;font-weight:600;line-height:1.15;">{label}</div>'
                    f'<div style="margin-top:4px;font-size:0.95rem;color:#0f172a;font-weight:800;line-height:1.2;">{value}</div>'
                    '</div>'
                ),
                unsafe_allow_html=True,
            )


def render_hourly_speed_profile_chart(profile: list[float]) -> None:
    normalized_profile = [
        max(float(speed_kph), 0.0)
        for speed_kph in (profile[:24] if profile else [])
    ]
    if len(normalized_profile) < 24:
        normalized_profile.extend([0.0] * (24 - len(normalized_profile)))

    width = 920.0
    height = 220.0
    left = 48.0
    right = 20.0
    top = 18.0
    bottom = 40.0
    chart_width = width - left - right
    chart_height = height - top - bottom

    min_speed = min(normalized_profile)
    max_speed = max(normalized_profile)
    if math.isclose(min_speed, max_speed):
        min_speed = max(min_speed - 1.0, 0.0)
        max_speed = max_speed + 1.0

    def x_coord(hour: int) -> float:
        if len(normalized_profile) <= 1:
            return left + (chart_width / 2.0)
        return left + (chart_width * hour / (len(normalized_profile) - 1))

    def y_coord(speed_kph: float) -> float:
        ratio = (speed_kph - min_speed) / max(max_speed - min_speed, 1e-6)
        return top + chart_height - (chart_height * ratio)

    points = [(x_coord(hour), y_coord(speed_kph)) for hour, speed_kph in enumerate(normalized_profile)]
    line_path = " ".join(
        ("M" if index == 0 else "L") + f" {x_value:.2f} {y_value:.2f}"
        for index, (x_value, y_value) in enumerate(points)
    )
    area_path = (
        f"M {points[0][0]:.2f} {top + chart_height:.2f} "
        + " ".join(f"L {x_value:.2f} {y_value:.2f}" for x_value, y_value in points)
        + f" L {points[-1][0]:.2f} {top + chart_height:.2f} Z"
    )

    y_ticks = []
    for ratio in (0.0, 0.5, 1.0):
        value = min_speed + ((max_speed - min_speed) * ratio)
        y_value = y_coord(value)
        y_ticks.append(
            f'<line x1="{left:.2f}" y1="{y_value:.2f}" x2="{width - right:.2f}" y2="{y_value:.2f}" '
            'stroke="#e2e8f0" stroke-width="1" stroke-dasharray="3 4" />'
            f'<text x="{left - 8:.2f}" y="{y_value + 4:.2f}" text-anchor="end" '
            'font-size="11" fill="#64748b">'
            f'{value:.1f}</text>'
        )

    x_tick_hours = (0, 6, 12, 18, 23)
    x_ticks = []
    for hour in x_tick_hours:
        x_value = x_coord(hour)
        x_ticks.append(
            f'<line x1="{x_value:.2f}" y1="{top + chart_height:.2f}" x2="{x_value:.2f}" y2="{top + chart_height + 6:.2f}" '
            'stroke="#94a3b8" stroke-width="1" />'
            f'<text x="{x_value:.2f}" y="{height - 10:.2f}" text-anchor="middle" '
            'font-size="11" fill="#64748b">'
            f'{hour:02d}시</text>'
        )

    point_nodes = "".join(
        f'<circle cx="{x_value:.2f}" cy="{y_value:.2f}" r="2.8" fill="#0f766e" stroke="#ffffff" stroke-width="1.5" />'
        for x_value, y_value in points
    )
    avg_speed = sum(normalized_profile) / len(normalized_profile)

    html = f"""
    <div style="margin-top:8px;border:1px solid #e2e8f0;border-radius:12px;background:#ffffff;padding:10px 12px 6px 12px;">
      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:8px;font-size:12px;color:#475569;">
        <span><strong>시간대별 적용 속도</strong></span>
        <span>평균 {avg_speed:.1f} km/h</span>
        <span>최저 {min(normalized_profile):.1f} km/h</span>
        <span>최고 {max(normalized_profile):.1f} km/h</span>
      </div>
      <svg viewBox="0 0 {width:.0f} {height:.0f}" style="width:100%;height:auto;display:block;">
        <rect x="0" y="0" width="{width:.0f}" height="{height:.0f}" fill="#ffffff"></rect>
        {''.join(y_ticks)}
        <line x1="{left:.2f}" y1="{top + chart_height:.2f}" x2="{width - right:.2f}" y2="{top + chart_height:.2f}" stroke="#94a3b8" stroke-width="1.2" />
        <path d="{area_path}" fill="rgba(20,184,166,0.12)"></path>
        <path d="{line_path}" fill="none" stroke="#0f766e" stroke-width="2.6" stroke-linejoin="round" stroke-linecap="round"></path>
        {point_nodes}
        {''.join(x_ticks)}
      </svg>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


def render_reward_weight_guide(reward_config: dict, spatial_mode: str, time_unit_label: str, distance_unit_label: str) -> None:
    wait_weight = float(reward_config.get("wait_time", 0.0) or 0.0)
    distance_weight = float(reward_config.get("travel_distance", 0.0) or 0.0)
    completed_weight = float(reward_config.get("completed_request", 0.0) or 0.0)
    cancelled_weight = float(reward_config.get("cancelled_request", 0.0) or 0.0)
    violation_weight = float(reward_config.get("violation", 0.0) or 0.0)

    reward_formula = (
        f"Reward = -(대기 요청 수 x {wait_weight:g}) "
        f"- (이번 step 이동거리 x {distance_weight:g}) "
        f"+ (완료 요청 수 x {completed_weight:g}) "
        f"- (취소 요청 수 x {cancelled_weight:g})"
    )
    distance_basis = (
        f"`grid`에서는 {distance_unit_label} 기준 이동거리"
        if spatial_mode == "grid"
        else "현재 `lat/lon` 보상 엔진 내부 경로 좌표 거리 스칼라"
    )
    st.markdown("**보상 계산 기준**")
    st.code(reward_formula, language="text")
    st.caption(
        "대기시간 패널티: 현재 step에서 `waiting` 또는 `assigned` 상태인 요청 1건당 한 번 차감됩니다. "
        f"즉 실제 초({time_unit_label})가 아니라 step 단위 누적이라 `Time Step`이 작을수록 같은 실제 대기 시간에도 더 크게 쌓일 수 있습니다."
    )
    st.caption(
        "이동거리 패널티: 이번 step에서 차량이 이동한 거리 1단위당 차감됩니다. "
        f"기준 단위는 {distance_basis} 입니다."
    )
    st.caption("완료 보상: 이번 step에서 `completed`로 새로 기록된 요청 1건당 가산됩니다.")
    st.caption("취소 패널티: 이번 step에서 최대 대기시간 초과로 `cancelled` 처리된 요청 1건당 차감됩니다.")
    st.caption(
        "제약 위반 패널티: 입력값은 유지되지만 현재 기본 `WeightedRewardCalculator`에는 직접 합산되지 않습니다. "
        f"현재 설정값은 {violation_weight:g} 입니다."
    )


def _aggregate_reward_breakdown(step_logs: list[dict]) -> dict[str, float]:
    totals = {
        "waiting_penalty": 0.0,
        "distance_penalty": 0.0,
        "completed_bonus": 0.0,
        "cancelled_penalty": 0.0,
    }
    for step_log in step_logs:
        breakdown = step_log.get("reward_breakdown", {}) or {}
        for key in totals:
            totals[key] += float(breakdown.get(key, 0.0) or 0.0)
    totals["total_reward"] = sum(totals.values())
    return totals


def _format_reward_factor(value: float) -> str:
    rounded = round(float(value), 3)
    if math.isclose(rounded, round(rounded)):
        return str(int(round(rounded)))
    return f"{rounded:.3f}".rstrip("0").rstrip(".")


def _dashboard_units(spatial_mode: str) -> tuple[str, str]:
    if spatial_mode == "latlon":
        return ("sec", "deg")
    return ("step", "cell")


def _geo_distance_meters(a: list[float] | tuple[float, float], b: list[float] | tuple[float, float]) -> float:
    lat1, lon1 = math.radians(float(a[0])), math.radians(float(a[1]))
    lat2, lon2 = math.radians(float(b[0])), math.radians(float(b[1]))
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    hav = (
        math.sin(delta_lat / 2.0) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2.0) ** 2
    )
    return 2.0 * 6_371_000.0 * math.asin(min(1.0, math.sqrt(hav)))


def _path_distance_km(path: list[list[float]] | list[tuple[float, float]]) -> float:
    if len(path) < 2:
        return 0.0
    return sum(_geo_distance_meters(start, end) for start, end in zip(path, path[1:])) / 1000.0


def _frame_actual_distance_km(frame: dict) -> float:
    total_km = 0.0
    for vehicle in frame.get("vehicles", []):
        for path in vehicle.get("actual_route_paths", []):
            normalized_path = [point for point in path if isinstance(point, (list, tuple)) and len(point) >= 2]
            total_km += _path_distance_km(normalized_path)
    return total_km


def _vehicle_actual_distance_km(vehicle: dict) -> float:
    total_km = 0.0
    for path in vehicle.get("actual_route_paths", []):
        normalized_path = [point for point in path if isinstance(point, (list, tuple)) and len(point) >= 2]
        total_km += _path_distance_km(normalized_path)
    return total_km


def _frame_vehicle_positions(frame: dict) -> dict[str, tuple[float, float]]:
    positions: dict[str, tuple[float, float]] = {}
    for vehicle in frame.get("vehicles", []):
        position = vehicle.get("position", [])
        if isinstance(position, (list, tuple)) and len(position) >= 2:
            positions[str(vehicle.get("vehicle_id"))] = (float(position[0]), float(position[1]))
    return positions


def _format_duration_label(total_seconds: float) -> str:
    seconds_int = max(0, int(round(float(total_seconds))))
    hours, remainder = divmod(seconds_int, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{seconds_int} sec ({hours:02d}:{minutes:02d}:{seconds:02d})"


def _compute_latlon_distance_speed_metrics(timeline: list[dict]) -> dict[str, float | int]:
    if not timeline:
        return {
            "step_distance_km": 0.0,
            "cumulative_distance_km": 0.0,
            "step_speed_kph": 0.0,
            "average_speed_kph": 0.0,
            "total_simulation_seconds": 0.0,
            "vehicle_operating_seconds": 0.0,
            "moving_vehicle_count": 0,
        }

    cumulative_distance_km = _frame_actual_distance_km(timeline[-1])
    previous_distance_km = _frame_actual_distance_km(timeline[-2]) if len(timeline) >= 2 else 0.0
    step_distance_km = max(0.0, cumulative_distance_km - previous_distance_km)

    moving_time_seconds = 0.0
    moving_vehicle_count = 0
    step_seconds = 0.0
    if len(timeline) >= 2:
        prev_frame = timeline[-2]
        current_frame = timeline[-1]
        step_seconds = max(0.0, float(current_frame.get("time", 0)) - float(prev_frame.get("time", 0)))
        prev_positions = _frame_vehicle_positions(prev_frame)
        current_positions = _frame_vehicle_positions(current_frame)
        for vehicle_id, current_position in current_positions.items():
            previous_position = prev_positions.get(vehicle_id)
            if previous_position is None:
                continue
            if previous_position != current_position:
                moving_vehicle_count += 1

    for previous_frame, current_frame in zip(timeline, timeline[1:]):
        delta_seconds = max(0.0, float(current_frame.get("time", 0)) - float(previous_frame.get("time", 0)))
        if delta_seconds <= 0:
            continue
        previous_positions = _frame_vehicle_positions(previous_frame)
        current_positions = _frame_vehicle_positions(current_frame)
        for vehicle_id, current_position in current_positions.items():
            previous_position = previous_positions.get(vehicle_id)
            if previous_position is None:
                continue
            if previous_position != current_position:
                moving_time_seconds += delta_seconds

    step_speed_kph = 0.0
    if moving_vehicle_count > 0 and step_seconds > 0:
        step_speed_kph = step_distance_km / ((step_seconds / 3600.0) * moving_vehicle_count)

    average_speed_kph = 0.0
    if moving_time_seconds > 0:
        average_speed_kph = cumulative_distance_km / (moving_time_seconds / 3600.0)

    total_simulation_seconds = max(
        0.0,
        float(timeline[-1].get("time", 0)) - float(timeline[0].get("time", 0)),
    )

    return {
        "step_distance_km": step_distance_km,
        "cumulative_distance_km": cumulative_distance_km,
        "step_speed_kph": step_speed_kph,
        "average_speed_kph": average_speed_kph,
        "total_simulation_seconds": total_simulation_seconds,
        "vehicle_operating_seconds": moving_time_seconds,
        "moving_vehicle_count": moving_vehicle_count,
    }


def _sanitize_preset_name(name: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "_", name.strip())
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned.strip("._")


def list_environment_preset_paths() -> list[Path]:
    if not ENVIRONMENT_PRESET_DIR.exists():
        return []
    return sorted(ENVIRONMENT_PRESET_DIR.glob("*.json"))


def load_environment_preset(path_or_name: str | Path) -> dict:
    path = Path(path_or_name)
    if not path.suffix:
        path = ENVIRONMENT_PRESET_DIR / f"{path.name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def save_environment_preset(name: str, config: dict, spatial_mode: str) -> Path:
    preset_name = _sanitize_preset_name(name)
    if not preset_name:
        raise ValueError("환경 프리셋 이름을 입력해 주세요.")
    ENVIRONMENT_PRESET_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "name": preset_name,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "spatial_mode": spatial_mode,
        "simulation": copy.deepcopy(config.get("simulation", {})),
    }
    preset_path = ENVIRONMENT_PRESET_DIR / f"{preset_name}.json"
    preset_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return preset_path


def apply_environment_preset(base_config: dict, preset: dict) -> None:
    base_config.setdefault("simulation", {})
    simulation_payload = preset.get("simulation", {})
    if isinstance(simulation_payload, dict):
        for key, value in simulation_payload.items():
            base_config["simulation"][key] = copy.deepcopy(value)


def list_condition_preset_paths() -> list[Path]:
    if not CONDITION_PRESET_DIR.exists():
        return []
    return sorted(CONDITION_PRESET_DIR.glob("*.json"))


def load_condition_preset(path_or_name: str | Path) -> dict:
    path = Path(path_or_name)
    if not path.suffix:
        path = CONDITION_PRESET_DIR / f"{path.name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _build_current_condition_preset_payload(config: dict, scenario: dict, spatial_mode: str, preset_name: str) -> dict:
    current_vehicle_positions = _read_current_vehicle_positions(config, scenario, spatial_mode)
    current_requests = _read_current_requests(scenario, spatial_mode)
    request_generation_meta = copy.deepcopy(scenario.get("metadata", {}).get("request_generation", {}))
    if "request_generation_radius_input" in st.session_state:
        request_generation_meta["radius"] = float(st.session_state.get("request_generation_radius_input", request_generation_meta.get("radius", 0.0)))
    if "dashboard_request_generation_seed" in st.session_state:
        request_generation_meta["seed"] = int(st.session_state.get("dashboard_request_generation_seed", request_generation_meta.get("seed", 0)))
    request_generation_meta["request_count"] = len(current_requests)

    return {
        "name": preset_name,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "spatial_mode": spatial_mode,
        "vehicles": {
            "count": max(1, int(config.get("vehicles", {}).get("count", len(current_vehicle_positions) or 1))),
            "capacity": int(config.get("vehicles", {}).get("capacity", 1)),
            "speed": float(config.get("vehicles", {}).get("speed", 1.0)),
            "available_time": int(config.get("vehicles", {}).get("available_time", 1)),
            "initial_positions": copy.deepcopy(current_vehicle_positions),
        },
        "request_generation": request_generation_meta,
        "requests": copy.deepcopy(current_requests),
    }


def save_condition_preset(name: str, config: dict, scenario: dict, spatial_mode: str) -> Path:
    preset_name = _sanitize_preset_name(name)
    if not preset_name:
        raise ValueError("프리셋 이름을 입력해 주세요.")
    CONDITION_PRESET_DIR.mkdir(parents=True, exist_ok=True)
    payload = _build_current_condition_preset_payload(config, scenario, spatial_mode, preset_name)
    preset_path = CONDITION_PRESET_DIR / f"{preset_name}.json"
    preset_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return preset_path


def apply_condition_preset(base_config: dict, base_scenario: dict, preset: dict) -> None:
    base_config.setdefault("vehicles", {})
    base_scenario.setdefault("metadata", {})
    vehicle_payload = preset.get("vehicles", {})
    for field in ("count", "capacity", "speed", "available_time", "initial_positions"):
        if field in vehicle_payload:
            base_config["vehicles"][field] = copy.deepcopy(vehicle_payload[field])

    if "request_generation" in preset:
        base_scenario["metadata"]["request_generation"] = copy.deepcopy(preset.get("request_generation", {}))
    if "requests" in preset:
        base_scenario["requests"] = copy.deepcopy(preset.get("requests", []))


def sync_condition_editor_state(config: dict, scenario: dict, spatial_mode: str, source_signature: str) -> None:
    if st.session_state.get("dashboard_condition_editor_source") == source_signature:
        return

    config.setdefault("vehicles", {})
    vehicle_count = max(1, int(config["vehicles"].get("count", 1)))
    vehicle_capacity = max(1, int(config["vehicles"].get("capacity", 1)))
    vehicle_available_time = max(1, int(config["vehicles"].get("available_time", 1)))
    vehicle_speed = float(config["vehicles"].get("speed", 1.0))
    default_position = (
        DEFAULT_LATLON_START
        if spatial_mode == "latlon"
        else get_first_demand_origin(scenario, spatial_mode)
    )
    configured_positions = list(config["vehicles"].get("initial_positions", []))
    last_position = _normalize_coordinate_pair(default_position, spatial_mode, default_position)

    st.session_state["vehicle_count_input"] = vehicle_count
    st.session_state["vehicle_capacity_input"] = vehicle_capacity
    st.session_state["vehicle_available_time_input"] = vehicle_available_time
    st.session_state["vehicle_speed_input"] = vehicle_speed

    for index in range(vehicle_count):
        candidate = configured_positions[index] if index < len(configured_positions) else last_position
        position = _normalize_coordinate_pair(candidate, spatial_mode, default_position)
        last_position = position
        if spatial_mode == "grid":
            st.session_state[f"vehicle_x_{index}"] = int(position[0])
            st.session_state[f"vehicle_y_{index}"] = int(position[1])
        else:
            st.session_state[f"vehicle_lat_{index}"] = float(position[0])
            st.session_state[f"vehicle_lon_{index}"] = float(position[1])

    requests = list(scenario.get("requests", []))
    request_count = (
        max(1, len(requests))
        if requests
        else (DEFAULT_LATLON_REQUEST_COUNT if spatial_mode == "latlon" else 1)
    )
    st.session_state["request_count_input"] = request_count
    generation_defaults = scenario.get("metadata", {}).get("request_generation", {})
    default_radius = 500.0 if spatial_mode == "latlon" else 3.0
    st.session_state["request_generation_radius_input"] = float(generation_defaults.get("radius", default_radius))
    st.session_state["dashboard_request_generation_seed"] = int(
        generation_defaults.get("seed", (uuid.uuid4().int >> 96) & 0xFFFFFFFF)
    )
    st.session_state["dashboard_request_generation_lock"] = bool(requests)
    st.session_state["dashboard_generated_requests"] = copy.deepcopy(requests)
    st.session_state["dashboard_request_generation_signature"] = ""

    for index in range(request_count):
        request = requests[index] if index < len(requests) else _default_request_template(index, spatial_mode)
        request_defaults = _read_request_defaults_from_data(index, request, spatial_mode)
        st.session_state[f"req_id_{index}"] = str(request_defaults["request_id"])
        st.session_state[f"created_at_{index}"] = int(request_defaults["created_at"])
        st.session_state[f"max_wait_{index}"] = int(request_defaults["max_wait_time"])
        if spatial_mode == "grid":
            st.session_state[f"origin_x_{index}"] = int(request_defaults["origin"][0])
            st.session_state[f"origin_y_{index}"] = int(request_defaults["origin"][1])
            st.session_state[f"dest_x_{index}"] = int(request_defaults["destination"][0])
            st.session_state[f"dest_y_{index}"] = int(request_defaults["destination"][1])
        else:
            st.session_state[f"origin_lat_{index}"] = float(request_defaults["origin"][0])
            st.session_state[f"origin_lon_{index}"] = float(request_defaults["origin"][1])
            st.session_state[f"dest_lat_{index}"] = float(request_defaults["destination"][0])
            st.session_state[f"dest_lon_{index}"] = float(request_defaults["destination"][1])

    st.session_state["dashboard_vehicle_count"] = vehicle_count
    st.session_state["dashboard_request_count"] = request_count
    st.session_state["dashboard_condition_editor_source"] = source_signature


def render_condition_preset_loader() -> str | None:
    preset_paths = list_condition_preset_paths()
    preset_names = [path.stem for path in preset_paths]
    active_preset = st.session_state.get("dashboard_active_condition_preset", "")
    if active_preset and active_preset not in preset_names:
        active_preset = ""
        st.session_state["dashboard_active_condition_preset"] = ""

    options = ["(없음)", *preset_names]
    current_value = active_preset if active_preset else "(없음)"
    pending_select = st.session_state.pop("dashboard_condition_preset_pending_select", None)
    if pending_select is not None:
        st.session_state["dashboard_condition_preset_select"] = pending_select
    elif "dashboard_condition_preset_select" not in st.session_state:
        st.session_state["dashboard_condition_preset_select"] = current_value
    elif st.session_state["dashboard_condition_preset_select"] not in options:
        st.session_state["dashboard_condition_preset_select"] = current_value

    st.subheader("차량/요청 프리셋")
    st.caption("현재 차량 설정과 요청 설정을 별도 프리셋으로 저장해두고, 다른 실험 조건과 조합해 다시 불러올 수 있습니다.")
    selected_name = st.selectbox(
        "저장된 프리셋",
        options,
        key="dashboard_condition_preset_select",
        help="불러오면 차량 수, 시작 위치, 요청 수, 출발지/도착지, 시간 설정이 현재 편집기에 채워집니다.",
    )
    col1, col2 = st.columns(2)
    with col1:
        if st.button("불러오기", use_container_width=True):
            st.session_state["dashboard_active_condition_preset"] = "" if selected_name == "(없음)" else selected_name
            st.session_state["dashboard_condition_preset_pending_select"] = selected_name
            st.session_state["dashboard_condition_editor_source"] = ""
            st.session_state["dashboard_preset_flash"] = (
                "기본 차량/요청 설정으로 돌아왔습니다."
                if selected_name == "(없음)"
                else f"`{selected_name}` 프리셋을 불러왔습니다."
            )
            st.rerun()
    with col2:
        if st.button("프리셋 해제", use_container_width=True):
            st.session_state["dashboard_active_condition_preset"] = ""
            st.session_state["dashboard_condition_preset_pending_select"] = "(없음)"
            st.session_state["dashboard_condition_editor_source"] = ""
            st.session_state["dashboard_preset_flash"] = "적용 중인 프리셋을 해제했습니다."
            st.rerun()

    flash_message = st.session_state.pop("dashboard_preset_flash", "")
    if flash_message:
        st.success(flash_message)

    if active_preset:
        try:
            preset = load_condition_preset(active_preset)
            saved_at = str(preset.get("saved_at", "-"))
            st.caption(f"현재 적용 프리셋: `{active_preset}` / 저장 시각: {saved_at}")
        except FileNotFoundError:
            st.warning("선택한 프리셋 파일을 찾지 못했습니다. 다시 저장하거나 다른 프리셋을 선택해 주세요.")
            st.session_state["dashboard_active_condition_preset"] = ""
            active_preset = ""

    return active_preset or None


def render_environment_preset_loader() -> str | None:
    preset_paths = list_environment_preset_paths()
    preset_names = [path.stem for path in preset_paths]
    active_preset = st.session_state.get("dashboard_active_environment_preset", "")
    if active_preset and active_preset not in preset_names:
        active_preset = ""
        st.session_state["dashboard_active_environment_preset"] = ""

    options = ["(없음)", *preset_names]
    current_value = active_preset if active_preset else "(없음)"
    pending_select = st.session_state.pop("dashboard_environment_preset_pending_select", None)
    if pending_select is not None:
        st.session_state["dashboard_environment_preset_select"] = pending_select
    elif "dashboard_environment_preset_select" not in st.session_state:
        st.session_state["dashboard_environment_preset_select"] = current_value
    elif st.session_state["dashboard_environment_preset_select"] not in options:
        st.session_state["dashboard_environment_preset_select"] = current_value

    st.subheader("환경 프리셋")
    st.caption("현재 환경 설정을 별도 프리셋으로 저장해두고, 차량/요청 조건과 조합해 다시 불러올 수 있습니다.")
    selected_name = st.selectbox(
        "저장된 환경 프리셋",
        options,
        key="dashboard_environment_preset_select",
        help="불러오면 시뮬레이션 시작 시각, 시간 간격, 라우팅 백엔드, 평균 속도 설정 같은 환경 설정이 편집기에 채워집니다.",
    )
    col1, col2 = st.columns(2)
    with col1:
        if st.button("불러오기", use_container_width=True, key="load_environment_preset_button"):
            st.session_state["dashboard_active_environment_preset"] = "" if selected_name == "(없음)" else selected_name
            st.session_state["dashboard_environment_preset_pending_select"] = selected_name
            st.session_state["dashboard_environment_preset_flash"] = (
                "기본 환경 설정으로 돌아왔습니다."
                if selected_name == "(없음)"
                else f"`{selected_name}` 환경 프리셋을 불러왔습니다."
            )
            st.rerun()
    with col2:
        if st.button("프리셋 해제", use_container_width=True, key="clear_environment_preset_button"):
            st.session_state["dashboard_active_environment_preset"] = ""
            st.session_state["dashboard_environment_preset_pending_select"] = "(없음)"
            st.session_state["dashboard_environment_preset_flash"] = "적용 중인 환경 프리셋을 해제했습니다."
            st.rerun()

    flash_message = st.session_state.pop("dashboard_environment_preset_flash", "")
    if flash_message:
        st.success(flash_message)

    if active_preset:
        try:
            preset = load_environment_preset(active_preset)
            saved_at = str(preset.get("saved_at", "-"))
            st.caption(f"현재 적용 환경 프리셋: `{active_preset}` / 저장 시각: {saved_at}")
        except FileNotFoundError:
            st.warning("선택한 환경 프리셋 파일을 찾지 못했습니다. 다시 저장하거나 다른 프리셋을 선택해 주세요.")
            st.session_state["dashboard_active_environment_preset"] = ""
            active_preset = ""

    return active_preset or None


def render_condition_preset_saver(config: dict, scenario: dict, spatial_mode: str) -> None:
    st.caption("현재 편집 중인 차량 설정과 요청 설정을 프리셋으로 저장할 수 있습니다.")
    active_preset = st.session_state.get("dashboard_active_condition_preset", "")
    if active_preset and st.session_state.get("dashboard_condition_preset_name_source") != active_preset:
        st.session_state["dashboard_condition_preset_name"] = active_preset
        st.session_state["dashboard_condition_preset_name_source"] = active_preset
    elif "dashboard_condition_preset_name" not in st.session_state:
        st.session_state["dashboard_condition_preset_name"] = active_preset
    preset_name = st.text_input(
        "프리셋 이름",
        key="dashboard_condition_preset_name",
        help="같은 이름으로 저장하면 기존 프리셋을 덮어씁니다.",
    )
    if st.button("현재 차량/요청 설정 저장", use_container_width=True):
        try:
            save_condition_preset(preset_name, config, scenario, spatial_mode)
        except ValueError as exc:
            st.error(str(exc))
        else:
            saved_name = _sanitize_preset_name(preset_name)
            st.session_state["dashboard_active_condition_preset"] = saved_name
            st.session_state["dashboard_condition_preset_pending_select"] = saved_name
            st.session_state["dashboard_condition_editor_source"] = ""
            st.session_state["dashboard_preset_flash"] = f"`{saved_name}` 프리셋을 저장했습니다."
            st.rerun()


def render_environment_preset_saver(config: dict, spatial_mode: str) -> None:
    st.caption("현재 편집 중인 환경 설정을 프리셋으로 저장할 수 있습니다.")
    active_preset = st.session_state.get("dashboard_active_environment_preset", "")
    if active_preset and st.session_state.get("dashboard_environment_preset_name_source") != active_preset:
        st.session_state["dashboard_environment_preset_name"] = active_preset
        st.session_state["dashboard_environment_preset_name_source"] = active_preset
    elif "dashboard_environment_preset_name" not in st.session_state:
        st.session_state["dashboard_environment_preset_name"] = active_preset
    preset_name = st.text_input(
        "환경 프리셋 이름",
        key="dashboard_environment_preset_name",
        help="같은 이름으로 저장하면 기존 환경 프리셋을 덮어씁니다.",
    )
    if st.button("현재 환경 설정 저장", use_container_width=True, key="save_environment_preset_button"):
        try:
            save_environment_preset(preset_name, config, spatial_mode)
        except ValueError as exc:
            st.error(str(exc))
        else:
            saved_name = _sanitize_preset_name(preset_name)
            st.session_state["dashboard_active_environment_preset"] = saved_name
            st.session_state["dashboard_environment_preset_pending_select"] = saved_name
            st.session_state["dashboard_environment_preset_flash"] = f"`{saved_name}` 환경 프리셋을 저장했습니다."
            st.rerun()


def _sync_dashboard_batch_control_defaults(
    config: dict,
    scenario: dict,
    policy_name: str,
    source_signature: str,
) -> None:
    if st.session_state.get("dashboard_batch_control_source") == source_signature:
        return

    spatial_mode = detect_spatial_mode(config, scenario)
    current_requests = _read_current_requests(scenario, spatial_mode)
    request_generation_meta = scenario.get("metadata", {}).get("request_generation", {})
    current_request_count = max(1, len(current_requests))
    current_radius = float(
        request_generation_meta.get(
            "radius",
            _default_request_generation_radius(spatial_mode),
        )
    )
    current_seed = int(
        request_generation_meta.get(
            "seed",
            st.session_state.get("dashboard_request_generation_seed", 101),
        )
    )
    current_max_wait = int(
        (current_requests[0] if current_requests else {}).get(
            "max_wait_time",
            max(1, int(config.get("simulation", {}).get("time_step", 30))),
        )
    )
    current_window_start = str(config.get("simulation", {}).get("window_start", "") or _current_day_start_text())
    current_vehicle_count = max(1, int(config.get("vehicles", {}).get("count", 1)))

    st.session_state["dashboard_batch_policies"] = [policy_name]
    st.session_state["dashboard_batch_vehicle_counts_text"] = str(current_vehicle_count)
    st.session_state["dashboard_batch_window_start_text"] = current_window_start
    st.session_state["dashboard_batch_fixed_request_set"] = False
    st.session_state["dashboard_batch_request_counts_text"] = str(current_request_count)
    st.session_state["dashboard_batch_radius_text"] = str(int(current_radius) if float(current_radius).is_integer() else current_radius)
    st.session_state["dashboard_batch_max_wait_text"] = str(current_max_wait)
    st.session_state["dashboard_batch_seed_text"] = str(current_seed)
    st.session_state["dashboard_batch_save_run_payloads"] = False
    st.session_state["dashboard_batch_control_source"] = source_signature


def _build_dashboard_batch_spec(config: dict, scenario: dict) -> dict:
    policies = list(st.session_state.get("dashboard_batch_policies", []))
    if not policies:
        raise ValueError("배치 실험 정책을 하나 이상 선택해 주세요.")

    fixed_request_set = bool(st.session_state.get("dashboard_batch_fixed_request_set", False))
    axes: dict[str, list[object]] = {
        "vehicle_count": _parse_batch_int_list(
            st.session_state.get("dashboard_batch_vehicle_counts_text", ""),
            "차량 수 목록",
            minimum=1,
        ),
        "window_start": _parse_batch_datetime_list(
            st.session_state.get("dashboard_batch_window_start_text", ""),
            "시뮬레이션 시작 시각 목록",
        ),
    }
    if not fixed_request_set:
        axes["request_count"] = _parse_batch_int_list(
            st.session_state.get("dashboard_batch_request_counts_text", ""),
            "요청 수 목록",
            minimum=1,
        )
        axes["radius"] = _parse_batch_float_list(
            st.session_state.get("dashboard_batch_radius_text", ""),
            "생성 반경 목록",
            minimum=1.0 if detect_spatial_mode(config, scenario) == "grid" else 50.0,
        )
        axes["max_wait_time"] = _parse_batch_int_list(
            st.session_state.get("dashboard_batch_max_wait_text", ""),
            "최대 대기 시간 목록",
            minimum=1,
        )
        axes["seed"] = _parse_batch_int_list(
            st.session_state.get("dashboard_batch_seed_text", ""),
            "시드 목록",
            minimum=0,
        )

    return {
        "policies": policies,
        "fixed_request_set": fixed_request_set,
        "save_run_payloads": bool(st.session_state.get("dashboard_batch_save_run_payloads", False)),
        "axes": axes,
        "base_request_count": len(scenario.get("requests", [])),
        "base_vehicle_count": int(config.get("vehicles", {}).get("count", 1)),
    }


def render_batch_experiment_controls(
    config: dict,
    scenario: dict,
    policy_name: str,
    source_signature: str,
) -> dict | None:
    _sync_dashboard_batch_control_defaults(config, scenario, policy_name, source_signature)
    spatial_mode = detect_spatial_mode(config, scenario)
    radius_unit = _request_generation_distance_unit(spatial_mode)

    st.subheader("배치 실험")
    st.caption("현재 편집 중인 환경/차량/요청 상태를 기준값으로 사용해 여러 조건 조합을 자동 실행합니다.")
    st.multiselect(
        "배치 정책",
        ["nearest", "eta", "random"],
        key="dashboard_batch_policies",
        help="선택한 정책 각각에 대해 같은 조건 조합을 반복 실행합니다.",
    )
    st.checkbox(
        "현재 요청 세트 고정",
        key="dashboard_batch_fixed_request_set",
        help="켜면 현재 화면의 요청 세트를 그대로 사용하고, 끄면 요청 수/반경/최대 대기/시드 조합으로 새 요청을 생성합니다.",
    )
    st.text_input(
        "차량 수 목록",
        key="dashboard_batch_vehicle_counts_text",
        help="예: 1,2,3",
    )
    st.text_input(
        "시뮬레이션 시작 시각 목록",
        key="dashboard_batch_window_start_text",
        help="예: 2026-04-03 08:00:00, 2026-04-03 18:00:00",
    )
    request_inputs_disabled = bool(st.session_state.get("dashboard_batch_fixed_request_set", False))
    st.text_input(
        "요청 수 목록",
        key="dashboard_batch_request_counts_text",
        disabled=request_inputs_disabled,
        help="예: 4,8,12",
    )
    st.text_input(
        f"생성 반경 목록 ({radius_unit})",
        key="dashboard_batch_radius_text",
        disabled=request_inputs_disabled,
        help=f"예: 500,1000 ({radius_unit})",
    )
    st.text_input(
        "최대 대기 시간 목록 (sec)",
        key="dashboard_batch_max_wait_text",
        disabled=request_inputs_disabled,
        help="예: 300,600",
    )
    st.text_input(
        "시드 목록",
        key="dashboard_batch_seed_text",
        disabled=request_inputs_disabled,
        help="예: 101,202",
    )
    st.checkbox(
        "개별 실행 JSON 저장",
        key="dashboard_batch_save_run_payloads",
        help="켜면 각 case/policy 실행 결과를 JSON 파일로 함께 저장합니다.",
    )

    preview_error = ""
    preview_case_count = 0
    preview_run_count = 0
    try:
        preview_spec = _build_dashboard_batch_spec(config, scenario)
        preview_case_count = len(_expand_batch_case_grid(preview_spec["axes"]))
        preview_run_count = preview_case_count * max(1, len(preview_spec["policies"]))
    except ValueError as exc:
        preview_error = str(exc)

    if preview_error:
        st.warning(preview_error)
    else:
        st.caption(f"예상 case 수: {preview_case_count} / 총 실행 수: {preview_run_count}")
        if preview_run_count > 120:
            st.warning("실행 수가 많습니다. 대시보드에서 오래 걸릴 수 있으니 먼저 작은 조합으로 점검하는 것을 권장합니다.")

    col1, col2 = st.columns(2)
    with col1:
        run_requested = st.button("배치 실험 실행", use_container_width=True)
    with col2:
        clear_requested = st.button("배치 결과 지우기", use_container_width=True)

    if clear_requested:
        st.session_state.pop("dashboard_batch_result", None)
        st.session_state.pop("dashboard_batch_flash", None)
        st.rerun()

    flash_message = st.session_state.pop("dashboard_batch_flash", "")
    if flash_message:
        st.success(flash_message)

    if not run_requested:
        return None
    try:
        return _build_dashboard_batch_spec(config, scenario)
    except ValueError as exc:
        st.error(str(exc))
        return None


def main() -> None:
    st.set_page_config(page_title="Reinforcement Learning Mobility Simulator", layout="wide")
    st.title("모빌리티 시뮬레이터")
    st.caption("① 기본 조건 조정 → ② 실행 → ③ OSM 지도와 결과 확인")


    config_files = sorted((ROOT_DIR / "configs").glob("*.yaml"))
    scenario_files = sorted((ROOT_DIR / "scenarios").glob("*.yaml"))

    with st.sidebar:
        st.header("시뮬레이션 설정")
        advanced_settings = st.toggle("상세 설정", value=False, help="세부 환경·좌표·보상·프리셋·배치 실험을 표시합니다.")
        with st.expander("실험 데이터", expanded=False):
            selected_config = st.selectbox(
                "기본 Config", config_files,
                index=_preferred_config_index(config_files), format_func=lambda p: p.name,
                help="좌표계·경로 계산·차량·보상의 기본 조건을 불러옵니다.",
            )
            selected_scenario = st.selectbox(
                "기본 Scenario", scenario_files,
                index=_preferred_scenario_index(scenario_files), format_func=lambda p: p.name,
                help="재생할 요청의 출발지·도착지·생성 시각·대기 시간을 불러옵니다.",
            )

    base_config = load_config(selected_config)
    base_scenario = load_scenario(selected_scenario)
    base_config.setdefault("vehicles", {})
    base_config["vehicles"]["count"] = 1
    if detect_spatial_mode(base_config, base_scenario) == "latlon":
        base_config.setdefault("simulation", {})
        base_config["simulation"]["window_start"] = _current_day_start_text()
        base_config["simulation"].pop("timezone_offset", None)
        base_config["vehicles"]["initial_positions"] = [[DEFAULT_LATLON_START[0], DEFAULT_LATLON_START[1]]]
    elif isinstance(base_config["vehicles"].get("initial_positions"), list):
        base_config["vehicles"]["initial_positions"] = list(base_config["vehicles"]["initial_positions"][:1])

    with st.sidebar:
        policy_name = st.selectbox(
            "정책",
            ["nearest", "eta", "random"],
            index=0,
            format_func=_policy_label,
            help="배차 의사결정 규칙입니다. `nearest`는 가장 가까운 차량, `eta`는 예상 도착시간, `random`은 임의 선택을 사용합니다.",
        )
        st.caption("지도: OpenStreetMap · 경로: 내장 OSM 도로망 · API 키 없이 사용")
        active_environment_preset = st.session_state.get("dashboard_active_environment_preset") or None
        active_condition_preset = st.session_state.get("dashboard_active_condition_preset") or None
        if advanced_settings:
            active_environment_preset = render_environment_preset_loader()
            active_condition_preset = render_condition_preset_loader()

    if active_environment_preset:
        try:
            preset_payload = load_environment_preset(active_environment_preset)
        except FileNotFoundError:
            st.sidebar.warning("선택한 환경 프리셋 파일을 찾지 못해 기본 환경 설정으로 진행합니다.")
            st.session_state["dashboard_active_environment_preset"] = ""
            active_environment_preset = None
        else:
            apply_environment_preset(base_config, preset_payload)

    if active_condition_preset:
        try:
            preset_payload = load_condition_preset(active_condition_preset)
        except FileNotFoundError:
            st.sidebar.warning("선택한 차량/요청 프리셋 파일을 찾지 못해 기본 설정으로 진행합니다.")
            st.session_state["dashboard_active_condition_preset"] = ""
            active_condition_preset = None
        else:
            apply_condition_preset(base_config, base_scenario, preset_payload)

    editor_source_signature = json.dumps(
        {
            "config": selected_config.name,
            "scenario": selected_scenario.name,
            "environment_preset": active_environment_preset or "",
            "condition_preset": active_condition_preset or "",
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    if advanced_settings:
        config, scenario = build_dashboard_inputs(base_config, base_scenario, editor_source_signature, True)
    else:
        with st.sidebar:
            config, scenario = build_dashboard_inputs(base_config, base_scenario, editor_source_signature, False)
    config.pop("integrations", None)
    if detect_spatial_mode(config, scenario) == "latlon":
        config["simulation"]["routing_provider"] = "osm"
    config.setdefault("visualization", {})
    config["visualization"]["map_provider"] = "openstreetmap"

    with st.sidebar:
        batch_spec_to_run = None
        if advanced_settings:
            render_environment_preset_saver(config, detect_spatial_mode(config, scenario))
            render_condition_preset_saver(config, scenario, detect_spatial_mode(config, scenario))
            with st.expander("연결 진단", expanded=False):
                render_routing_status_panel(config, scenario)
            batch_spec_to_run = render_batch_experiment_controls(
                config, scenario, policy_name, editor_source_signature,
            )

    run_requested = st.button("시뮬레이션 실행", type="primary", use_container_width=True)
    preview_env = MobilityEnv(config=config, scenario=scenario)
    initial_snapshot = build_initial_preview_snapshot(preview_env.timeline[0], scenario)
    spatial_mode = detect_spatial_mode(config, scenario)

    st.subheader("초기 상태")
    if advanced_settings:
        render_initial_summary(config, scenario, initial_snapshot)
    else:
        render_compact_metric_cards([
            ("차량", f"{config['vehicles']['count']}대"),
            ("요청", f"{len(scenario.get('requests', []))}건"),
            ("배차 정책", _policy_label(policy_name)),
        ], columns_per_row=3)
    render_widget_alignment_spacer(8)
    if spatial_mode == "latlon" and advanced_settings:
        map_col, control_col = st.columns([1.58, 0.82])
        with map_col:
            render_spatial_view(
                initial_snapshot,
                config,
                scenario,
                compact=True,
                show_supporting_info=False,
            )
        with control_col:
            render_initial_position_editor(config, scenario, initial_snapshot)
    else:
        render_spatial_view(
            initial_snapshot,
            config,
            scenario,
            compact=True,
            show_supporting_info=False,
        )

    if batch_spec_to_run is not None:
        progress_bar = st.progress(0.0, text="배치 실험 준비 중...")

        def _update_batch_progress(current_run: int, total_runs: int, case_label: str, policy_name: str) -> None:
            ratio = min(max(float(current_run) / max(1, total_runs), 0.0), 1.0)
            progress_bar.progress(
                ratio,
                text=f"배치 실험 실행 중... {current_run}/{total_runs} | {case_label} | {policy_name}",
            )

        with st.spinner("배치 실험을 자동 실행하고 있습니다..."):
            batch_result = run_dashboard_batch_experiment(
                config,
                scenario,
                batch_spec_to_run,
                progress_callback=_update_batch_progress,
            )
        progress_bar.empty()
        st.session_state["dashboard_batch_result"] = batch_result
        st.session_state["dashboard_batch_flash"] = (
            f"배치 실험 완료: case {batch_result['case_count']}개 / 실행 {batch_result['run_count']}회"
        )
        st.rerun()

    if run_requested:
        try:
            result = run_simulation(config, scenario, policy_name)
        except ValueError as exc:
            st.error(f"경로 또는 입력을 확인해 주세요: {exc}")
            st.stop()
        st.session_state["dashboard_result"] = result
        st.session_state["dashboard_config"] = copy.deepcopy(config)
        st.session_state["dashboard_scenario"] = copy.deepcopy(scenario)
        st.session_state["dashboard_run_signature"] = make_run_signature(config, scenario, policy_name)
        st.session_state["dashboard_policy_name"] = policy_name
        st.session_state["dashboard_selected_frame"] = max(0, len(result["timeline"]) - 1)

    has_single_result = "dashboard_result" in st.session_state
    if not has_single_result:
        st.info("좌측 입력값을 조정한 뒤 `시뮬레이션 실행`을 눌러 결과를 확인하세요.")
    else:
        current_signature = make_run_signature(config, scenario, policy_name)
        if st.session_state.get("dashboard_run_signature") != current_signature:
            st.warning("현재 화면 설정이 마지막 실행 결과와 다릅니다. 차량·요청·속도 설정을 바꿨다면 `시뮬레이션 실행`을 다시 눌러야 반영됩니다.")

        result = st.session_state["dashboard_result"]
        st.divider()
        st.subheader("실행 결과")
        active_config = copy.deepcopy(st.session_state.get("dashboard_config", config))
        # 지도 표시 변경은 이미 계산된 이동 결과에도 즉시 반영합니다.
        active_config["visualization"] = copy.deepcopy(config["visualization"])
        active_scenario = st.session_state.get("dashboard_scenario", scenario)
        render_result_summary(result, active_config, active_scenario)

        timeline = result["timeline"]
        max_frame_index = max(0, len(timeline) - 1)
        if "dashboard_selected_frame" not in st.session_state:
            st.session_state["dashboard_selected_frame"] = max_frame_index
        st.session_state["dashboard_selected_frame"] = min(
            max_frame_index,
            max(0, int(st.session_state["dashboard_selected_frame"])),
        )
        selected_frame = int(st.session_state["dashboard_selected_frame"])
        frame = timeline[selected_frame]

        active_timeline = timeline[: selected_frame + 1]
        if detect_spatial_mode(active_config, active_scenario) == "latlon":
            map_col, label_col = st.columns([1.55, 0.55])
            with map_col:
                render_spatial_view(
                    frame,
                    active_config,
                    active_scenario,
                    compact=False,
                    timeline=active_timeline,
                    show_supporting_info=False,
                )
            with label_col:
                render_map_label_panel(build_map_payload(frame, timeline=active_timeline), compact=False)
        else:
            render_spatial_view(
                frame,
                active_config,
                active_scenario,
                compact=False,
                timeline=active_timeline,
                show_supporting_info=False,
            )
        render_timeline_scrubber(frame, timeline, active_config, active_scenario)
        render_dashboard_autoplay_driver(timeline, active_config, active_scenario)
        render_step_summary(frame, active_config, active_scenario, timeline=active_timeline)

        tab1, tab2 = st.tabs(["요청 상태", "차량 상태"])
        with tab1:
            st.dataframe(frame["requests"], use_container_width=True)
        with tab2:
            st.dataframe(frame["vehicles"], use_container_width=True)

        with st.expander("선택 단계 상세", expanded=False):
            render_frame_details(
                frame,
                st.session_state.get("dashboard_config", config),
                timeline=active_timeline,
            )

        if detect_spatial_mode(
            st.session_state.get("dashboard_config", config),
            st.session_state.get("dashboard_scenario", scenario),
        ) == "latlon":
            with st.expander("지도 참고 정보", expanded=False):
                render_spatial_supporting_info(
                    frame,
                    active_config,
                    st.session_state.get("dashboard_scenario", scenario),
                    timeline=active_timeline,
                )

    if "dashboard_batch_result" in st.session_state:
        st.divider()
        st.subheader("배치 실험 결과")
        render_batch_experiment_results(st.session_state["dashboard_batch_result"])



def build_dashboard_inputs(base_config: dict, base_scenario: dict, source_signature: str, advanced: bool) -> tuple[dict, dict]:
    """위젯이 숨겨져도 설정 스냅샷으로 간편/상세 전환 중 값을 보존합니다."""
    saved = st.session_state.get("dashboard_editor_snapshot", {})
    same_source = saved.get("source") == source_signature
    config = copy.deepcopy(saved["config"] if same_source else base_config)
    scenario = copy.deepcopy(saved["scenario"] if same_source else base_scenario)
    if not same_source or saved.get("advanced") != advanced:
        # Streamlit은 사라진 위젯의 상태를 정리하므로 진입 시 스냅샷에서 다시 채웁니다.
        st.session_state.pop("dashboard_condition_editor_source", None)
        for key in ("quick_vehicle_count", "quick_request_count", "quick_duration"):
            st.session_state.pop(key, None)
    config.pop("integrations", None)
    if detect_spatial_mode(config, scenario) == "latlon":
        config["simulation"]["routing_provider"] = "osm"
    if advanced:
        config, scenario = build_editable_inputs(config, scenario, source_signature)
    else:
        config, scenario = build_quick_inputs(config, scenario)
    st.session_state["dashboard_editor_snapshot"] = {
        "source": source_signature, "advanced": advanced,
        "config": copy.deepcopy(config), "scenario": copy.deepcopy(scenario),
    }
    return config, scenario


def build_quick_inputs(config: dict, scenario: dict) -> tuple[dict, dict]:
    """기본 조건만 표시하고 기존 개별 좌표·요청·보상은 그대로 유지합니다."""
    spatial_mode = detect_spatial_mode(config, scenario)
    simulation = config.setdefault("simulation", {})
    simulation["space_mode"] = spatial_mode
    vehicles = config.setdefault("vehicles", {})
    st.markdown("**기본 조건**")
    vehicle_count = int(st.number_input(
        "차량 수", min_value=1, max_value=max(50, int(vehicles.get("count", 1))),
        value=int(vehicles.get("count", 1)), key="quick_vehicle_count",
    ))
    old_count = max(1, len(scenario.get("requests", [])))
    request_count = int(st.number_input(
        "요청 수", min_value=1, max_value=max(200, old_count), value=old_count, key="quick_request_count",
    ))
    old_duration = int(simulation.get("max_time", 7200 if spatial_mode == "latlon" else 100))
    duration = int(st.number_input(
        "실행 시간 (초)" if spatial_mode == "latlon" else "실행 길이 (단계)",
        min_value=1, max_value=max(86400 if spatial_mode == "latlon" else 500, old_duration),
        value=old_duration, key="quick_duration", help="7200초는 2시간입니다.",
    ))
    simulation["max_time"] = duration
    if int(vehicles.get("available_time", old_duration)) == old_duration:
        vehicles["available_time"] = duration
    positions = shared_ensure_vehicle_positions(config, vehicle_count=vehicle_count, spatial_mode=spatial_mode)
    regenerate = st.button("요청 다시 생성", key="quick_regenerate", use_container_width=True)
    if regenerate or request_count != len(scenario.get("requests", [])):
        generation = scenario.setdefault("metadata", {}).setdefault("request_generation", {})
        seed = int(generation.get("seed", simulation.get("random_seed", 42)))
        if regenerate:
            seed += 1
        radius = float(generation.get("radius", _default_request_generation_radius(spatial_mode)))
        scenario["requests"] = generate_random_requests(positions, request_count, spatial_mode, config, seed, radius)
        generation.update({"seed": seed, "radius": radius, "request_count": request_count})
    if spatial_mode == "latlon":
        st.caption(f"실행 {duration / 60:g}분 · 경로: {_provider_label(simulation.get('routing_provider', 'osm'))}")
    return config, scenario


def build_editable_inputs(base_config: dict, base_scenario: dict, source_signature: str) -> tuple[dict, dict]:
    config = copy.deepcopy(base_config)
    scenario = copy.deepcopy(base_scenario)
    config.setdefault("simulation", {})
    spatial_mode = detect_spatial_mode(config, scenario)
    config["simulation"]["space_mode"] = spatial_mode
    sync_condition_editor_state(config, scenario, spatial_mode, source_signature)
    _consume_pending_vehicle_position_update(spatial_mode)
    _consume_pending_initial_map_move(config, scenario, spatial_mode)
    time_unit_label = "sec" if spatial_mode == "latlon" else "step"
    distance_unit_label = "deg" if spatial_mode == "latlon" else "cell"
    speed_unit_label = "deg/step" if spatial_mode == "latlon" else "cell/step"
    if spatial_mode == "latlon":
        config["simulation"].setdefault("routing_provider", "osm")
        config["simulation"].setdefault("window_start", _current_day_start_text())
        config.setdefault("visualization", {})
        config["visualization"].setdefault("map_provider", "openstreetmap")

    with st.expander("환경 설정", expanded=False):
        _render_section_description("시뮬레이션 시간 간격과 OSM 주행 평균 속도 같은 실행 조건을 설정합니다.")
        if spatial_mode == "grid":
            col1, col2, col3 = st.columns(3)
            with col1:
                config["simulation"]["grid_width"] = st.number_input(
                    "Grid Width",
                    min_value=4,
                    max_value=50,
                    value=int(config["simulation"]["grid_width"]),
                    help="격자 시뮬레이션에서 X축 셀 수입니다.",
                )
            with col2:
                config["simulation"]["grid_height"] = st.number_input(
                    "Grid Height",
                    min_value=4,
                    max_value=50,
                    value=int(config["simulation"]["grid_height"]),
                    help="격자 시뮬레이션에서 Y축 셀 수입니다.",
                )
            with col3:
                config["simulation"]["max_time"] = st.number_input(
                    f"Max Time ({time_unit_label})",
                    min_value=10,
                    max_value=500,
                    value=int(config["simulation"]["max_time"]),
                    help="에피소드가 종료되는 최대 시뮬레이션 시간입니다.",
                )
        else:
            col1, col2, col3 = st.columns(3)
            with col1:
                config["simulation"]["max_time"] = st.number_input(
                    f"Max Time ({time_unit_label})",
                    min_value=60,
                    max_value=86400,
                    value=int(config["simulation"]["max_time"]),
                    help="에피소드 최대 길이입니다. 기본값 7200 sec는 2시간 시뮬레이션을 뜻합니다.",
                )
            with col2:
                config["simulation"]["time_step"] = st.number_input(
                    f"Time Step ({time_unit_label})",
                    min_value=1,
                    max_value=3600,
                    value=int(config["simulation"].get("time_step", 30)),
                    help="한 step이 표현하는 실제 시간 길이입니다. 작을수록 더 촘촘하게 재생됩니다.",
                )
            with col3:
                st.text_input(
                    "Spatial Mode",
                    value="latlon",
                    disabled=True,
                    help="현재 시나리오가 위도/경도 좌표계를 사용한다는 뜻입니다.",
                )
            config["simulation"]["routing_provider"] = "osm"
            st.caption("OSM 도로망으로 경로를 계산합니다. 인터넷 없이 계산하며, 지도 배경에는 인터넷이 필요합니다.")
            config["simulation"]["osm_average_speed_kph"] = st.number_input(
                "주행 평균 속도 (km/h)", min_value=5.0, max_value=80.0,
                value=float(config["simulation"].get("osm_average_speed_kph", 25.0)), step=1.0,
                help="실시간 교통이 아닌 시뮬레이션용 일정 속도입니다.",
            )
            config["simulation"]["window_start"] = st.text_input(
                "시뮬레이션 시작 시각", value=str(config["simulation"].get("window_start", "2026-04-02 00:00:00")),
            )
            st.caption("시간 기준: 한국 시간(KST). 기본 도로망 범위를 벗어나면 좌표를 수정해 주세요.")

    with st.expander("차량 설정", expanded=False):
        _render_section_description(
            f"차량 수, 용량, 가용 시간과 시작 위치를 설정합니다. lat/lon 모드 기본 시작점은 `{DEFAULT_LATLON_START_LABEL}`이며, 차량별로 직접 수정할 수 있습니다."
        )
        current_vehicle_count = int(config["vehicles"]["count"])
        previous_vehicle_count = int(st.session_state.get("dashboard_vehicle_count", 0))
        default_vehicle_count = 1
        vehicle_count = st.number_input(
            "차량 수",
            min_value=1,
            max_value=max(50, current_vehicle_count),
            key="vehicle_count_input",
            help="초기 배치할 차량 대수입니다.",
        )
        config["vehicles"]["count"] = int(vehicle_count)
        current_capacity = int(config["vehicles"]["capacity"])
        config["vehicles"]["capacity"] = st.number_input(
            "차량 최대 수용 인원",
            min_value=1,
            max_value=max(10, current_capacity),
            key="vehicle_capacity_input",
            help="차량 한 대가 동시에 탑승시킬 수 있는 최대 승객 수입니다.",
        )
        current_speed = float(config["vehicles"]["speed"])
        if spatial_mode == "grid":
            config["vehicles"]["speed"] = st.number_input(
                f"차량 속도 ({speed_unit_label})",
                min_value=1.0,
                max_value=max(5.0, current_speed),
                step=1.0,
                key="vehicle_speed_input",
            )
        else:
            # Keep an internal fallback speed for simple routing without exposing the
            # ambiguous degree-based control in the UI.
            config["vehicles"]["speed"] = max(current_speed, 1e-6)
        current_available_time = int(config["vehicles"]["available_time"])
        simulation_max_time = max(1, int(config.get("simulation", {}).get("max_time", current_available_time)))
        config["vehicles"]["available_time"] = st.number_input(
            f"차량 가용 시간 ({time_unit_label})",
            min_value=1,
            max_value=max(simulation_max_time, current_available_time),
            key="vehicle_available_time_input",
            help="이 시간까지 차량을 사용할 수 있습니다. 보통 시뮬레이션 Max Time 이내에서 설정합니다.",
        )

        default_start_position = DEFAULT_LATLON_START if spatial_mode == "latlon" else get_first_demand_origin(scenario, spatial_mode)
        initial_positions = build_default_vehicle_positions(
            config,
            vehicle_count,
            spatial_mode,
            default_start_position,
            previous_vehicle_count=previous_vehicle_count,
        )
        edited_positions: list[list[float]] = []
        st.caption(
            "기본 시작 위치는 강남 기준 기본 좌표로 채워집니다. 차량 수를 늘리면 마지막으로 입력한 좌표를 복사해 새 차량 시작 위치에 사용합니다."
            if spatial_mode == "latlon"
            else "기본 시작 위치는 첫 번째 수요 발생 위치로 채워집니다. 차량 수를 늘리면 마지막으로 입력한 좌표를 복사해 새 차량 시작 위치에 사용합니다."
        )

        for index in range(vehicle_count):
            c1, c2 = st.columns(2)
            with c1:
                if spatial_mode == "grid":
                    _set_widget_default(f"vehicle_x_{index}", int(initial_positions[index][0]))
                    start_x = st.number_input(
                        f"차량 {index + 1} 시작 X",
                        key=f"vehicle_x_{index}",
                        help="차량의 초기 X 좌표입니다.",
                    )
                else:
                    _set_widget_default(f"vehicle_lat_{index}", float(initial_positions[index][0]))
                    start_x = st.number_input(
                        f"차량 {index + 1} 시작 위도",
                        format="%.7f",
                        key=f"vehicle_lat_{index}",
                        help="차량의 초기 위도입니다. 지도 시작 위치가 여기서 결정됩니다.",
                    )
            with c2:
                if spatial_mode == "grid":
                    _set_widget_default(f"vehicle_y_{index}", int(initial_positions[index][1]))
                    start_y = st.number_input(
                        f"차량 {index + 1} 시작 Y",
                        key=f"vehicle_y_{index}",
                        help="차량의 초기 Y 좌표입니다.",
                    )
                else:
                    _set_widget_default(f"vehicle_lon_{index}", float(initial_positions[index][1]))
                    start_y = st.number_input(
                        f"차량 {index + 1} 시작 경도",
                        format="%.7f",
                        key=f"vehicle_lon_{index}",
                        help="차량의 초기 경도입니다. 지도 시작 위치가 여기서 결정됩니다.",
                    )
            if spatial_mode == "grid":
                edited_positions.append([int(start_x), int(start_y)])
            else:
                edited_positions.append([float(start_x), float(start_y)])
                search_query_key = f"vehicle_address_query_{index}"
                search_results_key = f"vehicle_address_results_{index}"
                search_error_key = f"vehicle_address_error_{index}"
                result_select_key = f"vehicle_address_result_index_{index}"
                search_col1, search_col2 = st.columns([1.0, 0.35])
                with search_col1:
                    _set_widget_default(search_query_key, "")
                    address_query = st.text_input(
                        f"차량 {index + 1} 시작 위치 주소 검색",
                        key=search_query_key,
                        help="도로명 주소, 지번 주소, 건물명, 상세 주소(층/호 포함)를 입력할 수 있습니다. 층/호 정보는 검색 시 기본 주소로 정규화됩니다.",
                    )
                with search_col2:
                    render_widget_alignment_spacer()
                    address_search_clicked = st.button(
                        "주소 검색",
                        key=f"vehicle_address_search_{index}",
                        use_container_width=True,
                        help="입력한 주소를 좌표로 변환합니다.",
                    )
                if address_search_clicked:
                    results, error_message = _search_address_candidates(address_query)
                    if results and len(results) == 1 and not error_message and bool(results[0].get("is_precise")):
                        single_result = results[0]
                        st.session_state[search_results_key] = results
                        st.session_state[search_error_key] = ""
                        st.session_state["dashboard_vehicle_address_flash"] = (
                            f"차량 {index + 1} 시작 위치를 `{single_result['label']}` 좌표로 설정했습니다."
                        )
                        _queue_vehicle_position_update(
                            index,
                            [float(single_result["lat"]), float(single_result["lon"])],
                        )
                        st.rerun()
                    st.session_state[search_results_key] = results
                    st.session_state[search_error_key] = error_message or ""
                    if results:
                        st.session_state[result_select_key] = 0

                search_error = str(st.session_state.get(search_error_key, "") or "").strip()
                search_results = list(st.session_state.get(search_results_key, []))
                address_flash = st.session_state.pop("dashboard_vehicle_address_flash", "")
                if address_flash:
                    st.success(address_flash)
                if search_error:
                    st.warning(search_error)
                if search_results:
                    selected_result_index = st.selectbox(
                        f"차량 {index + 1} 주소 검색 결과",
                        options=list(range(len(search_results))),
                        key=result_select_key,
                        format_func=lambda idx: (
                            f"[{search_results[int(idx)].get('precision_label', '-')}] "
                            f"{search_results[int(idx)]['label']}"
                        ),
                        help="적용할 주소 검색 결과를 고릅니다.",
                    )
                    selected_result = search_results[int(selected_result_index)]
                    st.caption(
                        f"선택 좌표: 위도 {selected_result['lat']:.6f}, 경도 {selected_result['lon']:.6f}"
                    )
                    detail_notes: list[str] = [f"정확도: {selected_result.get('precision_label', '-')}"]
                    matched_query = str(selected_result.get("matched_query", "") or "").strip()
                    if matched_query:
                        detail_notes.append(f"실제 검색어: {matched_query}")
                    if selected_result.get("matched_with_variant"):
                        detail_notes.append("상세주소를 정규화한 검색 결과")
                    st.caption(" / ".join(detail_notes))
                    if not bool(selected_result.get("is_precise")):
                        st.warning("이 결과는 건물 수준이 아니라 도로/행정동 중심점일 수 있습니다. 지도에서 위치를 꼭 확인해 주세요.")
                    if st.button(
                        "이 주소로 시작 위치 설정",
                        key=f"vehicle_address_apply_{index}",
                        use_container_width=True,
                    ):
                        st.session_state["dashboard_vehicle_address_flash"] = (
                            f"차량 {index + 1} 시작 위치를 `{selected_result['label']}` 좌표로 설정했습니다."
                        )
                        _queue_vehicle_position_update(
                            index,
                            [float(selected_result["lat"]), float(selected_result["lon"])],
                        )
                        st.rerun()

        config["vehicles"]["initial_positions"] = edited_positions
        st.session_state["dashboard_vehicle_count"] = int(vehicle_count)

    with st.expander("요청 설정", expanded=False):
        _render_section_description("요청 수와 생성 반경만 지정하면, 현재 차량 시작 위치 근방에서 출발지/도착지, 생성 시각, 최대 대기 시간을 겹치지 않게 랜덤 생성합니다. lat/lon 모드에서는 생성 좌표가 도로망 밖이면 가까운 OSM 도로 노드로 스냅합니다.")
        available_request_count = len(scenario.get("requests", []))
        existing_request_count = max(1, available_request_count)
        request_count = st.number_input(
            "요청 수",
            min_value=1,
            max_value=max(200, available_request_count, existing_request_count),
            key="request_count_input",
            help="자동 생성하거나 재생할 총 요청 건수입니다.",
        )
        generation_radius_unit = _request_generation_distance_unit(spatial_mode)
        default_radius = float(
            scenario.get("metadata", {}).get("request_generation", {}).get("radius", _default_request_generation_radius(spatial_mode))
        )
        _set_widget_default("request_generation_radius_input", default_radius)

        generator_col1, generator_col2 = st.columns([1.2, 0.8])
        with generator_col1:
            generation_radius = st.number_input(
                f"차량 근방 생성 반경 ({generation_radius_unit})",
                min_value=1.0 if spatial_mode == "grid" else 50.0,
                max_value=50.0 if spatial_mode == "grid" else 5000.0,
                step=1.0 if spatial_mode == "grid" else 50.0,
                key="request_generation_radius_input",
                help="각 요청의 출발지와 도착지는 차량 시작 위치 중 하나를 기준으로 이 반경 안에서 샘플링됩니다.",
            )
        with generator_col2:
            render_widget_alignment_spacer()
            regenerate_requests = st.button(
                "요청 다시 생성",
                use_container_width=True,
                help="같은 차량 설정과 반경을 유지한 채 새로운 랜덤 요청 세트를 만듭니다.",
            )

        if regenerate_requests:
            st.session_state["dashboard_request_generation_seed"] = (uuid.uuid4().int >> 96) & 0xFFFFFFFF
            st.session_state["dashboard_request_generation_lock"] = False

        vehicle_positions = copy.deepcopy(config.get("vehicles", {}).get("initial_positions", []))
        request_seed = int(st.session_state.get("dashboard_request_generation_seed", (uuid.uuid4().int >> 96) & 0xFFFFFFFF))
        generation_signature = build_random_request_generation_signature(
            vehicle_positions,
            int(request_count),
            spatial_mode,
            float(generation_radius),
            request_seed,
            config,
        )
        request_generation_locked = bool(st.session_state.get("dashboard_request_generation_lock", False))
        existing_generated_requests = copy.deepcopy(st.session_state.get("dashboard_generated_requests", []))
        if request_generation_locked and existing_generated_requests:
            resized_requests = _resize_request_collection(existing_generated_requests, int(request_count), spatial_mode)
            _write_request_defaults_to_state(resized_requests, spatial_mode)
            st.session_state["dashboard_request_generation_signature"] = generation_signature
        if (
            not request_generation_locked
            and (
                regenerate_requests
                or st.session_state.get("dashboard_request_generation_signature") != generation_signature
                or "dashboard_generated_requests" not in st.session_state
            )
        ):
            st.session_state["dashboard_generated_requests"] = generate_random_requests(
                vehicle_positions=vehicle_positions,
                request_count=int(request_count),
                spatial_mode=spatial_mode,
                config=config,
                seed=request_seed,
                radius=float(generation_radius),
            )
            st.session_state["dashboard_request_generation_signature"] = generation_signature

        current_generated_requests = copy.deepcopy(st.session_state.get("dashboard_generated_requests", []))
        bulk_wait_signature = json.dumps(
            {
                "generation_signature": generation_signature,
                "request_count": len(current_generated_requests),
                "max_wait_values": [int(request.get("max_wait_time", 0) or 0) for request in current_generated_requests],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        default_bulk_max_wait = (
            int(current_generated_requests[0].get("max_wait_time", 1) or 1)
            if current_generated_requests
            else max(1, int(config["simulation"].get("time_step", 1)))
        )
        if st.session_state.get("dashboard_bulk_max_wait_signature") != bulk_wait_signature:
            st.session_state["request_bulk_max_wait_input"] = default_bulk_max_wait
            st.session_state["dashboard_bulk_max_wait_signature"] = bulk_wait_signature

        bulk_col1, bulk_col2 = st.columns([1.2, 0.8])
        with bulk_col1:
            bulk_max_wait_time = st.number_input(
                f"최대 대기 시간 일괄 설정 ({time_unit_label})",
                min_value=1,
                max_value=max(1, int(config["simulation"].get("max_time", 1))),
                key="request_bulk_max_wait_input",
                help="현재 생성된 모든 요청의 최대 대기 시간을 같은 값으로 맞춥니다.",
            )
        with bulk_col2:
            render_widget_alignment_spacer()
            apply_bulk_max_wait = st.button(
                "최대 대기 일괄 적용",
                use_container_width=True,
                help="표에 보이는 현재 요청 전체에 같은 최대 대기 시간을 적용합니다.",
            )

        if apply_bulk_max_wait and current_generated_requests:
            updated_requests = copy.deepcopy(current_generated_requests)
            for request in updated_requests:
                request["max_wait_time"] = int(bulk_max_wait_time)
            _write_request_defaults_to_state(updated_requests, spatial_mode)
            st.session_state["dashboard_request_generation_lock"] = True
            current_generated_requests = updated_requests

        scenario["requests"] = copy.deepcopy(current_generated_requests)
        scenario.setdefault("metadata", {})
        scenario["metadata"]["request_generation"] = {
            "radius": float(generation_radius),
            "seed": int(request_seed),
        }
        st.session_state["dashboard_request_count"] = int(request_count)

        wait_min, wait_max = _random_wait_time_bounds(
            int(config["simulation"].get("max_time", 1)),
            int(config["simulation"].get("time_step", 1)),
        )
        st.caption(
            f"랜덤 생성 규칙: 차량 시작 위치 반경 {float(generation_radius):.0f} {generation_radius_unit} 안에서 요청을 만들고, "
            f"생성 시각은 0~{int(config['simulation']['max_time'])} {time_unit_label}, 최대 대기 시간은 {wait_min}~{wait_max} {time_unit_label} 범위에서 무작위로 배정합니다. "
            "lat/lon 모드에서는 생성된 좌표가 도로망 위가 아니면 가까운 접근 가능한 OSM 도로 노드로 보정합니다."
        )

        if spatial_mode == "latlon":
            preview_rows = [
                {
                    "요청 ID": request["request_id"],
                    f"생성 시각 ({time_unit_label})": int(request["created_at"]),
                    "출발 위도": round(float(request["origin"][0]), 6),
                    "출발 경도": round(float(request["origin"][1]), 6),
                    "도착 위도": round(float(request["destination"][0]), 6),
                    "도착 경도": round(float(request["destination"][1]), 6),
                    f"최대 대기 ({time_unit_label})": int(request["max_wait_time"]),
                }
                for request in scenario["requests"]
            ]
        else:
            preview_rows = [
                {
                    "요청 ID": request["request_id"],
                    f"생성 시각 ({time_unit_label})": int(request["created_at"]),
                    "출발 X": int(request["origin"][0]),
                    "출발 Y": int(request["origin"][1]),
                    "도착 X": int(request["destination"][0]),
                    "도착 Y": int(request["destination"][1]),
                    f"최대 대기 ({time_unit_label})": int(request["max_wait_time"]),
                }
                for request in scenario["requests"]
            ]
        st.dataframe(preview_rows, use_container_width=True, height=min(320, 80 + 36 * max(1, len(preview_rows))))

    with st.expander("보상 가중치", expanded=False):
        _render_section_description("학습 보상 함수의 가중치를 조정합니다. 완료 보상, 대기시간 패널티, 이동거리 패널티가 정책의 성향을 크게 바꿉니다.")
        col1, col2, col3 = st.columns(3)
        with col1:
            config["reward"]["wait_time"] = st.number_input(
                "대기시간 패널티",
                min_value=0.0,
                value=float(config["reward"]["wait_time"]),
                step=0.1,
                help="승객 대기 시간이 길어질수록 받는 불이익입니다.",
            )
            config["reward"]["travel_distance"] = st.number_input(
                "이동거리 패널티",
                min_value=0.0,
                value=float(config["reward"]["travel_distance"]),
                step=0.1,
                help="차량이 많이 움직일수록 받는 비용입니다.",
            )
        with col2:
            config["reward"]["completed_request"] = st.number_input(
                "완료 보상",
                min_value=0.0,
                value=float(config["reward"]["completed_request"]),
                step=0.1,
                help="요청을 성공적으로 완료했을 때 얻는 보상입니다.",
            )
            config["reward"]["cancelled_request"] = st.number_input(
                "취소 패널티",
                min_value=0.0,
                value=float(config["reward"]["cancelled_request"]),
                step=0.1,
                help="대기 초과 등으로 요청이 취소됐을 때 받는 패널티입니다.",
            )
        with col3:
            config["reward"]["violation"] = st.number_input(
                "제약 위반 패널티",
                min_value=0.0,
                value=float(config["reward"]["violation"]),
                step=0.1,
                help="잘못된 배차나 용량 초과 같은 제약 위반에 대한 패널티입니다.",
            )
        render_reward_weight_guide(config["reward"], spatial_mode, time_unit_label, distance_unit_label)

    return config, scenario


def run_simulation(config: dict, scenario: dict, policy_name: str) -> dict:
    env = MobilityEnv(config=config, scenario=scenario)
    policy = build_policy(policy_name)
    return run_episode(env, policy).to_dict()


def make_run_signature(config: dict, scenario: dict, policy_name: str) -> str:
    simulation_config = copy.deepcopy(config)
    simulation_config.pop("visualization", None)
    simulation_config.pop("integrations", None)
    payload = {
        "config": simulation_config,
        "scenario": scenario,
        "policy_name": policy_name,
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def _split_batch_list_text(raw_text: object) -> list[str]:
    text = str(raw_text or "").strip()
    if not text:
        return []
    return [chunk.strip() for chunk in re.split(r"[\n,]+", text) if chunk.strip()]


def _parse_batch_int_list(raw_text: object, field_label: str, minimum: int | None = None) -> list[int]:
    values: list[int] = []
    for chunk in _split_batch_list_text(raw_text):
        try:
            value = int(chunk)
        except ValueError as exc:
            raise ValueError(f"`{field_label}`에는 정수만 입력해 주세요: `{chunk}`") from exc
        if minimum is not None and value < minimum:
            raise ValueError(f"`{field_label}`에는 {minimum} 이상의 값만 입력할 수 있습니다.")
        values.append(value)
    if not values:
        raise ValueError(f"`{field_label}` 값을 하나 이상 입력해 주세요.")
    return list(dict.fromkeys(values))


def _parse_batch_float_list(raw_text: object, field_label: str, minimum: float | None = None) -> list[float]:
    values: list[float] = []
    for chunk in _split_batch_list_text(raw_text):
        try:
            value = float(chunk)
        except ValueError as exc:
            raise ValueError(f"`{field_label}`에는 숫자만 입력해 주세요: `{chunk}`") from exc
        if minimum is not None and value < minimum:
            raise ValueError(f"`{field_label}`에는 {minimum:g} 이상의 값만 입력할 수 있습니다.")
        values.append(value)
    if not values:
        raise ValueError(f"`{field_label}` 값을 하나 이상 입력해 주세요.")
    deduped: list[float] = []
    for value in values:
        if value not in deduped:
            deduped.append(value)
    return deduped


def _parse_batch_datetime_list(raw_text: object, field_label: str) -> list[str]:
    values: list[str] = []
    for chunk in _split_batch_list_text(raw_text):
        parsed = _parse_display_datetime(chunk)
        if parsed is None:
            raise ValueError(f"`{field_label}` 형식이 올바르지 않습니다: `{chunk}`")
        values.append(parsed.strftime("%Y-%m-%d %H:%M:%S"))
    if not values:
        raise ValueError(f"`{field_label}` 값을 하나 이상 입력해 주세요.")
    return list(dict.fromkeys(values))


def _derive_average_wait_seconds_from_logs(request_logs: list[dict]) -> float:
    if not request_logs:
        return 0.0
    waits: list[float] = []
    for request in request_logs:
        created_at = float(request.get("created_at", 0) or 0)
        pickup_time = request.get("pickup_time")
        cancelled_at = request.get("cancelled_at")
        if pickup_time is not None:
            waits.append(max(0.0, float(pickup_time) - created_at))
        elif cancelled_at is not None:
            waits.append(max(0.0, float(cancelled_at) - created_at))
        else:
            waits.append(max(0.0, float(request.get("waiting_time", 0) or 0)))
    return sum(waits) / len(waits)


def _derive_vehicle_operating_seconds(timeline: list[dict]) -> float:
    if len(timeline) < 2:
        return 0.0
    operating_seconds = 0.0
    for previous_frame, current_frame in zip(timeline, timeline[1:]):
        step_seconds = max(0.0, float(current_frame.get("time", 0)) - float(previous_frame.get("time", 0)))
        if step_seconds <= 0:
            continue
        previous_positions = {
            str(vehicle.get("vehicle_id")): tuple(vehicle.get("position", ()))
            for vehicle in previous_frame.get("vehicles", [])
        }
        current_positions = {
            str(vehicle.get("vehicle_id")): tuple(vehicle.get("position", ()))
            for vehicle in current_frame.get("vehicles", [])
        }
        for vehicle_id, current_position in current_positions.items():
            if previous_positions.get(vehicle_id) != current_position:
                operating_seconds += step_seconds
    return operating_seconds


def _expand_batch_case_grid(axes: dict[str, list[object]]) -> list[dict[str, object]]:
    keys = [key for key, values in axes.items() if values]
    if not keys:
        return [{}]
    cases: list[dict[str, object]] = []
    for combination in itertools.product(*(axes[key] for key in keys)):
        cases.append({key: value for key, value in zip(keys, combination)})
    return cases


def _format_batch_case_label(index: int, case_values: dict[str, object]) -> str:
    if not case_values:
        return f"case_{index:03d}"
    parts = []
    for key, value in case_values.items():
        normalized_key = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", str(key)).strip("._-") or "field"
        normalized_value = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", str(value)).strip("._-") or "value"
        parts.append(f"{normalized_key}-{normalized_value}")
    return f"case_{index:03d}__" + "__".join(parts[:8])


def _write_batch_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _aggregate_batch_policy_rows(run_rows: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for row in run_rows:
        grouped.setdefault(str(row["policy"]), []).append(row)

    summary_rows: list[dict] = []
    for policy_name, rows in sorted(grouped.items()):
        best_row = max(rows, key=lambda item: float(item["total_reward"]))
        summary_rows.append(
            {
                "policy": policy_name,
                "run_count": len(rows),
                "avg_total_reward": round(sum(float(item["total_reward"]) for item in rows) / len(rows), 3),
                "avg_success_rate": round(sum(float(item["success_rate"]) for item in rows) / len(rows), 4),
                "avg_completed_requests": round(sum(float(item["completed_requests"]) for item in rows) / len(rows), 3),
                "avg_cancelled_requests": round(sum(float(item["cancelled_requests"]) for item in rows) / len(rows), 3),
                "avg_total_distance": round(sum(float(item["total_distance"]) for item in rows) / len(rows), 4),
                "avg_wait_seconds": round(sum(float(item["average_wait_seconds"]) for item in rows) / len(rows), 3),
                "best_total_reward": round(float(best_row["total_reward"]), 3),
                "best_case_id": best_row["case_id"],
            }
        )
    return summary_rows


def _write_dashboard_batch_report(path: Path, result_payload: dict) -> None:
    top_rows = sorted(result_payload["run_rows"], key=lambda row: float(row["total_reward"]), reverse=True)[:10]
    lines = [
        "# Dashboard Batch Experiment Report",
        "",
        f"- 생성 시각: `{result_payload['generated_at']}`",
        f"- 총 case 수: `{result_payload['case_count']}`",
        f"- 총 실행 수: `{result_payload['run_count']}`",
        f"- 정책: `{', '.join(result_payload['spec']['policies'])}`",
        "",
        "## 정책별 요약",
        "",
    ]
    if result_payload["policy_summary_rows"]:
        header = "| policy | run_count | avg_total_reward | avg_success_rate | best_case_id |"
        divider = "| --- | --- | --- | --- | --- |"
        body = [
            f"| {row['policy']} | {row['run_count']} | {row['avg_total_reward']} | {row['avg_success_rate']} | {row['best_case_id']} |"
            for row in result_payload["policy_summary_rows"]
        ]
        lines.extend([header, divider, *body])
    else:
        lines.append("_데이터 없음_")
    lines.extend(
        [
            "",
            "## 상위 실행 결과",
            "",
        ]
    )
    if top_rows:
        header = "| case_id | policy | total_reward | success_rate | completed_requests | cancelled_requests |"
        divider = "| --- | --- | --- | --- | --- | --- |"
        body = [
            f"| {row['case_id']} | {row['policy']} | {row['total_reward']} | {row['success_rate']} | {row['completed_requests']} | {row['cancelled_requests']} |"
            for row in top_rows
        ]
        lines.extend([header, divider, *body])
    else:
        lines.append("_데이터 없음_")
    lines.extend(
        [
            "",
            "## 배치 설정",
            "",
            "```json",
            json.dumps(result_payload["spec"], ensure_ascii=False, indent=2),
            "```",
            "",
            "## 출력 파일",
            "",
            "- `run_results.csv`",
            "- `policy_summary.csv`",
            "- `report.md`",
        ]
    )
    if result_payload["spec"].get("save_run_payloads"):
        lines.append("- `runs/*.json`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _prepare_dashboard_batch_case(
    base_config: dict,
    base_scenario: dict,
    spatial_mode: str,
    case_values: dict[str, object],
    fixed_request_set: bool,
) -> tuple[dict, dict]:
    config = copy.deepcopy(base_config)
    scenario = copy.deepcopy(base_scenario)
    config.setdefault("simulation", {})
    config.setdefault("vehicles", {})
    scenario.setdefault("metadata", {})

    vehicle_count = int(case_values.get("vehicle_count", config.get("vehicles", {}).get("count", 1)))
    config["vehicles"]["count"] = vehicle_count
    if "window_start" in case_values:
        config["simulation"]["window_start"] = str(case_values["window_start"])

    vehicle_positions = shared_ensure_vehicle_positions(config, vehicle_count=vehicle_count, spatial_mode=spatial_mode)

    if not fixed_request_set:
        first_request = (scenario.get("requests", []) or [{}])[0]
        request_count = int(case_values.get("request_count", len(scenario.get("requests", [])) or 1))
        generation_radius = float(
            case_values.get(
                "radius",
                scenario.get("metadata", {}).get("request_generation", {}).get("radius", _default_request_generation_radius(spatial_mode)),
            )
        )
        generation_seed = int(
            case_values.get(
                "seed",
                scenario.get("metadata", {}).get("request_generation", {}).get("seed", 42),
            )
        )
        fixed_max_wait = int(
            case_values.get(
                "max_wait_time",
                first_request.get("max_wait_time", config.get("simulation", {}).get("time_step", 30)),
            )
        )
        scenario["requests"] = shared_generate_random_requests(
            vehicle_positions=vehicle_positions,
            request_count=request_count,
            spatial_mode=spatial_mode,
            config=config,
            seed=generation_seed,
            radius=generation_radius,
            fixed_max_wait_time=fixed_max_wait,
        )
        scenario["metadata"]["request_generation"] = {
            "radius": generation_radius,
            "seed": generation_seed,
            "request_count": request_count,
            "fixed_max_wait_time": fixed_max_wait,
        }

    return config, scenario


def run_dashboard_batch_experiment(
    base_config: dict,
    base_scenario: dict,
    batch_spec: dict,
    progress_callback: Callable[..., None] | None = None,
) -> dict:
    spatial_mode = detect_spatial_mode(base_config, base_scenario)
    case_values_list = _expand_batch_case_grid(batch_spec["axes"])
    policies = list(batch_spec["policies"])
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_dir = ROOT_DIR / "results" / "dashboard_batch_reports" / f"dashboard_batch_{timestamp}"
    runs_dir = output_dir / "runs"
    if batch_spec.get("save_run_payloads"):
        runs_dir.mkdir(parents=True, exist_ok=True)
    else:
        output_dir.mkdir(parents=True, exist_ok=True)

    run_rows: list[dict] = []
    total_runs = max(1, len(case_values_list) * max(1, len(policies)))
    current_run = 0
    for case_index, case_values in enumerate(case_values_list, start=1):
        config, scenario = _prepare_dashboard_batch_case(
            base_config,
            base_scenario,
            spatial_mode,
            case_values,
            fixed_request_set=bool(batch_spec.get("fixed_request_set", False)),
        )
        case_label = _format_batch_case_label(case_index, case_values)
        for policy_name in policies:
            result = run_simulation(config, scenario, policy_name)
            metrics = result.get("metrics", {})
            row = {
                "case_id": case_label,
                "policy": policy_name,
                "vehicle_count": int(config.get("vehicles", {}).get("count", 1)),
                "request_count": len(scenario.get("requests", [])),
                "window_start": str(config.get("simulation", {}).get("window_start", "")),
                "generation_radius": scenario.get("metadata", {}).get("request_generation", {}).get("radius", "-"),
                "generation_seed": scenario.get("metadata", {}).get("request_generation", {}).get("seed", "-"),
                "max_wait_time": scenario.get("metadata", {}).get("request_generation", {}).get("fixed_max_wait_time", "-"),
                "total_reward": round(float(result.get("total_reward", 0.0)), 3),
                "completed_requests": int(metrics.get("completed_requests", 0)),
                "cancelled_requests": int(metrics.get("cancelled_requests", 0)),
                "success_rate": round(float(metrics.get("success_rate", 0.0)), 4),
                "total_distance": round(float(metrics.get("total_distance", 0.0)), 4),
                "average_wait_seconds": round(_derive_average_wait_seconds_from_logs(result.get("request_logs", [])), 3),
                "vehicle_operating_seconds": round(_derive_vehicle_operating_seconds(result.get("timeline", [])), 3),
            }
            run_rows.append(row)
            current_run += 1
            if progress_callback is not None:
                progress_callback(
                    current_run,
                    total_runs,
                    case_label=case_label,
                    policy_name=policy_name,
                )
            if batch_spec.get("save_run_payloads"):
                run_path = runs_dir / f"{case_label}__{policy_name}.json"
                run_path.write_text(
                    json.dumps(
                        {
                            "generated_at": generated_at,
                            "case_id": case_label,
                            "policy": policy_name,
                            "case_values": case_values,
                            "config": config,
                            "scenario": scenario,
                            "result": result,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )

    policy_summary_rows = _aggregate_batch_policy_rows(run_rows)
    _write_batch_csv(output_dir / "run_results.csv", run_rows)
    _write_batch_csv(output_dir / "policy_summary.csv", policy_summary_rows)
    result_payload = {
        "generated_at": generated_at,
        "output_dir": str(output_dir),
        "run_rows": run_rows,
        "policy_summary_rows": policy_summary_rows,
        "case_count": len(case_values_list),
        "run_count": len(run_rows),
        "spec": copy.deepcopy(batch_spec),
    }
    _write_dashboard_batch_report(output_dir / "report.md", result_payload)
    return result_payload


def find_diagnostic_route(scenario: dict, config: dict) -> tuple[tuple[float, float], tuple[float, float]] | None:
    requests = scenario.get("requests", [])
    if requests:
        first_request = requests[0]
        origin = tuple(first_request.get("origin", []))
        destination = tuple(first_request.get("destination", []))
        if len(origin) >= 2 and len(destination) >= 2:
            return (float(origin[0]), float(origin[1])), (float(destination[0]), float(destination[1]))

    positions = config.get("vehicles", {}).get("initial_positions", [])
    if len(positions) >= 2:
        start = tuple(positions[0])
        end = tuple(positions[1])
        if len(start) >= 2 and len(end) >= 2:
            return (float(start[0]), float(start[1])), (float(end[0]), float(end[1]))
    return None




def render_routing_status_panel(config: dict, scenario: dict) -> None:
    if detect_spatial_mode(config, scenario) != "latlon":
        st.info("격자 모드는 외부 지도나 경로 서비스를 사용하지 않습니다.")
        return
    from simulator.osm_routing import load_osm_graph
    graph = load_osm_graph()
    st.success(f"내장 OSM 도로망 준비 완료: {len(graph['nodes']):,}개 지점")
    st.caption("서울 동작·서초 실습 지역. API 키와 외부 경로 요청 없이 계산합니다. 일방통행을 반영하며 실시간 교통·회전 제한은 반영하지 않습니다.")
    st.caption(f"범위 [남, 서, 북, 동]: {graph['bounds']}")


def render_initial_summary(config: dict, scenario: dict, snapshot: dict) -> None:
    st.markdown("**초기 환경 요약**")
    spatial_mode = detect_spatial_mode(config, scenario)
    time_unit_label, distance_unit_label = _dashboard_units(spatial_mode)
    summary_items = [
        ("차량 수", str(len(snapshot["vehicles"]))),
        ("요청 수", str(len(scenario.get("requests", [])))),
    ]
    if spatial_mode == "grid":
        summary_items.append(("Grid", f'{config["simulation"]["grid_width"]} x {config["simulation"]["grid_height"]}'))
    else:
        summary_items.extend(
            [
                ("좌표계", "lat/lon"),
                (f"Time Step ({time_unit_label})", str(config["simulation"].get("time_step", 30))),
            ]
        )
    render_compact_metric_cards(summary_items, columns_per_row=2)
    extra_items = []
    if spatial_mode != "grid":
        extra_items.extend(
            [
                ("라우팅", _provider_label(str(config["simulation"].get("routing_provider", "simple")))),
                ("평균 속도 (km/h)", str(config["simulation"].get("osm_average_speed_kph", 25.0))),
            ]
        )
    if extra_items:
        render_widget_alignment_spacer(4)
        render_compact_metric_cards(extra_items, columns_per_row=2)


def build_initial_preview_snapshot(snapshot: dict, scenario: dict) -> dict:
    preview_snapshot = copy.deepcopy(snapshot)
    scenario_requests = list(scenario.get("requests", []))
    if not scenario_requests:
        return preview_snapshot

    snapshot_request_by_id = {
        str(request.get("request_id")): copy.deepcopy(request)
        for request in snapshot.get("requests", [])
    }
    current_time = int(float(snapshot.get("time", 0)))
    preview_requests: list[dict] = []
    for request in scenario_requests:
        request_id = str(request.get("request_id", ""))
        snapshot_request = snapshot_request_by_id.get(request_id, {})
        created_at = int(request.get("created_at", snapshot_request.get("created_at", 0)) or 0)
        preview_request = {
            **snapshot_request,
            "request_id": request_id,
            "created_at": created_at,
            "origin": copy.deepcopy(request.get("origin", snapshot_request.get("origin", [0.0, 0.0]))),
            "destination": copy.deepcopy(request.get("destination", snapshot_request.get("destination", [0.0, 0.0]))),
            "max_wait_time": int(request.get("max_wait_time", snapshot_request.get("max_wait_time", 0)) or 0),
        }
        preview_request.setdefault("status", "waiting" if created_at <= current_time else "pending")
        preview_request["waiting_time"] = max(0, current_time - created_at)
        preview_requests.append(preview_request)

    preview_snapshot["requests"] = preview_requests
    return preview_snapshot


def _current_vehicle_count(config: dict, snapshot: dict | None = None) -> int:
    configured_count = max(1, int(config.get("vehicles", {}).get("count", 1)))
    snapshot_count = len(snapshot.get("vehicles", [])) if snapshot else 0
    session_count = int(st.session_state.get("dashboard_vehicle_count", configured_count))
    return max(1, configured_count, snapshot_count, session_count)


def _current_request_count(scenario: dict) -> int:
    session_count = int(st.session_state.get("dashboard_request_count", 0) or 0)
    generated_count = len(st.session_state.get("dashboard_generated_requests", []))
    scenario_count = len(scenario.get("requests", []))
    return max(1, session_count, generated_count, scenario_count)


def _read_current_vehicle_positions(config: dict, scenario: dict, spatial_mode: str) -> list[list[float]]:
    vehicle_count = _current_vehicle_count(config)
    default_position = get_first_demand_origin(scenario, spatial_mode)
    configured_positions = list(config.get("vehicles", {}).get("initial_positions", []))
    positions: list[list[float]] = []
    last_position = _normalize_coordinate_pair(default_position, spatial_mode, default_position)
    for index in range(vehicle_count):
        candidate = configured_positions[index] if index < len(configured_positions) else last_position
        resolved_position = _read_vehicle_position(index, spatial_mode, candidate, default_position)
        positions.append(resolved_position)
        last_position = resolved_position
    return positions


def _read_current_requests(scenario: dict, spatial_mode: str) -> list[dict]:
    request_count = _current_request_count(scenario)
    generated_requests = list(st.session_state.get("dashboard_generated_requests", []))
    base_requests = generated_requests or list(scenario.get("requests", []))
    current_requests: list[dict] = []
    for index in range(request_count):
        base_request = (
            base_requests[index]
            if index < len(base_requests)
            else _default_request_template(index, spatial_mode)
        )
        current_requests.append(_read_request_defaults(index, base_request, spatial_mode))
    return current_requests


def _initial_map_target_options(snapshot: dict, scenario: dict, config: dict | None = None) -> list[tuple[str, str]]:
    options: list[tuple[str, str]] = []
    vehicle_count = (
        _current_vehicle_count(config or {}, snapshot)
        if config is not None
        else max(1, len(snapshot.get("vehicles", [])))
    )
    for index in range(1, vehicle_count + 1):
        options.append((f"vehicle:{index - 1}", f"V{index} 차량 시작 위치"))
    request_count = _current_request_count(scenario)
    for index in range(1, request_count + 1):
        options.append((f"origin:{index - 1}", f"O{index} 출발지"))
    for index in range(1, request_count + 1):
        options.append((f"destination:{index - 1}", f"D{index} 도착지"))
    return options


def _current_target_coordinate(target_key: str, config: dict, scenario: dict, spatial_mode: str) -> list[float]:
    target_type, index_text = target_key.split(":", maxsplit=1)
    index = int(index_text)
    if target_type == "vehicle":
        positions = _read_current_vehicle_positions(config, scenario, spatial_mode)
        if index < len(positions):
            return list(positions[index])
        fallback = get_first_demand_origin(scenario, spatial_mode)
        return list(_normalize_coordinate_pair(None, spatial_mode, fallback))

    requests = _read_current_requests(scenario, spatial_mode)
    request_defaults = (
        requests[index]
        if index < len(requests)
        else _default_request_template(index, spatial_mode)
    )
    if target_type == "origin":
        return list(request_defaults["origin"])
    return list(request_defaults["destination"])


def _move_latlon_coordinate(coord: list[float], direction: str, step_meters: float) -> list[float]:
    lat, lon = float(coord[0]), float(coord[1])
    lat_step = float(step_meters) / 111_320.0
    lon_divisor = max(1.0, 111_320.0 * math.cos(math.radians(lat)))
    lon_step = float(step_meters) / lon_divisor
    if direction == "up":
        lat += lat_step
    elif direction == "down":
        lat -= lat_step
    elif direction == "left":
        lon -= lon_step
    elif direction == "right":
        lon += lon_step
    return [max(-90.0, min(90.0, lat)), max(-180.0, min(180.0, lon))]


def _queue_vehicle_position_update(index: int, updated_coord: list[float]) -> None:
    st.session_state["dashboard_pending_vehicle_position_update"] = {
        "index": int(index),
        "coord": [float(updated_coord[0]), float(updated_coord[1])],
    }


def _consume_pending_vehicle_position_update(spatial_mode: str) -> None:
    pending_update = st.session_state.pop("dashboard_pending_vehicle_position_update", None)
    if not pending_update:
        return
    index = int(pending_update.get("index", -1))
    coord = pending_update.get("coord")
    if index < 0 or not isinstance(coord, list) or len(coord) < 2:
        return
    if spatial_mode == "grid":
        st.session_state[f"vehicle_x_{index}"] = int(coord[0])
        st.session_state[f"vehicle_y_{index}"] = int(coord[1])
    else:
        st.session_state[f"vehicle_lat_{index}"] = float(coord[0])
        st.session_state[f"vehicle_lon_{index}"] = float(coord[1])
    st.session_state["dashboard_request_generation_lock"] = True


def _normalize_address_query_text(query: str) -> str:
    text = re.sub(r"\s+", " ", str(query or "").replace("\n", " ")).strip(" ,")
    return re.sub(r"\s*,\s*", ", ", text)


def _strip_trailing_detail_address(query: str) -> str:
    current = _normalize_address_query_text(query)
    trailing_patterns = [
        r"(?:\s|,)*(?:지하\s*)?\d+\s*층$",
        r"(?:\s|,)*(?:B|b)\s*\d+$",
        r"(?:\s|,)*\d+\s*호$",
        r"(?:\s|,)*\d+\s*호실$",
        r"(?:\s|,)*\d+\s*층\s*\d+\s*호$",
        r"(?:\s|,)*\d+\s*호\s*\d+\s*층$",
    ]
    while True:
        previous = current
        current = re.sub(r"(\d+(?:-\d+)?)\s*번지\b", r"\1", current)
        for pattern in trailing_patterns:
            current = re.sub(pattern, "", current).strip(" ,")
        if current == previous:
            break
    return _normalize_address_query_text(current)


def _build_address_search_queries(query: str) -> list[str]:
    original = _normalize_address_query_text(query)
    if not original:
        return []

    candidates: list[str] = [original]
    stripped = _strip_trailing_detail_address(original)
    if stripped and stripped != original:
        candidates.append(stripped)

    without_parens = _normalize_address_query_text(re.sub(r"\([^)]*\)", " ", stripped))
    if without_parens and without_parens != stripped:
        candidates.append(without_parens)

    paren_chunks = [
        _normalize_address_query_text(chunk)
        for chunk in re.findall(r"\(([^)]*)\)", original)
        if _normalize_address_query_text(chunk)
    ]
    for chunk in paren_chunks:
        if chunk not in candidates:
            candidates.append(chunk)
        combined = _normalize_address_query_text(f"{without_parens} {chunk}")
        if combined and combined not in candidates:
            candidates.append(combined)

    expanded_candidates: list[str] = []
    for candidate in candidates:
        if candidate not in expanded_candidates:
            expanded_candidates.append(candidate)
        prefixed = candidate if candidate.startswith("대한민국 ") else f"대한민국 {candidate}"
        if prefixed not in expanded_candidates:
            expanded_candidates.append(prefixed)
    return expanded_candidates


def _classify_address_result(item: dict) -> tuple[str, bool]:
    address = item.get("address") or {}
    addresstype = str(item.get("addresstype") or "").strip().lower()
    place_rank = int(item.get("place_rank") or 0)
    has_house_number = bool(address.get("house_number"))
    exact_address_types = {
        "building",
        "amenity",
        "office",
        "shop",
        "commercial",
        "house",
        "residential",
        "tourism",
        "attraction",
    }
    broad_address_types = {
        "quarter",
        "suburb",
        "neighbourhood",
        "city_district",
        "borough",
        "town",
        "village",
        "hamlet",
    }
    if has_house_number or addresstype in exact_address_types or place_rank >= 28:
        return "건물/출입구 수준", True
    if addresstype in broad_address_types or place_rank <= 22:
        return "행정동/지역 수준", False
    return "도로/지점 수준", False


@st.cache_data(show_spinner=False, ttl=600)
def _search_address_candidates(query: str, limit: int = 5) -> tuple[list[dict], str | None]:
    search_query = _normalize_address_query_text(query)
    if not search_query:
        return [], "검색할 주소를 입력해 주세요."

    search_queries = _build_address_search_queries(search_query)
    raw_results: list[dict] = []
    seen_keys: set[tuple[int, int, str]] = set()
    last_error: str | None = None

    for search_variant in search_queries:
        params = urlencode(
            {
                "q": search_variant,
                "format": "jsonv2",
                "limit": max(1, min(int(limit), 10)),
                "addressdetails": 1,
                "accept-language": "ko",
                "countrycodes": "kr",
            }
        )
        request = Request(
            f"https://nominatim.openstreetmap.org/search?{params}",
            headers={
                "User-Agent": "RLMobilitySimulator/1.0 (dashboard address search)",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=6) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            last_error = f"주소 검색 요청이 실패했습니다. HTTP {exc.code}"
            continue
        except URLError:
            last_error = "주소 검색 서버에 연결하지 못했습니다."
            continue
        except Exception:
            last_error = "주소 검색 중 오류가 발생했습니다."
            continue

        for item in payload if isinstance(payload, list) else []:
            try:
                lat = float(item["lat"])
                lon = float(item["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            precision_label, is_precise = _classify_address_result(item)
            label = str(item.get("display_name", search_variant))
            dedupe_key = (round(lat * 1_000_000), round(lon * 1_000_000), label)
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            raw_results.append(
                {
                    "label": label,
                    "lat": lat,
                    "lon": lon,
                    "precision_label": precision_label,
                    "is_precise": is_precise,
                    "matched_query": search_variant,
                    "matched_with_variant": search_variant != search_query,
                    "addresstype": str(item.get("addresstype") or ""),
                    "place_rank": int(item.get("place_rank") or 0),
                }
            )

    if not raw_results:
        return [], last_error or "검색 결과가 없습니다."

    sorted_results = sorted(
        raw_results,
        key=lambda item: (
            0 if item.get("is_precise") else 1,
            0 if not item.get("matched_with_variant") else 1,
            -int(item.get("place_rank") or 0),
            str(item.get("label") or ""),
        ),
    )
    return sorted_results[: max(1, min(int(limit), 10))], None


def _apply_initial_map_move(
    target_key: str,
    direction: str,
    step_size: float,
    config: dict,
    scenario: dict,
    spatial_mode: str,
) -> None:
    target_type, index_text = target_key.split(":", maxsplit=1)
    index = int(index_text)
    current_coord = _current_target_coordinate(target_key, config, scenario, spatial_mode)
    if spatial_mode == "grid":
        deltas = {
            "up": [0, 1],
            "down": [0, -1],
            "left": [-1, 0],
            "right": [1, 0],
        }
        delta = deltas.get(direction, [0, 0])
        updated_coord = [int(current_coord[0] + delta[0]), int(current_coord[1] + delta[1])]
    else:
        updated_coord = _move_latlon_coordinate(current_coord, direction, step_size)

    if target_type == "vehicle":
        vehicle_positions = _read_current_vehicle_positions(config, scenario, spatial_mode)
        if index >= len(vehicle_positions):
            return
        vehicle_positions[index] = list(updated_coord)
        if spatial_mode == "grid":
            st.session_state[f"vehicle_x_{index}"] = int(vehicle_positions[index][0])
            st.session_state[f"vehicle_y_{index}"] = int(vehicle_positions[index][1])
        else:
            st.session_state[f"vehicle_lat_{index}"] = float(vehicle_positions[index][0])
            st.session_state[f"vehicle_lon_{index}"] = float(vehicle_positions[index][1])
        st.session_state["dashboard_request_generation_lock"] = True
        return

    requests = _read_current_requests(scenario, spatial_mode)
    requests = _resize_request_collection(requests, max(index + 1, len(requests), 1), spatial_mode)
    field = "origin" if target_type == "origin" else "destination"
    requests[index][field] = updated_coord
    _write_request_defaults_to_state(requests, spatial_mode)
    st.session_state["dashboard_request_generation_lock"] = True


def _queue_initial_map_move(target_key: str, direction: str, step_size: float) -> None:
    st.session_state["dashboard_pending_initial_map_move"] = {
        "target_key": str(target_key),
        "direction": str(direction),
        "step_size": float(step_size),
    }


def _consume_pending_initial_map_move(config: dict, scenario: dict, spatial_mode: str) -> None:
    pending_move = st.session_state.pop("dashboard_pending_initial_map_move", None)
    if not pending_move:
        return
    target_key = str(pending_move.get("target_key", "")).strip()
    direction = str(pending_move.get("direction", "")).strip()
    step_size = float(pending_move.get("step_size", 0.0))
    if not target_key or direction not in {"up", "down", "left", "right"}:
        return
    _apply_initial_map_move(target_key, direction, step_size, config, scenario, spatial_mode)


def _normalize_initial_step_widget_value(step_key: str, value: object) -> object:
    if value is None:
        return value
    if step_key in {
        "initial_map_move_step_meters",
        "initial_map_move_level_meters",
        "initial_map_move_step_cells",
        "initial_map_move_level_cells",
    }:
        return int(round(float(value)))
    return value


def _queue_initial_step_value(step_key: str, value: object, level_key: str | None = None, level_value: object | None = None) -> None:
    st.session_state["dashboard_pending_initial_step_value"] = {
        "step_key": str(step_key),
        "value": _normalize_initial_step_widget_value(step_key, value),
        "level_key": str(level_key or ""),
        "level_value": (
            None
            if level_value is None or not level_key
            else _normalize_initial_step_widget_value(str(level_key), level_value)
        ),
    }


def _consume_pending_initial_step_value() -> None:
    pending_value = st.session_state.pop("dashboard_pending_initial_step_value", None)
    if not pending_value:
        return
    step_key = str(pending_value.get("step_key", "")).strip()
    if not step_key:
        return
    st.session_state[step_key] = _normalize_initial_step_widget_value(step_key, pending_value.get("value"))
    level_key = str(pending_value.get("level_key", "")).strip()
    if level_key:
        st.session_state[level_key] = _normalize_initial_step_widget_value(
            level_key,
            pending_value.get("level_value"),
        )


def render_initial_position_editor(config: dict, scenario: dict, snapshot: dict) -> None:
    spatial_mode = detect_spatial_mode(config, scenario)
    st.markdown("**위치 조정**")
    target_options = _initial_map_target_options(snapshot, scenario, config)
    if not target_options:
        st.info("조정할 위치 데이터가 없습니다.")
        return

    option_keys = [key for key, _label in target_options]
    option_labels = {key: label for key, label in target_options}
    selected_target = st.session_state.get("initial_map_selected_target", option_keys[0])
    if selected_target not in option_keys:
        selected_target = option_keys[0]
        st.session_state["initial_map_selected_target"] = selected_target

    selected_target = st.selectbox(
        "이동 대상",
        option_keys,
        key="initial_map_selected_target",
        format_func=lambda key: option_labels.get(key, key),
        help="차량 시작 위치, 요청 출발지, 요청 도착지 중 하나를 골라 상하좌우 버튼으로 미세 조정합니다.",
    )

    if spatial_mode == "latlon":
        _set_widget_default("initial_map_move_step_meters", 30)
        _set_widget_default("initial_map_move_level_meters", 10)
        step_level_key = "initial_map_move_level_meters"
        step_label = "이동 간격 (m)"
        step_value_key = "initial_map_move_step_meters"
        step_min = 1
        step_max = 500
        step_help = "버튼을 한 번 누를 때 이동하는 실제 거리 기준입니다."
        quick_step_values = [1, 10, 100]
        quick_step_labels = ["1m", "10m", "100m"]
    else:
        _set_widget_default("initial_map_move_step_cells", 1)
        _set_widget_default("initial_map_move_level_cells", 1)
        step_level_key = "initial_map_move_level_cells"
        step_label = "이동 간격 (cell)"
        step_value_key = "initial_map_move_step_cells"
        step_min = 1
        step_max = 10
        step_help = None
        quick_step_values = [1, 2, 5]
        quick_step_labels = ["1", "2", "5"]
    _consume_pending_initial_step_value()
    raw_step_level = st.session_state.get(step_level_key, quick_step_values[0])
    step_level = max(1, int(round(float(raw_step_level))))
    if raw_step_level != step_level or not isinstance(raw_step_level, int):
        st.session_state[step_level_key] = step_level
    raw_step_value = st.session_state.get(step_value_key, step_level)
    current_step_value = int(round(float(raw_step_value)))
    aligned_step_value = current_step_value
    if step_level > 1:
        aligned_step_value = max(
            step_level,
            int(round(current_step_value / step_level)) * step_level,
        )
    aligned_step_value = max(step_min, min(step_max, aligned_step_value))
    if raw_step_value != aligned_step_value or not isinstance(raw_step_value, int):
        st.session_state[step_value_key] = aligned_step_value
    step_kwargs = {
        "min_value": step_min,
        "max_value": step_max,
        "step": step_level,
        "key": step_value_key,
        "format": "%d",
    }
    if step_help:
        step_kwargs["help"] = step_help

    current_coord = _current_target_coordinate(selected_target, config, scenario, spatial_mode)
    if spatial_mode == "latlon":
        render_editor_metric_cards(
            [
                ("현재 위도", f"{float(current_coord[0]):.6f}"),
                ("현재 경도", f"{float(current_coord[1]):.6f}"),
            ]
        )
    else:
        render_editor_metric_cards(
            [
                ("현재 X", str(int(current_coord[0]))),
                ("현재 Y", str(int(current_coord[1]))),
            ]
        )

    render_widget_alignment_spacer(0)
    label_left, label_center, label_right = st.columns([1.0, 1.32, 1.0])
    with label_center:
        st.caption(step_label)
    _top_left, top_center, _top_right = st.columns([1.0, 1.32, 1.0])
    mid_left, mid_center, mid_right = st.columns([1.0, 1.32, 1.0])
    _bottom_left, bottom_center, _bottom_right = st.columns([1.0, 1.32, 1.0])
    move_direction = None
    with top_center:
        if st.button("상", use_container_width=True, key="initial_move_up"):
            move_direction = "up"
    with mid_left:
        if st.button("좌", use_container_width=True, key="initial_move_left"):
            move_direction = "left"
    with mid_center:
        step_size = st.number_input(step_label, label_visibility="collapsed", **step_kwargs)
    with mid_right:
        if st.button("우", use_container_width=True, key="initial_move_right"):
            move_direction = "right"
    with bottom_center:
        if st.button("하", use_container_width=True, key="initial_move_down"):
            move_direction = "down"
    render_widget_alignment_spacer(0)
    level_label_col, quick_col1, quick_col2, quick_col3 = st.columns([0.88, 1.0, 1.0, 1.0])
    with level_label_col:
        st.caption("이동 레벨")
    for quick_col, quick_value, quick_label in zip((quick_col1, quick_col2, quick_col3), quick_step_values, quick_step_labels):
        with quick_col:
            if st.button(
                quick_label,
                key=f"initial_step_preset_{quick_label}_{spatial_mode}",
                use_container_width=True,
            ):
                _queue_initial_step_value(step_kwargs["key"], quick_value, step_level_key, quick_value)
                st.rerun()

    if move_direction:
        _queue_initial_map_move(selected_target, move_direction, float(int(step_size)))
        st.rerun()


def render_result_summary(result: dict, config: dict, scenario: dict) -> None:
    spatial_mode = detect_spatial_mode(config, scenario)
    _, distance_unit_label = _dashboard_units(spatial_mode)
    reward_config = config.get("reward", {})
    reward_breakdown = _aggregate_reward_breakdown(result.get("step_logs", []))
    summary_items = [
        ("정책", str(result["policy_name"])),
        ("총 Reward", str(round(result["total_reward"], 2))),
        ("완료 요청", str(result["metrics"]["completed_requests"])),
        ("취소 요청", str(result["metrics"]["cancelled_requests"])),
        ("성공률", f'{result["metrics"]["success_rate"] * 100:.1f}%'),
    ]
    if spatial_mode == "latlon":
        spatial_metrics = _compute_latlon_distance_speed_metrics(result.get("timeline", []))
        summary_items.extend(
            [
                ("실제 이동거리 (km)", f'{spatial_metrics["cumulative_distance_km"]:.2f}'),
                ("평균 속도 (km/h)", f'{spatial_metrics["average_speed_kph"]:.2f}'),
                ("시뮬레이션 총 시간", _format_duration_label(float(spatial_metrics["total_simulation_seconds"]))),
                ("차량 운행 시간", _format_duration_label(float(spatial_metrics["vehicle_operating_seconds"]))),
            ]
        )
    else:
        summary_items.append((f"총 이동거리 ({distance_unit_label})", str(round(result["metrics"]["total_distance"], 2))))
    render_compact_metric_cards(summary_items, columns_per_row=4 if spatial_mode == "latlon" else 3)
    if spatial_mode == "latlon":
        st.caption(
            "지표 기준: 성공률 = 완료 요청 수 / 전체 요청 수, "
            "실제 이동거리 = 차량별 actual route 누적 경로를 km로 합산한 값, "
            "평균 속도 = 차량이 실제로 움직인 구간 기준 누적 이동거리 / 누적 이동 시간(km/h), "
            "시뮬레이션 총 시간 = 타임라인의 처음~마지막 상대 시간 차이, "
            "차량 운행 시간 = 차량별로 실제 위치가 변한 구간의 시간 합입니다."
        )
    else:
        st.caption("지표 기준: 성공률 = 완료 요청 수 / 전체 요청 수입니다.")

    waiting_weight = float(reward_config.get("wait_time", 0.0) or 0.0)
    distance_weight = float(reward_config.get("travel_distance", 0.0) or 0.0)
    completed_weight = float(reward_config.get("completed_request", 0.0) or 0.0)
    cancelled_weight = float(reward_config.get("cancelled_request", 0.0) or 0.0)
    waiting_load = 0.0 if math.isclose(waiting_weight, 0.0) else abs(reward_breakdown["waiting_penalty"]) / waiting_weight
    distance_load = 0.0 if math.isclose(distance_weight, 0.0) else abs(reward_breakdown["distance_penalty"]) / distance_weight
    reward_items = [
        ("대기 페널티 합", f'{reward_breakdown["waiting_penalty"]:.2f}'),
        ("이동 페널티 합", f'{reward_breakdown["distance_penalty"]:.2f}'),
        ("완료 보너스 합", f'+{reward_breakdown["completed_bonus"]:.2f}'),
        ("취소 페널티 합", f'{reward_breakdown["cancelled_penalty"]:.2f}'),
    ]
    render_compact_metric_cards(reward_items, columns_per_row=4)
    distance_basis_label = (
        "보상 계산용 이동거리 합"
        if spatial_mode == "latlon"
        else f"보상 계산용 이동거리 합 ({distance_unit_label})"
    )
    st.caption(
        "총 Reward = "
        f"- 대기({waiting_weight:g} x {_format_reward_factor(waiting_load)}) "
        f"- 이동({distance_weight:g} x {_format_reward_factor(distance_load)}) "
        f"+ 완료({completed_weight:g} x {int(result['metrics']['completed_requests'])}) "
        f"- 취소({cancelled_weight:g} x {int(result['metrics']['cancelled_requests'])})"
    )
    st.caption(
        "세부 합산: "
        f"대기 {reward_breakdown['waiting_penalty']:.2f} + "
        f"이동 {reward_breakdown['distance_penalty']:.2f} + "
        f"완료 +{reward_breakdown['completed_bonus']:.2f} + "
        f"취소 {reward_breakdown['cancelled_penalty']:.2f} = "
        f"{reward_breakdown['total_reward']:.2f}. "
        f"{distance_basis_label}은 {_format_reward_factor(distance_load)} 입니다."
    )
    if spatial_mode == "latlon":
        st.caption("참고: 보상의 이동 페널티는 결과 카드의 km 거리와 별도로, 보상 엔진 내부 경로 거리 스칼라 합을 기준으로 계산됩니다.")


def render_batch_experiment_results(batch_result: dict) -> None:
    run_rows = list(batch_result.get("run_rows", []))
    policy_summary_rows = list(batch_result.get("policy_summary_rows", []))
    if not run_rows:
        st.info("배치 실험 결과가 없습니다.")
        return

    best_reward_row = max(run_rows, key=lambda row: float(row.get("total_reward", 0.0)))
    best_success_row = max(run_rows, key=lambda row: float(row.get("success_rate", 0.0)))
    summary_items = [
        ("Case 수", str(batch_result.get("case_count", 0))),
        ("총 실행 수", str(batch_result.get("run_count", 0))),
        ("정책 수", str(len(batch_result.get("spec", {}).get("policies", [])))),
        ("최고 Reward", f'{float(best_reward_row.get("total_reward", 0.0)):.2f}'),
        ("최고 성공률", f'{float(best_success_row.get("success_rate", 0.0)) * 100:.1f}%'),
    ]
    render_compact_metric_cards(summary_items, columns_per_row=5)
    st.caption(
        f"생성 시각: {batch_result.get('generated_at', '-')} / 결과 폴더: {batch_result.get('output_dir', '-')}"
    )

    summary_tab, runs_tab, spec_tab = st.tabs(["정책별 요약", "개별 실행 결과", "배치 설정"])
    with summary_tab:
        st.dataframe(policy_summary_rows, use_container_width=True, height=240)
    with runs_tab:
        st.dataframe(run_rows, use_container_width=True, height=360)
    with spec_tab:
        st.code(json.dumps(batch_result.get("spec", {}), ensure_ascii=False, indent=2), language="json")


def build_route_failure_rows(frame: dict) -> list[dict]:
    failure_rows: list[dict] = []
    for vehicle in frame.get("vehicles", []):
        route_sources = list(vehicle.get("route_history_sources", []))
        route_request_ids = list(vehicle.get("route_history_request_ids", []))
        route_stages = list(vehicle.get("route_history_stages", []))
        route_metadata = list(vehicle.get("route_history_metadata", []))
        for index, route_source in enumerate(route_sources):
            metadata = route_metadata[index] if len(route_metadata) > index else {}
            fallback_reason = metadata.get("fallback_reason")
            if route_source != "simple_fallback":
                continue
            failure_rows.append(
                {
                    "vehicle_id": vehicle.get("vehicle_id"),
                    "request_id": route_request_ids[index] if len(route_request_ids) > index else None,
                    "stage": route_stages[index] if len(route_stages) > index else None,
                    "route_source": route_source,
                    "fallback_reason": fallback_reason or "-",
                    "request_transport": metadata.get("request_transport", "-"),
                    "requested_start": json.dumps(metadata.get("requested_start"), ensure_ascii=False),
                    "requested_end": json.dumps(metadata.get("requested_end"), ensure_ascii=False),
                    "failure_summary": summarize_route_failure(metadata),
                    "response_coord_type": metadata.get("response_coord_type", "-"),
                    "upstream_errors": " | ".join(metadata.get("upstream_errors", [])) or "-",
                }
            )
    return failure_rows


def summarize_route_failure(metadata: dict) -> str:
    return str(metadata.get("error") or metadata.get("fallback_reason") or "경로를 확인해 주세요.")


def build_road_snap_rows(frame: dict) -> list[dict]:
    rows: list[dict] = []
    for vehicle in frame.get("vehicles", []):
        route_sources = list(vehicle.get("route_history_sources", []))
        route_request_ids = list(vehicle.get("route_history_request_ids", []))
        route_stages = list(vehicle.get("route_history_stages", []))
        route_metadata = list(vehicle.get("route_history_metadata", []))
        for index, route_source in enumerate(route_sources):
            metadata = route_metadata[index] if len(route_metadata) > index else {}
            route_snap = metadata.get("road_snap") or metadata.get("snap")
            if not route_snap:
                continue
            start_meta = route_snap.get("start", {})
            end_meta = route_snap.get("end", {})
            rows.append(
                {
                    "vehicle_id": vehicle.get("vehicle_id"),
                    "request_id": route_request_ids[index] if len(route_request_ids) > index else None,
                    "stage": route_stages[index] if len(route_stages) > index else None,
                    "route_source": route_source,
                    "coordinate_mode": metadata.get("coordinate_mode"),
                    "response_coord_type": metadata.get("response_coord_type"),
                    "snap_start_m": start_meta.get("distance_meters"),
                    "snap_end_m": end_meta.get("distance_meters"),
                    "upstream_errors": " | ".join(metadata.get("upstream_errors", [])) or "-",
                }
            )
    return rows


def _shift_dashboard_frame(delta: int, max_index: int) -> None:
    current_index = int(st.session_state.get("dashboard_selected_frame", 0))
    st.session_state["dashboard_selected_frame"] = min(max_index, max(0, current_index + delta))


def _set_dashboard_frame(index: int, max_index: int) -> None:
    st.session_state["dashboard_selected_frame"] = min(max_index, max(0, int(index)))


def _next_frame_index_for_relative_interval(
    timeline: list[dict],
    current_index: int,
    interval_units: float,
) -> int:
    if not timeline:
        return 0
    max_index = len(timeline) - 1
    if current_index >= max_index:
        return max_index
    if interval_units <= 0:
        return min(max_index, current_index + 1)

    current_time = float(timeline[current_index].get("time", 0))
    target_time = current_time + float(interval_units)
    for next_index in range(current_index + 1, len(timeline)):
        if float(timeline[next_index].get("time", 0)) >= target_time:
            return next_index
    return max_index


def _dashboard_autoplay_base_interval_units(spatial_mode: str) -> int:
    return DASHBOARD_AUTOPLAY_BASE_SECONDS if spatial_mode == "latlon" else 1


def _dashboard_autoplay_interval_units(config: dict, scenario: dict) -> float:
    spatial_mode = detect_spatial_mode(config, scenario)
    base_interval_units = float(_dashboard_autoplay_base_interval_units(spatial_mode))
    speed_multiplier = int(st.session_state.get("dashboard_autoplay_speed_multiplier", 1) or 1)
    if speed_multiplier not in DASHBOARD_AUTOPLAY_SPEED_OPTIONS:
        speed_multiplier = 1
    return max(base_interval_units, base_interval_units * float(speed_multiplier))


def _start_dashboard_autoplay(max_index: int) -> None:
    current_index = int(st.session_state.get("dashboard_selected_frame", 0))
    if current_index >= max_index and max_index > 0:
        st.session_state["dashboard_selected_frame"] = 0
    st.session_state["dashboard_autoplay_enabled"] = True
    st.session_state["dashboard_autoplay_last_tick"] = time.monotonic()


def _stop_dashboard_autoplay() -> None:
    st.session_state["dashboard_autoplay_enabled"] = False
    st.session_state.pop("dashboard_autoplay_last_tick", None)


def render_dashboard_autoplay_driver(timeline: list[dict], config: dict, scenario: dict) -> None:
    max_index = max(0, len(timeline) - 1)
    autoplay_enabled = bool(st.session_state.get("dashboard_autoplay_enabled", False))
    run_every = "800ms" if autoplay_enabled else None

    @st.fragment(run_every=run_every)
    def _autoplay_fragment() -> None:
        if not bool(st.session_state.get("dashboard_autoplay_enabled", False)):
            return
        if not timeline:
            _stop_dashboard_autoplay()
            return
        current_index = int(st.session_state.get("dashboard_selected_frame", 0))
        interval_units = _dashboard_autoplay_interval_units(config, scenario)
        now = time.monotonic()
        last_tick = float(st.session_state.get("dashboard_autoplay_last_tick", now))
        if now - last_tick < 0.76:
            return
        if current_index < max_index:
            next_index = _next_frame_index_for_relative_interval(timeline, current_index, interval_units)
            if next_index <= current_index:
                next_index = min(max_index, current_index + 1)
            st.session_state["dashboard_selected_frame"] = next_index
            st.session_state["dashboard_autoplay_last_tick"] = now
            st.rerun()
        else:
            _stop_dashboard_autoplay()
            st.rerun()

    _autoplay_fragment()


def render_timeline_scrubber(frame: dict, timeline: list[dict], config: dict, scenario: dict) -> None:
    spatial_mode = detect_spatial_mode(config, scenario)
    time_unit_label, _ = _dashboard_units(spatial_mode)
    timeline_length = len(timeline)
    max_index = max(0, timeline_length - 1)
    current_index = int(st.session_state.get("dashboard_selected_frame", 0))
    autoplay_enabled = bool(st.session_state.get("dashboard_autoplay_enabled", False))
    base_interval_units = _dashboard_autoplay_base_interval_units(spatial_mode)
    current_speed_multiplier = int(st.session_state.get("dashboard_autoplay_speed_multiplier", 1) or 1)
    if current_speed_multiplier not in DASHBOARD_AUTOPLAY_SPEED_OPTIONS:
        current_speed_multiplier = 1
        st.session_state["dashboard_autoplay_speed_multiplier"] = 1
    _set_widget_default("dashboard_autoplay_speed_multiplier", 1)
    st.markdown("**맵 타임라인**")
    render_compact_metric_cards(
        [
            ("선택 단계", f"{current_index} / {max_index}"),
            (f"상대 시간 ({time_unit_label})", str(frame.get("time", "-"))),
            ("결과 시각", _format_display_datetime(frame.get("absolute_time"))),
        ],
        columns_per_row=3,
    )

    control_col_start, control_col_prev, slider_col, control_col_next, control_col_play, control_col_stop, interval_col = st.columns([0.18, 0.12, 1.0, 0.12, 0.18, 0.15, 0.28])
    with control_col_start:
        render_widget_alignment_spacer()
        st.button(
            "처음",
            key="timeline_reset_button",
            use_container_width=True,
            disabled=current_index <= 0,
            help="초기 상태 단계로 바로 이동합니다.",
            on_click=_set_dashboard_frame,
            args=(0, max_index),
        )
    with control_col_prev:
        render_widget_alignment_spacer()
        st.button(
            "◀",
            key="timeline_prev_button",
            use_container_width=True,
            disabled=current_index <= 0,
            help="한 단계 이전 결과로 이동합니다.",
            on_click=_shift_dashboard_frame,
            args=(-1, max_index),
        )
    with slider_col:
        render_widget_alignment_spacer()
        st.slider(
            "단계별 결과 보기",
            min_value=0,
            max_value=max_index,
            key="dashboard_selected_frame",
            help="슬라이더를 움직이면 위 지도와 아래 상태표가 같은 step으로 함께 갱신됩니다.",
            label_visibility="collapsed",
        )
    with control_col_next:
        render_widget_alignment_spacer()
        st.button(
            "▶",
            key="timeline_next_button",
            use_container_width=True,
            disabled=current_index >= max_index,
            help="한 단계 다음 결과로 이동합니다.",
            on_click=_shift_dashboard_frame,
            args=(1, max_index),
        )
    with control_col_play:
        render_widget_alignment_spacer()
        st.button(
            "재생",
            key="timeline_autoplay_start",
            use_container_width=True,
            disabled=max_index <= 0,
            help="마지막 단계까지 자동으로 한 단계씩 재생합니다.",
            on_click=_start_dashboard_autoplay,
            args=(max_index,),
        )
    with control_col_stop:
        render_widget_alignment_spacer()
        st.button(
            "정지",
            key="timeline_autoplay_stop",
            use_container_width=True,
            disabled=not autoplay_enabled,
            help="자동 재생을 멈춥니다.",
            on_click=_stop_dashboard_autoplay,
        )
    with interval_col:
        render_widget_alignment_spacer()
        st.selectbox(
            "재생 속도",
            DASHBOARD_AUTOPLAY_SPEED_OPTIONS,
            key="dashboard_autoplay_speed_multiplier",
            format_func=lambda value: f"{int(value)}배속",
            label_visibility="collapsed",
            help="1배속은 10초 기준이며, 선택한 배속만큼 한 번의 자동 재생 tick에서 더 멀리 진행합니다.",
        )
        base_label = f"{base_interval_units}초" if spatial_mode == "latlon" else f"{base_interval_units} {time_unit_label}"
        st.caption(f"재생 속도 (1배속 = {base_label})")
    st.caption(
        f"step 인덱스와 해당 시점의 상대 시간({time_unit_label})/결과 시각을 함께 보여줍니다. "
        f"`재생 속도`는 1배속을 {base_label}로 두고, 선택한 배속만큼 한 번에 더 많은 상대 시간 구간을 진행합니다."
    )


def render_step_summary(frame: dict, config: dict, scenario: dict, timeline: list[dict] | None = None) -> None:
    info = frame.get("info", {})
    spatial_mode = detect_spatial_mode(config, scenario)
    time_unit_label, distance_unit_label = _dashboard_units(spatial_mode)
    action = frame.get("action") or {"action_type": "NONE"}
    action_label = action.get("action_type", "NONE")
    if action.get("vehicle_id"):
        action_label += f" · {action['vehicle_id']}"
    if action.get("request_id"):
        action_label += f" -> {action['request_id']}"

    step_items = [
        ("현재 행동", action_label),
        ("Reward", str(round(frame.get("reward", 0.0), 3))),
        ("이번 step 완료 요청", str(info.get("completed_requests", 0))),
    ]
    if spatial_mode == "latlon":
        spatial_metrics = _compute_latlon_distance_speed_metrics(timeline or [frame])
        step_items.extend(
            [
                ("이번 step 이동거리 (km)", f'{spatial_metrics["step_distance_km"]:.3f}'),
                ("이번 step 평균 속도 (km/h)", f'{spatial_metrics["step_speed_kph"]:.2f}'),
            ]
        )
    else:
        step_items.append((f"이번 step 이동거리 ({distance_unit_label})", str(round(info.get("step_distance", 0.0), 2))))
    render_compact_metric_cards(step_items, columns_per_row=5 if spatial_mode == "latlon" else 4)


def render_frame_details(frame: dict, config: dict | None = None, timeline: list[dict] | None = None) -> None:
    spatial_mode = detect_spatial_mode(config or {}, {})
    time_unit_label, distance_unit_label = _dashboard_units(spatial_mode)
    st.markdown("**선택된 단계 정보**")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric(f"상대 시간 ({time_unit_label})", frame["time"])
    with col2:
        st.metric("결과 시각", _format_display_datetime(frame.get("absolute_time")))
    with col3:
        st.metric("단계", frame["stage"])
    with col4:
        st.metric("Reward", round(frame["reward"], 3))

    action = frame.get("action") or {"action_type": "NONE"}
    action_label = action.get("action_type", "NONE")
    if action.get("vehicle_id"):
        action_label += f" · {action['vehicle_id']}"
    if action.get("request_id"):
        action_label += f" -> {action['request_id']}"
    st.caption(f"현재 행동: {action_label}")

    info = frame.get("info", {})
    step_metrics = info.get("step_metrics", {})
    col4, col5, col6 = st.columns(3)
    with col4:
        st.metric("가능 행동 수", info.get("valid_action_count", 0))
    with col5:
        st.metric("이번 step 완료 요청", info.get("completed_requests", 0))
    with col6:
        if spatial_mode == "latlon":
            spatial_metrics = _compute_latlon_distance_speed_metrics(timeline or [frame])
            st.metric("이번 step 이동거리 (km)", round(spatial_metrics["step_distance_km"], 3))
        else:
            st.metric(f"이번 step 이동거리 ({distance_unit_label})", round(info.get("step_distance", 0.0), 2))

    if spatial_mode == "latlon":
        spatial_metrics = _compute_latlon_distance_speed_metrics(timeline or [frame])
        col7, col8, col9 = st.columns(3)
        with col7:
            st.metric("누적 이동거리 (km)", round(spatial_metrics["cumulative_distance_km"], 3))
        with col8:
            st.metric("이번 step 평균 속도 (km/h)", round(spatial_metrics["step_speed_kph"], 2))
        with col9:
            st.metric("누적 평균 속도 (km/h)", round(spatial_metrics["average_speed_kph"], 2))

    if step_metrics:
        if spatial_mode == "latlon":
            st.caption(
                "누적 상태: "
                f"완료 {step_metrics.get('completed_requests', 0)}, "
                f"취소 {step_metrics.get('cancelled_requests', 0)}, "
                f"실제 거리 {round(spatial_metrics['cumulative_distance_km'], 2)} km, "
                f"평균 속도 {round(spatial_metrics['average_speed_kph'], 2)} km/h"
            )
        else:
            st.caption(
                "누적 상태: "
                f"완료 {step_metrics.get('completed_requests', 0)}, "
                f"취소 {step_metrics.get('cancelled_requests', 0)}, "
                f"총거리 {round(step_metrics.get('total_distance', 0.0), 2)} {distance_unit_label}"
            )

    routing_provider = info.get("routing_provider")
    if routing_provider:
        route_failure_rows = build_route_failure_rows(frame)
        road_snap_rows = build_road_snap_rows(frame)
        route_sources = sorted(
            {
                vehicle.get("active_route_source")
                for vehicle in frame.get("vehicles", [])
                if vehicle.get("active_route_source")
            }
        )
        route_label = ", ".join(route_sources) if route_sources else "현재 활성 경로 없음"
        st.caption(f"라우팅 엔진: {routing_provider} / 현재 프레임 경로 출처: {route_label}")
        historical_sources = sorted(
            {
                source
                for vehicle in frame.get("vehicles", [])
                for source in (
                    list(vehicle.get("route_history_sources", []))
                    + list(vehicle.get("actual_route_sources", []))
                )
                if source
            }
        )
        st.caption("OSM 도로망 경로 · 일정 평균 속도 기준 · 실시간 교통 미반영")
        if route_failure_rows:
            st.warning("경로 오류가 있습니다. 좌표와 도로망 범위를 확인해 주세요.")
            st.dataframe(route_failure_rows, use_container_width=True)
        if road_snap_rows:
            with st.expander("도로 연결 위치", expanded=False):
                st.dataframe(road_snap_rows, use_container_width=True)


def detect_spatial_mode(config: dict, scenario: dict) -> str:
    return scenario.get("metadata", {}).get("coordinate_system") or config.get("simulation", {}).get("space_mode", "grid")


def get_first_demand_origin(scenario: dict, spatial_mode: str) -> tuple[float, float]:
    requests = list(scenario.get("requests", []))
    candidate_requests: list[dict] = []
    for index, request in enumerate(requests):
        created_at = st.session_state.get(f"created_at_{index}", request.get("created_at", 0))
        if spatial_mode == "grid":
            origin_x = st.session_state.get(f"origin_x_{index}", request.get("origin", [0, 0])[0])
            origin_y = st.session_state.get(f"origin_y_{index}", request.get("origin", [0, 0])[1])
        else:
            origin_x = st.session_state.get(f"origin_lat_{index}", request.get("origin", [DEFAULT_LATLON_START[0], DEFAULT_LATLON_START[1]])[0])
            origin_y = st.session_state.get(f"origin_lon_{index}", request.get("origin", [DEFAULT_LATLON_START[0], DEFAULT_LATLON_START[1]])[1])
        candidate_requests.append(
            {
                "created_at": int(created_at),
                "origin": [origin_x, origin_y],
                "request_id": request.get("request_id", f"R{index + 1}"),
                "pickup_at": request.get("metadata", {}).get("pickup_at", ""),
            }
        )

    if candidate_requests:
        first_request = min(
            candidate_requests,
            key=lambda item: (
                int(item.get("created_at", 0)),
                str(item.get("pickup_at", "")),
                str(item.get("request_id", "")),
            ),
        )
        origin = first_request.get("origin", [0, 0])
        if len(origin) >= 2:
            if spatial_mode == "grid":
                return (int(origin[0]), int(origin[1]))
            return (float(origin[0]), float(origin[1]))

    fallback = (0, 0) if spatial_mode == "grid" else DEFAULT_LATLON_START
    return fallback


def _normalize_coordinate_pair(
    candidate: list[float] | tuple[float, float] | None,
    spatial_mode: str,
    fallback: tuple[float, float],
) -> list[float]:
    if not isinstance(candidate, (list, tuple)) or len(candidate) < 2:
        candidate = fallback
    if spatial_mode == "grid":
        return [int(candidate[0]), int(candidate[1])]
    return [float(candidate[0]), float(candidate[1])]


def _read_vehicle_position(
    index: int,
    spatial_mode: str,
    candidate: list[float] | tuple[float, float] | None,
    fallback: tuple[float, float],
) -> list[float]:
    normalized_candidate = _normalize_coordinate_pair(candidate, spatial_mode, fallback)
    if spatial_mode == "grid":
        return [
            int(st.session_state.get(f"vehicle_x_{index}", normalized_candidate[0])),
            int(st.session_state.get(f"vehicle_y_{index}", normalized_candidate[1])),
        ]
    return [
        float(st.session_state.get(f"vehicle_lat_{index}", normalized_candidate[0])),
        float(st.session_state.get(f"vehicle_lon_{index}", normalized_candidate[1])),
    ]


def _request_generation_distance_unit(spatial_mode: str) -> str:
    return "m" if spatial_mode == "latlon" else "cell"


def _default_request_generation_radius(spatial_mode: str) -> float:
    return 500.0 if spatial_mode == "latlon" else 3.0


def _approx_point_distance(
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


def _sample_point_near_anchor(
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


def _find_non_overlapping_point(
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
        candidate = _sample_point_near_anchor(anchor, radius, spatial_mode, rng, config)
        if all(_approx_point_distance(candidate, existing, spatial_mode) >= min_gap for existing in used_points):
            return candidate

    fallback_anchor = normalized_anchors[len(used_points) % len(normalized_anchors)]
    if spatial_mode == "grid":
        fallback = [int(fallback_anchor[0]) + len(used_points) + 1, int(fallback_anchor[1]) + len(used_points) + 1]
        return _sample_point_near_anchor(fallback, 0.0, spatial_mode, rng, config)
    fallback = [
        float(fallback_anchor[0]) + (len(used_points) + 1) * 0.00045,
        float(fallback_anchor[1]) + (len(used_points) + 1) * 0.00045,
    ]
    return fallback


def _random_wait_time_bounds(max_time: int, time_step: int) -> tuple[int, int]:
    min_wait = max(1, time_step, int(max_time * 0.1))
    max_wait = max(min_wait, min(max_time, max(min_wait + time_step, int(max_time * 0.45))))
    return min_wait, max_wait


def generate_random_requests(
    vehicle_positions: list[list[float]],
    request_count: int,
    spatial_mode: str,
    config: dict,
    seed: int,
    radius: float,
) -> list[dict]:
    return shared_generate_random_requests(
        vehicle_positions=vehicle_positions,
        request_count=request_count,
        spatial_mode=spatial_mode,
        config=config,
        seed=seed,
        radius=radius,
    )


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


def _default_request_template(index: int, spatial_mode: str) -> dict:
    if spatial_mode == "grid":
        origin = [0, 0]
        destination = [1, 1]
    else:
        origin = [DEFAULT_LATLON_START[0], DEFAULT_LATLON_START[1]]
        destination = [DEFAULT_LATLON_DESTINATION_FALLBACK[0], DEFAULT_LATLON_DESTINATION_FALLBACK[1]]
    return {
        "request_id": f"R{index + 1}",
        "created_at": 0,
        "origin": origin,
        "destination": destination,
        "max_wait_time": 10,
    }


def _read_request_defaults_from_data(index: int, request: dict, spatial_mode: str) -> dict:
    base_request = copy.deepcopy(request) if request else _default_request_template(index, spatial_mode)
    origin_default = _normalize_coordinate_pair(base_request.get("origin"), spatial_mode, (0, 0) if spatial_mode == "grid" else DEFAULT_LATLON_START)
    destination_default = _normalize_coordinate_pair(base_request.get("destination"), spatial_mode, (1, 1) if spatial_mode == "grid" else DEFAULT_LATLON_DESTINATION_FALLBACK)
    request_defaults = {
        "request_id": str(base_request.get("request_id", f"R{index + 1}")),
        "created_at": int(base_request.get("created_at", 0)),
        "origin": origin_default,
        "destination": destination_default,
        "max_wait_time": int(base_request.get("max_wait_time", 10)),
    }
    if "metadata" in base_request:
        request_defaults["metadata"] = base_request["metadata"]
    return request_defaults


def _read_request_defaults(index: int, request: dict, spatial_mode: str) -> dict:
    base_request = _read_request_defaults_from_data(index, request, spatial_mode)
    request_id = str(st.session_state.get(f"req_id_{index}", base_request.get("request_id", f"R{index + 1}")))
    created_at = int(st.session_state.get(f"created_at_{index}", base_request.get("created_at", 0)))
    max_wait_time = int(st.session_state.get(f"max_wait_{index}", base_request.get("max_wait_time", 10)))
    origin_default = base_request["origin"]
    destination_default = base_request["destination"]

    if spatial_mode == "grid":
        origin = [
            int(st.session_state.get(f"origin_x_{index}", origin_default[0])),
            int(st.session_state.get(f"origin_y_{index}", origin_default[1])),
        ]
        destination = [
            int(st.session_state.get(f"dest_x_{index}", destination_default[0])),
            int(st.session_state.get(f"dest_y_{index}", destination_default[1])),
        ]
    else:
        origin = [
            float(st.session_state.get(f"origin_lat_{index}", origin_default[0])),
            float(st.session_state.get(f"origin_lon_{index}", origin_default[1])),
        ]
        destination = [
            float(st.session_state.get(f"dest_lat_{index}", destination_default[0])),
            float(st.session_state.get(f"dest_lon_{index}", destination_default[1])),
        ]

    request_defaults = {
        "request_id": request_id,
        "created_at": created_at,
        "origin": origin,
        "destination": destination,
        "max_wait_time": max_wait_time,
    }
    if "metadata" in base_request:
        request_defaults["metadata"] = base_request["metadata"]
    return request_defaults


def _write_request_defaults_to_state(
    requests: list[dict],
    spatial_mode: str,
    sync_request_count_widget: bool = False,
) -> None:
    normalized_requests = [
        _read_request_defaults_from_data(index, request, spatial_mode)
        for index, request in enumerate(requests)
    ]
    st.session_state["dashboard_generated_requests"] = copy.deepcopy(normalized_requests)
    if sync_request_count_widget:
        st.session_state["request_count_input"] = max(1, len(normalized_requests))
    for index, request_defaults in enumerate(normalized_requests):
        st.session_state[f"req_id_{index}"] = str(request_defaults["request_id"])
        st.session_state[f"created_at_{index}"] = int(request_defaults["created_at"])
        st.session_state[f"max_wait_{index}"] = int(request_defaults["max_wait_time"])
        if spatial_mode == "grid":
            st.session_state[f"origin_x_{index}"] = int(request_defaults["origin"][0])
            st.session_state[f"origin_y_{index}"] = int(request_defaults["origin"][1])
            st.session_state[f"dest_x_{index}"] = int(request_defaults["destination"][0])
            st.session_state[f"dest_y_{index}"] = int(request_defaults["destination"][1])
        else:
            st.session_state[f"origin_lat_{index}"] = float(request_defaults["origin"][0])
            st.session_state[f"origin_lon_{index}"] = float(request_defaults["origin"][1])
            st.session_state[f"dest_lat_{index}"] = float(request_defaults["destination"][0])
            st.session_state[f"dest_lon_{index}"] = float(request_defaults["destination"][1])


def _resize_request_collection(requests: list[dict], request_count: int, spatial_mode: str) -> list[dict]:
    normalized = [
        _read_request_defaults_from_data(index, request, spatial_mode)
        for index, request in enumerate(requests[:request_count])
    ]
    while len(normalized) < request_count:
        template = normalized[-1] if normalized else _default_request_template(len(normalized), spatial_mode)
        normalized.append(_clone_request_for_extension(template, len(normalized), spatial_mode))
    return normalized[:request_count]


def _clone_request_for_extension(template: dict, index: int, spatial_mode: str) -> dict:
    base = template if template else _default_request_template(index, spatial_mode)
    return {
        "request_id": f"R{index + 1}",
        "created_at": int(base.get("created_at", 0)),
        "origin": _normalize_coordinate_pair(base.get("origin"), spatial_mode, (0, 0) if spatial_mode == "grid" else DEFAULT_LATLON_START),
        "destination": _normalize_coordinate_pair(base.get("destination"), spatial_mode, (1, 1) if spatial_mode == "grid" else DEFAULT_LATLON_DESTINATION_FALLBACK),
        "max_wait_time": int(base.get("max_wait_time", 10)),
    }


def build_default_vehicle_positions(
    config: dict,
    vehicle_count: int,
    spatial_mode: str,
    default_position: tuple[float, float],
    previous_vehicle_count: int = 0,
) -> list[list[float]]:
    configured_positions = list(config.get("vehicles", {}).get("initial_positions", []))
    default_positions: list[list[float]] = []
    last_position = _normalize_coordinate_pair(default_position, spatial_mode, default_position)
    for index in range(vehicle_count):
        if previous_vehicle_count > 0 and index >= previous_vehicle_count:
            candidate = list(last_position)
        else:
            candidate = configured_positions[index] if index < len(configured_positions) else last_position
        resolved_position = _read_vehicle_position(index, spatial_mode, candidate, default_position)
        default_positions.append(resolved_position)
        last_position = resolved_position
    return default_positions


def build_default_requests(
    scenario: dict,
    request_count: int,
    spatial_mode: str,
    previous_request_count: int = 0,
) -> list[dict]:
    base_requests = list(scenario.get("requests", []))
    default_requests: list[dict] = []
    last_request: dict | None = None

    for index in range(request_count):
        if previous_request_count > 0 and index >= previous_request_count:
            request_defaults = _clone_request_for_extension(last_request or {}, index, spatial_mode)
        else:
            base_request = base_requests[index] if index < len(base_requests) else _clone_request_for_extension(last_request or {}, index, spatial_mode)
            request_defaults = _read_request_defaults(index, base_request, spatial_mode)
        default_requests.append(request_defaults)
        last_request = request_defaults

    return default_requests


def render_spatial_view(
    frame: dict,
    config: dict,
    scenario: dict,
    compact: bool = False,
    show_legend: bool = True,
    timeline: list[dict] | None = None,
    show_supporting_info: bool = True,
) -> None:
    if detect_spatial_mode(config, scenario) == "latlon":
        render_openstreetmap_map(frame, compact=compact, timeline=timeline, show_supporting_info=show_supporting_info)
        return
    render_grid(frame, config, compact=compact, show_legend=show_legend)




VEHICLE_COLOR_PALETTE = [
    "#0f766e",
    "#be123c",
    "#7c3aed",
    "#475569",
    "#db2777",
    "#4d7c0f",
    "#9a3412",
    "#1f2937",
]

LABEL_OFFSET_CYCLE = [
    [12, -14],
    [12, 14],
    [-12, -14],
    [-12, 14],
    [24, 0],
    [0, -24],
    [-24, 0],
    [0, 24],
    [24, -24],
    [-24, -24],
    [24, 24],
    [-24, 24],
]


def _hex_to_rgb(color: str) -> list[int]:
    color = color.lstrip("#")
    return [int(color[index:index + 2], 16) for index in (0, 2, 4)]


def _rgb_to_hex(rgb: list[int]) -> str:
    return "#" + "".join(f"{max(0, min(255, int(channel))):02x}" for channel in rgb)


def _mix_rgb(source: list[int], target: list[int], ratio: float) -> list[int]:
    return [
        int(round(source[index] * (1.0 - ratio) + target[index] * ratio))
        for index in range(3)
    ]


def _rgba(rgb: list[int], alpha: int) -> list[int]:
    return list(rgb) + [alpha]


def _build_vehicle_styles(vehicle_ids: list[str]) -> dict[str, dict]:
    styles: dict[str, dict] = {}
    ordered_vehicle_ids = list(dict.fromkeys(vehicle_ids))
    for index, vehicle_id in enumerate(ordered_vehicle_ids):
        base_rgb = _hex_to_rgb(VEHICLE_COLOR_PALETTE[index % len(VEHICLE_COLOR_PALETTE)])
        road_rgb = _mix_rgb(base_rgb, [0, 0, 0], 0.18)
        planned_rgb = _mix_rgb(base_rgb, [255, 255, 255], 0.38)
        active_rgb = _mix_rgb(base_rgb, [0, 0, 0], 0.08)
        styles[vehicle_id] = {
            "base_hex": _rgb_to_hex(base_rgb),
            "base_rgb": base_rgb,
            "road_hex": _rgb_to_hex(road_rgb),
            "road_rgb": road_rgb,
            "planned_hex": _rgb_to_hex(planned_rgb),
            "planned_rgb": planned_rgb,
            "active_hex": _rgb_to_hex(active_rgb),
            "active_rgb": active_rgb,
            "fill_rgba": _rgba(base_rgb, 235),
            "halo_rgba": _rgba(base_rgb, 80),
            "line_rgba": _rgba(road_rgb, 230),
            "road_rgba": _rgba(road_rgb, 220),
            "planned_rgba": _rgba(planned_rgb, 120),
            "actual_rgba": _rgba(base_rgb, 225),
            "active_rgba": _rgba(active_rgb, 210),
            "text_hex": _rgb_to_hex(road_rgb),
            "text_rgba": _rgba(road_rgb, 255),
        }
    return styles


def _request_status_border(
    request: dict,
    current_time: float,
    previous_time: float,
    include_status_labels: bool,
) -> dict:
    if not include_status_labels:
        return {
            "border_hex": "#ffffff",
            "border_rgba": [255, 255, 255, 255],
            "border_width": 2,
            "pulse": False,
            "size_scale": 1.0,
        }

    status = str(request.get("status") or "pending")
    created_at = float(request.get("created_at", 0))
    just_created = previous_time < created_at <= current_time or (current_time <= 0 and created_at <= 0)
    if status == "pending":
        color_hex = "#64748b"
        border_width = 3
        pulse = False
    elif status == "waiting" and just_created:
        color_hex = "#facc15"
        border_width = 7
        pulse = True
    elif status in {"waiting", "assigned"}:
        color_hex = "#d97706"
        border_width = 6
        pulse = True
    elif status == "onboard":
        color_hex = "#0ea5e9"
        border_width = 6
        pulse = True
    elif status == "completed":
        color_hex = "#94a3b8"
        border_width = 3
        pulse = False
    elif status == "cancelled":
        color_hex = "#64748b"
        border_width = 3
        pulse = False
    else:
        color_hex = "#ffffff"
        border_width = 2
        pulse = False
    return {
        "border_hex": color_hex,
        "border_rgba": _rgba(_hex_to_rgb(color_hex), 255),
        "border_width": border_width,
        "pulse": pulse,
        "size_scale": 0.62 if status == "pending" else 1.0,
    }


def _vehicle_status_border(status: str) -> dict:
    normalized = str(status or "idle")
    if normalized == "to_pickup":
        color_hex = "#d97706"
        border_width = 6
        pulse = True
    elif normalized == "onboard":
        color_hex = "#0ea5e9"
        border_width = 6
        pulse = True
    else:
        color_hex = "#ffffff"
        border_width = 3
        pulse = False
    return {
        "border_hex": color_hex,
        "border_rgba": _rgba(_hex_to_rgb(color_hex), 255),
        "border_width": border_width,
        "pulse": pulse,
    }


def _request_label_state(
    request: dict,
    request_index: int,
    current_time: float,
    previous_time: float,
    include_status_labels: bool,
) -> dict:
    status = str(request.get("status") or "pending")
    border_meta = _request_status_border(request, current_time, previous_time, include_status_labels)
    is_completed = status in {"completed", "cancelled"}
    origin_fill_hex = "#cbd5e1" if is_completed else "#f59e0b"
    destination_fill_hex = "#cbd5e1" if is_completed else "#2563eb"
    origin_text_hex = "#475569" if is_completed else "#b45309"
    destination_text_hex = "#475569" if is_completed else "#1d4ed8"
    return {
        "origin_label": f"O{request_index}",
        "destination_label": f"D{request_index}",
        "origin_fill_hex": origin_fill_hex,
        "origin_text_hex": origin_text_hex,
        "origin_fill_rgba": _rgba(_hex_to_rgb(origin_fill_hex), 210),
        "destination_fill_hex": destination_fill_hex,
        "destination_text_hex": destination_text_hex,
        "destination_fill_rgba": _rgba(_hex_to_rgb(destination_fill_hex), 210),
        "border_hex": border_meta["border_hex"],
        "border_rgba": border_meta["border_rgba"],
        "border_width": border_meta["border_width"],
        "size_scale": border_meta["size_scale"],
        "pulse": border_meta["pulse"],
    }


def _build_status_legend_rows() -> list[dict]:
    return [
        {
            "sample_shape": "circle",
            "sample_mode": "fill",
            "title": "차량 기본색",
            "chip_bg": "#0f766e",
            "chip_fg": "#ffffff",
        },
        {
            "sample_shape": "circle",
            "sample_mode": "fill",
            "title": "출발지 기본색",
            "chip_bg": "#f59e0b",
            "chip_fg": "#ffffff",
        },
        {
            "sample_shape": "square",
            "sample_mode": "fill",
            "title": "도착지 기본색",
            "chip_bg": "#2563eb",
            "chip_fg": "#ffffff",
        },
        {
            "sample_shape": "circle",
            "sample_mode": "border",
            "title": "발생 예정 소형 마커",
            "chip_bg": "#64748b",
            "chip_fg": "#ffffff",
        },
        {
            "sample_shape": "circle",
            "sample_mode": "border",
            "title": "수요 발생 강조 테두리",
            "chip_bg": "#facc15",
            "chip_fg": "#111827",
        },
        {
            "sample_shape": "circle",
            "sample_mode": "border",
            "title": "탑승 대기 강조 테두리",
            "chip_bg": "#d97706",
            "chip_fg": "#ffffff",
        },
        {
            "sample_shape": "circle",
            "sample_mode": "border",
            "title": "탑승 이동 강조 테두리",
            "chip_bg": "#0ea5e9",
            "chip_fg": "#ffffff",
        },
        {
            "sample_shape": "circle",
            "sample_mode": "fill",
            "title": "완료 지점 회색 처리",
            "chip_bg": "#cbd5e1",
            "chip_fg": "#475569",
        },
    ]


def render_map_legend(payload: dict) -> None:
    visible_items: list[str] = []
    if payload.get("vehicle_start_points"):
        visible_items.append(f"차량 시작 위치 {len(payload['vehicle_start_points'])}")
    if payload.get("vehicle_points"):
        visible_items.append(f"차량 현재 위치 {len(payload['vehicle_points'])}")
    if payload.get("request_origin_points"):
        visible_items.append(f"요청 출발지 {len(payload['request_origin_points'])}")
    if payload.get("request_destination_points"):
        visible_items.append(f"요청 도착지 {len(payload['request_destination_points'])}")
    if payload.get("request_pairs"):
        visible_items.append(f"요청 O-D 연결 {len(payload['request_pairs'])}")
    if payload.get("road_network_paths"):
        visible_items.append(f"도로망 경로 {len(payload['road_network_paths'])}")
    if payload.get("planned_route_history"):
        visible_items.append(f"계획 경로 이력 {len(payload['planned_route_history'])}")
    if payload.get("actual_vehicle_paths"):
        visible_items.append(f"실제 주행 누적 경로 {len(payload['actual_vehicle_paths'])}")
    if payload.get("active_routes"):
        visible_items.append(f"현재 경로 계획 {len(payload['active_routes'])}")

    if visible_items:
        st.caption("현재 프레임 표시 항목: " + " / ".join(visible_items))

    vehicle_legend = payload.get("vehicle_legend", [])
    if vehicle_legend:
        badges = "".join(
            f'<span style="display:inline-flex;align-items:center;gap:6px;padding:4px 10px;'
            f'border-radius:999px;background:{item["base_hex"]};color:white;font-size:12px;'
            f'font-weight:700;margin:0 8px 8px 0;">{item.get("label", item["vehicle_id"])}</span>'
            for item in vehicle_legend
        )
        st.markdown(
            (
                '<div style="margin-top:4px;font-size:12px;color:#475569;">'
                '차량은 고유 색상으로 구분되고, 같은 차량의 시작 위치/현재 위치/경로가 같은 색 계열로 표시됩니다.'
                '</div>'
                f'<div style="margin-top:6px;">{badges}</div>'
            ),
            unsafe_allow_html=True,
        )


def render_map_label_panel(payload: dict, compact: bool = False) -> None:
    status_legend_rows = payload.get("status_legend_rows", [])
    if not status_legend_rows:
        return

    max_height = 300 if compact else 500
    def _sample_html(row: dict) -> str:
        shape = row.get("sample_shape", "circle")
        radius = "999px" if shape == "circle" else "8px"
        sample_mode = row.get("sample_mode", "fill")
        background = row["chip_bg"] if sample_mode == "fill" else "#ffffff"
        border = f'3px solid {row["chip_bg"]}'
        return (
            f'<span style="display:inline-flex;align-items:center;justify-content:center;min-width:30px;'
            f'height:24px;padding:0 8px;border-radius:{radius};background:{background};'
            f'border:{border};color:{row["chip_fg"]};font-size:12px;font-weight:800;">{row.get("sample_label", "")}</span>'
        )

    def _rows_to_html(rows: list[dict]) -> str:
        return "".join(
            (
                '<div style="display:flex;align-items:flex-start;gap:10px;padding:8px 0;'
                'border-bottom:1px solid #e2e8f0;">'
                f'{_sample_html(row)}'
                '<div style="min-width:0;">'
                f'<div style="font-size:12px;font-weight:700;color:#0f172a;">{row["title"]}</div>'
                '</div>'
                '</div>'
            )
            for row in rows
        )

    st.markdown("**라벨 안내**")
    st.caption("맵에서는 O/D/V 번호만 보이고, 상태는 테두리 강조와 완료 시 회색 처리, 발생 전 축소 크기로 구분합니다.")
    st.markdown(
        (
            f'<div style="border:1px solid #e2e8f0;border-radius:12px;background:#ffffff;'
            f'padding:10px 12px;max-height:{max_height}px;overflow:auto;">'
            f"{_rows_to_html(status_legend_rows)}"
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def render_map_supporting_info(payload: dict, map_caption: str, compact: bool = False) -> None:
    st.caption(map_caption)
    render_map_legend(payload)
    if (not compact) and payload.get("route_debug_rows"):
        st.dataframe(payload["route_debug_rows"], use_container_width=True, height=180)


def render_spatial_supporting_info(
    frame: dict,
    config: dict,
    scenario: dict,
    timeline: list[dict] | None = None,
) -> None:
    if detect_spatial_mode(config, scenario) != "latlon":
        return
    payload = build_map_payload(frame, timeline=timeline)
    render_map_supporting_info(payload, "OpenStreetMap 지도와 내장 OSM 도로망 경로입니다. 지도 연결이 끊겨도 좌표와 경로를 표시합니다.")


def _apply_label_offsets(point_groups: list[list[dict]], threshold: float = 0.00018) -> None:
    clusters: list[dict] = []
    threshold_sq = threshold * threshold

    for group in point_groups:
        for point in group:
            if not point.get("label"):
                continue
            lon, lat = point["coordinates"]
            matched_cluster = None
            best_distance = None
            for cluster in clusters:
                cluster_lon, cluster_lat = cluster["center"]
                distance_sq = (lon - cluster_lon) ** 2 + (lat - cluster_lat) ** 2
                if distance_sq <= threshold_sq and (best_distance is None or distance_sq < best_distance):
                    matched_cluster = cluster
                    best_distance = distance_sq

            if matched_cluster is None:
                matched_cluster = {"center": [lon, lat], "count": 0}
                clusters.append(matched_cluster)

            offset_index = matched_cluster["count"]
            matched_cluster["count"] += 1
            base_offset = LABEL_OFFSET_CYCLE[offset_index % len(LABEL_OFFSET_CYCLE)]
            spread = 1.0 + 0.45 * (offset_index // len(LABEL_OFFSET_CYCLE))
            point["label_offset"] = [
                int(round(base_offset[0] * spread)),
                int(round(base_offset[1] * spread)),
            ]


def dedupe_coordinates(path: list[list[float]]) -> list[list[float]]:
    deduped: list[list[float]] = []
    for point in path:
        if len(point) < 2:
            continue
        normalized = [float(point[0]), float(point[1])]
        if not deduped or deduped[-1] != normalized:
            deduped.append(normalized)
    return deduped


def build_map_payload(frame: dict, timeline: list[dict] | None = None) -> dict:
    vehicle_ids = [str(vehicle.get("vehicle_id")) for vehicle in frame.get("vehicles", [])]
    if timeline:
        for vehicle in timeline[0].get("vehicles", []):
            vehicle_ids.append(str(vehicle.get("vehicle_id")))
    vehicle_styles = _build_vehicle_styles(vehicle_ids)
    vehicle_points = []
    vehicle_start_points = []
    request_origin_points = []
    request_destination_points = []
    request_pairs = []
    active_routes = []
    planned_route_history = []
    actual_vehicle_paths = []
    road_network_paths = []
    route_debug_rows = []
    label_rows = []
    status_legend_rows = _build_status_legend_rows() if timeline else []
    current_time = float(frame.get("time", 0))
    previous_time = (
        float(timeline[-2].get("time", 0))
        if timeline and len(timeline) >= 2
        else -1.0
    )

    for vehicle_index, vehicle in enumerate(frame["vehicles"], start=1):
        vehicle_id = str(vehicle["vehicle_id"])
        vehicle_label = f"V{vehicle_index}"
        vehicle_style = vehicle_styles.get(vehicle_id, _build_vehicle_styles([vehicle_id])[vehicle_id])
        vehicle_border = _vehicle_status_border(str(vehicle.get("status", "idle")))
        lat, lon = vehicle["position"]
        completed_request_ids = [str(request_id) for request_id in vehicle.get("completed_request_ids", [])]
        vehicle_points.append(
            {
                "vehicle_id": vehicle_id,
                "coordinates": [lon, lat],
                "status": vehicle["status"],
                "label": vehicle_label,
                "position_text": f"{lat:.6f}, {lon:.6f}",
                "current_load": int(vehicle.get("current_load", 0)),
                "capacity": int(vehicle.get("capacity", 0)),
                "assigned_request_id": vehicle.get("assigned_request_id"),
                "completed_request_count": len(completed_request_ids),
                "completed_request_ids": completed_request_ids,
                "actual_distance_km": round(_vehicle_actual_distance_km(vehicle), 3),
                "active_route_source": vehicle.get("active_route_source"),
                "color_hex": vehicle_style["base_hex"],
                "fill_hex": vehicle_style["base_hex"],
                "fill_rgba": vehicle_style["fill_rgba"],
                "halo_rgba": vehicle_style["halo_rgba"],
                "border_hex": vehicle_border["border_hex"],
                "border_rgba": vehicle_border["border_rgba"],
                "border_width": vehicle_border["border_width"],
                "pulse": vehicle_border["pulse"],
                "line_rgba": vehicle_style["line_rgba"],
                "text_hex": vehicle_style["text_hex"],
                "text_rgba": vehicle_style["text_rgba"],
            }
        )
        label_rows.append(
            {
                "label": vehicle_label,
                "title": f"차량 {vehicle_index}",
                "detail": f"실제 차량 ID: {vehicle_id}",
                "chip_bg": vehicle_style["base_hex"],
                "chip_fg": "#ffffff",
            }
        )
        for path_index, path in enumerate(vehicle.get("route_history_paths", []), start=1):
            history_path = dedupe_coordinates([[point[1], point[0]] for point in path if len(point) >= 2])
            route_metadata = (
                vehicle.get("route_history_metadata", [])[path_index - 1]
                if len(vehicle.get("route_history_metadata", [])) >= path_index
                else {}
            )
            if len(history_path) > 1:
                route_snap = route_metadata.get("road_snap") or route_metadata.get("snap") or {}
                planned_route_history.append(
                    {
                        "vehicle_id": vehicle_id,
                        "path": history_path,
                        "request_id": (
                            vehicle.get("route_history_request_ids", [])[path_index - 1]
                            if len(vehicle.get("route_history_request_ids", [])) >= path_index
                            else None
                        ),
                        "stage": (
                            vehicle.get("route_history_stages", [])[path_index - 1]
                            if len(vehicle.get("route_history_stages", [])) >= path_index
                            else None
                        ),
                        "route_source": (
                            vehicle.get("route_history_sources", [])[path_index - 1]
                            if len(vehicle.get("route_history_sources", [])) >= path_index
                            else None
                        ),
                        "route_metadata": route_metadata,
                        "color_hex": vehicle_style["planned_hex"],
                        "color_rgba": vehicle_style["planned_rgba"],
                    }
                )
                route_source = (
                    vehicle.get("route_history_sources", [])[path_index - 1]
                    if len(vehicle.get("route_history_sources", [])) >= path_index
                    else None
                )
                if route_source and (route_source.startswith("osm") or route_source.startswith("local_its")):
                    road_network_paths.append(
                        {
                            "vehicle_id": vehicle_id,
                            "path": history_path,
                            "request_id": (
                                vehicle.get("route_history_request_ids", [])[path_index - 1]
                                if len(vehicle.get("route_history_request_ids", [])) >= path_index
                                else None
                            ),
                            "stage": (
                                vehicle.get("route_history_stages", [])[path_index - 1]
                                if len(vehicle.get("route_history_stages", [])) >= path_index
                                else None
                            ),
                            "route_source": route_source,
                            "point_count": len(history_path),
                            "request_transport": route_metadata.get("request_transport"),
                            "coordinate_mode": route_metadata.get("coordinate_mode"),
                            "response_coord_type": route_metadata.get("response_coord_type"),
                            "color_hex": vehicle_style["road_hex"],
                            "color_rgba": vehicle_style["road_rgba"],
                        }
                    )
                route_debug_rows.append(
                    {
                        "vehicle_id": vehicle_id,
                        "request_id": (
                            vehicle.get("route_history_request_ids", [])[path_index - 1]
                            if len(vehicle.get("route_history_request_ids", [])) >= path_index
                            else None
                        ),
                        "stage": (
                            vehicle.get("route_history_stages", [])[path_index - 1]
                            if len(vehicle.get("route_history_stages", [])) >= path_index
                            else None
                        ),
                        "route_source": route_source,
                        "point_count": len(history_path),
                        "fallback_reason": route_metadata.get("fallback_reason"),
                        "request_transport": route_metadata.get("request_transport"),
                        "coordinate_mode": route_metadata.get("coordinate_mode"),
                        "response_coord_type": route_metadata.get("response_coord_type"),
                        "snap_start_m": (
                            route_snap.get("start", {}).get("distance_meters")
                        ),
                        "snap_end_m": (
                            route_snap.get("end", {}).get("distance_meters")
                        ),
                        "upstream_errors": " | ".join(route_metadata.get("upstream_errors", [])) or None,
                    }
                )
        for path_index, path in enumerate(vehicle.get("actual_route_paths", []), start=1):
            actual_path = dedupe_coordinates([[point[1], point[0]] for point in path if len(point) >= 2])
            if len(actual_path) > 1:
                actual_vehicle_paths.append(
                    {
                        "vehicle_id": vehicle_id,
                        "path": actual_path,
                        "request_id": (
                            vehicle.get("actual_route_request_ids", [])[path_index - 1]
                            if len(vehicle.get("actual_route_request_ids", [])) >= path_index
                            else None
                        ),
                        "stage": (
                            vehicle.get("actual_route_stages", [])[path_index - 1]
                            if len(vehicle.get("actual_route_stages", [])) >= path_index
                            else None
                        ),
                        "route_source": (
                            vehicle.get("actual_route_sources", [])[path_index - 1]
                            if len(vehicle.get("actual_route_sources", [])) >= path_index
                            else None
                        ),
                        "color_hex": vehicle_style["base_hex"],
                        "color_rgba": vehicle_style["actual_rgba"],
                    }
                )
        route_path = dedupe_coordinates([
            [point[1], point[0]]
            for point in vehicle.get("active_route_path", [])
            if len(point) >= 2
        ])
        if len(route_path) > 1:
            active_routes.append(
                {
                    "vehicle_id": vehicle_id,
                    "path": route_path,
                    "route_source": vehicle.get("active_route_source"),
                    "color_hex": vehicle_style["active_hex"],
                    "color_rgba": vehicle_style["active_rgba"],
                }
            )

    for index, request in enumerate(frame["requests"], start=1):
        origin = [request["origin"][1], request["origin"][0]]
        destination = [request["destination"][1], request["destination"][0]]
        request_id = request["request_id"]
        request_label_meta = _request_label_state(
            request,
            index,
            current_time=current_time,
            previous_time=previous_time,
            include_status_labels=bool(timeline),
        )
        request_origin_points.append(
            {
                "request_id": request_id,
                "coordinates": origin,
                "label": request_label_meta["origin_label"],
                "status": request.get("status"),
                "label_description": f"{request_id} 출발지",
                "color_hex": request_label_meta["origin_fill_hex"],
                "fill_hex": request_label_meta["origin_fill_hex"],
                "text_hex": request_label_meta["origin_text_hex"],
                "fill_rgba": request_label_meta["origin_fill_rgba"],
                "text_rgba": _rgba(_hex_to_rgb(request_label_meta["origin_text_hex"]), 255),
                "border_hex": request_label_meta["border_hex"],
                "border_rgba": request_label_meta["border_rgba"],
                "border_width": request_label_meta["border_width"],
                "pulse": request_label_meta["pulse"],
                "size_scale": request_label_meta["size_scale"],
                "marker_shape": "circle",
            }
        )
        request_destination_points.append(
            {
                "request_id": request_id,
                "coordinates": destination,
                "label": request_label_meta["destination_label"],
                "status": request.get("status"),
                "label_description": f"{request_id} 도착지",
                "color_hex": request_label_meta["destination_fill_hex"],
                "fill_hex": request_label_meta["destination_fill_hex"],
                "text_hex": request_label_meta["destination_text_hex"],
                "fill_rgba": request_label_meta["destination_fill_rgba"],
                "text_rgba": _rgba(_hex_to_rgb(request_label_meta["destination_text_hex"]), 255),
                "border_hex": request_label_meta["border_hex"],
                "border_rgba": request_label_meta["border_rgba"],
                "border_width": request_label_meta["border_width"],
                "pulse": request_label_meta["pulse"],
                "size_scale": request_label_meta["size_scale"],
                "marker_shape": "square",
            }
        )
        request_pairs.append(
            {
                "request_id": request_id,
                "path": dedupe_coordinates([origin, destination]),
                "status": request.get("status"),
            }
        )

    if timeline:
        vehicles_by_id: dict[str, list[list[float]]] = {}
        initial_vehicles = timeline[0].get("vehicles", [])
        vehicle_start_points = [
            {
                "vehicle_id": str(vehicle["vehicle_id"]),
                "coordinates": [vehicle["position"][1], vehicle["position"][0]],
                "color_hex": vehicle_styles[str(vehicle["vehicle_id"])]["base_hex"],
                "line_rgba": vehicle_styles[str(vehicle["vehicle_id"])]["line_rgba"],
            }
            for vehicle in initial_vehicles
        ]
        for snapshot in timeline:
            for vehicle in snapshot["vehicles"]:
                lat, lon = vehicle["position"]
                vehicles_by_id.setdefault(vehicle["vehicle_id"], []).append([lon, lat])
        if not actual_vehicle_paths:
            actual_vehicle_paths = [
                {
                    "vehicle_id": vehicle_id,
                    "path": dedupe_coordinates(path),
                    "color_hex": vehicle_styles[vehicle_id]["base_hex"],
                    "color_rgba": vehicle_styles[vehicle_id]["actual_rgba"],
                }
                for vehicle_id, path in vehicles_by_id.items()
                if len(dedupe_coordinates(path)) > 1
            ]
    else:
        vehicle_start_points = [
            {
                "vehicle_id": str(vehicle["vehicle_id"]),
                "coordinates": [vehicle["position"][1], vehicle["position"][0]],
                "color_hex": vehicle_styles[str(vehicle["vehicle_id"])]["base_hex"],
                "line_rgba": vehicle_styles[str(vehicle["vehicle_id"])]["line_rgba"],
            }
            for vehicle in frame["vehicles"]
        ]

    _apply_label_offsets([request_origin_points, request_destination_points, vehicle_points])

    return {
        "vehicle_points": vehicle_points,
        "vehicle_start_points": vehicle_start_points,
        "request_origin_points": request_origin_points,
        "request_destination_points": request_destination_points,
        "request_pairs": request_pairs,
        "active_routes": active_routes,
        "planned_route_history": planned_route_history,
        "actual_vehicle_paths": actual_vehicle_paths,
        "road_network_paths": road_network_paths,
        "route_debug_rows": route_debug_rows,
        "label_rows": label_rows,
        "status_legend_rows": status_legend_rows,
        "vehicle_legend": [
            {
                "vehicle_id": vehicle_id,
                "label": f"V{index}",
                "base_hex": style["base_hex"],
            }
            for index, (vehicle_id, style) in enumerate(vehicle_styles.items(), start=1)
        ],
    }


def build_coordinate_svg(payload: dict, compact: bool = False, include_intro: bool = True) -> str:
    vehicle_points = payload["vehicle_points"]
    vehicle_start_points = payload["vehicle_start_points"]
    request_origin_points = payload["request_origin_points"]
    request_destination_points = payload["request_destination_points"]
    request_pairs = payload["request_pairs"]
    actual_vehicle_paths = payload["actual_vehicle_paths"]
    active_routes = payload["active_routes"]
    planned_route_history = payload["planned_route_history"]
    road_network_paths = payload["road_network_paths"]
    all_points = vehicle_points + vehicle_start_points + request_origin_points + request_destination_points
    if not all_points:
        return ""

    coords = [point["coordinates"] for point in all_points]
    lons = [coord[0] for coord in coords]
    lats = [coord[1] for coord in coords]
    min_lon, max_lon = min(lons), max(lons)
    min_lat, max_lat = min(lats), max(lats)
    lon_span = max(max_lon - min_lon, 1e-6)
    lat_span = max(max_lat - min_lat, 1e-6)

    width = 760
    height = 300 if compact else 460
    pad = 28

    def project(coord: list[float]) -> tuple[float, float]:
        lon, lat = coord
        x = pad + ((lon - min_lon) / lon_span) * (width - pad * 2)
        y = height - pad - ((lat - min_lat) / lat_span) * (height - pad * 2)
        return x, y

    def path_to_svg(
        path: list[list[float]],
        color: str,
        stroke_width: int,
        opacity: float = 1.0,
        dash_array: str | None = None,
    ) -> str:
        if len(path) < 2:
            return ""
        d = " ".join(
            f"{'M' if index == 0 else 'L'} {project(point)[0]:.1f} {project(point)[1]:.1f}"
            for index, point in enumerate(path)
        )
        dash_attr = f' stroke-dasharray="{dash_array}"' if dash_array else ""
        return f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{stroke_width}" opacity="{opacity}"{dash_attr}/>'

    svg_lines = []
    for item in request_pairs:
        svg_lines.append(path_to_svg(item["path"], "#94a3b8", 2, 0.7, "6 6"))
    for item in road_network_paths:
        svg_lines.append(path_to_svg(item["path"], item.get("color_hex", "#14532d"), 6, 0.9))
    for item in planned_route_history:
        svg_lines.append(path_to_svg(item["path"], item.get("color_hex", "#7dd3fc"), 2, 0.5, "4 8"))
    for item in actual_vehicle_paths:
        svg_lines.append(path_to_svg(item["path"], item.get("color_hex", "#22c55e"), 4, 0.95))
    for item in active_routes:
        svg_lines.append(path_to_svg(item["path"], item.get("color_hex", "#0ea5a4"), 5, 0.95, "8 6"))

    def circles(
        points: list[dict],
        color: str,
        radius: int,
        shape: str = "circle",
        outline: str | None = None,
        fill_opacity: float = 1.0,
        stroke_opacity: float = 1.0,
        use_point_style: bool = True,
    ) -> str:
        elements = []
        for point in points:
            x, y = project(point["coordinates"])
            point_radius = max(3, int(round(radius * float(point.get("size_scale", 1.0)))))
            point_fill = point.get("fill_hex", point.get("color_hex", color)) if use_point_style else point.get("color_hex", color)
            point_stroke = (
                point.get("border_hex", "white" if outline is None else point.get("color_hex", outline))
                if use_point_style
                else ("white" if outline is None else point.get("color_hex", outline))
            )
            point_stroke_width = point.get("border_width", 2 if outline is None else 3) if use_point_style else (2 if outline is None else 3)
            if shape == "square":
                size = point_radius * 2
                elements.append(
                    f'<rect x="{x - point_radius:.1f}" y="{y - point_radius:.1f}" width="{size}" height="{size}" '
                    f'fill="{point_fill}" fill-opacity="{fill_opacity}" stroke="{point_stroke}" stroke-opacity="{stroke_opacity}" stroke-width="{point_stroke_width}"/>'
                )
            else:
                fill = point_fill if outline is None else "rgba(255,255,255,0.95)"
                stroke = point_stroke if outline is None else point.get("color_hex", outline)
                stroke_width = point_stroke_width if outline is None else 3
                elements.append(
                    f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{point_radius}" fill="{fill}" fill-opacity="{fill_opacity}" stroke="{stroke}" stroke-opacity="{stroke_opacity}" stroke-width="{stroke_width}"/>'
                )
        return "".join(elements)

    def emphasis_shapes(points: list[dict], radius: int, shape: str = "circle") -> str:
        elements = []
        for point in points:
            if not point.get("pulse"):
                continue
            x, y = project(point["coordinates"])
            point_radius = max(5, int(round(radius * float(point.get("size_scale", 1.0)) + 4)))
            border_color = point.get("border_hex", "#ffffff")
            border_width = max(3, int(point.get("border_width", 4)) - 1)
            if shape == "square":
                size = point_radius * 2
                elements.append(
                    f'<rect x="{x - point_radius:.1f}" y="{y - point_radius:.1f}" width="{size}" height="{size}" '
                    f'fill="none" stroke="{border_color}" stroke-opacity="0.45" stroke-width="{border_width}">'
                    '<animate attributeName="opacity" values="0.30;0.95;0.30" dur="1.1s" repeatCount="indefinite"/>'
                    '</rect>'
                )
            else:
                elements.append(
                    f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{point_radius}" fill="none" stroke="{border_color}" '
                    f'stroke-opacity="0.45" stroke-width="{border_width}">'
                    '<animate attributeName="opacity" values="0.30;0.95;0.30" dur="1.1s" repeatCount="indefinite"/>'
                    f'<animate attributeName="r" values="{max(4, point_radius - 2)};{point_radius + 1};{max(4, point_radius - 2)}" dur="1.1s" repeatCount="indefinite"/>'
                    '</circle>'
                )
        return "".join(elements)

    def labels(
        points: list[dict],
        color: str,
        dx: int = 8,
        dy: int = -8,
        label_key: str = "label",
    ) -> str:
        elements = []
        for point in points:
            x, y = project(point["coordinates"])
            label = point.get(label_key)
            if not label:
                continue
            text_color = point.get("text_hex", color)
            offset = point.get("label_offset", [dx, dy])
            elements.append(
                f'<text x="{x + offset[0]:.1f}" y="{y + offset[1]:.1f}" font-size="12" font-weight="700" fill="{text_color}">{label}</text>'
            )
        return "".join(elements)

    intro_html = ""
    if include_intro:
        intro_html = (
            '<div style="margin-bottom:8px;font-size:12px;color:#475569;">'
            "외부 지도 타일 없이 위경도를 정규화해 그린 좌표 검증 뷰입니다. "
            "요청 출발지(O), 도착지(D), 도로망 경로, 실제 주행 누적 경로를 구분해서 확인할 수 있습니다."
            "</div>"
        )

    return f"""
    {intro_html}
    <svg viewBox="0 0 {width} {height}" style="display:block;width:100%;height:{height}px;border:1px solid #dbe3ea;border-radius:12px;background:linear-gradient(180deg,#f8fbff 0%,#eef6f8 100%);">
      <rect x="{pad}" y="{pad}" width="{width - pad * 2}" height="{height - pad * 2}" fill="none" stroke="#cbd5e1" stroke-dasharray="4 4"/>
      {''.join(svg_lines)}
      {circles(vehicle_start_points, "#14532d", 8, outline="#14532d", use_point_style=False)}
      {emphasis_shapes(vehicle_points, 9 if compact else 10)}
      {circles(vehicle_points, "#0f766e", 10 if compact else 12, fill_opacity=0.18, stroke_opacity=0.35, use_point_style=False)}
      {circles(vehicle_points, "#0f766e", 7 if compact else 8)}
      {emphasis_shapes(request_destination_points, 6, shape="square")}
      {emphasis_shapes(request_origin_points, 6)}
      {circles(request_destination_points, "#2563eb", 6, shape="square")}
      {circles(request_origin_points, "#f59e0b", 6)}
      {labels(request_origin_points, "#b45309")}
      {labels(request_destination_points, "#1d4ed8")}
      {labels(vehicle_points, "#0f766e", dx=10, dy=-10)}
      <text x="{pad}" y="{height - 8}" font-size="11" fill="#64748b">lon {min_lon:.5f} ~ {max_lon:.5f}</text>
      <text x="{width - 180}" y="{height - 8}" font-size="11" fill="#64748b">lat {min_lat:.5f} ~ {max_lat:.5f}</text>
    </svg>
    """




def render_openstreetmap_map(
    frame: dict,
    compact: bool = False,
    timeline: list[dict] | None = None,
    show_supporting_info: bool = True,
) -> None:
    payload = build_map_payload(frame, timeline=timeline)
    all_points = (
        payload["vehicle_points"]
        + payload["vehicle_start_points"]
        + payload["request_origin_points"]
        + payload["request_destination_points"]
    )
    if not all_points:
        st.info("지도에 표시할 좌표 데이터가 없습니다.")
        return

    map_div_id = f"osm_{uuid.uuid4().hex}"
    fallback_div_id = f"osm_fallback_{uuid.uuid4().hex}"
    height = 340 if compact else 520
    component_height = height + (72 if compact else 88)
    fallback_svg = build_coordinate_svg(payload, compact=compact, include_intro=False)
    html = f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8" />
        <link
          rel="stylesheet"
          href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
          integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="
          crossorigin=""
        />
        <style>
          html, body, #{map_div_id} {{
            margin: 0;
            padding: 0;
            width: 100%;
            height: {height}px;
          }}
          #{fallback_div_id} {{
            display: none;
          }}
          .marker-origin {{
            min-width: 22px;
            height: 20px;
            padding: 0 6px;
            border-radius: 999px;
            background: #f59e0b;
            border: 2px solid white;
            color: white;
            font-size: 11px;
            font-weight: 700;
            display: flex;
            align-items: center;
            justify-content: center;
            box-shadow: 0 1px 6px rgba(0,0,0,0.25);
          }}
          .marker-destination {{
            min-width: 22px;
            height: 20px;
            padding: 0 6px;
            background: #2563eb;
            border: 2px solid white;
            color: white;
            font-size: 11px;
            font-weight: 700;
            display: flex;
            align-items: center;
            justify-content: center;
            box-shadow: 0 1px 6px rgba(0,0,0,0.25);
          }}
          .leaflet-container {{
            font-family: Arial, sans-serif;
          }}
          @keyframes markerPulse {{
            0% {{
              transform: scale(1);
              opacity: 0.88;
            }}
            50% {{
              transform: scale(1.1);
              opacity: 1;
            }}
            100% {{
              transform: scale(1);
              opacity: 0.88;
            }}
          }}
          .vehicle-tooltip {{
            background: rgba(255,255,255,0.96);
            border: 1px solid rgba(15, 23, 42, 0.12);
            color: #0f172a;
            font-size: 11px;
            font-weight: 700;
            box-shadow: 0 1px 6px rgba(0,0,0,0.12);
          }}
          .vehicle-popup {{
            min-width: 210px;
            color: #0f172a;
            font-size: 12px;
            line-height: 1.45;
          }}
          .vehicle-popup-title {{
            margin-bottom: 8px;
            font-size: 13px;
            font-weight: 800;
          }}
          .vehicle-popup-grid {{
            display: grid;
            grid-template-columns: auto 1fr;
            gap: 4px 10px;
          }}
          .vehicle-popup-grid dt {{
            margin: 0;
            color: #64748b;
            font-weight: 700;
          }}
          .vehicle-popup-grid dd {{
            margin: 0;
            color: #0f172a;
            font-weight: 600;
          }}
          .request-origin-tooltip {{
            background: rgba(245, 158, 11, 0.96);
            border: 1px solid rgba(180, 83, 9, 0.28);
            color: white;
            font-size: 11px;
            font-weight: 700;
            box-shadow: 0 1px 6px rgba(0,0,0,0.12);
          }}
          .request-destination-tooltip {{
            background: rgba(37, 99, 235, 0.96);
            border: 1px solid rgba(29, 78, 216, 0.28);
            color: white;
            font-size: 11px;
            font-weight: 700;
            box-shadow: 0 1px 6px rgba(0,0,0,0.12);
          }}
          .request-origin-dot {{
            width: 12px;
            height: 12px;
            border-radius: 999px;
            background: #f59e0b;
            border: 2px solid white;
            box-shadow: 0 1px 6px rgba(0,0,0,0.25);
          }}
          .request-destination-dot {{
            width: 12px;
            height: 12px;
            background: #2563eb;
            border: 2px solid white;
            box-shadow: 0 1px 6px rgba(0,0,0,0.25);
          }}
        </style>
      </head>
      <body>
        <div id="{fallback_div_id}">
          <div style="margin-bottom:8px;padding:8px 10px;border:1px solid #facc15;border-radius:10px;background:#fef9c3;color:#854d0e;font-size:12px;">
            <strong>OpenStreetMap 로드 실패</strong>
            <div data-fallback-reason style="margin-top:4px;">외부 지도 리소스를 불러오지 못해 좌표 검증 뷰로 전환했습니다.</div>
          </div>
          {fallback_svg}
        </div>
        <div id="{map_div_id}"></div>
        <script
          src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
          integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="
          crossorigin=""
        ></script>
        <script>
          const payload = {json.dumps(payload, ensure_ascii=False).replace(chr(60), chr(92) + "u003c")};
          const vehicleCoreRadius = {8 if compact else 11};
          const vehicleHaloRadius = {13 if compact else 19};
          const vehicleHaloOpacity = {0.18 if compact else 0.24};
          const vehicleLabelMinWidth = {24 if compact else 30};
          const vehicleLabelHeight = {20 if compact else 24};
          const vehicleLabelFontSize = {11 if compact else 12};
          const fallbackDiv = document.getElementById("{fallback_div_id}");
          const fallbackReason = fallbackDiv.querySelector("[data-fallback-reason]");
          const mapDiv = document.getElementById("{map_div_id}");
          const pointCandidates = [
            ...payload.vehicle_points.map(p => p.coordinates),
            ...payload.vehicle_start_points.map(p => p.coordinates),
            ...payload.request_origin_points.map(p => p.coordinates),
            ...payload.request_destination_points.map(p => p.coordinates)
          ];

          function showFallback(message) {{
            if (message && fallbackReason) {{
              fallbackReason.textContent = message;
            }}
            fallbackDiv.style.display = "block";
            mapDiv.style.display = "none";
          }}

          function showMap() {{
            const wasHidden = mapDiv.style.display === "none";
            fallbackDiv.style.display = "none";
            mapDiv.style.display = "block";
            if (wasHidden) window.dispatchEvent(new Event("resize"));
          }}

          if (!window.L) {{
            showFallback("Leaflet 리소스를 불러오지 못해 좌표 검증 뷰로 전환했습니다.");
          }} else {{
            const map = L.map("{map_div_id}", {{
              zoomControl: true,
              scrollWheelZoom: true
            }}).setView([37.501, 126.985], 13);

            const tileLayer = L.tileLayer("https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
              maxZoom: 19,
              attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>'
            }}).addTo(map);

            let tileLoaded = false;
            let tileErrors = 0;
            tileLayer.on("tileload", () => {{
              tileLoaded = true;
              showMap();
            }});
            tileLayer.on("tileerror", () => {{
              tileErrors += 1;
              if (!tileLoaded && tileErrors >= 4) {{
                showFallback("OpenStreetMap 타일을 불러오지 못해 좌표 검증 뷰로 전환했습니다.");
              }}
            }});
            window.setTimeout(() => {{
              if (!tileLoaded) {{
                showFallback("외부 지도 타일 응답이 없어 좌표 검증 뷰로 전환했습니다.");
              }}
            }}, 15000);

            function addPath(path, options, label) {{
              if (!path || path.length < 2) {{
                return;
              }}
              const latlngs = path.map(p => [p[1], p[0]]);
              L.polyline(latlngs, options)
                .addTo(map)
                .bindPopup(label || "");
              latlngs.forEach(p => pointCandidates.push([p[1], p[0]]));
            }}

            function tooltipDirection(offset) {{
              if (!offset) {{
                return "top";
              }}
              if (Math.abs(offset[0]) > Math.abs(offset[1])) {{
                return offset[0] >= 0 ? "right" : "left";
              }}
              return offset[1] >= 0 ? "bottom" : "top";
            }}

            function escapeHtml(value) {{
              return String(value ?? "")
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;")
                .replace(/'/g, "&#39;");
            }}

            function buildVehiclePopup(point) {{
              const rows = [
                ["차량", point.label || "-"],
                ["실제 ID", point.vehicle_id || "-"],
                ["상태", point.status || "-"],
                ["탑승 / 용량", `${{point.current_load ?? 0}} / ${{point.capacity ?? "-"}}`],
                ["배정 요청", point.assigned_request_id || "-"],
                ["완료 요청", point.completed_request_count ?? 0],
                ["누적 실주행", point.actual_distance_km != null ? `${{Number(point.actual_distance_km).toFixed(2)}} km` : "-"],
                ["현재 위치", point.position_text || "-"]
              ];
              if (point.active_route_source) {{
                rows.push(["경로 출처", point.active_route_source]);
              }}
              if (point.completed_request_ids && point.completed_request_ids.length) {{
                rows.push(["완료 ID", point.completed_request_ids.join(", ")]);
              }}
              const body = rows
                .map(([label, value]) => `<dt>${{escapeHtml(label)}}</dt><dd>${{escapeHtml(value)}}</dd>`)
                .join("");
              return (
                `<div class="vehicle-popup">` +
                `<div class="vehicle-popup-title" style="color:${{escapeHtml(point.color_hex || "#0f172a")}};">${{escapeHtml(point.label || point.vehicle_id || "차량")}}</div>` +
                `<dl class="vehicle-popup-grid">${{body}}</dl>` +
                `</div>`
              );
            }}

            function addCircle(point, options, label, tooltipText, tooltipClass) {{
              const latlng = [point.coordinates[1], point.coordinates[0]];
              const marker = L.circleMarker(latlng, options)
                .addTo(map)
                .bindPopup(label || "");
              if (tooltipText) {{
                const offset = point.label_offset || [0, -10];
                marker.bindTooltip(tooltipText, {{
                  permanent: true,
                  direction: tooltipDirection(offset),
                  offset: offset,
                  className: tooltipClass || "vehicle-tooltip"
                }});
              }}
              pointCandidates.push(point.coordinates);
            }}

            function addDivMarker(point) {{
              const isSquare = point.marker_shape === "square";
              const sizeScale = Number(point.size_scale || 1);
              const markerSize = Math.max(9, Math.round(12 * sizeScale));
              const markerBorder = Math.max(2, Number(point.border_width || 2));
              const labelMinWidth = Math.max(22, Math.round(28 * Math.max(0.9, sizeScale)));
              const labelHeight = Math.max(18, Math.round(20 * Math.max(0.9, sizeScale)));
              const labelFontSize = Math.max(10, Math.round(11 * Math.max(0.92, sizeScale)));
              const pulseStyle = point.pulse ? "animation:markerPulse 1.05s ease-in-out infinite;" : "";
              const icon = L.divIcon({{
                html: `
                  <div style="
                    width:${{markerSize}}px;
                    height:${{markerSize}}px;
                    border-radius:${{isSquare ? "2px" : "999px"}};
                    background:${{point.fill_hex || point.color_hex || "#f59e0b"}};
                    border:${{markerBorder}}px solid ${{point.border_hex || "#ffffff"}};
                    box-shadow:0 1px 6px rgba(0,0,0,0.25);
                    ${{pulseStyle}}
                  "></div>
                `,
                className: "",
                iconAnchor: [markerSize / 2, markerSize / 2]
              }});
              const latlng = [point.coordinates[1], point.coordinates[0]];
              const marker = L.marker(latlng, {{ icon }}).addTo(map).bindPopup(point.request_id || point.label || "");
              const offset = point.label_offset || [0, -10];
              marker.bindTooltip(
                `
                  <div style="
                    display:inline-flex;
                    align-items:center;
                    justify-content:center;
                    min-width:${{labelMinWidth}}px;
                    height:${{labelHeight}}px;
                    padding:0 6px;
                    border-radius:${{isSquare ? "8px" : "999px"}};
                    background:${{point.fill_hex || point.color_hex || "#f59e0b"}};
                    border:${{markerBorder}}px solid ${{point.border_hex || "rgba(255,255,255,0.98)"}};
                    color:${{point.text_hex || "#ffffff"}};
                    font-size:${{labelFontSize}}px;
                    font-weight:800;
                    box-shadow:0 1px 6px rgba(0,0,0,0.2);
                    white-space:nowrap;
                    ${{pulseStyle}}
                  ">${{escapeHtml(point.label)}}</div>
                `,
                {{
                  permanent: true,
                  direction: tooltipDirection(offset),
                  offset: offset,
                  className: "vehicle-tooltip"
                }}
              );
              pointCandidates.push(point.coordinates);
            }}

            function addVehicle(point) {{
              const latlng = [point.coordinates[1], point.coordinates[0]];
              L.circleMarker(latlng, {{
                radius: vehicleHaloRadius,
                color: point.color_hex,
                weight: 2,
                opacity: 0.68,
                fillColor: point.color_hex,
                fillOpacity: vehicleHaloOpacity,
                interactive: false
              }}).addTo(map);
              if (point.pulse) {{
                L.circleMarker(latlng, {{
                  radius: vehicleHaloRadius + 4,
                  color: point.border_hex || point.color_hex,
                  weight: Math.max(3, (point.border_width || 4) - 1),
                  opacity: 0.45,
                  fillOpacity: 0,
                  interactive: false
                }}).addTo(map);
              }}
              const marker = L.circleMarker(latlng, {{
                radius: vehicleCoreRadius,
                color: point.border_hex || "#ffffff",
                weight: point.border_width || 3,
                fillColor: point.fill_hex || point.color_hex,
                fillOpacity: 0.95
              }});
              marker.addTo(map).bindPopup(buildVehiclePopup(point));
              marker.bringToFront();

              const offset = point.label_offset || [0, -10];
              const labelIcon = L.divIcon({{
                className: "",
                iconAnchor: [0, 0],
                html: `
                  <div style="transform: translate(${{offset[0]}}px, ${{offset[1]}}px);">
                    <div style="
                      display:inline-flex;
                      align-items:center;
                      justify-content:center;
                      min-width:${{vehicleLabelMinWidth}}px;
                      height:${{vehicleLabelHeight}}px;
                      padding:0 6px;
                      border-radius:999px;
                      background:${{point.color_hex}};
                      border:${{Math.max(3, point.border_width || 3)}}px solid ${{point.border_hex || "rgba(255,255,255,0.98)"}};
                      color:#ffffff;
                      font-size:${{vehicleLabelFontSize}}px;
                      font-weight:800;
                      box-shadow:0 2px 10px rgba(15,23,42,0.28);
                      white-space:nowrap;
                      ${{point.pulse ? "animation:markerPulse 1.05s ease-in-out infinite;" : ""}}
                    ">
                      ${{escapeHtml(point.label)}}
                    </div>
                  </div>
                `
              }});
              L.marker(latlng, {{
                icon: labelIcon,
                interactive: false,
                keyboard: false,
                zIndexOffset: 900,
              }}).addTo(map);
              pointCandidates.push(point.coordinates);
            }}

            // O/D를 잇는 직선은 실제 주행 경로가 아니므로 OSM 지도에 그리지 않습니다.
            // 요청 출발지/도착지는 각각 마커로 표시하고, 실제 차량 이동은
            // actual_vehicle_paths 및 OSM active_routes 폴리라인으로만 표시합니다.
            payload.road_network_paths.forEach(line => addPath(line.path, {{
              color: line.color_hex,
              weight: 6,
              opacity: 0.9
            }}, "도로망 경로 · " + (line.vehicle_id || "")));
            payload.planned_route_history.forEach(line => addPath(line.path, {{
              color: line.color_hex,
              weight: 3,
              opacity: 0.5,
              dashArray: "4 8"
            }}, "계획 경로 이력 · " + (line.vehicle_id || "")));
            payload.actual_vehicle_paths.forEach(line => addPath(line.path, {{
              color: line.color_hex,
              weight: 4,
              opacity: 0.95
            }}, "실제 주행 누적 경로 · " + (line.vehicle_id || "")));
            payload.active_routes.forEach(line => addPath(line.path, {{
              color: line.color_hex,
              weight: 5,
              opacity: 0.95,
              dashArray: "8 6"
            }}, "현재 경로 계획 · " + (line.vehicle_id || "")));

            payload.vehicle_start_points.forEach(point => addCircle(point, {{
              radius: 8,
              color: point.color_hex,
              weight: 3,
              fillColor: "#ffffff",
              fillOpacity: 0.9
            }}, "차량 시작 위치 · " + (point.vehicle_id || "")));
            payload.vehicle_points.forEach(point => addVehicle(point));
            payload.request_origin_points.forEach(point => addDivMarker(point));
            payload.request_destination_points.forEach(point => addDivMarker(point));

            if (pointCandidates.length > 1) {{
              const bounds = L.latLngBounds(pointCandidates.map(p => [p[1], p[0]]));
              map.fitBounds(bounds, {{ padding: [24, 24] }});
            }} else if (pointCandidates.length === 1) {{
              map.setView([pointCandidates[0][1], pointCandidates[0][0]], {12 if compact else 13});
            }} else {{
              map.setView([37.4915254, 127.0310762], 13);
            }}
          }}
        </script>
      </body>
    </html>
    """
    components.html(html, height=component_height, scrolling=False)
    if show_supporting_info:
        render_map_supporting_info(
            payload,
            "OpenStreetMap 기반 지도입니다. 요청 O/D, 도로망 경로, 실제 주행 누적 경로를 한 화면에서 함께 확인할 수 있습니다.",
            compact=compact,
        )


def render_coordinate_view(
    frame: dict,
    compact: bool = False,
    timeline: list[dict] | None = None,
    show_supporting_info: bool = True,
) -> None:
    payload = build_map_payload(frame, timeline=timeline)
    if not (
        payload["vehicle_points"]
        or payload["vehicle_start_points"]
        or payload["request_origin_points"]
        or payload["request_destination_points"]
    ):
        st.info("좌표 뷰에 표시할 데이터가 없습니다.")
        return
    svg_html = build_coordinate_svg(
        payload,
        compact=compact,
        include_intro=show_supporting_info,
    )
    content_height = (360 if compact else 540)
    components.html(
        (
            "<!doctype html><html><head><meta charset='utf-8' />"
            "<style>html,body{margin:0;padding:0;background:transparent;font-family:Arial,sans-serif;}</style>"
            "</head><body>"
            f"{svg_html}"
            "</body></html>"
        ),
        height=content_height,
        scrolling=False,
    )
    if show_supporting_info:
        render_map_supporting_info(
            payload,
            "좌표 검증 뷰입니다. 요청 O/D, 도로망 경로, 실제 주행 누적 경로를 비교해서 확인할 수 있습니다.",
            compact=compact,
        )


def render_grid(frame: dict, config: dict, compact: bool = False, show_legend: bool = True) -> None:
    width = int(config["simulation"]["grid_width"])
    height = int(config["simulation"]["grid_height"])
    vehicle_positions = {tuple(vehicle["position"]): vehicle for vehicle in frame["vehicles"]}
    request_origins: dict[tuple[int, int], list[dict]] = {}
    request_destinations: dict[tuple[int, int], list[dict]] = {}

    for request in frame["requests"]:
        if request["status"] in {"waiting", "assigned", "pending"}:
            request_origins.setdefault(tuple(request["origin"]), []).append(request)
        if request["status"] in {"onboard", "completed"}:
            request_destinations.setdefault(tuple(request["destination"]), []).append(request)

    rows = []
    for y in reversed(range(height)):
        for x in range(width):
            cell_tokens = []
            if (x, y) in vehicle_positions:
                vehicle = vehicle_positions[(x, y)]
                cell_tokens.append(
                    f'<span class="vehicle">V{vehicle["vehicle_id"][1:]}</span>'
                )
            if (x, y) in request_origins:
                count = len(request_origins[(x, y)])
                cell_tokens.append(f'<span class="request waiting">R{count}</span>')
            if (x, y) in request_destinations:
                count = len(request_destinations[(x, y)])
                cell_tokens.append(f'<span class="request done">D{count}</span>')
            label = "".join(cell_tokens) if cell_tokens else '<span class="empty">.</span>'
            rows.append(f'<div class="cell">{label}</div>')

    cell_min_width = 28 if compact else 42
    cell_min_height = 28 if compact else 44
    token_font = 10 if compact else 12
    gap = 4 if compact else 6
    padding = 3 if compact else 5
    legend_margin = 6 if compact else 10
    legend_html = ""
    if show_legend:
        legend_html = """
        <div class="legend">
          <div class="legend-item"><span class="vehicle">V</span> 차량 위치</div>
          <div class="legend-item"><span class="request waiting">R</span> 대기/배정 요청 출발지</div>
          <div class="legend-item"><span class="request done">D</span> 탑승중/완료 요청 도착지</div>
        </div>
        """

    axis_html = f"""
    <div class="axis-note">
      좌하단이 기준점 `(0,0)` 입니다. 오른쪽으로 x 증가, 위로 y 증가
    </div>
    """

    html = f"""
    <style>
      .map-wrap {{
        margin-bottom: 10px;
      }}
      .axis-note {{
        font-size: 12px;
        color: #64748b;
        margin-bottom: 6px;
      }}
      .grid {{
        display: grid;
        grid-template-columns: repeat({width}, minmax({cell_min_width}px, 1fr));
        gap: {gap}px;
        margin-bottom: 12px;
      }}
      .cell {{
        min-height: {cell_min_height}px;
        border: 1px solid #d5dce5;
        border-radius: 8px;
        padding: {padding}px;
        background: linear-gradient(180deg, #f8fbff 0%, #eef4f8 100%);
        font-size: {token_font}px;
        line-height: 1.1;
        display: flex;
        align-items: center;
        justify-content: center;
        overflow: hidden;
      }}
      .vehicle {{
        display: inline-block;
        background: #0f766e;
        color: white;
        border-radius: 999px;
        padding: 1px 5px;
        margin-right: 2px;
        margin-bottom: 2px;
      }}
      .request {{
        display: inline-block;
        border-radius: 999px;
        padding: 1px 5px;
        margin-right: 2px;
        margin-bottom: 2px;
      }}
      .waiting {{
        background: #f59e0b;
        color: white;
      }}
      .done {{
        background: #2563eb;
        color: white;
      }}
      .empty {{
        color: #b8c2cc;
      }}
      .legend {{
        display: flex;
        gap: 8px;
        flex-wrap: wrap;
        margin-bottom: {legend_margin}px;
      }}
      .legend-item {{
        font-size: 12px;
      }}
    </style>
    <div class="map-wrap">
      {axis_html}
      {legend_html}
      <div class="grid">{''.join(rows)}</div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
