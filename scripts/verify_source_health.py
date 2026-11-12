"""Verify the diagnostic against the runtime archive without exposing its data.

Run serially, after ingest and before any other archive writer. Source-health
exit 1 is an operational warning, not a broken deployment. Missing evidence,
unexpected exits and detected archive mutations fail this check. SQLite shared
memory coordination files are not data and are excluded from the byte check.
"""

import argparse
import hashlib
from pathlib import Path
import subprocess
import sys

from ma_signal_monitor.config import load_config


def snapshot(db_path: Path) -> tuple[str | None, ...]:
    values = []
    for path in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-journal")):
        if not path.exists() or (path != db_path and path.stat().st_size == 0):
            values.append(None)
            continue
        with path.open("rb") as file:
            values.append(hashlib.file_digest(file, "sha256").hexdigest())
    return tuple(values)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        db_path = (
            args.root / load_config(args.root, include_discovered_sources=False).db_path
        )
        before = snapshot(db_path)
        if before[0] is None:
            print("Source-health verification unavailable: archive missing.")
            return 2
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "ma_signal_monitor.source_health_cli",
                "--root",
                str(args.root),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
            check=False,
        )
        if snapshot(db_path) != before:
            print("Source-health verification failed: archive bytes changed.")
            return 2
    except (OSError, ValueError, subprocess.TimeoutExpired):
        print("Source-health verification unavailable: runtime check could not finish.")
        return 2
    if result.returncode not in (0, 1):
        print(
            "Source-health verification unavailable: diagnostic did not return usable evidence."
        )
        return 2
    if result.returncode == 1:
        print(
            "::warning::Source-health diagnostic flagged silent sources; review the sources/status pages."
        )
    print(
        f"Source-health runtime check passed: diagnostic_exit={result.returncode}; archive unchanged."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
