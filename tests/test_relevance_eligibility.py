"""Unit tests for the candidate MA-eligibility gate (evals/relevance/eligibility.py).

These pin the tier decisions on the real Aug-14 2026 briefing items (the
motivating false positives) plus recall-safety cases, so the experiment's logic
is regression-guarded independently of the held-out label set. Offline and
deterministic; the module is not wired into production.
"""

from ma_signal_monitor import payers

from evals.relevance.eligibility import (
    classify_eligibility,
    eligible_for,
    entity_group_delta,
)


def tier(title, summary="", entities=None):
    return classify_eligibility(title, summary, entities or []).tier


# --- The five Aug-14 false positives must not reach the briefing bar ----------


def test_medicaid_only_excluded():
    assert (
        tier(
            "'Private option' Medicaid expansion under threat from CMS",
            "Arkansas Medicaid coverage for hundreds of thousands is uncertain.",
        )
        == "exclude"
    )


def test_general_cms_rule_excluded():
    assert (
        tier(
            "How will legal challenges fare against CMS rule on gender-affirming care?",
            "A CMS rule finalized this week may prove difficult to challenge.",
        )
        == "exclude"
    )


def test_payer_criminal_case_excluded():
    # Two payer aliases present, but a criminal-context story is not an MA signal.
    assert (
        tier(
            "Luigi Mangione pleads guilty in the killing of UnitedHealthcare CEO",
            "Mangione admitted to following the UnitedHealth executive.",
            entities=["UnitedHealthcare", "UnitedHealth"],
        )
        == "exclude"
    )


def test_securities_suit_excluded():
    assert (
        tier(
            "Investors renew fight against UnitedHealth, claiming $237M in insider stock sales",
            "UnitedHealth Group shareholders renew pressure in an amended complaint.",
            entities=["UnitedHealthcare", "UnitedHealth"],
        )
        == "exclude"
    )


def test_commercial_aca_excluded():
    assert (
        tier(
            "Cigna, UnitedHealthcare pitch 'level-funded' plans to employers",
            "The insurers are courting the employer market.",
            entities=["Cigna", "UnitedHealthcare"],
        )
        == "exclude"
    )


# --- Genuine MA signals must reach the briefing bar ---------------------------


def test_ma_specific_reaches_brief():
    assert (
        tier(
            "A look at where Aetna is seeing value-based care success in Medicare Advantage",
            "Aetna's value-based efforts pay off.",
            entities=["Aetna"],
        )
        == "brief"
    )


def test_ma_specific_rescues_medicaid_mention():
    # A real MA story that merely mentions Medicaid is NOT excluded.
    assert (
        tier(
            "Humana grows Medicare Advantage D-SNP membership among dual eligible members",
            "Growth spans Medicaid dual-eligible special needs plans.",
            entities=["Humana"],
        )
        == "brief"
    )


# --- The owner's "display-only, not briefed" disposition -----------------------


def test_medicare_adjacent_is_display_only():
    e = classify_eligibility(
        "CMS launches the Medicare GLP-1 Bridge",
        "A new Medicare program for GLP-1 access.",
        [],
    )
    assert e.tier == "display"
    assert not eligible_for(e.tier, "brief")
    assert not eligible_for(e.tier, "alert")
    assert eligible_for(e.tier, "display")


def test_payer_plus_medicare_context_is_alert_not_brief():
    e = classify_eligibility(
        "UnitedHealthcare updates Medicare provider directory",
        "Changes to the Medicare network.",
        ["UnitedHealthcare"],
    )
    assert e.tier == "alert"
    assert eligible_for(e.tier, "alert")
    assert not eligible_for(e.tier, "brief")


def test_no_medicare_context_excluded():
    assert tier("Local hospital opens new wing", "A ribbon cutting.") == "exclude"


# --- The entity double-count fix ----------------------------------------------


def test_entity_group_dedup_collapses_aliases():
    # Two aliases of one payer group -> one boost, not two (delta = -0.20).
    assert (
        entity_group_delta(["UnitedHealthcare", "UnitedHealth"], payers.ALIAS_TO_GROUP)
        == -0.20
    )


def test_entity_group_dedup_keeps_two_distinct_payers():
    assert (
        entity_group_delta(["UnitedHealthcare", "Humana"], payers.ALIAS_TO_GROUP) == 0.0
    )


def test_tier_ordering():
    assert eligible_for("brief", "alert")
    assert eligible_for("alert", "display")
    assert not eligible_for("display", "brief")
    assert not eligible_for("exclude", "display")
