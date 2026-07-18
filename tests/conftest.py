"""Shared fixtures for MA Signal Monitor tests."""

from datetime import datetime
from pathlib import Path

import pytest
import yaml

from ma_signal_monitor.config import (
    AppConfig,
    CategoryConfig,
    CausalEdgeConfig,
    CausalLayerConfig,
    ScoringConfig,
    SourceConfig,
)
from ma_signal_monitor.models import (
    Alert,
    InternalAlert,
    NormalizedItem,
    PublicInsightDraft,
    RawFeedItem,
    ScoredItem,
    ScoringReason,
)
from ma_signal_monitor.storage import StateStore


@pytest.fixture
def sample_raw_items() -> list[RawFeedItem]:
    """Sample raw feed items for testing."""
    return [
        RawFeedItem(
            source_name="Test Feed",
            source_type="rss",
            source_url="https://example.com/feed",
            source_priority=4,
            source_tags=["test", "cms"],
            title="UnitedHealthcare expands Medicare Advantage enrollment to new counties",
            link="https://example.com/article/1",
            published="Mon, 01 Jan 2024 12:00:00 +0000",
            summary="UnitedHealthcare announced expansion of Medicare Advantage service area enrollment to 15 new counties.",
            author="Test Author",
        ),
        RawFeedItem(
            source_name="Test Feed",
            source_type="rss",
            source_url="https://example.com/feed",
            source_priority=5,
            source_tags=["test", "regulatory"],
            title="CMS proposes new Star Ratings methodology for Medicare Advantage",
            link="https://example.com/article/2",
            published="Tue, 02 Jan 2024 08:00:00 +0000",
            summary="The Centers for Medicare & Medicaid Services released a proposed rule on Star Ratings.",
        ),
        RawFeedItem(
            source_name="Test Feed",
            source_type="rss",
            source_url="https://example.com/feed",
            source_priority=2,
            source_tags=["test"],
            title="Hospital opens new parking garage",
            link="https://example.com/article/3",
            published="Wed, 03 Jan 2024 10:00:00 +0000",
            summary="Springfield General Hospital opened a new multi-level parking garage for visitors.",
        ),
    ]


@pytest.fixture
def sample_normalized_items() -> list[NormalizedItem]:
    """Pre-normalized items for testing downstream stages."""
    return [
        NormalizedItem(
            item_id="abc123def456",
            source_name="Test Feed",
            source_type="rss",
            source_priority=4,
            source_tags=["test"],
            title="UnitedHealthcare expands Medicare Advantage enrollment to new counties",
            link="https://example.com/article/1",
            published_date=datetime(2024, 1, 1, 12, 0),
            summary="UnitedHealthcare announced expansion of Medicare Advantage service area enrollment.",
        ),
        NormalizedItem(
            item_id="xyz789ghi012",
            source_name="Test Feed",
            source_type="rss",
            source_priority=5,
            source_tags=["test"],
            title="CMS proposes new Star Ratings methodology for Medicare Advantage",
            link="https://example.com/article/2",
            published_date=datetime(2024, 1, 2, 8, 0),
            summary="CMS released a proposed rule on Star Ratings changes for risk adjustment.",
        ),
        NormalizedItem(
            item_id="low000score00",
            source_name="Test Feed",
            source_type="rss",
            source_priority=2,
            source_tags=["test"],
            title="Hospital opens new parking garage",
            link="https://example.com/article/3",
            published_date=datetime(2024, 1, 3, 10, 0),
            summary="Springfield General Hospital opened a new multi-level parking garage.",
        ),
    ]


@pytest.fixture
def sample_config() -> AppConfig:
    """A minimal but valid AppConfig for testing."""
    return AppConfig(
        webhook_url="https://webhook.site/test-uuid",
        webhook_mode="test",
        log_level="DEBUG",
        db_path="data/test_state.db",
        config_dir="config",
        min_relevance_score=0.3,
        sources=[
            SourceConfig(
                name="Test Feed",
                type="rss",
                url="https://example.com/feed",
                priority=4,
                enabled=True,
                tags=["test"],
            ),
        ],
        categories=[
            CategoryConfig(
                key="membership_movement",
                label="Membership Movement",
                description="Enrollment changes",
                weight=1.0,
                keywords=[
                    "enrollment",
                    "membership",
                    "market share",
                    "county expansion",
                    "service area",
                ],
            ),
            CategoryConfig(
                key="policy_regulatory",
                label="Policy / Regulatory Changes",
                description="CMS rules and policy",
                weight=1.2,
                keywords=[
                    "CMS",
                    "proposed rule",
                    "final rule",
                    "star rating",
                    "Stars",
                    "risk adjustment",
                ],
            ),
            CategoryConfig(
                key="financial_pressure",
                label="Financial / Operating Pressure",
                description="Margin and cost signals",
                weight=1.0,
                keywords=[
                    "margin",
                    "medical loss ratio",
                    "MLR",
                    "premium",
                    "cost trend",
                    "utilization",
                ],
            ),
            CategoryConfig(
                key="competitive_strategy",
                label="Competitive / Operational Strategy",
                description="Strategic moves",
                weight=0.9,
                keywords=[
                    "partnership",
                    "acquisition",
                    "value-based",
                    "network",
                    "care delivery",
                ],
            ),
        ],
        watched_entities=["UnitedHealthcare", "Humana", "Aetna", "CMS"],
        scoring=ScoringConfig(),
        # A minimal but valid 4-layer causal model over the four fixture
        # categories: drivers -> pressure -> response -> outcomes. Additive, so
        # tests that ignore the model are unaffected; the Angles tests use it to
        # exercise chains, cascades, and the About panel.
        causal_layers=[
            CausalLayerConfig(
                key="drivers",
                label="Structural & Policy Drivers",
                short="Drivers",
                order=1,
                categories=["policy_regulatory"],
            ),
            CausalLayerConfig(
                key="pressure",
                label="Economic Pressure",
                short="Pressure",
                order=2,
                categories=["financial_pressure"],
            ),
            CausalLayerConfig(
                key="response",
                label="Strategic Response",
                short="Response",
                order=3,
                categories=["competitive_strategy"],
            ),
            CausalLayerConfig(
                key="outcomes",
                label="Market Outcomes",
                short="Outcomes",
                order=4,
                categories=["membership_movement"],
            ),
        ],
        causal_edges=[
            CausalEdgeConfig(
                source="policy_regulatory",
                target="financial_pressure",
                weight=1.0,
                evidence=(
                    "CMS final-rule impact analyses tie benchmark and "
                    "risk-adjustment changes to MA plan margins."
                ),
            ),
            CausalEdgeConfig(
                source="financial_pressure",
                target="competitive_strategy",
                weight=0.9,
                evidence=(
                    "Payer 10-K and 8-K guidance cycles show margin pressure "
                    "preceding strategic repositioning."
                ),
            ),
            CausalEdgeConfig(
                source="competitive_strategy",
                target="membership_movement",
                weight=0.9,
                evidence=(
                    "Payer disclosures and KFF switching analyses connect "
                    "strategy shifts to enrollment movement."
                ),
            ),
            CausalEdgeConfig(
                source="policy_regulatory",
                target="competitive_strategy",
                weight=0.7,
                evidence=(
                    "CMS rule changes, per payer 8-K disclosures, prompt "
                    "network and value-based-care strategy shifts."
                ),
            ),
        ],
    )


@pytest.fixture
def temp_db(tmp_path) -> StateStore:
    """A temporary StateStore for testing."""
    store = StateStore(tmp_path / "test.db")
    yield store
    store.close()


@pytest.fixture
def sample_alert(sample_normalized_items, sample_config) -> Alert:
    """A sample Alert for renderer testing."""
    item = sample_normalized_items[0]
    scored = ScoredItem(
        item=item,
        relevance_score=0.65,
        reasons=[
            ScoringReason("title_keyword", "'enrollment' in title", 0.225),
            ScoringReason("entity_match", "UnitedHealthcare detected", 0.20),
        ],
        matched_categories=["membership_movement"],
        matched_entities=["UnitedHealthcare"],
    )
    return Alert(
        internal=InternalAlert(
            signal_type="MA Market Signal",
            source="Test Feed",
            title=item.title,
            publication_date="2024-01-01 12:00 UTC",
            entities=["UnitedHealthcare"],
            trigger_category="Membership Movement",
            relevance_score=0.65,
            summary=item.summary,
            why_it_matters="Involves named MA payer: UnitedHealthcare. Contains signal language.",
            suggested_checks=["Check enrollment data", "Review service area filings"],
            confidence="medium",
            source_url=item.link,
            scoring_reasons=["title_keyword: 'enrollment' in title (+0.225)"],
        ),
        public_draft=PublicInsightDraft(
            opening_hook="New developments around UnitedHealthcare...",
            analytic_angles=[
                "Enrollment shifts signal repositioning",
                "Watch for follow-on",
            ],
            uncertainty_caution="This is an early signal. Confirm against primary sources.",
            suggested_hashtags=["#MedicareAdvantage", "#Enrollment"],
            draft_paragraph="[DRAFT] Recent reporting signals movement in membership...",
        ),
        scored_item=scored,
    )


@pytest.fixture
def project_root_with_config(tmp_path) -> Path:
    """Create a temporary project root with valid config files."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    # sources.yaml
    sources = {
        "sources": [
            {
                "name": "Test Feed",
                "type": "rss",
                "url": "https://example.com/feed",
                "priority": 4,
                "enabled": True,
                "tags": ["test"],
            }
        ]
    }
    with open(config_dir / "sources.yaml", "w") as f:
        yaml.dump(sources, f)

    # taxonomy.yaml
    taxonomy = {
        "categories": {
            "membership_movement": {
                "label": "Membership Movement",
                "description": "Enrollment changes",
                "weight": 1.0,
                "keywords": ["enrollment", "membership"],
            },
        },
        "watched_entities": ["UnitedHealthcare"],
        "scoring": {
            "keyword_match_base": 0.15,
            "entity_match_boost": 0.20,
            "source_priority_weight": 0.10,
            "multi_category_boost": 0.10,
            "title_keyword_multiplier": 1.5,
        },
    }
    with open(config_dir / "taxonomy.yaml", "w") as f:
        yaml.dump(taxonomy, f)

    # .env
    env_content = (
        "WEBHOOK_URL=https://webhook.site/test-uuid\n"
        "WEBHOOK_MODE=test\n"
        "LOG_LEVEL=DEBUG\n"
    )
    with open(tmp_path / ".env", "w") as f:
        f.write(env_content)

    return tmp_path
