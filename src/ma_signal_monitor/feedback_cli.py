"""CLI for reader feedback (``ma-signal-feedback``).

Usage:
    ma-signal-feedback mark <item_id> <verdict> [category]
    ma-signal-feedback alert <item_id> <correct|false_positive|missed>
    ma-signal-feedback ingest-github
    ma-signal-feedback ingest-ntfy
    ma-signal-feedback mine-keywords
    ma-signal-feedback disagreements
    ma-signal-feedback summary <item_id>
    ma-signal-feedback stats

``mark`` records an owner verdict (weight 1.0) — useful for labelling archive
history into the golden set. Verdicts: relevant | irrelevant | wrong_category |
great. For ``wrong_category`` pass the corrected category key as ``[category]``.

``alert`` records whether a story that was actually posted to the webhook was
worth surfacing (``correct`` / ``false_positive``), or whether a story that
should have alerted but didn't (``missed``). This is data collection only —
it does not retrain scoring weights. Run it periodically against recent
alerts (see docs/feedback.md). ``correct``/``false_positive`` require the
story to have actually been delivered; ``missed`` requires that it wasn't —
the command checks ``delivery_log`` and refuses a mismatched label.

``ingest-github`` pulls crowd reactions from giscus-backed Discussions (needs
GISCUS_* config and a GITHUB_TOKEN). ``ingest-ntfy`` pulls owner 👍/👎 votes from
the ntfy feedback topic (needs NTFY_FEEDBACK_TOPIC).
"""

import sys
from pathlib import Path

from ma_signal_monitor.config import load_config
from ma_signal_monitor.storage import VALID_VERDICTS, StateStore

# Friendly CLI words for the `alert` command, mapped to the verdicts stored
# in the `feedback` table (see storage.ALERT_VERDICTS).
_ALERT_VERDICT_WORDS = {
    "correct": "alert_correct",
    "false_positive": "alert_false_positive",
    "missed": "alert_missed",
}


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

        elif cmd == "alert" and len(args) >= 3:
            item_id, word = args[1], args[2]
            if word not in _ALERT_VERDICT_WORDS:
                print(
                    f"Unknown alert verdict {word!r}. "
                    f"Choose: {', '.join(_ALERT_VERDICT_WORDS)}"
                )
                sys.exit(2)
            if store.get_story(item_id) is None:
                print(f"No story with id {item_id}.")
                sys.exit(1)
            delivered = store.get_alert_delivered(item_id)
            verdict = _ALERT_VERDICT_WORDS[word]
            if word in ("correct", "false_positive") and not delivered:
                print(
                    f"{item_id} was never successfully posted to the webhook "
                    "— use 'missed' instead, or check delivery_log."
                )
                sys.exit(2)
            if word == "missed" and delivered:
                print(
                    f"{item_id} WAS posted to the webhook — use 'correct' or "
                    "'false_positive' instead."
                )
                sys.exit(2)
            store.add_feedback(item_id, verdict, channel="cli")
            print(f"Recorded alert outcome '{word}' for {item_id}.")

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

        elif cmd == "mine-keywords":
            from ma_signal_monitor.keyword_mining import mine_keywords

            res = mine_keywords(store, config)
            print(
                f"Labeled stories: {res['positives']} relevant, "
                f"{res['negatives']} irrelevant."
            )
            if not res["inclusion"] and not res["exclusion"]:
                print("Not enough labels yet (need a few of each). Rate some stories.")
            else:
                print("\nInclusion candidates (frequent in relevant, not in taxonomy):")
                for c in res["inclusion"]:
                    print(
                        f"  {c['score']:>6.2f}  {c['term']:<28} "
                        f"(rel {c['relevant_docs']}, irrel {c['irrelevant_docs']})"
                    )
                print("\nExclusion candidates (frequent in irrelevant):")
                for c in res["exclusion"]:
                    print(
                        f"  {c['score']:>6.2f}  {c['term']:<28} "
                        f"(rel {c['relevant_docs']}, irrel {c['irrelevant_docs']})"
                    )
                print("\nReview these and edit taxonomy.yaml by hand.")

        elif cmd == "disagreements":
            from ma_signal_monitor.disagreement import find_disagreements

            rows = store.get_scored_owner_feedback()
            res = find_disagreements(rows, config.min_relevance_score)
            print(
                f"Owner-labeled stories vs. the scorer: {res['labeled']} "
                f"(threshold {config.min_relevance_score:.2f})."
            )
            if not res["over_scored"] and not res["under_scored"]:
                if res["labeled"] == 0:
                    print("No owner-labeled stories yet. Rate some stories first.")
                else:
                    print("No disagreements — the scorer and your verdicts agree. 🎉")
            else:

                def _print(rows: list[dict]) -> None:
                    for e in rows:
                        print(
                            f"  +{e['gap']:>5.2f}  score {e['score']:.2f}  "
                            f"{e['source'][:18]:<18}  {e['title'][:48]}"
                        )

                print("\nOver-scored (scorer cleared it, you marked irrelevant):")
                _print(res["over_scored"])
                print("\nUnder-scored (scorer buried it, you marked relevant):")
                _print(res["under_scored"])
                print(
                    "\nOver-scored → exclusion-keyword / weight candidates; "
                    "under-scored → inclusion-keyword / golden-set candidates."
                )

        elif cmd == "summary" and len(args) >= 2:
            item_id = args[1]
            s = store.get_feedback_summary(item_id)
            print(f"Story {item_id}")
            print(f"  your latest verdict: {s['my_verdict'] or '(none)'}")
            if s["counts"]:
                for verdict, n in sorted(s["counts"].items()):
                    print(f"  {verdict:<15} {n}")
            else:
                print("  no feedback yet")

            alert_info = store.get_alert_feedback(item_id)
            if alert_info:
                print(f"\n  combined score: {alert_info['relevance_score']}")
                print(f"  threshold at score time: {alert_info['threshold_at_score']}")
                print(
                    f"  posted to webhook: {'yes' if alert_info['delivered'] else 'no'}"
                )
                if alert_info["scoring_breakdown"]:
                    print("  scoring breakdown:")
                    for r in alert_info["scoring_breakdown"]:
                        print(
                            f"    {r['factor']:<18} {r['contribution']:+.3f}  {r['detail']}"
                        )
                if alert_info["alert_verdicts"]:
                    print("  alert verdicts:")
                    for v in alert_info["alert_verdicts"]:
                        print(
                            f"    {v['verdict']:<20} ({v['channel']}, {v['created_at']})"
                        )

        elif cmd == "stats":
            print(f"Total feedback rows: {store.count_feedback()}")

        else:
            print(main.__doc__)
            sys.exit(2)
    finally:
        store.close()
