"""Shadow report: what the MA-eligibility gate WOULD change, without turning it on.

The gate ships behind ``ma_eligibility_gate`` (default off). Turning it on
changes three public surfaces at once: it hides tier-``exclude`` stories from
the feed, it requires tier >= alert for a public draft, and it requires tier
``brief`` for the Daily Briefing. This report replays a window of the production
archive through the gate offline and lists, surface by surface, exactly which
stories would move, so the owner can read the cost before paying it.

WHY OFFLINE, NOT A LIVE SHADOW FLAG: the tier is a pure function of a story's
title, summary, and matched entities (``eligibility.classify_eligibility``), and
all three are stored in ``stories``. So replaying the archive gives the same
answer a live shadow run would, for any past window, today, with no production
change. A live flag would also have had a visible side effect: the story page
renders a stored tier as an "MA <tier>" badge, so persisting tiers with the gate
"off" would have published them.

What it approximates, stated:
  * Scores. With the gate on, payer aliases collapse to groups
    (``entity_group_delta``), lowering some scores. The gated score is rebuilt
    from the stored ``scoring_breakdown`` (sum of contributions, plus the delta,
    clamped), so it matches production except for rows whose breakdown is
    missing, where the stored score is used and counted in the header.
  * The briefing. Production builds it from a 24-hour lookback ending when the
    digest runs. This groups by calendar day of ``COALESCE(published_date,
    fetched_at)`` instead, so a day's lists are indicative, not exact.
  * The vocabulary is TODAY's ``config/taxonomy.yaml``, which is the one that
    would run if the gate were switched on now.

Usage:
    python -m evals.relevance.shadow_report --db /tmp/state.db \\
        --since 2026-10-01 --until 2026-10-15 --out /tmp/shadow.md

Read-only against the database. Prints a summary line; writes Markdown.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))

from ma_signal_monitor.payers import ALIAS_TO_GROUP  # noqa: E402

from evals.relevance.eligibility import (  # noqa: E402
    classify_eligibility,
    eligible_for,
    entity_group_delta,
)

# Production defaults (config/app.yaml): the feed's display floor, the alert /
# briefing threshold, the briefing cap, and the entity boost the delta undoes.
DISPLAY_FLOOR = 0.1
ALERT_THRESHOLD = 0.3
BRIEFING_CAP = 12
ENTITY_BOOST = 0.20
LIST_CAP = 25  # titles listed per section; counts are always complete


@dataclass
class Row:
    item_id: str
    title: str
    source: str
    day: str
    score: float  # as stored (gate off)
    gated_score: float  # as it would be with the gate on
    tier: str
    reason: str
    rebuilt: bool  # gated_score came from the breakdown, not the fallback


def _gated_score(stored: float, breakdown_json: str | None, delta: float):
    """Rebuild the gate-on score. Returns (score, rebuilt_from_breakdown)."""
    try:
        reasons = json.loads(breakdown_json) if breakdown_json else None
    except (TypeError, ValueError):
        reasons = None
    if not reasons:
        return max(0.0, min(1.0, stored + delta)), False
    if any(r.get("factor") == "exclusion_veto" for r in reasons):
        return 0.0, True
    raw = sum(float(r.get("contribution") or 0.0) for r in reasons)
    return max(0.0, min(1.0, raw + delta)), True


def load_rows(db_path: str, since: str, until: str | None) -> list[Row]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    q = (
        "SELECT item_id, title, summary, source_name, relevance_score, entities, "
        "scoring_breakdown, COALESCE(published_date, fetched_at) AS ts "
        "FROM stories WHERE duplicate_of IS NULL "
        "AND COALESCE(published_date, fetched_at) >= ?"
    )
    params: list = [since]
    if until is not None:
        q += " AND COALESCE(published_date, fetched_at) < ?"
        params.append(until)
    q += " ORDER BY item_id"
    raw = con.execute(q, params).fetchall()
    con.close()

    out = []
    for r in raw:
        try:
            ents = json.loads(r["entities"] or "[]")
        except ValueError:
            ents = []
        elig = classify_eligibility(r["title"], r["summary"], ents)
        delta = entity_group_delta(ents, ALIAS_TO_GROUP, ENTITY_BOOST)
        stored = float(r["relevance_score"] or 0.0)
        gated, rebuilt = _gated_score(stored, r["scoring_breakdown"], delta)
        out.append(
            Row(
                item_id=r["item_id"],
                title=r["title"] or "",
                source=r["source_name"] or "",
                day=(r["ts"] or "")[:10],
                score=stored,
                gated_score=round(gated, 3),
                tier=elig.tier,
                reason=elig.reasons[0] if elig.reasons else "",
                rebuilt=rebuilt,
            )
        )
    return out


def _hidden_class(r: Row) -> str:
    """Why a visible story leaves the feed: its exclusion class, or the floor."""
    if r.tier != "exclude":
        return "score under the display floor after the alias correction"
    return _reason_class(r.reason)


def _reason_class(reason: str) -> str:
    """'excluded: medicaid_only:Medicaid' -> 'medicaid_only'; else the reason."""
    if reason.startswith("excluded: "):
        return reason[len("excluded: ") :].split(":", 1)[0]
    return reason


def analyse(rows: list[Row]) -> dict:
    visible = [r for r in rows if r.score >= DISPLAY_FLOOR]
    # Two ways off the feed: the display floor hides tier 'exclude', and the
    # alias correction can push a score under the archive floor whatever the
    # tier. Production stores the corrected score, so both count.
    hidden = [
        r for r in visible if r.tier == "exclude" or r.gated_score < DISPLAY_FLOOR
    ]

    alerts_now = [r for r in rows if r.score >= ALERT_THRESHOLD]
    alerts_kept = [
        r
        for r in alerts_now
        if r.gated_score >= ALERT_THRESHOLD and eligible_for(r.tier, "alert")
    ]
    kept_ids = {r.item_id for r in alerts_kept}
    alerts_lost = [r for r in alerts_now if r.item_id not in kept_ids]

    by_day: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        by_day[r.day].append(r)
    days = []
    for day in sorted(by_day):
        day_rows = by_day[day]
        now = sorted(
            (r for r in day_rows if r.score >= ALERT_THRESHOLD),
            key=lambda r: -r.score,
        )[:BRIEFING_CAP]
        gated = sorted(
            (
                r
                for r in day_rows
                if r.gated_score >= ALERT_THRESHOLD and r.tier == "brief"
            ),
            key=lambda r: -r.gated_score,
        )[:BRIEFING_CAP]
        now_ids = {r.item_id for r in now}
        gated_ids = {r.item_id for r in gated}
        days.append(
            {
                "day": day,
                "now": len(now),
                "gated": len(gated),
                "leave": [r for r in now if r.item_id not in gated_ids],
                "enter": [r for r in gated if r.item_id not in now_ids],
            }
        )

    return {
        "n": len(rows),
        "not_rebuilt": sum(1 for r in rows if not r.rebuilt),
        "visible_tiers": Counter(r.tier for r in visible),
        "visible": visible,
        "hidden": hidden,
        "hidden_by_reason": Counter(_hidden_class(r) for r in hidden),
        "alerts_now": alerts_now,
        "alerts_kept": alerts_kept,
        "alerts_lost": alerts_lost,
        "days": days,
    }


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "n/a"


def _line(r: Row, gated: bool = False) -> str:
    score = f"{r.score:.2f}"
    if gated and r.gated_score != r.score:
        score += f" -> {r.gated_score:.2f}"
    return f"- {score} | {r.tier} | {r.title} ({r.source}). _{r.reason}_"


def render(a: dict, since: str, until: str | None) -> str:
    window = f"{since} to {until}" if until else f"{since} onward"
    out = [
        f"# Eligibility gate shadow report, {window}",
        "",
        f"{a['n']} non-duplicate stories in the window. Gate OFF is what the site "
        "does today; gate ON is what it would do. Nothing here changes "
        "production.",
    ]
    if a["not_rebuilt"]:
        out.append(
            f"{a['not_rebuilt']} rows had no scoring breakdown; their gate-on "
            "score is the stored score plus the alias correction."
        )

    vis = a["visible"]
    out += [
        "",
        "## Feed (stories at or above the display floor)",
        "",
        f"Visible today: {len(vis)}. Hidden with the gate on: {len(a['hidden'])} "
        f"({_pct(len(a['hidden']), len(vis))}).",
        "",
        "Tier mix of what is visible today: "
        + ", ".join(
            f"{t} {a['visible_tiers'].get(t, 0)}"
            for t in ("brief", "alert", "display", "exclude")
        )
        + ".",
        "",
        "Why the hidden ones are hidden:",
        "",
    ]
    out += [f"- {cls}: {n}" for cls, n in a["hidden_by_reason"].most_common()]
    out += ["", f"Highest-scoring hidden stories (up to {LIST_CAP}):", ""]
    out += [
        _line(r, gated=True)
        for r in sorted(a["hidden"], key=lambda r: -r.score)[:LIST_CAP]
    ]

    out += [
        "",
        "## Public drafts (alert stream)",
        "",
        f"Drafted today: {len(a['alerts_now'])}. Kept with the gate on: "
        f"{len(a['alerts_kept'])}. Dropped: {len(a['alerts_lost'])} "
        f"({_pct(len(a['alerts_lost']), len(a['alerts_now']))}).",
        "",
        f"Dropped, highest score first (up to {LIST_CAP}):",
        "",
    ]
    out += [
        _line(r, gated=True)
        for r in sorted(a["alerts_lost"], key=lambda r: -r.score)[:LIST_CAP]
    ]

    out += [
        "",
        "## Daily Briefing, by calendar day (indicative)",
        "",
        "| Day | Gate off | Gate on | Leave | Enter |",
        "|---|---|---|---|---|",
    ]
    for d in a["days"]:
        out.append(
            f"| {d['day']} | {d['now']} | {d['gated']} | "
            f"{len(d['leave'])} | {len(d['enter'])} |"
        )
    out += ["", "Stories leaving or entering a briefing:", ""]
    for d in a["days"]:
        if not d["leave"] and not d["enter"]:
            continue
        out.append(f"### {d['day']}")
        out.append("")
        if d["leave"]:
            out += ["Leave:"] + [_line(r, gated=True) for r in d["leave"]]
        if d["enter"]:
            if d["leave"]:
                out.append("")
            out += ["Enter:"] + [_line(r, gated=True) for r in d["enter"]]
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, help="path to a state.db (read-only)")
    ap.add_argument("--since", required=True, help="inclusive ISO date lower bound")
    ap.add_argument("--until", default=None, help="exclusive ISO date upper bound")
    ap.add_argument("--out", required=True, help="Markdown output path")
    args = ap.parse_args(argv)

    a = analyse(load_rows(args.db, args.since, args.until))
    Path(args.out).write_text(render(a, args.since, args.until))
    print(
        f"wrote {args.out}: n={a['n']} feed_hidden={len(a['hidden'])}/"
        f"{len(a['visible'])} drafts_dropped={len(a['alerts_lost'])}/"
        f"{len(a['alerts_now'])}"
    )


if __name__ == "__main__":
    main()
