"""Alert drafting - generates structured internal alerts and public insight drafts.

This module takes scored and classified items and produces the two-section
alert structure: an internal analytic alert and a draft public insight angle.
"""

import logging

from ma_signal_monitor.classify import classify_item, get_category_label
from ma_signal_monitor.config import AppConfig
from ma_signal_monitor.models import (
    Alert,
    InternalAlert,
    PublicInsightDraft,
    ScoredItem,
)

logger = logging.getLogger("ma_signal_monitor.drafting")

# Maps category keys to suggested internal checks
_SUGGESTED_CHECKS: dict[str, list[str]] = {
    "membership_movement": [
        "Check latest enrollment data for named entities",
        "Review service area change filings",
        "Compare against prior period membership trends",
    ],
    "demographic_shifts": [
        "Review population projections for mentioned regions",
        "Check D-SNP enrollment trends",
        "Assess impact on benefit design assumptions",
    ],
    "policy_regulatory": [
        "Review full rule text or advance notice",
        "Assess impact on current bid assumptions",
        "Check Stars methodology changes if applicable",
        "Identify compliance timeline requirements",
    ],
    "financial_pressure": [
        "Review latest MLR and financial filings",
        "Check benefit change filings for named entities",
        "Assess premium trend against benchmarks",
    ],
    "competitive_strategy": [
        "Review competitive landscape in mentioned markets",
        "Check network adequacy data for affected areas",
        "Assess strategic implications for positioning",
    ],
}

_HASHTAG_MAP: dict[str, list[str]] = {
    "membership_movement": ["#MedicareAdvantage", "#Enrollment", "#MarketShare"],
    "demographic_shifts": ["#MedicareAdvantage", "#Demographics", "#DualEligible"],
    "policy_regulatory": ["#MedicareAdvantage", "#CMS", "#HealthPolicy"],
    "financial_pressure": ["#MedicareAdvantage", "#HealthcareFinance", "#MLR"],
    "competitive_strategy": ["#MedicareAdvantage", "#HealthcareStrategy", "#ValueBasedCare"],
}


def _confidence_from_score(score: float) -> str:
    """Map a relevance score to a confidence label."""
    if score >= 0.7:
        return "high"
    if score >= 0.4:
        return "medium"
    return "low"


def _generate_why_it_matters(scored: ScoredItem, category_key: str) -> str:
    """Generate a brief 'why it matters' explanation from scoring reasons."""
    parts = []
    if scored.matched_entities:
        parts.append(
            f"Involves named MA payer(s): {', '.join(scored.matched_entities[:3])}"
        )
    if len(scored.matched_categories) > 1:
        parts.append(
            f"Crosses multiple signal categories ({len(scored.matched_categories)}), "
            "suggesting broader market implications"
        )
    top_reasons = sorted(scored.reasons, key=lambda r: r.contribution, reverse=True)[:2]
    for reason in top_reasons:
        if reason.factor in ("title_keyword", "body_keyword"):
            parts.append(f"Contains signal language: {reason.detail}")
            break

    if not parts:
        parts.append("Matched general MA-related signal criteria")

    return ". ".join(parts) + "."


def _generate_opening_hook(scored: ScoredItem, category_label: str) -> str:
    """Generate a suggested opening hook for thought leadership."""
    title = scored.item.title
    if scored.matched_entities:
        entity = scored.matched_entities[0]
        return (
            f"New developments around {entity} highlight evolving dynamics in "
            f"Medicare Advantage — this time touching on {category_label.lower()}."
        )
    return (
        f"A recent signal in {category_label.lower()} warrants attention: "
        f'"{title[:80]}{"..." if len(title) > 80 else ""}"'
    )


def _generate_analytic_angles(scored: ScoredItem, category_key: str) -> list[str]:
    """Generate 2-4 possible analytic angles."""
    angles = []

    if "membership_movement" in scored.matched_categories:
        angles.append(
            "Enrollment shifts may signal competitive repositioning — "
            "worth tracking against CMS enrollment snapshots"
        )
    if "policy_regulatory" in scored.matched_categories:
        angles.append(
            "Regulatory changes here could ripple into bid strategy "
            "and benefit design for upcoming plan year"
        )
    if "financial_pressure" in scored.matched_categories:
        angles.append(
            "Financial pressure signals could foreshadow benefit "
            "reductions or market exits in affected geographies"
        )
    if "competitive_strategy" in scored.matched_categories:
        angles.append(
            "Strategic moves by major payers often precede broader "
            "industry shifts — watch for follow-on announcements"
        )
    if "demographic_shifts" in scored.matched_categories:
        angles.append(
            "Demographic trends in the MA-eligible population continue "
            "to reshape growth opportunities and risk profiles"
        )

    # Ensure at least 2 angles
    if len(angles) < 2:
        angles.append(
            "The timing relative to the annual bid cycle may amplify "
            "the significance of this signal"
        )
    if len(angles) < 2:
        angles.append(
            "Cross-referencing with recent CMS data releases could "
            "reveal additional context"
        )

    return angles[:4]


def _generate_draft_paragraph(scored: ScoredItem, category_label: str) -> str:
    """Generate a short draft paragraph for public thought leadership."""
    item = scored.item
    summary_excerpt = item.summary[:150] + ("..." if len(item.summary) > 150 else "")

    return (
        f"[DRAFT — requires manual editing before publication] "
        f"Recent reporting signals movement in {category_label.lower()} "
        f"within the Medicare Advantage landscape. {summary_excerpt} "
        f"As the MA market continues to evolve, signals like this merit "
        f"careful tracking against enrollment data, regulatory timelines, "
        f"and competitive positioning."
    )


def draft_alert(scored: ScoredItem, config: AppConfig) -> Alert:
    """Generate a complete alert from a scored item.

    Args:
        scored: The scored and classified item.
        config: Application configuration.

    Returns:
        An Alert with both internal and public draft sections.
    """
    category_key = classify_item(scored, config)
    category_label = get_category_label(category_key, config)

    pub_date = ""
    if scored.item.published_date:
        pub_date = scored.item.published_date.strftime("%Y-%m-%d %H:%M UTC")

    internal = InternalAlert(
        signal_type="MA Market Signal",
        source=scored.item.source_name,
        title=scored.item.title,
        publication_date=pub_date,
        entities=scored.matched_entities[:5],
        trigger_category=category_label,
        relevance_score=scored.relevance_score,
        summary=scored.item.summary,
        why_it_matters=_generate_why_it_matters(scored, category_key),
        suggested_checks=_SUGGESTED_CHECKS.get(category_key, [
            "Review source article for additional context",
            "Check for related filings or announcements",
        ]),
        confidence=_confidence_from_score(scored.relevance_score),
        source_url=scored.item.link,
        scoring_reasons=[
            f"{r.factor}: {r.detail} (+{r.contribution:.3f})"
            for r in sorted(scored.reasons, key=lambda r: r.contribution, reverse=True)[:5]
        ],
    )

    public_draft = PublicInsightDraft(
        opening_hook=_generate_opening_hook(scored, category_label),
        analytic_angles=_generate_analytic_angles(scored, category_key),
        uncertainty_caution=(
            "Note: This is an early signal based on public reporting. "
            "Confirm against primary sources before drawing conclusions. "
            "Market dynamics may shift as additional information emerges."
        ),
        suggested_hashtags=_HASHTAG_MAP.get(category_key, ["#MedicareAdvantage"]),
        draft_paragraph=_generate_draft_paragraph(scored, category_label),
    )

    return Alert(internal=internal, public_draft=public_draft, scored_item=scored)


def draft_alerts(
    scored_items: list[ScoredItem], config: AppConfig
) -> list[Alert]:
    """Generate alerts for all scored items above the relevance threshold.

    Args:
        scored_items: List of scored items (should already be sorted).
        config: Application configuration.

    Returns:
        List of Alert objects for items meeting the relevance threshold.
    """
    alerts = []
    for scored in scored_items:
        if scored.relevance_score >= config.min_relevance_score:
            try:
                alerts.append(draft_alert(scored, config))
            except Exception as e:
                logger.warning(
                    "Failed to draft alert for '%s': %s",
                    scored.item.title[:50], e,
                )

    logger.info("Drafted %d alerts from %d scored items", len(alerts), len(scored_items))
    return alerts
