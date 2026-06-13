"""Tests for giscus → feedback ingest."""

import json
from datetime import datetime

import pytest
import responses

from ma_signal_monitor.feedback_ingest import (
    GITHUB_GRAPHQL_URL,
    ingest_github_feedback,
    ingest_ntfy_feedback,
)
from ma_signal_monitor.models import NormalizedItem, ScoredItem


def _seed(store, item_id, category="policy_regulatory"):
    item = NormalizedItem(
        item_id=item_id,
        source_name="Test Feed",
        source_type="rss",
        source_priority=3,
        source_tags=["test"],
        title=f"Story {item_id}",
        link=f"https://example.com/{item_id}",
        published_date=datetime(2024, 1, 1, 12, 0),
        summary="summary",
    )
    store.upsert_story(
        ScoredItem(item=item, relevance_score=0.7, matched_categories=[category]),
        primary_category=category,
    )


def _giscus_config(sample_config):
    sample_config.giscus_repo = "owner/repo"
    sample_config.giscus_repo_id = "R_kgABC"
    sample_config.giscus_category_id = "DIC_kwABC"
    return sample_config


def _one_page(nodes):
    return {
        "data": {
            "repository": {
                "discussions": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": nodes,
                }
            }
        }
    }


def test_requires_config(sample_config, temp_db):
    with pytest.raises(ValueError):
        ingest_github_feedback(sample_config, temp_db, token="t")


def test_requires_token(sample_config, temp_db, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(ValueError):
        ingest_github_feedback(_giscus_config(sample_config), temp_db)


@responses.activate
def test_maps_reactions_to_verdicts(sample_config, temp_db):
    _seed(temp_db, "story-1")
    responses.add(
        responses.POST,
        GITHUB_GRAPHQL_URL,
        json=_one_page(
            [
                {
                    "title": "story-1",
                    "reactions": {
                        "nodes": [
                            {
                                "content": "THUMBS_UP",
                                "databaseId": 1,
                                "user": {"login": "alice"},
                            },
                            {
                                "content": "THUMBS_DOWN",
                                "databaseId": 2,
                                "user": {"login": "bob"},
                            },
                            {
                                "content": "CONFUSED",
                                "databaseId": 3,
                                "user": {"login": "cara"},
                            },
                            {
                                "content": "EYES",
                                "databaseId": 4,
                                "user": {"login": "dan"},
                            },
                        ]
                    },
                },
                # A discussion that doesn't map to a known story is skipped.
                {
                    "title": "not-a-story",
                    "reactions": {
                        "nodes": [
                            {
                                "content": "THUMBS_UP",
                                "databaseId": 9,
                                "user": {"login": "eve"},
                            }
                        ]
                    },
                },
            ]
        ),
        status=200,
    )

    summary = ingest_github_feedback(_giscus_config(sample_config), temp_db, token="t")

    # EYES is ignored; the unknown-story reaction is skipped.
    assert summary["recorded"] == 3
    counts = temp_db.get_feedback_summary("story-1")["counts"]
    assert counts == {"relevant": 1, "irrelevant": 1, "wrong_category": 1}
    # Crowd votes do not set the owner verdict.
    assert temp_db.get_feedback_summary("story-1")["my_verdict"] is None


@responses.activate
def test_ingest_is_idempotent(sample_config, temp_db):
    _seed(temp_db, "story-1")
    responses.add(
        responses.POST,
        GITHUB_GRAPHQL_URL,
        json=_one_page(
            [
                {
                    "title": "story-1",
                    "reactions": {
                        "nodes": [
                            {
                                "content": "THUMBS_UP",
                                "databaseId": 1,
                                "user": {"login": "alice"},
                            }
                        ]
                    },
                }
            ]
        ),
        status=200,
    )

    first = ingest_github_feedback(_giscus_config(sample_config), temp_db, token="t")
    second = ingest_github_feedback(sample_config, temp_db, token="t")
    assert first["recorded"] == 1
    assert second["recorded"] == 0
    assert temp_db.count_feedback() == 1


# --- ntfy ingest ---

NTFY_JSON_URL = "https://ntfy.sh/fb-topic/json"


def _ntfy_config(sample_config):
    sample_config.ntfy_feedback_topic = "fb-topic"
    sample_config.ntfy_server = "https://ntfy.sh"
    return sample_config


def _ndjson(*objs):
    return "\n".join(json.dumps(o) for o in objs)


def test_ntfy_requires_config(sample_config, temp_db):
    with pytest.raises(ValueError):
        ingest_ntfy_feedback(sample_config, temp_db)


@responses.activate
def test_ntfy_maps_votes(sample_config, temp_db):
    _seed(temp_db, "story-1")
    responses.add(
        responses.GET,
        NTFY_JSON_URL,
        body=_ndjson(
            {"id": "m1", "event": "open"},  # non-message, ignored
            {
                "id": "m2",
                "event": "message",
                "message": json.dumps({"item_id": "story-1", "verdict": "relevant"}),
            },
            {
                "id": "m3",
                "event": "message",
                "message": json.dumps({"item_id": "ghost", "verdict": "relevant"}),
            },
            {"id": "m4", "event": "message", "message": "not json"},
        ),
        status=200,
    )

    summary = ingest_ntfy_feedback(_ntfy_config(sample_config), temp_db)

    assert summary["recorded"] == 1
    s = temp_db.get_feedback_summary("story-1")
    # ntfy is an owner channel → weight 1.0 → sets my_verdict.
    assert s["my_verdict"] == "relevant"


@responses.activate
def test_ntfy_idempotent(sample_config, temp_db):
    _seed(temp_db, "story-1")
    responses.add(
        responses.GET,
        NTFY_JSON_URL,
        body=_ndjson(
            {
                "id": "m2",
                "event": "message",
                "message": json.dumps({"item_id": "story-1", "verdict": "relevant"}),
            }
        ),
        status=200,
    )

    first = ingest_ntfy_feedback(_ntfy_config(sample_config), temp_db)
    second = ingest_ntfy_feedback(sample_config, temp_db)
    assert first["recorded"] == 1
    assert second["recorded"] == 0
    assert temp_db.count_feedback() == 1
