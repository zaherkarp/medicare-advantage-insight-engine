"""Tests for the Daily Briefing synthesis lede."""

from datetime import datetime

from ma_signal_monitor.synthesis import build_lede

# Banned mad-libs phrases the synthesis must never emit (the durable regression
# lock for the "no hype" voice).
BANNED = (
    "evolving dynamics",
    "merit careful tracking",
    "continues to evolve",
    "amplify the significance",
    "warrants attention",
)

AEP = datetime(2024, 11, 1, 12, 0)  # inside the Annual Enrollment Period
SUMMER = datetime(2024, 7, 23, 12, 0)  # no active calendar window


def _facet(primary, *, entities=None, states=None):
    return {
        "primary_category": primary,
        "categories": [primary],
        "entities": entities or [],
        "states": states or [],
        "title": "headline",
        "relevance_score": 0.5,
    }


def test_empty_window_returns_none(sample_config):
    assert build_lede([], [], SUMMER, sample_config) is None


def test_momentum_up_and_breakdown(sample_config):
    current = [
        _facet("membership_movement"),
        _facet("membership_movement"),
        _facet("policy_regulatory"),
    ]
    previous = [_facet("membership_movement")]
    lede = build_lede(current, previous, AEP, sample_config)
    assert lede.momentum == "up"
    assert lede.total == 3 and lede.prev_total == 1
    assert "up from 1" in lede.summary
    assert "Membership Movement (2)" in lede.breakdown


def test_aep_frames_enrollment_as_seasonal(sample_config):
    current = [
        _facet("membership_movement", entities=["UnitedHealthcare"]) for _ in range(4)
    ]
    current.append(_facet("policy_regulatory"))
    lede = build_lede(current, [], AEP, sample_config)
    assert lede.season_note is not None
    assert "AEP" in lede.season_note
    assert "seasonal" in lede.season_note
    # Policy is not seasonal during AEP -> flagged as off-cycle.
    assert lede.offcycle_note is not None
    assert "off-cycle" in lede.offcycle_note.lower()


def test_offseason_points_to_next_milestone(sample_config):
    current = [_facet("financial_pressure"), _facet("policy_regulatory")]
    previous = [_facet("policy_regulatory") for _ in range(5)]
    lede = build_lede(current, previous, SUMMER, sample_config)
    assert lede.momentum == "down"
    assert lede.season_note is not None
    assert "off-cycle" in lede.season_note.lower()
    assert "Next milestone" in lede.season_note
    assert lede.offcycle_note is None  # no active window -> nothing to contrast


def test_single_story_new_momentum(sample_config):
    lede = build_lede([_facet("policy_regulatory")], [], SUMMER, sample_config)
    assert lede.momentum == "new"
    assert lede.total == 1
    assert "no comparable prior window" in lede.summary


def test_top_payers_named_in_summary(sample_config):
    current = [
        _facet("membership_movement", entities=["UnitedHealthcare"]),
        _facet("financial_pressure", entities=["Humana"]),
    ]
    lede = build_lede(current, [], AEP, sample_config)
    assert "Most named" in lede.summary


def test_no_madlibs_anywhere(sample_config):
    current = [
        _facet("membership_movement", entities=["Humana"]),
        _facet("policy_regulatory"),
    ]
    lede = build_lede(current, [_facet("policy_regulatory")], AEP, sample_config)
    blob = " ".join(
        filter(
            None, [lede.summary, lede.season_note, lede.offcycle_note, lede.breakdown]
        )
    )
    for phrase in BANNED:
        assert phrase not in blob
