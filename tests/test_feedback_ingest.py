"""Tests for giscus → feedback ingest (owner and crowd reactions)."""

from datetime import datetime

import pytest
import responses

from ma_signal_monitor.feedback_ingest import (
    GITHUB_GRAPHQL_URL,
    ingest_github_feedback,
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


# --- owner vs crowd reactions ---


def _reaction_page(*reactions):
    """One discussion for story-1 carrying the given (content, id, login) reactions."""
    return _one_page(
        [
            {
                "title": "story-1",
                "reactions": {
                    "nodes": [
                        {"content": c, "databaseId": i, "user": {"login": login}}
                        for c, i, login in reactions
                    ]
                },
            }
        ]
    )


def _rows(store):
    conn = store._get_conn()
    return [
        dict(r)
        for r in conn.execute(
            "SELECT voter_key, channel, weight, verdict FROM feedback ORDER BY id"
        ).fetchall()
    ]


@responses.activate
def test_owner_reaction_is_owner_verdict(sample_config, temp_db):
    """The owner's own reaction records as giscus_owner at weight 1.0 and sets
    my_verdict; another login's records as crowd (github, 0.2) and does not."""
    _seed(temp_db, "story-1")
    responses.add(
        responses.POST,
        GITHUB_GRAPHQL_URL,
        json=_reaction_page(("THUMBS_UP", 1, "zaherkarp"), ("THUMBS_DOWN", 2, "alice")),
        status=200,
    )
    config = _giscus_config(sample_config)
    config.feedback_owner_login = "zaherkarp"

    summary = ingest_github_feedback(config, temp_db, token="t")

    assert summary["recorded"] == 2
    assert summary["recorded_owner"] == 1
    assert _rows(temp_db) == [
        {
            "voter_key": "zaherkarp",
            "channel": "giscus_owner",
            "weight": 1.0,
            "verdict": "relevant",
        },
        {
            "voter_key": "alice",
            "channel": "github",
            "weight": 0.2,
            "verdict": "irrelevant",
        },
    ]
    # Only the owner row counts as "my verdict".
    assert temp_db.get_feedback_summary("story-1")["my_verdict"] == "relevant"


@responses.activate
def test_owner_login_match_is_case_insensitive(sample_config, temp_db):
    _seed(temp_db, "story-1")
    responses.add(
        responses.POST,
        GITHUB_GRAPHQL_URL,
        json=_reaction_page(("HEART", 1, "ZaherKarp")),
        status=200,
    )
    config = _giscus_config(sample_config)
    config.feedback_owner_login = "zaherkarp"

    ingest_github_feedback(config, temp_db, token="t")

    assert _rows(temp_db)[0]["channel"] == "giscus_owner"


@responses.activate
def test_empty_owner_login_records_everything_as_crowd(sample_config, temp_db):
    """No owner configured: even the owner's account is crowd signal. This is
    the paired negative for the owner test above, so a channel that labelled
    every reaction owner could not pass both."""
    _seed(temp_db, "story-1")
    responses.add(
        responses.POST,
        GITHUB_GRAPHQL_URL,
        json=_reaction_page(("THUMBS_UP", 1, "zaherkarp"), ("THUMBS_UP", 2, "alice")),
        status=200,
    )
    config = _giscus_config(sample_config)
    config.feedback_owner_login = ""

    summary = ingest_github_feedback(config, temp_db, token="t")

    assert summary["recorded_owner"] == 0
    assert {r["channel"] for r in _rows(temp_db)} == {"github"}
    assert {r["weight"] for r in _rows(temp_db)} == {0.2}
    assert temp_db.get_feedback_summary("story-1")["my_verdict"] is None


@responses.activate
def test_owner_verdict_feeds_keyword_mining_labels(sample_config, temp_db):
    """giscus_owner rows are owner ground truth everywhere OWNER_CHANNELS is
    read, not just in my_verdict."""
    _seed(temp_db, "story-1")
    responses.add(
        responses.POST,
        GITHUB_GRAPHQL_URL,
        json=_reaction_page(("THUMBS_DOWN", 1, "zaherkarp")),
        status=200,
    )
    config = _giscus_config(sample_config)
    config.feedback_owner_login = "zaherkarp"

    ingest_github_feedback(config, temp_db, token="t")

    owner = temp_db.get_scored_owner_feedback()
    assert [(r["item_id"], r["verdict"]) for r in owner] == [("story-1", "irrelevant")]


@responses.activate
def test_reaction_already_filed_as_crowd_is_not_duplicated(sample_config, temp_db):
    """An owner reaction ingested as crowd before FEEDBACK_OWNER_LOGIN was set
    must not be inserted a second time as giscus_owner (the unique index is
    per channel, so it would otherwise allow both)."""
    _seed(temp_db, "story-1")
    temp_db.add_feedback(
        "story-1",
        "relevant",
        channel="github",
        voter_key="zaherkarp",
        source_ref="reaction:1",
    )
    responses.add(
        responses.POST,
        GITHUB_GRAPHQL_URL,
        json=_reaction_page(
            ("THUMBS_UP", 1, "zaherkarp"), ("THUMBS_UP", 2, "zaherkarp")
        ),
        status=200,
    )
    config = _giscus_config(sample_config)
    config.feedback_owner_login = "zaherkarp"

    summary = ingest_github_feedback(config, temp_db, token="t")

    # Reaction 1 stays its single crowd row; reaction 2 (new) is still recorded
    # as owner, which is the paired case proving the skip is not blanket.
    assert summary["recorded"] == 1
    assert summary["recorded_owner"] == 1
    assert temp_db.count_feedback() == 2
    assert sorted(r["channel"] for r in _rows(temp_db)) == ["giscus_owner", "github"]
