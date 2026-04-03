#!/usr/bin/env python3
"""Run multiple simulator conditions and export a report."""

from __future__ import annotations

import argparse
import copy
import csv
from datetime import datetime
import itertools
import json
from pathlib import Path
import re
import sys
from typing import Any

import yaml

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from simulator.env import MobilityEnv
from simulator.evaluation.runner import run_episode
from simulator.policies.factory import build_policy
from simulator.scenarios.generator import (
    default_request_generation_radius,
    ensure_vehicle_positions,
    generate_random_requests,
)
from simulator.scenarios.loader import load_config, load_scenario


def _slugify(value: object) -> str:
    text = str(value).strip()
    text = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", text)
    return text.strip("._-") or "value"


def _load_yaml(path: str | Path) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _set_nested_value(payload: dict, dotted_path: str, value: Any) -> None:
    keys = [part for part in dotted_path.split(".") if part]
    if not keys:
        return
    current = payload
    for key in keys[:-1]:
        if key not in current or not isinstance(current[key], dict):
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value


def _expand_grid(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    if not grid:
        return [{}]
    keys = list(grid.keys())
    values_list: list[list[Any]] = []
    for key in keys:
        raw_values = grid.get(key, [])
        if isinstance(raw_values, list):
            values = raw_values
        else:
            values = [raw_values]
        values_list.append(values or [None])
    cases: list[dict[str, Any]] = []
    for combination in itertools.product(*values_list):
        cases.append({key: value for key, value in zip(keys, combination)})
    return cases


def _derive_average_wait_seconds(request_logs: list[dict]) -> float:
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
    for prev_frame, current_frame in zip(timeline, timeline[1:]):
        step_seconds = max(0.0, float(current_frame.get("time", 0)) - float(prev_frame.get("time", 0)))
        if step_seconds <= 0:
            continue
        prev_positions = {
            str(vehicle.get("vehicle_id")): tuple(vehicle.get("position", ()))
            for vehicle in prev_frame.get("vehicles", [])
        }
        current_positions = {
            str(vehicle.get("vehicle_id")): tuple(vehicle.get("position", ()))
            for vehicle in current_frame.get("vehicles", [])
        }
        for vehicle_id, current_position in current_positions.items():
            if prev_positions.get(vehicle_id) != current_position:
                operating_seconds += step_seconds
    return operating_seconds


def _prepare_case(
    base_config: dict,
    base_scenario: dict,
    fixed_values: dict[str, Any],
    case_values: dict[str, Any],
) -> tuple[dict, dict, dict]:
    config = copy.deepcopy(base_config)
    scenario = copy.deepcopy(base_scenario)
    config.setdefault("simulation", {})
    config.setdefault("vehicles", {})
    scenario.setdefault("metadata", {})

    merged_values = {**fixed_values, **case_values}
    request_generation_values = {
        key.split(".", 1)[1]: value
        for key, value in merged_values.items()
        if key.startswith("request_generation.")
    }
    for key, value in merged_values.items():
        if key.startswith("request_generation."):
            continue
        if key.startswith("scenario.metadata."):
            _set_nested_value(scenario, key.removeprefix("scenario."), value)
            continue
        if key.startswith("scenario."):
            _set_nested_value(scenario, key.removeprefix("scenario."), value)
            continue
        _set_nested_value(config, key, value)

    spatial_mode = str(config.get("simulation", {}).get("space_mode", "grid"))
    vehicle_count = int(config.get("vehicles", {}).get("count", 1))
    positions = ensure_vehicle_positions(config, vehicle_count=vehicle_count, spatial_mode=spatial_mode)

    if request_generation_values:
        request_count = int(request_generation_values.get("request_count", len(scenario.get("requests", [])) or 1))
        radius = float(
            request_generation_values.get(
                "radius",
                scenario.get("metadata", {}).get("request_generation", {}).get("radius", default_request_generation_radius(spatial_mode)),
            )
        )
        seed = int(request_generation_values.get("seed", config.get("simulation", {}).get("random_seed", 42)))
        fixed_max_wait = request_generation_values.get("max_wait_time")
        scenario["requests"] = generate_random_requests(
            vehicle_positions=positions,
            request_count=request_count,
            spatial_mode=spatial_mode,
            config=config,
            seed=seed,
            radius=radius,
            fixed_max_wait_time=None if fixed_max_wait is None else int(fixed_max_wait),
        )
        scenario["metadata"]["request_generation"] = {
            "radius": radius,
            "seed": seed,
            "request_count": request_count,
            "fixed_max_wait_time": None if fixed_max_wait is None else int(fixed_max_wait),
        }

    scenario.setdefault("scenario_name", "experiment_case")
    return config, scenario, request_generation_values


def _build_case_label(index: int, case_values: dict[str, Any]) -> str:
    if not case_values:
        return f"case_{index:03d}"
    parts = [f"{_slugify(key.split('.')[-1])}-{_slugify(value)}" for key, value in sorted(case_values.items())]
    return f"case_{index:03d}__" + "__".join(parts[:8])


def _flatten_case_values(case_values: dict[str, Any]) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, value in case_values.items():
        flattened[key.replace(".", "__")] = value
    return flattened


def _aggregate_by_policy(rows: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row["policy"]), []).append(row)

    summaries: list[dict] = []
    for policy, policy_rows in sorted(grouped.items()):
        best_row = max(policy_rows, key=lambda item: float(item["total_reward"]))
        summaries.append(
            {
                "policy": policy,
                "run_count": len(policy_rows),
                "avg_total_reward": round(sum(float(item["total_reward"]) for item in policy_rows) / len(policy_rows), 3),
                "avg_success_rate": round(sum(float(item["success_rate"]) for item in policy_rows) / len(policy_rows), 4),
                "avg_completed_requests": round(sum(float(item["completed_requests"]) for item in policy_rows) / len(policy_rows), 3),
                "avg_cancelled_requests": round(sum(float(item["cancelled_requests"]) for item in policy_rows) / len(policy_rows), 3),
                "avg_total_distance": round(sum(float(item["total_distance"]) for item in policy_rows) / len(policy_rows), 3),
                "avg_wait_seconds": round(sum(float(item["average_wait_seconds"]) for item in policy_rows) / len(policy_rows), 3),
                "best_total_reward": round(float(best_row["total_reward"]), 3),
                "best_case_id": best_row["case_id"],
            }
        )
    return summaries


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _markdown_table(rows: list[dict], columns: list[str]) -> str:
    if not rows:
        return "_데이터 없음_"
    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join(["---"] * len(columns)) + " |"
    body = [
        "| " + " | ".join(str(row.get(column, "")) for column in columns) + " |"
        for row in rows
    ]
    return "\n".join([header, divider, *body])


def _write_markdown_report(
    path: Path,
    suite_path: Path,
    base_config_path: Path,
    base_scenario_path: Path,
    run_rows: list[dict],
    policy_summary_rows: list[dict],
    suite_spec: dict,
) -> None:
    top_rows = sorted(run_rows, key=lambda row: float(row["total_reward"]), reverse=True)[:10]
    lines = [
        f"# {suite_spec.get('report_title', 'Simulation Experiment Report')}",
        "",
        f"- Suite 파일: `{suite_path}`",
        f"- Base Config: `{base_config_path}`",
        f"- Base Scenario: `{base_scenario_path}`",
        f"- 전체 실행 수: `{len(run_rows)}`",
        f"- 정책 수: `{len({row['policy'] for row in run_rows})}`",
        "",
        "## 정책별 요약",
        "",
        _markdown_table(
            policy_summary_rows,
            [
                "policy",
                "run_count",
                "avg_total_reward",
                "avg_success_rate",
                "avg_completed_requests",
                "avg_cancelled_requests",
                "avg_total_distance",
                "avg_wait_seconds",
                "best_total_reward",
                "best_case_id",
            ],
        ),
        "",
        "## 상위 실험 결과",
        "",
        _markdown_table(
            top_rows,
            [
                "case_id",
                "policy",
                "total_reward",
                "success_rate",
                "completed_requests",
                "cancelled_requests",
                "average_wait_seconds",
                "total_distance",
            ],
        ),
        "",
        "## 실험 축",
        "",
        "```yaml",
        yaml.safe_dump(
            {
                "policies": suite_spec.get("policies", []),
                "fixed": suite_spec.get("fixed", {}),
                "grid": suite_spec.get("grid", {}),
            },
            allow_unicode=True,
            sort_keys=False,
        ).rstrip(),
        "```",
        "",
        "## 출력 파일",
        "",
        "- `run_results.csv`: 개별 실행 결과",
        "- `policy_summary.csv`: 정책별 평균 요약",
        "- `report.md`: Markdown 요약 레포트",
        "- `runs/*.json`: 개별 실행 상세 결과",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", required=True, help="실험 조건 grid를 정의한 YAML 파일")
    parser.add_argument("--output-dir", default=None, help="결과 저장 디렉터리. 비우면 suite 설정 또는 timestamp 기반 기본 경로 사용")
    args = parser.parse_args()

    suite_path = Path(args.suite).resolve()
    suite_spec = _load_yaml(suite_path)
    base_config_path = (suite_path.parent / suite_spec["base_config"]).resolve()
    base_scenario_path = (suite_path.parent / suite_spec["base_scenario"]).resolve()

    base_config = load_config(base_config_path)
    base_scenario = load_scenario(base_scenario_path)
    policies = list(suite_spec.get("policies", ["nearest"]))
    fixed_values = dict(suite_spec.get("fixed", {}))
    grid_values = dict(suite_spec.get("grid", {}))
    case_values_list = _expand_grid(grid_values)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    default_output_dir = ROOT_DIR / "results" / "experiment_reports" / f"{suite_path.stem}_{timestamp}"
    output_dir = Path(args.output_dir).resolve() if args.output_dir else Path(suite_spec.get("output_dir", default_output_dir)).resolve()
    runs_dir = output_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    run_rows: list[dict] = []
    save_run_payloads = bool(suite_spec.get("save_run_payloads", True))

    for case_index, case_values in enumerate(case_values_list, start=1):
        config, scenario, _request_generation_values = _prepare_case(base_config, base_scenario, fixed_values, case_values)
        case_label = _build_case_label(case_index, case_values)
        flattened_case_values = _flatten_case_values(case_values)
        for policy_name in policies:
            env = MobilityEnv(config=config, scenario=scenario)
            policy = build_policy(policy_name)
            result = run_episode(env, policy).to_dict()
            metrics = result.get("metrics", {})
            row = {
                "case_id": case_label,
                "policy": policy_name,
                "scenario_name": result.get("scenario_name"),
                "total_reward": round(float(result.get("total_reward", 0.0)), 3),
                "completed_requests": int(metrics.get("completed_requests", 0)),
                "cancelled_requests": int(metrics.get("cancelled_requests", 0)),
                "success_rate": round(float(metrics.get("success_rate", 0.0)), 4),
                "total_distance": round(float(metrics.get("total_distance", 0.0)), 4),
                "average_wait_seconds": round(_derive_average_wait_seconds(result.get("request_logs", [])), 3),
                "vehicle_operating_seconds": round(_derive_vehicle_operating_seconds(result.get("timeline", [])), 3),
            }
            row.update(flattened_case_values)
            run_rows.append(row)

            if save_run_payloads:
                payload = {
                    "suite_path": str(suite_path),
                    "case_id": case_label,
                    "policy": policy_name,
                    "case_values": case_values,
                    "config": config,
                    "scenario": scenario,
                    "result": result,
                }
                run_path = runs_dir / f"{case_label}__{policy_name}.json"
                run_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    policy_summary_rows = _aggregate_by_policy(run_rows)
    _write_csv(output_dir / "run_results.csv", run_rows)
    _write_csv(output_dir / "policy_summary.csv", policy_summary_rows)
    _write_markdown_report(
        output_dir / "report.md",
        suite_path=suite_path,
        base_config_path=base_config_path,
        base_scenario_path=base_scenario_path,
        run_rows=run_rows,
        policy_summary_rows=policy_summary_rows,
        suite_spec=suite_spec,
    )

    print(json.dumps(
        {
            "output_dir": str(output_dir),
            "run_count": len(run_rows),
            "case_count": len(case_values_list),
            "policies": policies,
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
