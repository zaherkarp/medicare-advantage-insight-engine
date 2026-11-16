"""Tests for the news-track snapshot layer (research/news/snapshot.py).

Offline: ``fetch`` is driven through an injected opener, and the committed
snapshot is checked from disk. Nothing here touches the network.
"""

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ma_signal_monitor.research.news import snapshot as snap

COMMITTED = Path(__file__).resolve().parents[2] / "evals" / "news" / "snapshot"

TITLE = "Aetna sued over alleged denials of preauthorizations"


def _row(
    item_id="0123456789abcdef", title=TITLE, summary="Body text.", date="2026-11-01"
):
    return {
        "title": title,
        "summary": summary,
        "source": "Becker's Payer Issues",
        "category": "uncategorized",
        "url": f"/story/{item_id}.html",
        "date": date,
    }


# --- strip_syndication ----------------------------------------------------


def test_full_tail_is_removed_with_its_bracket_ellipsis():
    s = (
        "Body text ends here […] The post "
        f"{TITLE} appeared first on Becker's Payer Issues | Payer News ."
    )
    assert snap.strip_syndication(TITLE, s) == ("Body text ends here", "full")


def test_tail_cut_inside_the_phrase_is_removed():
    s = f"Body […] The post {TITLE} appeared fir..."
    assert snap.strip_syndication(TITLE, s) == ("Body", "truncated")


def test_tail_cut_inside_the_title_is_removed():
    s = f"Body […] The post {TITLE[:20]}..."
    assert snap.strip_syndication(TITLE, s) == ("Body", "truncated")


def test_tail_cut_after_the_phrase_counts_as_full():
    s = f"Body […] The post {TITLE} appeared first on Becker's Payer I..."
    assert snap.strip_syndication(TITLE, s) == ("Body", "full")


def test_prose_mentioning_the_post_is_untouched():
    s = "The post office in Toledo closed after the merger."
    assert snap.strip_syndication(TITLE, s) == (s, None)


def test_tail_naming_a_different_title_is_untouched():
    s = "Body […] The post Something else entirely..."
    assert snap.strip_syndication(TITLE, s) == (s, None)


def test_summary_without_tail_is_untouched():
    assert snap.strip_syndication(TITLE, "Plain summary.") == ("Plain summary.", None)


# --- clean_row / story_id -------------------------------------------------


def test_story_id_comes_from_the_url():
    assert snap.story_id("/story/0123456789abcdef.html") == "0123456789abcdef"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "/story/0123.html",
        "https://x/story/0123456789abcdef.html",
        "/s/0123456789abcdef.html",
    ],
)
def test_malformed_story_url_is_an_error(url):
    with pytest.raises(snap.SnapshotError):
        snap.story_id(url)


def test_empty_date_stays_empty():
    assert snap.clean_row(_row(date="")).date == ""


def test_clean_row_keeps_other_fields_verbatim():
    story = snap.clean_row(_row(summary="  Body  "))
    assert story.summary == "  Body  "
    assert story.source == "Becker's Payer Issues"
    assert story.syndication is None


# --- fetch / load / verify --------------------------------------------------


def _fetch(tmp_path, rows):
    data = json.dumps(rows).encode("utf-8")
    return snap.fetch(
        tmp_path,
        "https://example.invalid/search-index.json",
        deploy_commit="abc123",
        now=datetime(2026, 11, 16, 19, 46, 15, tzinfo=timezone.utc),
        opener=lambda url: data,
    ), data


def test_fetch_writes_raw_bytes_and_manifest(tmp_path):
    rows = [_row(), _row(item_id="fedcba9876543210", date="")]
    manifest, data = _fetch(tmp_path, rows)
    assert (tmp_path / "search-index-2026-11-16.json").read_bytes() == data
    on_disk = json.loads((tmp_path / "manifest.json").read_text())
    assert on_disk == manifest
    assert manifest["fetched_at"] == "2026-11-16T19:46:15Z"
    assert manifest["rows"] == 2
    assert manifest["deploy_commit"] == "abc123"
    assert manifest["cleaning"]["empty_date"] == 1
    assert snap.verify(tmp_path) == []


def test_fetch_refuses_duplicate_ids(tmp_path):
    with pytest.raises(snap.SnapshotError):
        _fetch(tmp_path, [_row(), _row()])


def test_load_detects_tampered_bytes(tmp_path):
    _fetch(tmp_path, [_row()])
    raw = tmp_path / "search-index-2026-11-16.json"
    raw.write_bytes(raw.read_bytes().replace(b"Body", b"Bodx"))
    with pytest.raises(snap.SnapshotError, match="sha256"):
        snap.load_corpus(tmp_path)
    assert snap.verify(tmp_path) != []


def test_verify_detects_edited_cleaning_summary(tmp_path):
    _fetch(tmp_path, [_row()])
    path = tmp_path / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["cleaning"]["empty_date"] = 99
    path.write_text(json.dumps(manifest))
    assert snap.verify(tmp_path) != []


def test_manifest_is_deterministic(tmp_path):
    _fetch(tmp_path / "a", [_row()])
    _fetch(tmp_path / "b", [_row()])
    assert (tmp_path / "a" / "manifest.json").read_bytes() == (
        tmp_path / "b" / "manifest.json"
    ).read_bytes()


# --- the committed snapshot -------------------------------------------------


def test_committed_snapshot_verifies():
    assert snap.verify(COMMITTED) == []


def test_committed_snapshot_has_no_residual_syndication():
    stories = snap.load_corpus(COMMITTED)
    assert stories
    assert not [s.item_id for s in stories if "appeared first on" in s.summary]


def test_committed_snapshot_is_not_modified_by_loading(tmp_path):
    copy = tmp_path / "snap"
    shutil.copytree(COMMITTED, copy)
    before = {p.name: p.read_bytes() for p in copy.iterdir()}
    snap.load_corpus(copy)
    snap.verify(copy)
    assert {p.name: p.read_bytes() for p in copy.iterdir()} == before
