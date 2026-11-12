"""Silent-source report (``ma-signal-source-health``).

Prints every enabled source that has gone silent for
``config.source_silent_days`` or more — see ``source_health.py`` for what
"silent" means and why it needs ``source_fetch_log`` rather than the
``stories`` table (a broken source has no story rows to compute anything
from, which is exactly how 16 sources went unnoticed for months).

Read-only: never disables anything, matching ``source_review.py``'s stance
that a human confirms any change to ``sources.yaml``. Exits 1 when any
source is flagged, so it can gate a CI/cron step if an operator wants that;
run by hand otherwise. Exits 2 when the archive is unavailable or has no
fetch observations; missing evidence is not a healthy result. The deployment
runs scripts/verify_source_health.py after ingest: it accepts silent-source
warnings but fails unavailable evidence or archive mutation. It suppresses
source details in public Actions logs. The live/static `/sources` and
`/status` pages remain the primary surfaces for source-level findings.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

from ma_signal_monitor.config import load_config
from ma_signal_monitor.source_health import flag_silent_sources
from ma_signal_monitor.storage import StateStore


def main(argv: list[str] | None = None) -> int:
    """CLI entry point (installed as ``ma-signal-source-health``).

    Usage: ``ma-signal-source-health [--root PATH]``
    """
    parser = argparse.ArgumentParser(
        description="Report enabled sources that have gone silent."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Project root containing config/ and the archive DB (default: cwd)",
    )
    args = parser.parse_args(argv)

    config = load_config(args.root)
    store = None
    try:
        store = StateStore(args.root / config.db_path, read_only=True)
        # Look back over at least 2x the silent-days threshold so a source
        # that persisted right at the edge of the window still has that
        # attempt on record instead of appearing to have none at all.
        health = store.get_source_fetch_health(
            lookback_days=max(60, config.source_silent_days * 2)
        )
    except (sqlite3.Error, OSError) as exc:
        print(f"Source health unavailable: {exc}", file=sys.stderr)
        return 2
    finally:
        if store is not None:
            store.close()

    if not health:
        print(
            "Source health unavailable: no fetch observations in the lookback window.",
            file=sys.stderr,
        )
        return 2

    flagged = flag_silent_sources(health, config)

    if not flagged:
        print(f"No silent sources (threshold: {config.source_silent_days}d).")
        return 0

    print(
        f"{len(flagged)} silent source(s) (threshold: {config.source_silent_days}d):\n"
    )
    for f in sorted(flagged, key=lambda f: -f["priority"]):
        print(f"  [p{f['priority']}] {f['source_name']}: {f['reason']}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
