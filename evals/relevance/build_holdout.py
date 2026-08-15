"""Build the held-out relevance evaluation set from a published archive.

Reproducible and deterministic: no RNG. Stories are ordered by ``item_id`` and
sampled by a fixed stride, so the same ``state.db`` always yields the same set.

Why this window is a valid holdout: the taxonomy/gate was last changed on
2026-08-04, so stories fetched from 2026-08-05 onward were scored under the
*current* config and were never tuned against. The golden set stays the
development set; this is the holdout that gets scored once.

Usage:
    # fetch the same archive deploy-pages.yml publishes
    curl -fsSL https://zaherkarp.github.io/medicare-advantage-insight-engine/data/state.db -o /tmp/state.db
    python -m evals.relevance.build_holdout --db /tmp/state.db --out evals/relevance/holdout_2026-08.yaml

The emitted file carries a ``proposed_label`` per row (a rubric heuristic) that
the OWNER must confirm or correct before any precision number is reported. The
proposed labels are NOT ground truth; they exist only to make labeling a
confirm/correct pass instead of from-scratch work.
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from pathlib import Path

# Allow running as a script or a module.
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))

from ma_signal_monitor import payers  # noqa: E402
from ma_signal_monitor.scoring import _keyword_in_text  # noqa: E402

from evals.relevance.eligibility import classify_eligibility  # noqa: E402

HELDOUT_SINCE = "2026-08-05"  # day after the last taxonomy/gate change (2026-08-04)

# Deterministic stride sampling caps for the recall/true-negative strata.
DISPLAY_CAP = 60  # 0.1 <= score < 0.3  (recall-critical band)
SUBFLOOR_CAP = 25  # score < 0.1         (true-negative sanity)


def _match_entities(title: str, summary: str) -> list[str]:
    text = f"{title or ''} {summary or ''}"
    out: list[str] = []
    for group in payers.PAYER_GROUPS:
        for alias in group.aliases:
            if _keyword_in_text(alias, text):
                out.append(alias)
                break
    return out


def _proposed_label(tier: str) -> tuple[str, str]:
    """Map an eligibility tier to a proposed human label + note (owner confirms)."""
    return {
        "brief": ("relevant_brief", "carries an MA-specific signal"),
        "alert": (
            "relevant_context",
            "payer + Medicare context; defensible MA implication",
        ),
        "display": (
            "borderline_display",
            "Medicare-adjacent, no MA line — display-only?",
        ),
        "exclude": (
            "irrelevant",
            "owner-designated noise class or no Medicare context",
        ),
    }[tier]


def _stride(rows: list, cap: int) -> list:
    if len(rows) <= cap:
        return rows
    step = len(rows) / cap
    return [rows[int(i * step)] for i in range(cap)]


def build(db_path: str) -> dict:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    q = (
        "SELECT item_id, title, summary, source_name, source_priority, "
        "relevance_score, primary_category, entities, states "
        "FROM stories WHERE duplicate_of IS NULL AND fetched_at >= ? "
        "ORDER BY item_id"
    )
    rows = cur.execute(q, (HELDOUT_SINCE,)).fetchall()
    con.close()

    alert, display, subfloor = [], [], []
    for r in rows:
        s = r["relevance_score"] or 0.0
        (alert if s >= 0.3 else display if s >= 0.1 else subfloor).append(r)

    picked = alert + _stride(display, DISPLAY_CAP) + _stride(subfloor, SUBFLOOR_CAP)
    picked.sort(key=lambda r: r["item_id"])

    items = []
    for r in picked:
        ents = _match_entities(r["title"], r["summary"])
        elig = classify_eligibility(r["title"], r["summary"], ents)
        proposed, note = _proposed_label(elig.tier)
        items.append(
            {
                "item_id": r["item_id"],
                "title": r["title"],
                "summary": (r["summary"] or "")[:500],
                "source_name": r["source_name"],
                "source_priority": r["source_priority"],
                "score_at_capture": round(r["relevance_score"] or 0.0, 3),
                "stratum": (
                    "alert"
                    if (r["relevance_score"] or 0) >= 0.3
                    else "display"
                    if (r["relevance_score"] or 0) >= 0.1
                    else "subfloor"
                ),
                "predicted_tier": elig.tier,
                "proposed_label": proposed,  # OWNER CONFIRMS/CORRECTS -> `label`
                "label": None,  # ground truth — fill in review
                "note": note,
            }
        )

    fingerprint = hashlib.sha256(
        "\n".join(f"{it['item_id']}|{it['title']}" for it in items).encode()
    ).hexdigest()
    return {
        "_meta": {
            "purpose": "Held-out relevance evaluation set. proposed_label is a "
            "rubric heuristic, NOT ground truth; fill `label` by owner review "
            "before reporting any precision/recall.",
            "heldout_since": HELDOUT_SINCE,
            "strata": {
                "alert": len(alert),
                "display_sampled": len(_stride(display, DISPLAY_CAP)),
                "subfloor_sampled": len(_stride(subfloor, SUBFLOOR_CAP)),
            },
            "n": len(items),
            "content_sha256": fingerprint,
            "label_values": [
                "relevant_brief",
                "relevant_context",
                "borderline_display",
                "irrelevant",
            ],
        },
        "items": items,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--db", required=True, help="path to a published state.db (read-only)"
    )
    ap.add_argument("--out", required=True, help="output YAML path")
    args = ap.parse_args()

    import yaml

    data = build(args.db)
    with open(args.out, "w") as f:
        f.write(
            "# Held-out relevance evaluation set — proposed labels await owner "
            "confirmation.\n# Regenerate: python -m evals.relevance.build_holdout "
            "--db <state.db> --out <this file>\n"
        )
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True, width=100)
    m = data["_meta"]
    print(
        f"wrote {args.out}: n={m['n']} strata={m['strata']} sha={m['content_sha256'][:12]}"
    )


if __name__ == "__main__":
    main()
