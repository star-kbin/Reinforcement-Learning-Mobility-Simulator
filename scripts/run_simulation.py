#!/usr/bin/env python3
"""Run a single simulation episode."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from simulator.env import MobilityEnv
from simulator.evaluation.runner import run_episode
from simulator.policies.factory import build_policy
from simulator.scenarios.loader import load_config, load_scenario


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--policy", default="nearest", choices=["random", "nearest", "eta"])
    parser.add_argument("--output", default=None)
    parser.add_argument("--tmap-app-key", default=os.environ.get("TMAP_APP_KEY"))
    args = parser.parse_args()

    config = load_config(args.config)
    scenario = load_scenario(args.scenario)
    if args.tmap_app_key:
        config.setdefault("integrations", {})
        config["integrations"]["tmap_app_key"] = args.tmap_app_key
    env = MobilityEnv(config=config, scenario=scenario)
    policy = build_policy(args.policy)
    result = run_episode(env, policy)

    payload = result.to_dict()
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
