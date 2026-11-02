"""Explicit-ID archive repair: read-only plan, guarded atomic apply/rollback.

Never called by ingest, legacy backfill or deployment. Plans contain private
before/after values and must remain outside the public repository/site.
"""

import argparse
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from ma_signal_monitor.classify import classify_item
from ma_signal_monitor.config import AppConfig, load_config
from ma_signal_monitor.drafting import draft_alert
from ma_signal_monitor.eligibility import eligible_for
from ma_signal_monitor.geo import detect_states
from ma_signal_monitor.models import NormalizedItem
from ma_signal_monitor.scoring import score_item

DERIVED_FIELDS = (
    "relevance_score",
    "primary_category",
    "categories",
    "entities",
    "states",
    "public_draft",
    "scoring_breakdown",
    "threshold_at_score",
    "eligibility_tier",
    "eligibility_reason",
)
SOURCE_FIELDS = {
    "item_id",
    "title",
    "link",
    "source_name",
    "source_priority",
    "summary",
    "published_date",
    "fetched_at",
    "duplicate_of",
}
MAX_ITEMS = 100


def _hash(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _code_hash() -> str:
    root = Path(__file__).parent
    names = (
        "archive_repair.py",
        "scoring.py",
        "classify.py",
        "drafting.py",
        "eligibility.py",
        "geo.py",
        "payers.py",
        "models.py",
        "config.py",
    )
    return _hash(
        {n: hashlib.sha256((root / n).read_bytes()).hexdigest() for n in names}
    )


def _config_hash(config: AppConfig) -> str:
    # Only effective derivation inputs; never export credentials or private paths.
    names = (
        "categories",
        "watched_entities",
        "ma_context_terms",
        "ma_boost_terms",
        "scoring",
        "exclusions_hard",
        "exclusions_soft",
        "ma_eligibility_gate",
        "eligibility_ma_specific",
        "eligibility_medicare_adjacent",
        "eligibility_negative_context",
        "min_relevance_score",
    )
    data = asdict(config)
    return _hash({name: data[name] for name in names})


def _connect(path: str | Path, *, writable=False):
    mode = "rw" if writable else "ro"
    conn = sqlite3.connect(Path(path).resolve().as_uri() + f"?mode={mode}", uri=True)
    conn.row_factory = sqlite3.Row
    if not writable:
        conn.execute("PRAGMA query_only=ON")
    return conn


def _ids(item_ids) -> list[str]:
    ids = list(item_ids)
    if not 1 <= len(ids) <= MAX_ITEMS or any(
        not isinstance(i, str) or not i for i in ids
    ):
        raise ValueError("Supply 1 to 100 explicit, nonempty story IDs")
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate story IDs are not allowed")
    return sorted(ids)


def _rows(conn, ids):
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(stories)")}
    if not SOURCE_FIELDS.union(DERIVED_FIELDS) <= columns:
        raise ValueError(
            "Archive schema is incomplete; repair never creates or migrates it"
        )
    rows = {}
    for item_id in ids:
        row = conn.execute(
            "SELECT * FROM stories WHERE item_id = ?", (item_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Missing story ID: {item_id}")
        rows[item_id] = dict(row)
    return rows


def _source_hash(row):
    return _hash({k: v for k, v in row.items() if k not in DERIVED_FIELDS})


def _derived(row):
    return {k: row[k] for k in DERIVED_FIELDS}


def _rebuild(row, config):
    # These stored inputs are exactly those used by the current derivation code.
    # Tags/type/author are not persisted and are not consumed by these paths.
    if not row["fetched_at"] or not isinstance(row["source_priority"], int):
        raise ValueError("Stored fetch date and integer source priority are required")
    item = NormalizedItem(
        item_id=row["item_id"],
        source_name=row["source_name"],
        source_type="archive",
        source_priority=row["source_priority"],
        source_tags=[],
        title=row["title"],
        link=row["link"],
        summary=row["summary"] or "",
        published_date=datetime.fromisoformat(row["published_date"])
        if row["published_date"]
        else None,
        fetched_at=datetime.fromisoformat(row["fetched_at"]),
    )
    scored = score_item(item, config)
    selected = scored.relevance_score >= config.min_relevance_score and (
        not config.ma_eligibility_gate
        or eligible_for(scored.eligibility_tier or "", "alert")
    )
    # Unlike draft_alerts, do not swallow a drafting failure and clear old text.
    draft = asdict(draft_alert(scored, config).public_draft) if selected else None
    return dict(
        relevance_score=scored.relevance_score,
        primary_category=classify_item(scored, config),
        categories=json.dumps(scored.matched_categories),
        entities=json.dumps(scored.matched_entities),
        states=json.dumps(detect_states(scored)),
        public_draft=json.dumps(draft) if draft else None,
        scoring_breakdown=json.dumps([asdict(r) for r in scored.reasons]),
        threshold_at_score=config.min_relevance_score,
        eligibility_tier=scored.eligibility_tier,
        eligibility_reason=scored.eligibility_reason,
    )


def plan_repair(db_path, config, item_ids) -> dict:
    """Return reproducible before/after evidence without opening a writable DB."""
    ids = _ids(item_ids)
    with closing(_connect(db_path)) as conn:
        conn.execute("BEGIN")
        rows = _rows(conn, ids)
        plan = dict(
            version=1,
            code_sha256=_code_hash(),
            config_sha256=_config_hash(config),
            items=[
                dict(
                    item_id=i,
                    source_sha256=_source_hash(rows[i]),
                    before=_derived(rows[i]),
                    after=_rebuild(rows[i], config),
                )
                for i in ids
            ],
        )
    plan["plan_sha256"] = _hash(plan)
    return plan


def _validate_plan(plan, expected_sha):
    if set(plan) != {"version", "code_sha256", "config_sha256", "items", "plan_sha256"}:
        raise ValueError("Invalid plan fields")
    payload = {k: v for k, v in plan.items() if k != "plan_sha256"}
    if (
        plan["version"] != 1
        or _hash(payload) != expected_sha
        or plan["plan_sha256"] != expected_sha
    ):
        raise ValueError("Plan hash/version does not match the reviewed plan")
    _ids([entry["item_id"] for entry in plan["items"]])
    for entry in plan["items"]:
        if set(entry) != {"item_id", "source_sha256", "before", "after"}:
            raise ValueError("Invalid plan entry")
        for side in ("before", "after"):
            if set(entry[side]) != set(DERIVED_FIELDS):
                raise ValueError("Plan may update only the fixed derived fields")


def apply_repair(db_path, plan, expected_sha, config=None, *, rollback=False) -> dict:
    """Atomically apply a reviewed plan, or restore its guarded before-images.

    Same-plan retries are no-ops. Changes to source fields, current derived
    values, code or effective configuration require a new review. Rollback uses
    the recorded post-image and does not recompute historical values.
    """
    _validate_plan(plan, expected_sha)
    if not rollback and (
        config is None
        or _config_hash(config) != plan["config_sha256"]
        or _code_hash() != plan["code_sha256"]
    ):
        raise ValueError("Code/configuration changed; generate and review a new plan")
    with closing(_connect(db_path, writable=True)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            rows = _rows(conn, [e["item_id"] for e in plan["items"]])
            updates = []
            for entry in plan["items"]:
                row = rows[entry["item_id"]]
                if _source_hash(row) != entry["source_sha256"]:
                    raise ValueError(f"Source/identity changed for {entry['item_id']}")
                current = _derived(row)
                old, new = (
                    (entry["after"], entry["before"])
                    if rollback
                    else (entry["before"], entry["after"])
                )
                if current != old and current != new:
                    raise ValueError(f"Derived values changed for {entry['item_id']}")
                if not rollback and _rebuild(row, config) != entry["after"]:
                    raise ValueError("Recomputed result differs from reviewed plan")
                if current != new:
                    updates.append((entry["item_id"], new))
            # Every row and recomputed value is validated before the first write.
            sql = (
                "UPDATE stories SET "
                + ", ".join(f"{k} = ?" for k in DERIVED_FIELDS)
                + " WHERE item_id = ?"
            )
            for item_id, values in updates:
                conn.execute(sql, (*[values[k] for k in DERIVED_FIELDS], item_id))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {
        "operation": "rollback" if rollback else "apply",
        "plan_sha256": expected_sha,
        "selected": len(plan["items"]),
        "changed": len(updates),
        "unchanged": len(plan["items"]) - len(updates),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("plan", "apply", "rollback"))
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--item-id", action="append")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--expected-sha")
    args = parser.parse_args(argv)
    if args.operation == "plan":
        if not args.item_id or args.expected_sha:
            parser.error("plan requires --item-id and does not accept --expected-sha")
        config = load_config(args.root, include_discovered_sources=False)
        plan = plan_repair(args.db, config, args.item_id)
        fd = os.open(args.plan, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2, allow_nan=False)
            f.write("\n")
        print(
            json.dumps(
                {
                    "plan_sha256": plan["plan_sha256"],
                    "selected": len(plan["items"]),
                    "changed": sum(e["before"] != e["after"] for e in plan["items"]),
                }
            )
        )
    else:
        if args.item_id or not args.expected_sha:
            parser.error(
                "apply/rollback require --expected-sha and do not accept --item-id"
            )
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        config = (
            load_config(args.root, include_discovered_sources=False)
            if args.operation == "apply"
            else None
        )
        print(
            json.dumps(
                apply_repair(
                    args.db,
                    plan,
                    args.expected_sha,
                    config,
                    rollback=args.operation == "rollback",
                )
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
