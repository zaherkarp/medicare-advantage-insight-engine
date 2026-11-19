"""Back-test selection policies side by side: volume over the archive, accuracy on labels.

Four policies decide which stories reach the surfaced stream (public drafts and
the briefing's candidate pool):

* ``A`` current threshold: stored ``relevance_score`` >= 0.3.
* ``B`` gate only: eligibility tier ``brief`` (the gate as a hard filter).
* ``C`` threshold, plus anything the gate would brief: ``A or tier == brief``.
  The gate stops being a filter and becomes a rescue path for MA-specific
  stories that score under the threshold. Nothing ``A`` surfaces is hidden.
* ``D`` threshold, minus what the gate calls noise: ``A and tier != exclude``.

Two kinds of evidence, never mixed:

* **Volume** (``--db``): how many stories each policy surfaces per day over any
  window of the archive, and exactly which stories ``C`` adds over ``A``. No
  labels needed; says nothing about whether a story is wanted.
* **Accuracy** (``--holdout``, repeatable): recall and noise against owner
  labels, honoring ``_meta.excluded_from_scoring`` as ``scorecard`` does. A
  policy designed after seeing a holdout is only a development estimate there.

``--disagreements-out`` writes the stories where ``A`` and ``C`` disagree (the
ones ``C`` adds) as an unlabeled holdout, excluding every item already in a
``--holdout`` file by id or content. Those are the only stories whose labels
can separate ``A`` from ``C``.

Usage:
    python -m evals.relevance.backtest --db /tmp/state.db \\
        --since 2026-08-18 --until 2026-11-16 \\
        --holdout evals/relevance/holdout_2026-08.yaml \\
        --holdout evals/relevance/holdout_2026-10.yaml \\
        --out /tmp/backtest.md

Read-only against the database.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))

from evals.relevance.build_holdout import content_signature  # noqa: E402
from evals.relevance.eligibility import classify_eligibility  # noqa: E402

THRESHOLD = 0.3
LIST_CAP = 40

POLICIES = {
    "A": ("current threshold", lambda s, t: s >= THRESHOLD),
    "B": ("gate only", lambda s, t: t == "brief"),
    "C": ("threshold, plus gate-brief", lambda s, t: s >= THRESHOLD or t == "brief"),
    "D": (
        "threshold, minus gate noise",
        lambda s, t: s >= THRESHOLD and t != "exclude",
    ),
}


def _entities(raw: str | None) -> list[str]:
    try:
        return json.loads(raw or "[]")
    except ValueError:
        return []


def volume(db_path: str, since: str, until: str | None) -> dict:
    """Per-day counts per policy, and the stories C adds over A."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    q = (
        "SELECT item_id, title, summary, source_name, relevance_score, entities, "
        "substr(COALESCE(published_date, fetched_at), 1, 10) AS day "
        "FROM stories WHERE duplicate_of IS NULL "
        "AND COALESCE(published_date, fetched_at) >= ?"
    )
    params: list = [since]
    if until is not None:
        q += " AND COALESCE(published_date, fetched_at) < ?"
        params.append(until)
    rows = con.execute(q + " ORDER BY item_id", params).fetchall()
    con.close()

    per_day: dict[str, Counter] = defaultdict(Counter)
    added = []
    for item_id, title, summary, source, score, ents, day in rows:
        s = float(score or 0.0)
        tier = classify_eligibility(title, summary, _entities(ents)).tier
        for key, (_, rule) in POLICIES.items():
            per_day[day][key] += rule(s, tier)
        if POLICIES["C"][1](s, tier) and not POLICIES["A"][1](s, tier):
            added.append(
                {
                    "item_id": item_id,
                    "title": title,
                    "summary": (summary or "")[:500],
                    "source_name": source,
                    "score_at_capture": round(s, 3),
                    "day": day,
                }
            )
    days = sorted(per_day)
    totals = Counter()
    for d in days:
        totals.update(per_day[d])
    return {"days": days, "per_day": per_day, "totals": totals, "added": added}


def accuracy(holdout_path: str) -> dict:
    """Recall and noise per policy against one owner-labeled holdout."""
    from evals.relevance.scorecard import _entities as holdout_entities
    from evals.relevance.scorecard import load

    items, _, meta = load(holdout_path)
    # Score only stories the owner labeled. scorecard.load fills a missing
    # label from proposed_label, which is derived from the gate itself, so a
    # partly labeled set would grade the policies partly on their own guesses.
    total = len(items)
    items = [it for it in items if it.get("label")]
    brief = [it for it in items if it["_label"] == "relevant_brief"]
    wanted = [
        it for it in items if it["_label"] in ("relevant_brief", "relevant_context")
    ]
    out = {"n": len(items), "total": total, "brief": len(brief), "wanted": len(wanted)}
    out["unlabeled"] = not items
    scored = [
        (
            it,
            float(it.get("score_at_capture") or 0.0),
            classify_eligibility(
                it["title"], it.get("summary", ""), holdout_entities(it)
            ).tier,
        )
        for it in items
    ]
    rows = {}
    for key, (_, rule) in POLICIES.items():
        sel = [it for it, s, tier in scored if rule(s, tier)]
        rows[key] = {
            "selected": len(sel),
            "brief_caught": sum(it["_label"] == "relevant_brief" for it in sel),
            "wanted_caught": sum(
                it["_label"] in ("relevant_brief", "relevant_context") for it in sel
            ),
            "noise": sum(it["_label"] == "irrelevant" for it in sel),
        }
    out["policies"] = rows
    return out


def _pct(a: int, b: int) -> str:
    return f"{100 * a / b:.0f}%" if b else "n/a"


def render(
    vol: dict | None, acc: dict[str, dict], since: str, until: str | None
) -> str:
    out = ["# Selection policy back-test", ""]
    for key, (name, _) in POLICIES.items():
        out.append(f"- **{key}**: {name}")
    if vol is not None:
        n = len(vol["days"]) or 1
        window = f"{since} to {until}" if until else f"{since} onward"
        out += [
            "",
            f"## Volume, {window} ({len(vol['days'])} days with stories)",
            "",
            "| Policy | Per day | Total |",
            "|---|---|---|",
        ]
        for key in POLICIES:
            t = vol["totals"][key]
            out.append(f"| {key} | {t / n:.1f} | {t} |")
        added = sorted(vol["added"], key=lambda r: -r["score_at_capture"])
        out += ["", f"C adds {len(added)} stories over A:", ""]
        out += [
            f"- {r['score_at_capture']:.2f} | {r['day']} | {r['title']} ({r['source_name']})"
            for r in added[:LIST_CAP]
        ]
        if len(added) > LIST_CAP:
            out.append(f"- ... and {len(added) - LIST_CAP} more")
    for path, a in acc.items():
        out += [
            "",
            f"## Accuracy, {Path(path).name} (n={a['n']}; {a['brief']} Brief, "
            f"{a['wanted']} Brief or Context)",
            "",
        ]
        if a["unlabeled"]:
            out += ["No owner labels yet: proposed labels are circular. Skip.", ""]
            continue
        if a["n"] < a["total"]:
            out += [
                f"Partly labeled: scored on the {a['n']} owner-labeled stories of "
                f"{a['total']}; the rest are left out, not filled with guesses.",
                "",
            ]
        out += [
            "| Policy | Surfaced | Brief caught | Brief or Context caught | Noise |",
            "|---|---|---|---|---|",
        ]
        for key, r in a["policies"].items():
            out.append(
                f"| {key} | {r['selected']} | {r['brief_caught']}/{a['brief']} | "
                f"{r['wanted_caught']}/{a['wanted']} | {r['noise']}/{r['selected']} "
                f"({_pct(r['noise'], r['selected'])}) |"
            )
    return "\n".join(out).rstrip() + "\n"


def disagreements(added: list[dict], holdouts: list[str], since: str) -> dict:
    """The stories C adds over A, as an unlabeled holdout disjoint from prior ones."""
    import hashlib

    from evals.relevance.build_holdout import load_excluded

    ex_ids, ex_sigs = load_excluded(holdouts)
    items = []
    for r in sorted(added, key=lambda r: r["item_id"]):
        if r["item_id"] in ex_ids:
            continue
        if content_signature(r["title"], r["summary"]) in ex_sigs:
            continue
        items.append(
            {
                "item_id": r["item_id"],
                "title": r["title"],
                "summary": r["summary"],
                "source_name": r["source_name"],
                "score_at_capture": r["score_at_capture"],
                "stratum": "c_adds",
                "predicted_tier": "brief",
                "proposed_label": "relevant_brief",
                "label": None,
                "note": "added by policy C (gate brief tier, under the threshold)",
            }
        )
    sha = hashlib.sha256(
        "\n".join(f"{it['item_id']}|{it['title']}" for it in items).encode()
    ).hexdigest()
    return {
        "_meta": {
            "purpose": "Disagreement set: every story policy C surfaces that policy A "
            "does not. Fill `label` by owner review; proposed_label is circular.",
            "heldout_since": since,
            "strata": {"c_adds": len(items)},
            "n": len(items),
            "content_sha256": sha,
            "label_values": [
                "relevant_brief",
                "relevant_context",
                "borderline_display",
                "irrelevant",
            ],
        },
        "items": items,
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", help="a state.db copy (read-only); enables volume")
    ap.add_argument("--since", default="2026-08-05")
    ap.add_argument("--until", default=None)
    ap.add_argument("--holdout", action="append", default=[], help="repeatable")
    ap.add_argument("--out", required=True, help="Markdown report path")
    ap.add_argument(
        "--disagreements-out",
        help="write the stories C adds over A (needs --db) as an unlabeled holdout",
    )
    args = ap.parse_args(argv)

    vol = volume(args.db, args.since, args.until) if args.db else None
    acc = {h: accuracy(h) for h in args.holdout}
    Path(args.out).write_text(render(vol, acc, args.since, args.until))
    msg = f"wrote {args.out}"
    if args.disagreements_out:
        if vol is None:
            ap.error("--disagreements-out needs --db")
        import yaml

        data = disagreements(vol["added"], args.holdout, args.since)
        with open(args.disagreements_out, "w") as f:
            f.write("# Disagreement set (policy C adds over A); labels await owner.\n")
            yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True, width=100)
        msg += f"; {args.disagreements_out}: n={data['_meta']['n']}"
    print(msg)


if __name__ == "__main__":
    main()
