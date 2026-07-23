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
    "brokerage_distribution": [
        "Review AEP enrollment volume and lead economics for the named brokerage",
        "Check SEC filings for the brokerage's financial position",
        "Assess distribution-channel shift vs. captive/agent mix",
    ],
}

_HASHTAG_MAP: dict[str, list[str]] = {
    "membership_movement": ["#MedicareAdvantage", "#Enrollment", "#MarketShare"],
    "demographic_shifts": ["#MedicareAdvantage", "#Demographics", "#DualEligible"],
    "policy_regulatory": ["#MedicareAdvantage", "#CMS", "#HealthPolicy"],
    "financial_pressure": ["#MedicareAdvantage", "#HealthcareFinance", "#MLR"],
    "competitive_strategy": [
        "#MedicareAdvantage",
        "#HealthcareStrategy",
        "#ValueBasedCare",
    ],
    "brokerage_distribution": ["#MedicareAdvantage", "#Brokerage", "#Distribution"],
}

# Per-category concrete follow-up: a specific dataset/filing to check, not a
# speculative "this may signal…" angle.
_CROSS_REFERENCES: dict[str, str] = {
    "membership_movement": "Cross-check against CMS monthly enrollment snapshots.",
    "demographic_shifts": "Check against CMS/KFF age-in and D-SNP population data.",
    "policy_regulatory": "Compare against the current CMS rate/bid-cycle documents.",
    "financial_pressure": (
        "Compare MLR and margin guidance against prior-quarter filings."
    ),
    "competitive_strategy": "Watch for follow-on filings from the named payers.",
    "brokerage_distribution": (
        "Track lead volume and AEP marketing economics for the named brokerage."
    ),
}


def _confidence_from_score(score: float) -> str:
    """Map a relevance score to a confidence label."""
    if score >= 0.7:
        return "high"
    if score >= 0.4:
        return "medium"
    return "low"


def _generate_why_it_matters(scored: ScoredItem, config: AppConfig) -> str:
    """State, in facts, why the item cleared the bar — no speculation."""
    parts = []
    if scored.matched_entities:
        parts.append(f"Names MA payer(s): {', '.join(scored.matched_entities[:3])}")
    if len(scored.matched_categories) > 1:
        labels = ", ".join(
            get_category_label(c, config) for c in scored.matched_categories
        )
        parts.append(
            f"Spans {len(scored.matched_categories)} signal categories: {labels}"
        )
    top_reasons = sorted(scored.reasons, key=lambda r: r.contribution, reverse=True)[:2]
    for reason in top_reasons:
        if reason.factor in ("title_keyword", "body_keyword"):
            parts.append(f"Signal term: {reason.detail}")
            break

    if not parts:
        parts.append("Matched general MA relevance criteria")

    return ". ".join(parts) + "."


def _generate_opening_hook(scored: ScoredItem, category_label: str) -> str:
    """A factual lead line: the actors and the topic, no significance claims."""
    source = scored.item.source_name
    if scored.matched_entities:
        who = ", ".join(scored.matched_entities[:2])
        return f"{who} — {category_label.lower()}. Via {source}."
    title = scored.item.title
    clipped = f"{title[:80]}{'…' if len(title) > 80 else ''}"
    return f'{category_label} signal via {source}: "{clipped}".'


def _generate_analytic_angles(scored: ScoredItem, config: AppConfig) -> list[str]:
    """Concrete follow-ups — checks to run, not conjecture about significance."""
    angles: list[str] = []
    if len(scored.matched_categories) > 1:
        labels = ", ".join(
            get_category_label(c, config) for c in scored.matched_categories
        )
        angles.append(
            f"Spans {len(scored.matched_categories)} signal categories: {labels}."
        )
    for cat in scored.matched_categories:
        ref = _CROSS_REFERENCES.get(cat)
        if ref and ref not in angles:
            angles.append(ref)

    # Guarantee at least two angles with factual fallbacks (never hype).
    fallbacks = [
        "Single-source so far — corroborate against a second outlet.",
        "Confirm the Medicare Advantage angle against a primary source.",
    ]
    for fb in fallbacks:
        if len(angles) >= 2:
            break
        angles.append(fb)

    return angles[:4]


def _generate_draft_paragraph(scored: ScoredItem, category_label: str) -> str:
    """A tight, sourced draft paragraph — the facts, marked for review."""
    item = scored.item
    summary = item.summary.strip()
    if len(summary) > 220:
        summary = summary[:220].rstrip() + "…"
    who = ""
    if scored.matched_entities:
        who = f" involving {', '.join(scored.matched_entities[:3])}"

    return (
        f"[DRAFT — verify before any external use] {item.source_name} reports "
        f"on {category_label.lower()}{who}: {summary}"
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
        why_it_matters=_generate_why_it_matters(scored, config),
        suggested_checks=_SUGGESTED_CHECKS.get(
            category_key,
            [
                "Review source article for additional context",
                "Check for related filings or announcements",
            ],
        ),
        confidence=_confidence_from_score(scored.relevance_score),
        source_url=scored.item.link,
        scoring_reasons=[
            f"{r.factor}: {r.detail} (+{r.contribution:.3f})"
            for r in sorted(scored.reasons, key=lambda r: r.contribution, reverse=True)[
                :5
            ]
        ],
    )

    public_draft = PublicInsightDraft(
        opening_hook=_generate_opening_hook(scored, category_label),
        analytic_angles=_generate_analytic_angles(scored, config),
        uncertainty_caution=(
            "Note: This is an early signal based on public reporting. "
            "Confirm against primary sources before drawing conclusions. "
            "Market dynamics may shift as additional information emerges."
        ),
        suggested_hashtags=_HASHTAG_MAP.get(category_key, ["#MedicareAdvantage"]),
        draft_paragraph=_generate_draft_paragraph(scored, category_label),
    )

    return Alert(internal=internal, public_draft=public_draft, scored_item=scored)


def draft_alerts(scored_items: list[ScoredItem], config: AppConfig) -> list[Alert]:
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
                    scored.item.title[:50],
                    e,
                )

    logger.info(
        "Drafted %d alerts from %d scored items", len(alerts), len(scored_items)
    )
    return alerts
