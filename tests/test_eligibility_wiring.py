"""The MA-eligibility gate wired behind the ``ma_eligibility_gate`` flag.

Covers both flag states:

* ON  — the five 2026-08-14 briefing false positives drop from the briefing,
        alert, and public-display surfaces; the brief-worthy signals still
        brief; the payer-alias score dedup applies; and a per-story eligibility
        reason is persisted for audit.
* OFF — byte-identical to the pre-gate pipeline (score unchanged, no tier, no
        gating anywhere).

The gate's own tier logic is unit-tested in ``test_relevance_eligibility.py``;
this module tests the *wiring* end to end against the real shipped taxonomy.
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from ma_signal_monitor.digest import build_digest
from ma_signal_monitor.drafting import draft_alerts
from ma_signal_monitor.config import load_config
from ma_signal_monitor.models import NormalizedItem, ScoredItem
from ma_signal_monitor.scoring import score_item
from ma_signal_monitor.storage import StateStore

_PROJECT_ROOT = Path(__file__).resolve().parents[1]

NOW = datetime(2026, 8, 14, 12, 0, 0)

# The five 2026-08-14 briefing false positives (archetypes from
# evals/relevance/README.md) with the payer aliases the scorer would match.
FALSE_POSITIVES = [
    (
        "'Private option' Medicaid expansion under threat from CMS",
        "Arkansas Medicaid coverage for hundreds of thousands is uncertain.",
        [],
    ),
    (
        "How will legal challenges fare against CMS rule on gender-affirming care?",
        "A CMS rule finalized this week may prove difficult to challenge.",
        [],
    ),
    (
        "Luigi Mangione pleads guilty in the killing of UnitedHealthcare CEO",
        "Mangione admitted to following the UnitedHealth executive.",
        ["UnitedHealthcare", "UnitedHealth"],
    ),
    (
        "Investors renew fight against UnitedHealth over $237M in insider stock sales",
        "UnitedHealth Group shareholders renew pressure in an amended complaint.",
        ["UnitedHealthcare", "UnitedHealth"],
    ),
    (
        "Cigna, UnitedHealthcare pitch 'level-funded' plans to employers",
        "The insurers are courting the employer commercial market.",
        ["Cigna", "UnitedHealthcare"],
    ),
]

# Genuine MA signals that must still reach the briefing bar.
BRIEF_WORTHY = [
    (
        "Aetna sees value-based care success in Medicare Advantage",
        "Aetna's value-based efforts pay off in MA.",
    ),
    (
        "Humana grows Medicare Advantage D-SNP membership among dual eligible members",
        "Growth spans dual-eligible special needs plans.",
    ),
]


@pytest.fixture
def real_config(monkeypatch):
    """The shipped config (real taxonomy + eligibility vocab). Flag off by default."""
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "ops@example.com")
    return load_config(_PROJECT_ROOT)


def _item(title: str, summary: str) -> NormalizedItem:
    return NormalizedItem(
        item_id=f"id-{hash((title, summary)) & 0xFFFFFFFF:x}",
        source_name="Test Feed",
        source_type="rss",
        source_priority=4,
        source_tags=[],
        title=title,
        link="https://example.com/x",
        published_date=NOW,
        summary=summary,
    )


# --------------------------------------------------------------------------
# Scoring: entity-group dedup + eligibility tier, gated by the flag
# --------------------------------------------------------------------------


def test_flag_off_leaves_scoring_untouched(real_config):
    real_config.ma_eligibility_gate = False
    item = _item(*FALSE_POSITIVES[2][:2])
    scored = score_item(item, real_config)
    # Both UHC-group aliases still counted (the +0.40 double-count is intact),
    # no dedup reason, and no eligibility tier computed.
    assert scored.matched_entities == ["UnitedHealthcare", "UnitedHealth"]
    assert scored.eligibility_tier is None
    assert scored.eligibility_reason is None
    assert not any(r.factor == "entity_group_dedup" for r in scored.reasons)


def test_flag_on_dedupes_same_group_aliases(real_config):
    item = _item(*FALSE_POSITIVES[2][:2])
    real_config.ma_eligibility_gate = False
    off = score_item(item, real_config)
    real_config.ma_eligibility_gate = True
    on = score_item(item, real_config)
    # One company matched under two aliases -> the boost collapses to one (-0.20).
    assert on.relevance_score == round(off.relevance_score - 0.20, 3)
    assert any(
        r.factor == "entity_group_dedup" and r.contribution == -0.20 for r in on.reasons
    )


def test_flag_on_keeps_two_distinct_payers(real_config):
    # Cigna + UnitedHealthcare are two distinct groups -> no dedup (delta 0).
    item = _item(*FALSE_POSITIVES[4][:2])
    real_config.ma_eligibility_gate = False
    off = score_item(item, real_config)
    real_config.ma_eligibility_gate = True
    on = score_item(item, real_config)
    assert on.relevance_score == off.relevance_score
    assert not any(r.factor == "entity_group_dedup" for r in on.reasons)


def test_flag_on_marks_five_fps_exclude(real_config):
    real_config.ma_eligibility_gate = True
    for title, summary, _entities in FALSE_POSITIVES:
        scored = score_item(_item(title, summary), real_config)
        assert scored.eligibility_tier == "exclude", title
        assert scored.eligibility_reason  # a defensible reason is attached


def test_flag_on_marks_brief_worthy_brief(real_config):
    real_config.ma_eligibility_gate = True
    for title, summary in BRIEF_WORTHY:
        scored = score_item(_item(title, summary), real_config)
        assert scored.eligibility_tier == "brief", title


# --------------------------------------------------------------------------
# Storage: the public display floor hides tier 'exclude' only when gated
# --------------------------------------------------------------------------


def _seed(
    store, item_id, *, tier, score=0.5, published=NOW, category="policy_regulatory"
):
    item = NormalizedItem(
        item_id=item_id,
        source_name="Test Feed",
        source_type="rss",
        source_priority=4,
        source_tags=[],
        title=f"Story {item_id}",
        link=f"https://example.com/{item_id}",
        published_date=published,
        summary="A summary.",
    )
    scored = ScoredItem(
        item=item,
        relevance_score=score,
        matched_categories=[category],
        eligibility_tier=tier,
        eligibility_reason=(f"test-reason:{tier}" if tier else None),
    )
    store.upsert_story(scored, primary_category=category)


def test_display_floor_off_shows_everything(tmp_path):
    store = StateStore(tmp_path / "off.db")  # gate off (default)
    _seed(store, "brief", tier="brief")
    _seed(store, "exclude", tier="exclude")
    _seed(store, "nulltier", tier=None)
    ids = {r["item_id"] for r in store.get_stories()}
    assert ids == {"brief", "exclude", "nulltier"}
    assert store.count_stories() == 3
    store.close()


def test_display_floor_on_hides_only_exclude(tmp_path):
    store = StateStore(tmp_path / "on.db", eligibility_gate=True)
    _seed(store, "brief", tier="brief")
    _seed(store, "alert", tier="alert")
    _seed(store, "display", tier="display")
    _seed(store, "exclude", tier="exclude")
    _seed(store, "nulltier", tier=None)  # scored before the gate ran
    ids = {r["item_id"] for r in store.get_stories()}
    # Only owner-designated noise (exclude) is hidden; NULL-tier legacy rows stay.
    assert ids == {"brief", "alert", "display", "nulltier"}
    assert store.count_stories() == 4
    # The operator's full-archive view (include_duplicates) is never gated.
    assert store.count_stories(include_duplicates=True) == 5
    store.close()


def test_display_floor_persists_and_surfaces_reason(tmp_path):
    store = StateStore(tmp_path / "audit.db", eligibility_gate=True)
    _seed(store, "exclude", tier="exclude")
    # Excluded stories stay in the archive (audit) and carry their reason, even
    # though they're hidden from the feed — get_story is never gated.
    row = store.get_story("exclude")
    assert row is not None
    assert row["eligibility_tier"] == "exclude"
    assert row["eligibility_reason"] == "test-reason:exclude"
    store.close()


# --------------------------------------------------------------------------
# Briefing: get_recent_top_stories requires tier=brief only when gated
# --------------------------------------------------------------------------


def test_briefing_off_is_score_gated(real_config, tmp_path):
    real_config.ma_eligibility_gate = False
    store = StateStore(tmp_path / "digest_off.db")
    _seed(store, "brief", tier="brief", score=0.5, published=NOW - timedelta(hours=1))
    _seed(
        store, "exclude", tier="exclude", score=0.6, published=NOW - timedelta(hours=2)
    )
    digest = build_digest(store, real_config, now=NOW)
    titles = {s.title for _label, stories in digest.sections for s in stories}
    assert titles == {"Story brief", "Story exclude"}  # both, as before
    store.close()


def test_briefing_on_requires_brief_tier(real_config, tmp_path):
    real_config.ma_eligibility_gate = True
    store = StateStore(tmp_path / "digest_on.db", eligibility_gate=True)
    # An exclude and an alert story both above the score floor, plus a brief one.
    _seed(store, "brief", tier="brief", score=0.5, published=NOW - timedelta(hours=1))
    _seed(store, "alert", tier="alert", score=0.6, published=NOW - timedelta(hours=2))
    _seed(
        store, "exclude", tier="exclude", score=0.7, published=NOW - timedelta(hours=3)
    )
    digest = build_digest(store, real_config, now=NOW)
    titles = {s.title for _label, stories in digest.sections for s in stories}
    assert titles == {"Story brief"}  # only tier=brief briefs
    store.close()


# --------------------------------------------------------------------------
# Alerts: draft_alerts requires tier>=alert only when gated
# --------------------------------------------------------------------------


def _scored_for_alert(item_id, *, tier, score=0.5):
    item = _item(f"Story {item_id}", "A summary.")
    item.item_id = item_id
    return ScoredItem(
        item=item,
        relevance_score=score,
        matched_categories=["policy_regulatory"],
        eligibility_tier=tier,
        eligibility_reason=(f"test:{tier}" if tier else None),
    )


def test_alerts_off_are_score_gated(real_config):
    real_config.ma_eligibility_gate = False
    scored = [
        _scored_for_alert("brief", tier="brief", score=0.5),
        _scored_for_alert("alert", tier="alert", score=0.5),
        _scored_for_alert("exclude", tier="exclude", score=0.6),
    ]
    alerts = draft_alerts(scored, real_config)
    assert len(alerts) == 3  # every >=0.3 item alerts, as before


def test_alerts_on_require_alert_tier(real_config):
    real_config.ma_eligibility_gate = True
    scored = [
        _scored_for_alert("brief", tier="brief", score=0.5),
        _scored_for_alert("alert", tier="alert", score=0.5),
        _scored_for_alert("display", tier="display", score=0.5),
        _scored_for_alert("exclude", tier="exclude", score=0.6),
    ]
    alerts = draft_alerts(scored, real_config)
    fired = {a.scored_item.item.item_id for a in alerts}
    # tier >= alert fires (brief, alert); display and exclude are held back even
    # though they clear the score threshold.
    assert fired == {"brief", "alert"}
