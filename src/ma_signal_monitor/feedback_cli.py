"""CLI for reader feedback (``ma-signal-feedback``).

Usage:
    ma-signal-feedback mark <item_id> <verdict> [category]
    ma-signal-feedback ingest-github
    ma-signal-feedback ingest-ntfy
    ma-signal-feedback summary <item_id>
    ma-signal-feedback stats

``mark`` records an owner verdict (weight 1.0) — useful for labelling archive
history into the golden set. Verdicts: relevant | irrelevant | wrong_category |
great. For ``wrong_category`` pass the corrected category key as ``[category]``.

``ingest-github`` pulls crowd reactions from giscus-backed Discussions (needs
GISCUS_* config and a GITHUB_TOKEN). ``ingest-ntfy`` pulls owner 👍/👎 votes from
the ntfy feedback topic (needs NTFY_FEEDBACK_TOPIC).
"""

import sys
from pathlib import Path

from ma_signal_monitor.config import load_config
from ma_signal_monitor.storage import VALID_VERDICTS, StateStore


def _open(root: Path) -> tuple:
    config = load_config(root)
    store = StateStore(root / config.db_path)
    return config, store


def main() -> None:
    args = sys.argv[1:]
    cmd = args[0] if args else "stats"
    root = Path.cwd()
    config, store = _open(root)
    try:
        if cmd == "mark" and len(args) >= 3:
            item_id, verdict = args[1], args[2]
            category = args[3] if len(args) > 3 else None
            if verdict not in VALID_VERDICTS:
                print(
                    f"Unknown verdict {verdict!r}. Choose: {', '.join(sorted(VALID_VERDICTS))}"
                )
                sys.exit(2)
            if store.get_story(item_id) is None:
                print(f"No story with id {item_id}.")
                sys.exit(1)
            if verdict == "wrong_category":
                valid = {c.key for c in config.categories}
                if category not in valid:
                    print(
                        f"wrong_category needs a valid category key: {', '.join(sorted(valid))}"
                    )
                    sys.exit(2)
            store.add_feedback(
                item_id, verdict, channel="cli", suggested_category=category
            )
            print(f"Recorded {verdict} for {item_id}.")

        elif cmd == "ingest-github":
            from ma_signal_monitor.feedback_ingest import ingest_github_feedback

            try:
                summary = ingest_github_feedback(config, store)
            except ValueError as e:
                print(f"Cannot ingest: {e}")
                sys.exit(1)
            print(
                f"giscus ingest: scanned {summary['discussions']} discussion(s), "
                f"matched {summary['reactions_matched']} reaction(s), "
                f"recorded {summary['recorded']} new row(s)."
            )

        elif cmd == "ingest-ntfy":
            from ma_signal_monitor.feedback_ingest import ingest_ntfy_feedback

            try:
                summary = ingest_ntfy_feedback(config, store)
            except ValueError as e:
                print(f"Cannot ingest: {e}")
                sys.exit(1)
            print(
                f"ntfy ingest: scanned {summary['messages']} message(s), "
                f"recorded {summary['recorded']} new row(s)."
            )

        elif cmd == "summary" and len(args) >= 2:
            s = store.get_feedback_summary(args[1])
            print(f"Story {args[1]}")
            print(f"  your latest verdict: {s['my_verdict'] or '(none)'}")
            if s["counts"]:
                for verdict, n in sorted(s["counts"].items()):
                    print(f"  {verdict:<15} {n}")
            else:
                print("  no feedback yet")

        elif cmd == "stats":
            print(f"Total feedback rows: {store.count_feedback()}")

        else:
            print(main.__doc__)
            sys.exit(2)
    finally:
        store.close()
