"""Scenario and config loading."""

from __future__ import annotations

from pathlib import Path

import yaml

from simulator.entities.request import Request


def load_yaml(path: str | Path) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return data or {}


def load_config(path: str | Path) -> dict:
    return load_yaml(path)


def load_scenario(path: str | Path) -> dict:
    return load_yaml(path)


def build_requests(scenario: dict) -> list[Request]:
    requests: list[Request] = []
    for item in scenario.get("requests", []):
        requests.append(
            Request(
                request_id=item["request_id"],
                created_at=item["created_at"],
                origin=tuple(item["origin"]),
                destination=tuple(item["destination"]),
                max_wait_time=item["max_wait_time"],
                status="waiting" if item["created_at"] <= 0 else "pending",
            )
        )
    return requests
