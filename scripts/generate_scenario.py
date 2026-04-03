#!/usr/bin/env python3
"""Placeholder script for random scenario generation."""

from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def main() -> None:
    print("Scenario generator is not implemented yet.")


if __name__ == "__main__":
    main()
