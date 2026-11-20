"""The MA-eligibility RESCUE path, behind the ``ma_eligibility_rescue`` flag.

Rescue is policy C in ``evals/relevance/backtest.py``: the score threshold,
plus any story the eligibility gate tiers ``brief`` even under the threshold.
It only adds. These tests pin both flag states on every surface it touches:

* scoring computes and stores a tier, but never changes the score;
* a public draft goes to a sub-threshold brief story, never to a sub-threshold
  story of any other tier, and never to fewer stories than the threshold alone;
* the briefing's candidate pool admits a sub-threshold brief story and drops
  nothing over the threshold;
* the story page does not publish the tier badge (that is a gate-audit surface).

Flag off is byte-identical to the threshold alone.
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ma_signal_monitor.config import load_config
from ma_signal_monitor.digest import build_digest
from ma_signal_monitor.drafting import clears_score_bar, draft_alerts
from ma_signal_monitor.models import NormalizedItem, ScoredItem
from ma_signal_monitor.scoring import score_item
from ma_signal_monitor.storage import StateStore

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 3, 12, 0, 0)

# A labeled rescue from holdout_c_adds_2026-11.yaml (owner: relevant_brief),
# phrased with the MA vocabulary that tiers it brief.
MA_TITLE = "Medicare Advantage plans owe Providence $1B, CEO says"
MA_SUMMARY = "The health system says Medicare Advantage insurers owe it $1 billion."


@pytest.fixture
def real_config(monkeypatch):
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "ops@example.com")
    return load_config(_PROJECT_ROOT)


def _item(item_id, title, summary="", priority=4):
    return NormalizedItem(
        item_id=item_id,
        source_name="Test Feed",
        source_type="rss",
        source_priority=priority,
        source_tags=[],
        title=title,
        link=f"https://example.com/{item_id}",
        published_date=NOW,
        summary=summary,
    )


def _scored(item_id, *, score, tier):
    return ScoredItem(
        item=_item(item_id, f"Story {item_id}", "A summary."),
        relevance_score=score,
        matched_categories=["policy_regulatory"],
        eligibility_tier=tier,
    )


# -- the shared predicate ---------------------------------------------------


@pytest.mark.parametrize(
    "score,tier,off,on",
    [
        (0.5, "exclude", True, True),  # over the threshold: always clears
        (0.5, None, True, True),
        (0.2, "brief", False, True),  # the rescue
        (0.2, "alert", False, False),  # only brief is rescued
        (0.2, "display", False, False),
        (0.2, None, False, False),
    ],
)
def test_clears_score_bar(real_config, score, tier, off, on):
    s = _scored("x", score=score, tier=tier)
    real_config.ma_eligibility_rescue = False
    assert clears_score_bar(s, real_config) is off
    real_config.ma_eligibility_rescue = True
    assert clears_score_bar(s, real_config) is on


# -- scoring -----------------------------------------------------------------


def test_scoring_tiers_without_changing_the_score(real_config):
    item = _item("p", MA_TITLE, MA_SUMMARY)
    off = score_item(item, real_config)
    assert off.eligibility_tier is None
    real_config.ma_eligibility_rescue = True
    on = score_item(item, real_config)
    assert on.eligibility_tier == "brief"
    assert on.relevance_score == off.relevance_score
    assert [r.factor for r in on.reasons] == [r.factor for r in off.reasons]


# -- public drafts ------------------------------------------------------------


def test_drafts_rescue_only_adds(real_config):
    scored = [
        _scored("over", score=0.5, tier="exclude"),
        _scored("brief-under", score=0.2, tier="brief"),
        _scored("alert-under", score=0.2, tier="alert"),
    ]
    real_config.ma_eligibility_rescue = False
    off = {a.scored_item.item.item_id for a in draft_alerts(scored, real_config)}
    assert off == {"over"}
    real_config.ma_eligibility_rescue = True
    on = {a.scored_item.item.item_id for a in draft_alerts(scored, real_config)}
    assert on == {"over", "brief-under"}


# -- briefing -----------------------------------------------------------------


def _seed(store, item_id, *, score, tier, hours_ago=1):
    item = _item(item_id, f"Story {item_id}", "A summary.")
    item.published_date = NOW - timedelta(hours=hours_ago)
    store.upsert_story(
        ScoredItem(
            item=item,
            relevance_score=score,
            matched_categories=["policy_regulatory"],
            eligibility_tier=tier,
        ),
        primary_category="policy_regulatory",
    )


def _briefed(store, config):
    digest = build_digest(store, config, now=NOW)
    return {s.title for _label, stories in digest.sections for s in stories}


def test_briefing_rescue_only_adds(real_config, tmp_path):
    for flag in (False, True):
        real_config.ma_eligibility_rescue = flag
        store = StateStore(tmp_path / f"b{flag}.db", eligibility_rescue=flag)
        _seed(store, "over", score=0.5, tier="exclude")
        _seed(store, "brief-under", score=0.2, tier="brief", hours_ago=2)
        _seed(store, "display-under", score=0.2, tier="display", hours_ago=3)
        got = _briefed(store, real_config)
        store.close()
        if flag:
            assert got == {"Story over", "Story brief-under"}
        else:
            assert got == {"Story over"}


def test_briefing_rescue_with_gate_still_requires_brief(real_config, tmp_path):
    # Both flags: the gate's brief bar still applies; rescue only widens the
    # score side. An over-threshold exclude story stays out.
    real_config.ma_eligibility_gate = True
    real_config.ma_eligibility_rescue = True
    store = StateStore(
        tmp_path / "both.db", eligibility_gate=True, eligibility_rescue=True
    )
    _seed(store, "over-exclude", score=0.6, tier="exclude")
    _seed(store, "brief-under", score=0.2, tier="brief", hours_ago=2)
    assert _briefed(store, real_config) == {"Story brief-under"}
    store.close()


# -- story page ----------------------------------------------------------------


def test_story_page_hides_tier_badge_unless_gate_on(real_config, tmp_path):
    from ma_signal_monitor.web.app import create_app

    store = StateStore(tmp_path / "web.db")
    _seed(store, "s1", score=0.2, tier="brief")
    real_config.ma_eligibility_rescue = True
    html = TestClient(create_app(real_config, store)).get("/story/s1").text
    assert "badge-eligibility" not in html
    real_config.ma_eligibility_gate = True
    html = TestClient(create_app(real_config, store)).get("/story/s1").text
    assert "badge-eligibility" in html
    store.close()


def test_rescue_defaults_off(real_config):
    assert real_config.ma_eligibility_rescue is False
