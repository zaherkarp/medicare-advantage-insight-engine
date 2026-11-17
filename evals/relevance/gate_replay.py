"""Read-only four-way archive replay for the revised, experimental MA gate.

Run clocks are historical briefing execution times, with UTC offsets. The input
archive must come from a gate-off run. Availability is bounded by fetched_at,
and a story's briefing window uses its published time with fetched_at fallback.
Archived duplicate assignments are reused. The candidate also groups known
event families before the briefing cap. This replays selection, not draft text
generation or historical edits to articles and duplicate links.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import yaml

from evals.relevance.build_holdout import content_signature
from evals.relevance.shadow_report import _gated_score
from ma_signal_monitor.config import AppConfig, load_config
from ma_signal_monitor.eligibility import (
    classify_eligibility,
    eligible_for,
    entity_group_delta,
    vocab_from_config,
)
from ma_signal_monitor.payers import ALIAS_TO_GROUP

ROOT = Path(__file__).resolve().parents[2]
ARMS = {
    "baseline": (False, False),
    "alias_only": (True, False),
    "eligibility_only": (False, True),
    "combined": (True, True),
}
LABELS = {"relevant_brief", "relevant_context", "borderline_display", "irrelevant"}
STRATEGY_ACTION = re.compile(
    r"\b(?:acquir\w*|buy|buys|deal|divest\w*|exit\w*|joint venture|"
    r"pulls back|sell\w*|shuts? down|wind\w* down)\b",
    re.IGNORECASE,
)
STRATEGY_BUSINESS = re.compile(
    r"\b(?:health plans?|managed care|insurance|insurers?|senior primary care|"
    r"senior clinics?|centerwell)\b",
    re.IGNORECASE,
)


def timestamp(value: str, *, require_offset: bool = False) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None and require_offset:
        raise ValueError(f"UTC offset required for {value}")
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)


@dataclass(frozen=True)
class Evidence:
    event_id: str
    checked_at: datetime
    status: str
    url: str
    rationale: str


@dataclass(frozen=True)
class Story:
    item_id: str
    title: str
    link: str
    summary: str
    fetched: datetime
    published: datetime
    duplicate: bool
    score: float
    alias_score: float
    tier: str
    reason: str
    payer_present: bool = False


def load_evidence(path: Path | None) -> dict[str, Evidence]:
    if path is None:
        return {}
    rows = yaml.safe_load(path.read_text())
    result = {}
    for item_id, row in rows.items():
        url = urlparse(row["url"])
        if (
            row["status"] not in {"confirmed_context", "review"}
            or url.scheme != "https"
            or not url.hostname
            or not row["event_id"].strip()
            or not row["rationale"].strip()
        ):
            raise ValueError(f"Invalid evidence for {item_id}")
        result[str(item_id)] = Evidence(
            row["event_id"],
            timestamp(str(row["checked_at"]), require_offset=True),
            row["status"],
            row["url"],
            row["rationale"],
        )
    return result


def load_archive(
    path: Path, start: datetime, end: datetime, config: AppConfig
) -> list[Story]:
    vocab = vocab_from_config(config)
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            """SELECT item_id, title, link, summary, published_date, fetched_at,
                      duplicate_of, relevance_score, entities, scoring_breakdown,
                      eligibility_tier
               FROM stories ORDER BY item_id"""
        )
        result = []
        for row in rows:
            fetched = timestamp(row["fetched_at"])
            published = timestamp(row["published_date"] or row["fetched_at"])
            if fetched >= end or max(fetched, published) < start:
                continue
            if row["eligibility_tier"] is not None:
                raise ValueError(f"Gate-on archive row {row['item_id']}")
            score = float(row["relevance_score"] or 0)
            entities = json.loads(row["entities"] or "[]")
            delta = entity_group_delta(
                entities, ALIAS_TO_GROUP, config.scoring.entity_match_boost
            )
            corrected = score
            if delta:
                rebuilt_score, rebuilt = _gated_score(
                    score, row["scoring_breakdown"], delta
                )
                if not rebuilt:
                    raise ValueError(f"Missing score breakdown for {row['item_id']}")
                corrected = round(rebuilt_score, 3)
            tier = classify_eligibility(row["title"], row["summary"], entities, vocab)
            result.append(
                Story(
                    row["item_id"],
                    row["title"],
                    row["link"],
                    row["summary"] or "",
                    fetched,
                    published,
                    row["duplicate_of"] is not None,
                    score,
                    corrected,
                    tier.tier,
                    tier.reasons[0],
                    tier.payer_present,
                )
            )
    return result


def archive_clocks(path: Path, start: datetime, end: datetime) -> list[datetime]:
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        generated = [
            timestamp(row[0]) for row in db.execute("SELECT generated_at FROM digests")
        ]
    return sorted({clock for clock in generated if start <= clock < end})


def feed_tier(tier: str, evidence: Evidence | None) -> str:
    if tier == "exclude" and evidence and evidence.status == "confirmed_context":
        return "display"
    return tier


def evaluate(
    stories: list[Story],
    evidence: dict[str, Evidence],
    clocks: list[datetime],
    start: datetime,
    end: datetime,
    config: AppConfig,
) -> dict:
    """Compare scores, eligibility and briefing rank on identical inputs."""
    snapshots = {}
    row_details = {}
    archive_slice = [
        s for s in stories if not s.duplicate and start <= s.published < end
    ]
    fetched_slice = [s for s in stories if start <= s.fetched < end]
    for story in stories:
        entry = evidence.get(story.item_id)
        observed = entry if entry and entry.checked_at < end else None
        revised_tier = feed_tier(story.tier, observed)
        row_details[story.item_id] = {
            "title": story.title,
            "baseline_score": story.score,
            "alias_score": story.alias_score,
            "original_tier": story.tier,
            "revised_tier": revised_tier,
            "tier_reason": story.reason,
            "archive_duplicate": story.duplicate,
            "evidence_url": observed.url if observed else None,
            "evidence_rationale": observed.rationale if observed else None,
            "review": story.tier == "exclude"
            and bool(
                observed
                or (
                    STRATEGY_ACTION.search(story.title)
                    and (story.payer_present or STRATEGY_BUSINESS.search(story.title))
                )
            ),
        }

    for arm, (alias, gate) in ARMS.items():

        def score(story: Story) -> float:
            return story.alias_score if alias else story.score

        feed = [
            s.item_id
            for s in archive_slice
            if score(s) >= config.archive_min_score
            and (not gate or row_details[s.item_id]["revised_tier"] != "exclude")
        ]
        drafts = [
            s.item_id
            for s in fetched_slice
            if score(s) >= config.min_relevance_score
            and (not gate or eligible_for(s.tier, "alert"))
        ]
        runs = {}
        for clock in clocks:
            candidates = sorted(
                (
                    s
                    for s in stories
                    if not s.duplicate
                    and clock - timedelta(hours=config.digest_lookback_hours)
                    <= s.published
                    <= clock
                    and s.fetched <= clock
                    and score(s) >= config.digest_min_score
                    and (not gate or s.tier == "brief")
                ),
                key=lambda s: (score(s), s.published),
                reverse=True,
            )
            ranked = []
            event_duplicates = []
            events = set()
            for story in candidates:
                entry = evidence.get(story.item_id)
                event = (
                    ("event", entry.event_id)
                    if gate and entry and entry.checked_at <= clock
                    else ("item", story.item_id)
                )
                if event not in events:
                    ranked.append(story.item_id)
                    events.add(event)
                else:
                    event_duplicates.append(story.item_id)
            runs[clock.isoformat()] = {
                "selected": ranked[: config.digest_max_items],
                "below_cap": ranked[config.digest_max_items :],
                "event_duplicates": event_duplicates,
            }
        snapshots[arm] = {"feed": feed, "drafts": drafts, "briefings": runs}
    baseline = snapshots["baseline"]
    for arm in ("alias_only", "eligibility_only", "combined"):
        current = snapshots[arm]
        current["changes"] = {}
        for surface in ("feed", "drafts"):
            current["changes"][surface] = {
                "left": sorted(set(baseline[surface]) - set(current[surface])),
                "entered": sorted(set(current[surface]) - set(baseline[surface])),
            }
        for clock, run in current["briefings"].items():
            before = set(baseline["briefings"][clock]["selected"])
            after = set(run["selected"])
            run["left"] = sorted(before - after)
            run["entered"] = sorted(after - before)
    return {"arms": snapshots, "items": row_details}


def acceptance(
    stories: list[Story], replay: dict, labels: dict, prior: dict, min_gain: float
) -> dict:
    """Score complete, independently reviewed inputs with explicit event isolation."""
    rows = {s.item_id: s for s in stories if not s.duplicate}
    if not 0 < min_gain <= 1:
        raise ValueError("Minimum precision gain must be in (0, 1]")
    if (
        not rows
        or set(labels) != set(rows)
        or not prior.get("event_ids")
        or not prior.get("items")
    ):
        raise ValueError("Acceptance requires full labels and a prior-corpus manifest")
    old_events = set(prior["event_ids"])
    old_ids = {it.get("item_id") for it in prior["items"]}
    old_titles = {content_signature(it["title"], None) for it in prior["items"]}
    old_text = {
        content_signature(it["title"], it.get("summary")) for it in prior["items"]
    }
    for item_id, row in labels.items():
        if (
            row.get("label") not in LABELS
            or not row.get("reviewer", "").strip()
            or row.get("blind") is not True
            or not row.get("event_id", "").strip()
        ):
            raise ValueError(f"Incomplete independent label for {item_id}")
        story = rows[item_id]
        if (
            item_id in old_ids
            or row["event_id"] in old_events
            or content_signature(story.title, None) in old_titles
            or content_signature(story.title, story.summary) in old_text
        ):
            raise ValueError(f"Prior-corpus overlap for {item_id}")
    all_brief = {
        row["event_id"] for row in labels.values() if row["label"] == "relevant_brief"
    }
    if not all_brief:
        raise ValueError("Acceptance requires brief-worthy events")
    result = {}
    for arm, selection in replay["arms"].items():
        slots = [
            labels[item_id]
            for run in selection["briefings"].values()
            for item_id in run["selected"]
        ]
        hit = [row for row in slots if row["label"] == "relevant_brief"]
        recalled = {row["event_id"] for row in hit}
        result[arm] = {
            "briefing_precision": len(hit) / len(slots) if slots else 0.0,
            "briefing_slots": len(slots),
            "event_recall": len(recalled) / len(all_brief),
            "missed_brief_events": sorted(all_brief - recalled),
        }
    hidden = set(replay["arms"]["combined"]["changes"]["feed"]["left"])
    hidden_relevant = sorted(
        item_id
        for item_id in hidden
        if labels[item_id]["label"] in {"relevant_brief", "relevant_context"}
    )
    baseline_misses = set(result["baseline"]["missed_brief_events"])
    combined_misses = set(result["combined"]["missed_brief_events"])
    demoted = sorted(combined_misses - baseline_misses)
    gain = (
        result["combined"]["briefing_precision"]
        - result["baseline"]["briefing_precision"]
    )
    checks = {
        "no_relevant_hidden": not hidden_relevant,
        "at_most_one_brief_event_demoted": len(demoted) <= 1,
        "minimum_precision_gain": gain >= min_gain,
    }
    return {
        "metrics": result,
        "hidden_relevant": hidden_relevant,
        "brief_events_demoted": demoted,
        "precision_gain": gain,
        "minimum_precision_gain": min_gain,
        "checks": checks,
        "status": (
            "fail"
            if not all(checks.values())
            else "needs_demotion_review"
            if demoted
            else "passes_measured_floors"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument(
        "--since", required=True, help="Inclusive timestamp with UTC offset"
    )
    parser.add_argument(
        "--until", required=True, help="Exclusive timestamp with UTC offset"
    )
    parser.add_argument(
        "--clock",
        action="append",
        help="Override the archived briefing run times. Repeat for each run.",
    )
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--prior", type=Path)
    parser.add_argument("--min-precision-gain", type=float)
    parser.add_argument("--blind-out", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    inputs = {
        p.resolve() for p in (args.db, args.labels, args.prior, args.evidence) if p
    }
    outputs = [p.resolve() for p in (args.out, args.blind_out) if p]
    if inputs.intersection(outputs) or len(set(outputs)) != len(outputs):
        parser.error("Output paths must be distinct from each other and all inputs")
    start = timestamp(args.since, require_offset=True)
    end = timestamp(args.until, require_offset=True)
    clocks = (
        sorted({timestamp(c, require_offset=True) for c in args.clock})
        if args.clock
        else archive_clocks(args.db, start, end)
    )
    if not clocks:
        parser.error("No archived briefing clocks found. Supply --clock.")
    if not start < end or any(not start <= c < end for c in clocks):
        parser.error("Briefing run clocks must fall inside the evaluation window")
    acceptance_inputs = (args.labels, args.prior, args.min_precision_gain)
    if any(value is not None for value in acceptance_inputs) and any(
        value is None for value in acceptance_inputs
    ):
        parser.error("Acceptance requires --labels, --prior, and --min-precision-gain")
    # SEC identity is validated by load_config; the offline replay makes no HTTP calls.
    os.environ.setdefault("SEC_CONTACT_EMAIL", "replay@example.invalid")
    config = load_config(ROOT)
    with args.db.open("rb") as archive:
        source_sha = hashlib.file_digest(archive, "sha256").hexdigest()
    stories = load_archive(
        args.db, start - timedelta(hours=config.digest_lookback_hours), end, config
    )
    replay = evaluate(stories, load_evidence(args.evidence), clocks, start, end, config)
    replay["archive_sha256"] = source_sha
    replay["window"] = [start.isoformat(), end.isoformat()]
    replay["selection_config"] = {
        "feed_floor": config.archive_min_score,
        "draft_floor": config.min_relevance_score,
        "briefing_floor": config.digest_min_score,
        "briefing_lookback_hours": config.digest_lookback_hours,
        "briefing_cap": config.digest_max_items,
        "entity_match_boost": config.scoring.entity_match_boost,
    }
    if args.blind_out:
        blind_rows = {
            story.item_id: {
                "title": story.title,
                "summary": story.summary,
                "url": story.link,
            }
            for story in stories
            if not story.duplicate
        }
        args.blind_out.write_text(yaml.safe_dump(blind_rows, allow_unicode=True))
    if args.labels:
        replay["acceptance"] = acceptance(
            stories,
            replay,
            yaml.safe_load(args.labels.read_text()),
            yaml.safe_load(args.prior.read_text()),
            args.min_precision_gain,
        )
    args.out.write_text(json.dumps(replay, indent=2) + "\n")


if __name__ == "__main__":
    main()
