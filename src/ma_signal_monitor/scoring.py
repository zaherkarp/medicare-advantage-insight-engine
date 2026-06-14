"""Relevance scoring for normalized items.

Implements a transparent, explainable scoring model based on:
- Keyword presence in title and summary
- Source priority
- Named entity (payer) detection
- Multi-category matches
"""

import functools
import logging
import re

from ma_signal_monitor.config import AppConfig
from ma_signal_monitor.models import NormalizedItem, ScoredItem, ScoringReason

logger = logging.getLogger("ma_signal_monitor.scoring")


@functools.lru_cache(maxsize=2048)
def _keyword_pattern(keyword: str) -> re.Pattern:
    """Compile a case-insensitive, whole-token matcher for a keyword.

    Uses lookarounds rather than ``\\b`` so keywords with punctuation at their
    edges (e.g. ``value-based``, ``C-SNP``) still match as whole tokens. This
    prevents substring false positives like ``SNP`` in "snippet", ``bid`` in
    "forbidden", or ``MA`` in "Massachusetts".

    An optional trailing ``s``/``es`` is allowed so a singular keyword still
    matches its plural (``premium`` → "premiums", ``rating`` → "ratings")
    without re-opening the substring problem — ``MA`` still won't match
    "Massachusetts".
    """
    return re.compile(rf"(?<!\w){re.escape(keyword)}(?:es|s)?(?!\w)", re.IGNORECASE)


def _keyword_in_text(keyword: str, text: str) -> bool:
    """Check if a keyword appears in text (case-insensitive, whole-token)."""
    return bool(_keyword_pattern(keyword).search(text))


def score_item(item: NormalizedItem, config: AppConfig) -> ScoredItem:
    """Score a single item for relevance.

    The scoring model considers:
    1. Keyword matches from taxonomy categories (title matches weighted higher)
    2. Source priority (higher priority sources boost score)
    3. Named entity detection (known payer names)
    4. Multi-category matches (items touching multiple categories get a boost)

    Returns a ScoredItem with a score in [0.0, 1.0] and explanatory reasons.
    """
    sc = config.scoring
    reasons: list[ScoringReason] = []
    matched_categories: list[str] = []
    matched_entities: list[str] = []
    raw_score = 0.0

    text_combined = f"{item.title} {item.summary}".lower()
    title_lower = item.title.lower()

    # 1. Keyword matches per category
    for category in config.categories:
        category_matched = False
        for keyword in category.keywords:
            if _keyword_in_text(keyword, text_combined):
                contribution = sc.keyword_match_base * category.weight
                # Boost if keyword appears in title
                if _keyword_in_text(keyword, title_lower):
                    contribution *= sc.title_keyword_multiplier
                    reasons.append(
                        ScoringReason(
                            factor="title_keyword",
                            detail=f"'{keyword}' in title [{category.label}]",
                            contribution=contribution,
                        )
                    )
                else:
                    reasons.append(
                        ScoringReason(
                            factor="body_keyword",
                            detail=f"'{keyword}' in summary [{category.label}]",
                            contribution=contribution,
                        )
                    )
                raw_score += contribution
                category_matched = True
                # Only count first keyword match per category to avoid
                # over-scoring articles with many hits in one category
                break

        if category_matched:
            matched_categories.append(category.key)

    # 2. Source priority boost
    priority_contribution = (item.source_priority / 5.0) * sc.source_priority_weight
    raw_score += priority_contribution
    reasons.append(
        ScoringReason(
            factor="source_priority",
            detail=f"Source '{item.source_name}' priority {item.source_priority}/5",
            contribution=priority_contribution,
        )
    )

    # 3. Named entity detection
    for entity in config.watched_entities:
        if _keyword_in_text(entity, text_combined):
            raw_score += sc.entity_match_boost
            matched_entities.append(entity)
            reasons.append(
                ScoringReason(
                    factor="entity_match",
                    detail=f"Named entity '{entity}' detected",
                    contribution=sc.entity_match_boost,
                )
            )
            # Cap at 2 entity boosts to avoid runaway scores
            if len(matched_entities) >= 2:
                break

    # 4. Multi-category boost
    if len(matched_categories) > 1:
        multi_boost = sc.multi_category_boost * (len(matched_categories) - 1)
        raw_score += multi_boost
        reasons.append(
            ScoringReason(
                factor="multi_category",
                detail=f"Matches {len(matched_categories)} categories",
                contribution=multi_boost,
            )
        )

    # 5. Exclusion keywords. Soft terms each subtract a penalty (kept in the
    # reasons so the score stays explainable); a hard term vetoes the item to 0
    # but the item is still archived with the veto reason — never silently
    # dropped (see docs/assumptions.md: false positives over false negatives).
    for term in config.exclusions_soft:
        if _keyword_in_text(term, text_combined):
            raw_score -= sc.exclusion_penalty
            reasons.append(
                ScoringReason(
                    factor="exclusion_keyword",
                    detail=f"soft exclusion '{term}'",
                    contribution=-sc.exclusion_penalty,
                )
            )

    vetoed_by = next(
        (t for t in config.exclusions_hard if _keyword_in_text(t, text_combined)),
        None,
    )
    if vetoed_by is not None:
        removed = round(max(0.0, raw_score), 3)
        reasons.append(
            ScoringReason(
                factor="exclusion_veto",
                detail=f"hard exclusion '{vetoed_by}' forces score to 0",
                contribution=-removed,
            )
        )
        final_score = 0.0
    else:
        # Clamp to [0.0, 1.0]
        final_score = min(1.0, max(0.0, raw_score))

    return ScoredItem(
        item=item,
        relevance_score=round(final_score, 3),
        reasons=reasons,
        matched_categories=matched_categories,
        matched_entities=matched_entities,
    )


def score_items(items: list[NormalizedItem], config: AppConfig) -> list[ScoredItem]:
    """Score a list of items and return them sorted by relevance (descending).

    Args:
        items: Normalized items to score.
        config: Application configuration.

    Returns:
        List of ScoredItem objects sorted by relevance_score descending.
    """
    scored = [score_item(item, config) for item in items]
    scored.sort(key=lambda s: s.relevance_score, reverse=True)

    relevant_count = sum(
        1 for s in scored if s.relevance_score >= config.min_relevance_score
    )
    logger.info(
        "Scored %d items: %d above threshold (%.2f)",
        len(scored),
        relevant_count,
        config.min_relevance_score,
    )
    return scored
