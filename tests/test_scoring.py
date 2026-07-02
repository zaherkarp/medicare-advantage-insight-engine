"""Tests for relevance scoring."""

from datetime import datetime

from ma_signal_monitor.models import NormalizedItem
from ma_signal_monitor.scoring import _keyword_in_text, score_item, score_items


class TestKeywordMatching:
    """Whole-token keyword matching (precision over substring search)."""

    def test_matches_whole_word(self):
        assert _keyword_in_text("MLR", "the MLR rose sharply")
        assert _keyword_in_text("star rating", "a new star rating method")

    def test_rejects_substring_false_positives(self):
        assert not _keyword_in_text("SNP", "see the snippet below")
        assert not _keyword_in_text("bid", "this is forbidden")
        assert not _keyword_in_text("MA", "based in Massachusetts")

    def test_allows_plural(self):
        assert _keyword_in_text("premium", "premiums increased")
        assert _keyword_in_text("rating", "star ratings changed")
        assert _keyword_in_text("box", "several boxes shipped")

    def test_handles_punctuation_edged_keywords(self):
        assert _keyword_in_text("value-based", "a value-based contract")
        assert _keyword_in_text("C-SNP", "enrolled in a C-SNP plan")


class TestScoring:
    """Test the scoring model."""

    def test_relevant_item_scores_above_threshold(
        self, sample_normalized_items, sample_config
    ):
        """Items with MA keywords should score above the threshold."""
        # First item: UHC + enrollment + county expansion
        scored = score_item(sample_normalized_items[0], sample_config)
        assert scored.relevance_score >= sample_config.min_relevance_score
        assert len(scored.reasons) > 0

    def test_irrelevant_item_scores_below_threshold(
        self, sample_normalized_items, sample_config
    ):
        """Items without MA keywords should score below threshold."""
        # Third item: parking garage
        scored = score_item(sample_normalized_items[2], sample_config)
        assert scored.relevance_score < sample_config.min_relevance_score

    def test_entity_match_boosts_score(self, sample_config):
        """Named entity matches should increase the score."""
        item_with_entity = NormalizedItem(
            item_id="ent001",
            source_name="Test",
            source_type="rss",
            source_priority=3,
            source_tags=[],
            title="UnitedHealthcare plans something",
            link="https://x.com/1",
            published_date=datetime(2024, 1, 1),
            summary="Generic business news about plans.",
        )
        item_without_entity = NormalizedItem(
            item_id="ent002",
            source_name="Test",
            source_type="rss",
            source_priority=3,
            source_tags=[],
            title="Some company plans something",
            link="https://x.com/2",
            published_date=datetime(2024, 1, 1),
            summary="Generic business news about plans.",
        )
        score_with = score_item(item_with_entity, sample_config)
        score_without = score_item(item_without_entity, sample_config)
        assert score_with.relevance_score > score_without.relevance_score
        assert "UnitedHealthcare" in score_with.matched_entities

    def test_title_keyword_weighted_higher(self, sample_config):
        """Keywords in the title should contribute more than in the body."""
        item_title = NormalizedItem(
            item_id="tw001",
            source_name="Test",
            source_type="rss",
            source_priority=3,
            source_tags=[],
            title="New enrollment trends in Medicare",
            link="https://x.com/1",
            published_date=datetime(2024, 1, 1),
            summary="General healthcare news article.",
        )
        item_body = NormalizedItem(
            item_id="tw002",
            source_name="Test",
            source_type="rss",
            source_priority=3,
            source_tags=[],
            title="General healthcare news",
            link="https://x.com/2",
            published_date=datetime(2024, 1, 1),
            summary="New enrollment trends in Medicare.",
        )
        score_title = score_item(item_title, sample_config)
        score_body = score_item(item_body, sample_config)
        assert score_title.relevance_score > score_body.relevance_score

    def test_multi_category_boost(self, sample_config):
        """Items matching multiple categories get a boost."""
        item = NormalizedItem(
            item_id="mc001",
            source_name="Test",
            source_type="rss",
            source_priority=3,
            source_tags=[],
            title="CMS enrollment proposed rule affects Medicare Advantage premiums",
            link="https://x.com/1",
            published_date=datetime(2024, 1, 1),
            summary="CMS proposed rule on enrollment and premium changes for MA.",
        )
        scored = score_item(item, sample_config)
        assert len(scored.matched_categories) >= 2
        multi_reasons = [r for r in scored.reasons if r.factor == "multi_category"]
        assert len(multi_reasons) == 1

    def test_score_clamped_to_unit(self, sample_config):
        """Score should be clamped to [0.0, 1.0]."""
        item = NormalizedItem(
            item_id="clamp001",
            source_name="Test",
            source_type="rss",
            source_priority=5,
            source_tags=[],
            title="UnitedHealthcare Humana enrollment CMS Star Ratings proposed rule premium MLR partnership",
            link="https://x.com/1",
            published_date=datetime(2024, 1, 1),
            summary="UnitedHealthcare Humana enrollment CMS Stars risk adjustment MLR premium cost trend value-based",
        )
        scored = score_item(item, sample_config)
        assert 0.0 <= scored.relevance_score <= 1.0

    def test_scoring_returns_reasons(self, sample_normalized_items, sample_config):
        """Scored items include explanatory reasons."""
        scored = score_item(sample_normalized_items[0], sample_config)
        assert len(scored.reasons) > 0
        for reason in scored.reasons:
            assert reason.factor
            assert reason.detail
            assert isinstance(reason.contribution, float)

    def test_batch_scoring_sorted(self, sample_normalized_items, sample_config):
        """Batch scoring returns items sorted by score descending."""
        scored = score_items(sample_normalized_items, sample_config)
        assert len(scored) == len(sample_normalized_items)
        scores = [s.relevance_score for s in scored]
        assert scores == sorted(scores, reverse=True)

    def test_source_priority_affects_score(self, sample_config):
        """Higher source priority should increase the score."""
        item_high = NormalizedItem(
            item_id="sp001",
            source_name="High Priority",
            source_type="rss",
            source_priority=5,
            source_tags=[],
            title="Generic news",
            link="https://x.com/1",
            published_date=datetime(2024, 1, 1),
            summary="Some generic news about healthcare.",
        )
        item_low = NormalizedItem(
            item_id="sp002",
            source_name="Low Priority",
            source_type="rss",
            source_priority=1,
            source_tags=[],
            title="Generic news",
            link="https://x.com/2",
            published_date=datetime(2024, 1, 1),
            summary="Some generic news about healthcare.",
        )
        score_high = score_item(item_high, sample_config)
        score_low = score_item(item_low, sample_config)
        assert score_high.relevance_score > score_low.relevance_score


class TestExclusions:
    """Hard veto and soft penalty exclusion keywords."""

    def _item(self):
        return NormalizedItem(
            item_id="ex001",
            source_name="Test",
            source_type="rss",
            source_priority=4,
            source_tags=[],
            title="UnitedHealthcare expands enrollment to new counties",
            link="https://x.com/1",
            published_date=datetime(2024, 1, 1),
            summary="Medicare Advantage service area enrollment grows.",
        )

    def test_soft_penalty_lowers_score(self, sample_config):
        base = score_item(self._item(), sample_config).relevance_score
        sample_config.exclusions_soft = ["counties"]
        scored = score_item(self._item(), sample_config)
        assert scored.relevance_score == round(
            base - sample_config.scoring.exclusion_penalty, 3
        )
        assert any(r.factor == "exclusion_keyword" for r in scored.reasons)

    def test_hard_veto_forces_zero(self, sample_config):
        sample_config.exclusions_hard = ["counties"]
        scored = score_item(self._item(), sample_config)
        assert scored.relevance_score == 0.0
        assert any(r.factor == "exclusion_veto" for r in scored.reasons)

    def test_no_exclusions_unchanged(self, sample_config):
        # Default config has empty exclusion lists → no exclusion reasons.
        scored = score_item(self._item(), sample_config)
        assert not any(
            r.factor in ("exclusion_keyword", "exclusion_veto") for r in scored.reasons
        )


class TestMaContextGate:
    """Broad, low-priority sources need Medicare/MA context to score keywords."""

    def _cfg(self, sample_config):
        sample_config.ma_context_terms = ["Medicare", "Medicare Advantage", "D-SNP"]
        sample_config.scoring.ma_context_min_priority = 3
        return sample_config

    def _item(self, priority, title, summary):
        return NormalizedItem(
            item_id="g",
            source_name="State Feed",
            source_type="rss",
            source_priority=priority,
            source_tags=["state"],
            title=title,
            link="https://x.com/1",
            published_date=datetime(2024, 1, 1),
            summary=summary,
        )

    def test_broad_source_keyword_without_context_is_suppressed(self, sample_config):
        cfg = self._cfg(sample_config)
        # Priority 2 (< 3); brushes 'premium'/'enrollment'/'network'/'partnership'
        # but says nothing about Medicare and names no payer.
        scored = score_item(
            self._item(
                2,
                "County debates premium gas tax and open enrollment",
                "A local road network partnership advanced.",
            ),
            cfg,
        )
        assert scored.matched_categories == []
        assert any(r.factor == "ma_context_gate" for r in scored.reasons)
        assert scored.relevance_score < cfg.min_relevance_score

    def test_broad_source_with_medicare_term_counts(self, sample_config):
        cfg = self._cfg(sample_config)
        scored = score_item(
            self._item(
                2,
                "State reviews Medicare Advantage enrollment for seniors",
                "New D-SNP plan options for dual eligibles.",
            ),
            cfg,
        )
        assert "membership_movement" in scored.matched_categories
        assert not any(r.factor == "ma_context_gate" for r in scored.reasons)

    def test_broad_source_naming_a_payer_counts(self, sample_config):
        cfg = self._cfg(sample_config)
        scored = score_item(
            self._item(
                2,
                "UnitedHealthcare expands enrollment in the state",
                "The insurer adds members.",
            ),
            cfg,
        )
        assert "UnitedHealthcare" in scored.matched_entities
        assert not any(r.factor == "ma_context_gate" for r in scored.reasons)

    def test_dedicated_source_is_exempt(self, sample_config):
        cfg = self._cfg(sample_config)
        # Priority 3 == threshold (not below) → trusted, never gated.
        scored = score_item(
            self._item(
                3,
                "Premium and enrollment trends reshape the market",
                "Network and care delivery strategy shifts.",
            ),
            cfg,
        )
        assert scored.matched_categories
        assert not any(r.factor == "ma_context_gate" for r in scored.reasons)

    def test_gate_disabled_when_threshold_zero(self, sample_config):
        cfg = self._cfg(sample_config)
        cfg.scoring.ma_context_min_priority = 0
        scored = score_item(
            self._item(
                1,
                "Enrollment and premium news from the statehouse",
                "A network partnership was announced.",
            ),
            cfg,
        )
        assert scored.matched_categories  # nothing is gated
        assert not any(r.factor == "ma_context_gate" for r in scored.reasons)
