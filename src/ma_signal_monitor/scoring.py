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


def _has_ma_context(text: str, config: AppConfig) -> bool:
    """True if the text actually establishes Medicare Advantage context.

    Context is a watched payer entity or a core Medicare/MA anchor term
    (``config.ma_context_terms``). Used to gate keyword scoring for broad,
    low-priority sources so a lone generic keyword ("premium", "network") from
    a general-news firehose doesn't read as an MA signal (see :func:`score_item`).
    """
    for entity in config.watched_entities:
        if _keyword_in_text(entity, text):
            return True
    for term in config.ma_context_terms:
        if _keyword_in_text(term, text):
            return True
    return False


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

    # Broad, general-interest sources (low priority) constantly brush a taxonomy
    # keyword ("premium", "network", "earnings") in stories that have nothing to
    # do with Medicare Advantage. For those sources, require a real MA anchor —
    # a watched payer or a core Medicare/MA term — before keyword matches count.
    # Dedicated MA sources are higher priority and are trusted to be on-topic, so
    # they are never gated. Set scoring.ma_context_min_priority to 0 to disable.
    ma_context_gated = (
        item.source_priority < sc.ma_context_min_priority
        and not _has_ma_context(text_combined, config)
    )

    # 1. Keyword matches per category (suppressed for gated broad sources so the
    #    item falls back to the source-priority floor and is treated as noise).
    if ma_context_gated:
        reasons.append(
            ScoringReason(
                factor="ma_context_gate",
                detail=(
                    f"broad source (priority {item.source_priority}) lacks "
                    "Medicare/MA context; keyword matches not counted"
                ),
                contribution=0.0,
            )
        )
    else:
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

    # 3a. Entity-group dedup (MA-eligibility gate only). One payer matched under
    #     two aliases ("UnitedHealthcare" + "UnitedHealth") earns two entity
    #     boosts (+0.40) for a single company; collapse the boost to distinct
    #     payer groups so it counts once. A no-op (delta 0) for genuinely
    #     distinct payers. Off by default -> scores unchanged.
    if config.ma_eligibility_gate:
        from ma_signal_monitor.eligibility import entity_group_delta
        from ma_signal_monitor.payers import ALIAS_TO_GROUP

        delta = entity_group_delta(
            matched_entities, ALIAS_TO_GROUP, sc.entity_match_boost
        )
        if delta:
            raw_score += delta
            reasons.append(
                ScoringReason(
                    factor="entity_group_dedup",
                    detail="collapsed payer aliases to distinct payer groups",
                    contribution=delta,
                )
            )

    # 3b. Core MA vocabulary boost. Strong MA-plan terms ("Medicare Advantage",
    # "D-SNP", …) are direct relevance evidence independent of category
    # keywords — a story can be squarely about an MA plan without brushing any
    # category vocabulary (e.g. a health system dropping a payer's MA plans).
    # Applied once for the first matching term, like the entity boost.
    for term in config.ma_boost_terms:
        if _keyword_in_text(term, text_combined):
            raw_score += sc.ma_term_boost
            reasons.append(
                ScoringReason(
                    factor="ma_term",
                    detail=f"Core MA term '{term}' present",
                    contribution=sc.ma_term_boost,
                )
            )
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

    # 6. MA-eligibility tier (gate only). Deterministic, separate from the
    #    additive score above; persisted for audit and consulted by the
    #    briefing/alert/display gates. Off by default -> tier stays None and no
    #    gate acts on it. Lazy import keeps scoring <-> eligibility acyclic.
    eligibility_tier = eligibility_reason = None
    if config.ma_eligibility_gate:
        from ma_signal_monitor.eligibility import (
            classify_eligibility,
            vocab_from_config,
        )

        elig = classify_eligibility(
            item.title, item.summary, matched_entities, vocab_from_config(config)
        )
        eligibility_tier = elig.tier
        eligibility_reason = elig.reasons[0] if elig.reasons else None

    return ScoredItem(
        item=item,
        relevance_score=round(final_score, 3),
        reasons=reasons,
        matched_categories=matched_categories,
        matched_entities=matched_entities,
        eligibility_tier=eligibility_tier,
        eligibility_reason=eligibility_reason,
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
