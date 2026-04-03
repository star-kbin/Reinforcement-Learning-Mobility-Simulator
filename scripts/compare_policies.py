#!/usr/bin/env python3
"""Run multiple baseline policies on one scenario."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from simulator.env import MobilityEnv
from simulator.evaluation.runner import run_episode
from simulator.policies.factory import build_policy


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--scenario", required=True)
    args = parser.parse_args()

    policies = [build_policy("random"), build_policy("nearest"), build_policy("eta")]
    results = []
    for policy in policies:
        env = MobilityEnv.from_files(args.config, args.scenario)
        results.append(run_episode(env, policy).to_dict())

    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
