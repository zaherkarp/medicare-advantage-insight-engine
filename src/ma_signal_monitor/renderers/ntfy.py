"""ntfy.sh webhook renderer.

Produces a payload compatible with ntfy.sh's JSON publishing API.
Ntfy supports titles, tags, priority levels, markdown formatting,
and click-through URLs — all free and without signup.

See: https://docs.ntfy.sh/publish/#publish-as-json
"""

from ma_signal_monitor.models import Alert


_CONFIDENCE_TO_PRIORITY = {
    "high": 5,  # max/urgent
    "medium": 3,  # default
    "low": 2,  # low
}

_CONFIDENCE_TAG = {
    "high": "rotating_light",
    "medium": "warning",
    "low": "information_source",
}

_CATEGORY_TAG = {
    "membership_movement": "busts_in_silhouette",
    "membership movement": "busts_in_silhouette",
    "policy_regulatory": "classical_building",
    "policy / regulatory changes": "classical_building",
    "financial_pressure": "money_with_wings",
    "financial / operating pressure": "money_with_wings",
    "competitive_strategy": "handshake",
    "competitive / operational strategy": "handshake",
    "demographic_shifts": "bar_chart",
    "demographic shifts": "bar_chart",
}

_CONFIDENCE_LABEL = {
    "high": "High confidence",
    "medium": "Medium confidence",
    "low": "Early signal",
}


def render_ntfy(alert: Alert, topic: str = "") -> dict:
    """Render an alert as an ntfy.sh JSON payload.

    Args:
        alert: The alert to render.
        topic: Ntfy topic name (extracted from URL at delivery time if empty).

    Returns:
        A dictionary for JSON POST to ntfy.sh.
    """
    internal = alert.internal
    draft = alert.public_draft

    priority = _CONFIDENCE_TO_PRIORITY.get(internal.confidence, 3)
    confidence_tag = _CONFIDENCE_TAG.get(internal.confidence, "bell")
    category_tag = _CATEGORY_TAG.get(
        internal.trigger_category.lower(), "chart_with_upwards_trend"
    )
    tags = [confidence_tag, category_tag]

    # -- Build human-readable markdown body --

    # Line 1: category + confidence badge
    confidence_label = _CONFIDENCE_LABEL.get(internal.confidence, internal.confidence)
    lines = [f"**{internal.trigger_category}** · {confidence_label}"]

    # Line 2: source, date, entities — compact metadata
    meta_parts = [internal.source]
    if internal.publication_date:
        meta_parts.append(internal.publication_date)
    if internal.entities:
        meta_parts.append(", ".join(internal.entities))
    lines.append(" · ".join(meta_parts))

    # Summary as the lead paragraph (no label — it speaks for itself)
    lines.append("")
    lines.append(internal.summary)

    # Why it matters
    lines.append("")
    lines.append(f"**Why it matters**\n{internal.why_it_matters}")

    # Suggested next steps
    if internal.suggested_checks:
        lines.append("")
        lines.append("**Next steps**")
        for check in internal.suggested_checks[:3]:
            lines.append(f"- {check}")

    # Draft insight section
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("**Insight angle**")
    lines.append(f"_{draft.opening_hook}_")
    lines.append("")
    lines.append(draft.draft_paragraph)

    if draft.suggested_hashtags:
        lines.append("")
        lines.append(" ".join(draft.suggested_hashtags))

    payload = {
        "title": f"MA Signal: {internal.title}",
        "message": "\n".join(lines),
        "priority": priority,
        "tags": tags,
        "markdown": True,
    }

    if topic:
        payload["topic"] = topic

    if internal.source_url:
        payload["click"] = internal.source_url
        payload["actions"] = [
            {
                "action": "view",
                "label": "View Source",
                "url": internal.source_url,
            }
        ]

    return payload
