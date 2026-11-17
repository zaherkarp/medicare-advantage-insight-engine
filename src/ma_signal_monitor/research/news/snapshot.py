"""Pinned snapshot of the public search index: fetch, clean, verify.

This is the corpus layer of the news retrieval track
(docs/research/01-news-track-protocol.md §3). It turns one fetch of
``search-index.json`` into two committed files under a snapshot directory:

``search-index-<date>.json``
    The bytes exactly as served. Never edited. Everything else is derived.
``manifest.json``
    Provenance (URL, fetch time, deploy commit) and identity (SHA-256, size,
    row count) of those bytes, plus what cleaning removed.

Cleaning happens on load, in code, and is never written back: the raw file is
the single source and ``load_corpus`` is the only reader. Nothing here touches
``state.db`` or any production module's behaviour.

No near-duplicate clustering is done here. The index is already one story per
production duplicate group: ``static_export._search_index`` reads
``StateStore.get_stories`` with its default ``include_duplicates=False``.
Re-clustering the index by title over the whole snapshot was tried and merged
distinct stories that share a title template (different payers' dividend
notices, different plaintiffs' suits against HHS), so residual duplicates are
instead handled per question at judgment time (protocol §3, §5).

Usage::

    python -m ma_signal_monitor.research.news.snapshot fetch --dir evals/news/<new-dir> \\
        --deploy-commit <sha> --deploy-run <url>
    python -m ma_signal_monitor.research.news.snapshot verify --dir evals/news/snapshot

``fetch`` is the only command that touches the network, and it is never run
by tests or CI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SNAPSHOT_URL = "https://signal.zaherkarp.com/search-index.json"
MANIFEST_NAME = "manifest.json"

# Story pages are /story/<item_id>.html, where item_id is the 16-hex-char
# sha256 prefix that normalize.py assigns. The id is the join key for every
# judgment, so a URL of any other shape is an error, not a skip.
_STORY_URL = re.compile(r"^/story/([0-9a-f]{16})\.html$")

# Syndication tails, e.g. Becker's:
#   "... […] The post <title> appeared first on Becker's Payer Issues | ..."
# The summary is cut at 500 characters by normalize.py, so the tail can stop
# anywhere: after "appeared first on", inside it, or inside the title itself.
_TAIL_START = "The post "
_TAIL_PHRASE = "appeared first on"
_TRAILING_ELLIPSIS = re.compile(r"(?:\.\.\.|…)$")
_TRAILING_BRACKET_ELLIPSIS = re.compile(r"\s*\[(?:…|\.\.\.)\]\s*$")


class SnapshotError(Exception):
    """The snapshot directory is missing, inconsistent, or does not verify."""


@dataclass(frozen=True)
class Story:
    """One cleaned story. ``summary`` has its syndication tail removed."""

    item_id: str
    title: str
    summary: str
    source: str
    category: str
    date: str  # as served: "YYYY-MM-DD" or "" -- never back-filled
    syndication: str | None  # "full", "truncated", or None if no tail


# --------------------------------------------------------------------------
# Cleaning
# --------------------------------------------------------------------------


def strip_syndication(title: str, summary: str) -> tuple[str, str | None]:
    """Remove a "The post <title> appeared first on <outlet>" tail.

    Returns ``(text, kind)``. ``kind`` is ``"full"`` when the tail contains the
    "appeared first on" phrase, ``"truncated"`` when the 500-character cut fell
    before that phrase but the tail is still a prefix of the expected
    ``"The post <title> appeared first on "``, and ``None`` when nothing was
    removed. Requiring the story's own title, or the phrase, is what keeps
    ordinary prose containing "The post" (an office, a job) untouched.
    """
    start = summary.rfind(_TAIL_START)
    if start < 0:
        return summary, None
    tail = summary[start:]
    core = _TRAILING_ELLIPSIS.sub("", tail).rstrip()
    expected = f"{_TAIL_START}{title} {_TAIL_PHRASE} "
    if _TAIL_PHRASE in core:
        kind = "full"
    elif core and expected.startswith(core):
        kind = "truncated"
    else:
        return summary, None
    head = _TRAILING_BRACKET_ELLIPSIS.sub("", summary[:start]).rstrip()
    return head, kind


def story_id(url: str) -> str:
    """The production ``item_id`` from a story URL; raises if malformed."""
    m = _STORY_URL.match(url)
    if not m:
        raise SnapshotError(f"unexpected story URL shape: {url!r}")
    return m.group(1)


def clean_row(row: dict) -> Story:
    """One raw index row to a :class:`Story`. Text fields are not otherwise altered."""
    title = row.get("title") or ""
    summary, kind = strip_syndication(title, row.get("summary") or "")
    return Story(
        item_id=story_id(row.get("url") or ""),
        title=title,
        summary=summary,
        source=row.get("source") or "",
        category=row.get("category") or "",
        date=row.get("date") or "",
        syndication=kind,
    )


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _dump(obj: dict) -> bytes:
    """Deterministic JSON: sorted keys, two-space indent, trailing newline."""
    return (
        json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def read_manifest(snapshot_dir: Path) -> dict:
    path = snapshot_dir / MANIFEST_NAME
    if not path.exists():
        raise SnapshotError(f"no manifest at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_raw(snapshot_dir: Path, manifest: dict | None = None) -> list[dict]:
    """The raw rows, after checking the bytes against the manifest's SHA-256."""
    manifest = manifest or read_manifest(snapshot_dir)
    data = (snapshot_dir / manifest["file"]).read_bytes()
    actual = _sha256(data)
    if actual != manifest["sha256"]:
        raise SnapshotError(
            f"{manifest['file']} sha256 {actual} does not match manifest "
            f"{manifest['sha256']}"
        )
    rows = json.loads(data)
    if len(rows) != manifest["rows"]:
        raise SnapshotError(f"{len(rows)} rows, manifest says {manifest['rows']}")
    return rows


def load_corpus(snapshot_dir: Path) -> list[Story]:
    """Verified, cleaned stories in served order."""
    stories = [clean_row(r) for r in load_raw(snapshot_dir)]
    ids = [s.item_id for s in stories]
    if len(set(ids)) != len(ids):
        raise SnapshotError("duplicate item_id in snapshot")
    return stories


def cleaning_summary(stories: list[Story]) -> dict:
    """What cleaning removed, as recorded in the manifest."""
    return {
        "syndication_full": sum(s.syndication == "full" for s in stories),
        "syndication_truncated": sum(s.syndication == "truncated" for s in stories),
        "empty_date": sum(s.date == "" for s in stories),
        "rule": "strip_syndication(); applied on load, raw file unchanged",
    }


def fetch(
    snapshot_dir: Path,
    url: str = SNAPSHOT_URL,
    *,
    deploy_commit: str | None = None,
    deploy_run: str | None = None,
    now: datetime | None = None,
    opener: Callable[[str], bytes] | None = None,
) -> dict:
    """Download the index once and write the raw file plus a fresh manifest.

    ``deploy_commit`` / ``deploy_run`` cannot be read from the index itself;
    the operator supplies them from the Deploy Static Site run that last
    completed before the fetch, and the manifest says so.

    Refuses a directory that already holds anything. A pinned snapshot is
    never replaced: overwriting both the raw file and the manifest would still
    verify cleanly while silently changing the corpus that judgments refer to.
    A new snapshot goes in a new directory.
    """
    if snapshot_dir.exists() and any(snapshot_dir.iterdir()):
        raise SnapshotError(
            f"{snapshot_dir} is not empty; a pinned snapshot is never "
            "overwritten. Fetch into a new directory."
        )
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    opener = opener or _http_get
    data = opener(url)
    rows = json.loads(data)
    if not isinstance(rows, list):
        raise SnapshotError("index is not a JSON list")
    stories = [clean_row(r) for r in rows]
    if len({x.item_id for x in stories}) != len(stories):
        raise SnapshotError("duplicate item_id in index")
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    name = f"search-index-{now.date().isoformat()}.json"
    (snapshot_dir / name).write_bytes(data)
    manifest = {
        "url": url,
        "fetched_at": now.isoformat().replace("+00:00", "Z"),
        "file": name,
        "sha256": _sha256(data),
        "bytes": len(data),
        "rows": len(rows),
        "deploy_commit": deploy_commit,
        "deploy_run": deploy_run,
        "deploy_note": (
            "Supplied by the operator: the last Deploy Static Site run that "
            "completed before fetched_at. Not readable from the index itself."
        ),
        "cleaning": cleaning_summary(stories),
    }
    (snapshot_dir / MANIFEST_NAME).write_bytes(_dump(manifest))
    return manifest


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ma-signal-research"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def verify(snapshot_dir: Path) -> list[str]:
    """Problems found; empty when the raw file matches its manifest and the
    recorded cleaning summary reproduces exactly."""
    try:
        manifest = read_manifest(snapshot_dir)
        stories = load_corpus(snapshot_dir)
    except (SnapshotError, OSError, KeyError, json.JSONDecodeError) as e:
        return [str(e)]
    if manifest.get("cleaning") != cleaning_summary(stories):
        return ["cleaning summary in manifest does not match a fresh load"]
    return []


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_fetch = sub.add_parser("fetch", help="download the live index (network)")
    p_fetch.add_argument("--dir", type=Path, required=True)
    p_fetch.add_argument("--url", default=SNAPSHOT_URL)
    p_fetch.add_argument("--deploy-commit")
    p_fetch.add_argument("--deploy-run")
    p_verify = sub.add_parser("verify", help="check a committed snapshot")
    p_verify.add_argument("--dir", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.cmd == "fetch":
        m = fetch(
            args.dir,
            args.url,
            deploy_commit=args.deploy_commit,
            deploy_run=args.deploy_run,
        )
        print(
            f"{m['file']}: {m['rows']} rows, {m['bytes']} bytes, sha256 {m['sha256']}"
        )
        return 0
    problems = verify(args.dir)
    for p in problems:
        print(f"FAIL: {p}")
    if not problems:
        print("snapshot verifies")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
