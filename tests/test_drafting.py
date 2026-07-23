"""Tests for alert drafting — locks the fact-derived, no-hype voice.

This is the first direct coverage of ``drafting.py``; the BANNED set is the
durable guard against a regression back to the old templated "mad-libs".
"""

from datetime import datetime

from ma_signal_monitor.drafting import draft_alert
from ma_signal_monitor.models import NormalizedItem, ScoredItem, ScoringReason

BANNED = (
    "evolving dynamics",
    "merit careful tracking",
    "continues to evolve",
    "amplify the significance",
    "warrants attention",
    "suggesting broader market implications",
    "annual bid cycle",
)


def _scored(
    *,
    entities=None,
    categories=None,
    title="UnitedHealthcare grows MA enrollment in California",
):
    item = NormalizedItem(
        item_id="t1",
        source_name="Healthcare Dive",
        source_type="rss",
        source_priority=3,
        source_tags=["industry"],
        title=title,
        link="https://example.com/t1",
        published_date=datetime(2024, 11, 1, 12, 0),
        summary=(
            "UnitedHealthcare announced expansion of Medicare Advantage service "
            "area enrollment to 15 new counties."
        ),
    )
    return ScoredItem(
        item=item,
        relevance_score=0.65,
        reasons=[ScoringReason("title_keyword", "'enrollment' in title", 0.225)],
        matched_categories=categories or ["membership_movement"],
        matched_entities=(["UnitedHealthcare"] if entities is None else entities),
    )


def _all_prose(alert) -> str:
    return " ".join(
        [
            alert.public_draft.opening_hook,
            alert.public_draft.draft_paragraph,
            alert.internal.why_it_matters,
            *alert.public_draft.analytic_angles,
        ]
    )


def test_opening_hook_leads_with_facts(sample_config):
    alert = draft_alert(_scored(), sample_config)
    hook = alert.public_draft.opening_hook
    assert "UnitedHealthcare" in hook
    assert "Healthcare Dive" in hook


def test_draft_paragraph_marked_and_sourced(sample_config):
    para = draft_alert(_scored(), sample_config).public_draft.draft_paragraph
    assert para.startswith("[DRAFT")
    assert "15 new counties" in para  # grounded in the real summary


def test_analytic_angles_are_concrete_checks(sample_config):
    angles = draft_alert(_scored(), sample_config).public_draft.analytic_angles
    assert len(angles) >= 2
    assert any("CMS monthly enrollment" in a for a in angles)


def test_why_it_matters_names_the_payer(sample_config):
    why = draft_alert(_scored(), sample_config).internal.why_it_matters
    assert "UnitedHealthcare" in why


def test_no_entity_falls_back_to_title(sample_config):
    hook = draft_alert(_scored(entities=[]), sample_config).public_draft.opening_hook
    assert "Membership Movement" in hook  # category-led fallback
    assert '"' in hook  # quotes the headline


def test_structure_and_disclaimers_preserved(sample_config):
    draft = draft_alert(_scored(), sample_config).public_draft
    assert draft.uncertainty_caution  # disclaimer kept
    assert "#MedicareAdvantage" in draft.suggested_hashtags  # per-category tags kept


def test_multi_category_is_reported_not_hyped(sample_config):
    alert = draft_alert(
        _scored(categories=["membership_movement", "policy_regulatory"]),
        sample_config,
    )
    assert "categories" in alert.internal.why_it_matters
    assert "broader market implications" not in _all_prose(alert)


def test_no_madlibs_across_categories(sample_config):
    for cat in [
        "membership_movement",
        "policy_regulatory",
        "financial_pressure",
        "competitive_strategy",
    ]:
        blob = _all_prose(draft_alert(_scored(categories=[cat]), sample_config))
        for phrase in BANNED:
            assert phrase not in blob, f"{phrase!r} leaked for {cat}"
