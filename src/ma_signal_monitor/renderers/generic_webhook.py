"""Generic webhook renderer - plain JSON payload.

Produces a clean JSON payload suitable for Webhook.site, RequestBin,
Pipedream, or any general-purpose webhook inspector.
"""

from ma_signal_monitor.models import Alert


def render_generic(alert: Alert) -> dict:
    """Render an alert as a generic JSON webhook payload.

    Args:
        alert: The alert to render.

    Returns:
        A dictionary suitable for JSON serialization and POST to a webhook.
    """
    internal = alert.internal
    draft = alert.public_draft

    return {
        "source": "MA Signal Monitor",
        "alert_type": "medicare_advantage_signal",
        "section_a_internal_alert": {
            "signal_type": internal.signal_type,
            "source": internal.source,
            "title": internal.title,
            "publication_date": internal.publication_date,
            "entities": internal.entities,
            "trigger_category": internal.trigger_category,
            "relevance_score": internal.relevance_score,
            "summary": internal.summary,
            "why_it_matters": internal.why_it_matters,
            "suggested_checks": internal.suggested_checks,
            "confidence": internal.confidence,
            "source_url": internal.source_url,
            "scoring_reasons": internal.scoring_reasons,
        },
        "section_b_public_insight_draft": {
            "opening_hook": draft.opening_hook,
            "analytic_angles": draft.analytic_angles,
            "uncertainty_caution": draft.uncertainty_caution,
            "suggested_hashtags": draft.suggested_hashtags,
            "draft_paragraph": draft.draft_paragraph,
        },
    }
