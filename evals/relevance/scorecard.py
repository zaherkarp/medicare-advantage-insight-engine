"""Relevance precision/recall scorecard — labels vs archive.

The shipped ``scripts/scorecard.py`` reports the golden set (a *development*
set the taxonomy was tuned on) and archive *distributions*. It has no
labels-vs-archive precision mode. This adds one, against the held-out set
(``build_holdout.py``), and compares the current selection against the
candidate two-tier eligibility gate (``eligibility.py``).

Metrics (selection precision is a clean proxy for briefing precision — the
briefing is the top-scoring slice of the alert-grade set):

* Briefing precision  = P(label = relevant_brief | selected for briefing)
* Must-catch recall   = P(selected for briefing | label = relevant_brief)
* Alert precision      = P(label in {relevant_brief, relevant_context} | alerted)
* False-positive types = negative-class breakdown of current alert-grade misses
* Volume               = count clearing each bar
* Recall floors        = the owner's pre-registered pass/fail bar (below)

Recall floors (owner, 2026-10-21, set before any October label was scored):

* No ``relevant_brief`` story may be HIDDEN (tier ``exclude``: off the feed).
* At most ONE ``relevant_brief`` story may be DEMOTED (still on the feed, but
  below the briefing tier), and only if it is a one-off rather than a pattern.
  The count is checked here; "not a pattern" is a judgment, so the demoted
  stories are listed for the owner to read.
* No ``relevant_context`` story may be HIDDEN. Payer strategy news (deals,
  joint ventures, plan exits) is labeled ``relevant_context`` by owner ruling.

Baseline briefing/alert bar = stored ``relevance_score`` >= 0.3.
Candidate briefing bar = eligibility tier >= brief; alert bar = tier >= alert.

Run:
    python -m evals.relevance.scorecard --holdout evals/relevance/holdout_2026-08.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))

from evals.relevance.eligibility import classify_eligibility, eligible_for  # noqa: E402


def _pct(n: int, d: int) -> str:
    return f"{n}/{d} = {100 * n / d:.0f}%" if d else "n/a (0)"


def load(path: str) -> list[dict]:
    import yaml

    data = yaml.safe_load(open(path))
    items = data["items"]
    using_proposed = all(it.get("label") is None for it in items)
    for it in items:
        it["_label"] = it.get("label") or it.get("proposed_label")
    return items, using_proposed, data["_meta"]


def report(path: str) -> None:
    items, using_proposed, meta = load(path)
    if using_proposed:
        print("!" * 76)
        print("!!  USING PROPOSED (heuristic) LABELS — NOT OWNER-CONFIRMED.")
        print(
            "!!  The CANDIDATE column is CIRCULAR here: proposed_label is derived FROM the"
        )
        print(
            "!!  candidate, so it reads ~100% by construction and is meaningless until real"
        )
        print(
            "!!  labels replace it. The CURRENT column (bar independent of the labels) is a"
        )
        print(
            "!!  provisional-but-informative estimate. Fill `label` by owner review to report."
        )
        print("!" * 76)

    for it in items:
        elig = classify_eligibility(
            it["title"],
            it["summary"],
            # re-derive entities from the stored tier build is overkill;
            # payer presence only affects alert tier, recompute lightly:
            _entities(it),
        )
        it["_tier"] = elig.tier
        it["_neg"] = elig.negative_hits
        it["_base_alert"] = (it["score_at_capture"] or 0) >= 0.3

    rb = [it for it in items if it["_label"] == "relevant_brief"]
    # ---- Briefing precision (strict): numerator = relevant_brief ----
    base_brief = [it for it in items if it["_base_alert"]]
    cand_brief = [it for it in items if eligible_for(it["_tier"], "brief")]
    base_brief_ok = [it for it in base_brief if it["_label"] == "relevant_brief"]
    cand_brief_ok = [it for it in cand_brief if it["_label"] == "relevant_brief"]

    # ---- Must-catch recall: relevant_brief items that survive each bar ----
    base_recall = [it for it in rb if it["_base_alert"]]
    cand_recall = [it for it in rb if eligible_for(it["_tier"], "brief")]

    # ---- Alert precision (looser bar) ----
    base_alert = base_brief
    cand_alert = [it for it in items if eligible_for(it["_tier"], "alert")]
    relevant_any = {"relevant_brief", "relevant_context"}
    base_alert_ok = [it for it in base_alert if it["_label"] in relevant_any]
    cand_alert_ok = [it for it in cand_alert if it["_label"] in relevant_any]

    print(
        f"\nHeld-out set: n={meta['n']} (heldout since {meta['heldout_since']}); "
        f"relevant_brief labels: {len(rb)}\n"
    )
    print(
        "                                  CURRENT (score>=0.3)     CANDIDATE (Option A tier)"
    )
    print(
        f"  Briefing selection precision    {_pct(len(base_brief_ok), len(base_brief)):<24s} "
        f"{_pct(len(cand_brief_ok), len(cand_brief))}"
    )
    print(
        f"  Must-catch recall (rel_brief)   {_pct(len(base_recall), len(rb)):<24s} "
        f"{_pct(len(cand_recall), len(rb))}"
    )
    print(
        f"  Alert-stream precision          {_pct(len(base_alert_ok), len(base_alert)):<24s} "
        f"{_pct(len(cand_alert_ok), len(cand_alert))}"
    )
    print(
        f"  Volume clearing the bar         {len(base_brief):<24d} "
        f"brief={len(cand_brief)}  alert={len(cand_alert)}"
    )

    # ---- False-positive typing of CURRENT alert-grade misses ----
    fp = [it for it in base_brief if it["_label"] != "relevant_brief"]
    from collections import Counter

    types = Counter()
    for it in fp:
        types[it["_neg"][0].split(":")[0] if it["_neg"] else it["_tier"]] += 1
    print(
        f"\n  Current alert-grade items NOT labeled relevant_brief: {len(fp)}/{len(base_brief)}"
    )
    for t, n in types.most_common():
        print(f"      {t:16s} {n}")

    # ---- What the candidate would newly drop from the briefing bar ----
    dropped = [it for it in base_brief if not eligible_for(it["_tier"], "brief")]
    kept_rb = [it for it in dropped if it["_label"] == "relevant_brief"]
    print(
        f"\n  Candidate drops {len(dropped)} of {len(base_brief)} current alert-grade items "
        f"from the briefing bar."
    )
    print(f"      of those, labeled relevant_brief (recall cost): {len(kept_rb)}")
    if kept_rb:
        for it in kept_rb[:8]:
            print(f"        - [{it['score_at_capture']:.2f}] {it['title'][:64]}")

    _print_floors(items)


MAX_BRIEF_DEMOTED = 1


def floors(items: list[dict]) -> dict:
    """The owner's recall floors for the candidate gate. Pure, for testing.

    Expects each item to carry ``_label`` and ``_tier`` (as ``report`` sets).
    """
    brief = [it for it in items if it["_label"] == "relevant_brief"]
    context = [it for it in items if it["_label"] == "relevant_context"]
    brief_hidden = [it for it in brief if it["_tier"] == "exclude"]
    brief_demoted = [
        it
        for it in brief
        if it["_tier"] != "exclude" and not eligible_for(it["_tier"], "brief")
    ]
    context_hidden = [it for it in context if it["_tier"] == "exclude"]
    checks = {
        "no brief story hidden": not brief_hidden,
        f"at most {MAX_BRIEF_DEMOTED} brief story demoted": len(brief_demoted)
        <= MAX_BRIEF_DEMOTED,
        "no context story hidden": not context_hidden,
    }
    return {
        "brief_hidden": brief_hidden,
        "brief_demoted": brief_demoted,
        "context_hidden": context_hidden,
        "checks": checks,
        "passed": all(checks.values()),
    }


def _print_floors(items: list[dict]) -> None:
    f = floors(items)
    print("\n  Recall floors (owner, pre-registered 2026-10-21)")
    for name, ok in f["checks"].items():
        print(f"      {'PASS' if ok else 'FAIL'}  {name}")
    for key, head in (
        ("brief_hidden", "brief stories hidden from the feed"),
        ("brief_demoted", "brief stories demoted (judge: one-off or pattern?)"),
        ("context_hidden", "context stories hidden from the feed"),
    ):
        if f[key]:
            print(f"      {head}: {len(f[key])}")
            for it in f[key]:
                print(f"        - [{it['_tier']}] {it['title'][:70]}")
    print(f"  Floors overall: {'PASS' if f['passed'] else 'FAIL'}")


def _entities(it: dict) -> list[str]:
    """Lightweight payer detection for the alert-tier check (brief tier ignores it)."""
    from ma_signal_monitor import payers
    from ma_signal_monitor.scoring import _keyword_in_text

    text = f"{it['title']} {it.get('summary', '')}"
    out = []
    for g in payers.PAYER_GROUPS:
        for a in g.aliases:
            if _keyword_in_text(a, text):
                out.append(a)
                break
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--holdout", required=True)
    report(ap.parse_args().holdout)


if __name__ == "__main__":
    main()
