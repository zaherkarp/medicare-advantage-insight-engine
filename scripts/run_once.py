#!/usr/bin/env python3
"""Run the MA Signal Monitor once.

Usage:
    python scripts/run_once.py [--project-root /path/to/project]

This is a convenience wrapper around the main module.
"""

import argparse
import sys
from pathlib import Path

# Add src to path for direct script execution
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ma_signal_monitor.main import run


def main():
    parser = argparse.ArgumentParser(description="Run MA Signal Monitor once")
    parser.add_argument(
        "--project-root",
        type=str,
        default=str(Path(__file__).resolve().parent.parent),
        help="Path to the project root directory",
    )
    args = parser.parse_args()

    try:
        summary = run(project_root=args.project_root)
        print(f"\nRun summary:")
        for key, value in summary.items():
            print(f"  {key}: {value}")
    except (FileNotFoundError, ValueError) as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted")
        sys.exit(130)


if __name__ == "__main__":
    main()
