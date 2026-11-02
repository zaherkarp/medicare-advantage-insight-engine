"""SCAN identity regressions, separate from relevance/novelty judgments."""

import json
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from ma_signal_monitor.angles import build_angles
from ma_signal_monitor.config import load_config
from ma_signal_monitor.drafting import draft_alerts
from ma_signal_monitor.main import _persist_stories
from ma_signal_monitor.models import NormalizedItem
from ma_signal_monitor.scoring import _entity_in_text, score_item
from ma_signal_monitor.storage import StateStore
from ma_signal_monitor.web.routes import _facet_view, _story_view

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "text",
    [
        "SCAN Health Plan challenges CMS",
        "scan health plan challenges CMS",
        "SCAN Group announces expansion",
        "Scan Group announces expansion",
        "SCAN announces expansion",
        "SCAN sues CMS",
        "SCAN’s lawsuit challenges CMS",
        "The insurer SCAN challenges CMS",
        "California medical insurer SCAN Health Plan expands",
        "Medical payer SCAN announces expansion",
        "The medical insurer SCAN sues CMS",
        "SCAN files a lawsuit",
        "SCAN files an appeal",
        "A CT scan is discussed. SCAN Health Plan responds.",
    ],
)
def test_organization_mentions(text):
    assert _entity_in_text("SCAN", text)


@pytest.mark.parametrize(
    "text",
    [
        "Vera's platform scans clinical publications",
        "Researchers scan commercial directories",
        "Hospital adds CT scan service",
        "CT SCAN. Hospital announces new services.",
        "CT SCAN files are uploaded.",
        "CT SCAN group is compared with controls.",
        "SCAN files are uploaded.",
        "SCAN. Hospital announces new services.",
        "SCAN\nHospital announces new services.",
        "A brain scan group is compared with controls.",
        "SCANS announces a result",
        "scan announces findings",
        "Scanner announces findings",
    ],
)
def test_ordinary_and_imaging_mentions(text):
    assert not _entity_in_text("SCAN", text)


@pytest.fixture
def real_config(monkeypatch):
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "replay@example.invalid")
    monkeypatch.setenv("MA_ELIGIBILITY_GATE", "false")
    return load_config(ROOT)


def make_item(item_id, title, summary, priority=3):
    return NormalizedItem(
        item_id=item_id,
        source_name="Synthetic regression",
        source_type="fixture",
        source_priority=priority,
        source_tags=[],
        title=title,
        summary=summary,
        link=f"https://example.invalid/{item_id}",
        published_date=datetime(2026, 11, 2),
        fetched_at=datetime(2026, 11, 2),
    )


def test_no_context_or_boost_from_ordinary_scan(real_config):
    scored = score_item(
        make_item(
            "ordinary",
            "Researchers scan commercial directories",
            "The scan compares enrollment tools and generic insurer directories.",
        ),
        real_config,
    )
    assert scored.matched_entities == []
    assert scored.matched_categories == []
    assert any(r.factor == "ma_context_gate" for r in scored.reasons)
    assert not any(r.factor == "entity_match" for r in scored.reasons)
    assert draft_alerts([scored], real_config) == []


def test_title_does_not_borrow_summary_organization_context(real_config):
    scored = score_item(
        make_item("boundary", "SCAN", "Hospital announces new services."),
        real_config,
    )
    assert scored.matched_entities == []


@pytest.mark.parametrize("name", ["Humana", "UnitedHealthcare", "Aetna"])
def test_other_payer_identity_is_preserved(name, real_config):
    scored = score_item(
        make_item("payer", f"{name} announces enrollment growth", ""), real_config
    )
    assert name in scored.matched_entities
    assert not any(r.factor == "ma_context_gate" for r in scored.reasons)


def test_approved_scan_negative_satisfies_entity_exclusion(real_config):
    data = yaml.safe_load(
        (ROOT / "tests/fixtures/owner_development_2026_11.yaml").read_text()
    )
    row = next(c for c in data["cases"] if c["id"] == "86fe45ef857045ba")
    scored = score_item(
        make_item(row["id"], row["title"], row["summary"], row["source_priority"]),
        real_config,
    )
    assert set(row["expected"]["entity_exclusions"]).isdisjoint(scored.matched_entities)


def test_saved_facets_do_not_link_vendor_to_scan_lawsuit(tmp_path, real_config):
    # Synthetic paraphrases of the publicly observed failure pattern. These are
    # development controls, not exported private data or new owner labels.
    items = [
        make_item(
            "vendor",
            "Vera Health launches clinical search platform",
            "The platform scans publications and clinical guidelines.",
        ),
        make_item(
            "lawsuit",
            "SCAN Health Plan challenges CMS Star Ratings",
            "Medicare Advantage ratings litigation.",
        ),
    ]
    scored = [score_item(i, real_config) for i in items]
    with closing(StateStore(tmp_path / "state.db")) as store:
        _persist_stories(scored, draft_alerts(scored, real_config), real_config, store)
        vendor, lawsuit = (store.get_story(i.item_id) for i in items)
        assert json.loads(vendor["entities"]) == []
        assert _story_view(vendor)["public_draft"] is None
        assert "SCAN" in json.loads(lawsuit["entities"])
        # Include all archived scores to test the false association directly,
        # rather than relying solely on a low score hiding the vendor row.
        facets = [
            _facet_view(row)
            for row in store.get_recent_story_facets(datetime(2026, 11, 1), min_score=0)
        ]
        assert {f["item_id"] for f in facets} == {"vendor", "lawsuit"}
        cards = build_angles(facets, [], real_config)["angles"]
        assert not any(card["type"] == "payer_cascade" for card in cards)
