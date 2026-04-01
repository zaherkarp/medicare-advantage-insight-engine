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
    tags = [
        _CONFIDENCE_TAG.get(internal.confidence, "bell"),
        "chart_with_upwards_trend",
    ]

    # Build markdown body
    lines = [
        f"**Category:** {internal.trigger_category}",
        f"**Source:** {internal.source}",
        f"**Score:** {internal.relevance_score:.2f} ({internal.confidence} confidence)",
        f"**Date:** {internal.publication_date or 'N/A'}",
    ]

    if internal.entities:
        lines.append(f"**Entities:** {', '.join(internal.entities)}")

    lines.append("")
    lines.append(f"**Summary:** {internal.summary}")
    lines.append("")
    lines.append(f"**Why it matters:** {internal.why_it_matters}")

    if internal.suggested_checks:
        lines.append("")
        lines.append("**Suggested checks:**")
        for check in internal.suggested_checks[:3]:
            lines.append(f"- {check}")

    lines.append("")
    lines.append("---")
    lines.append(f"**Draft insight:** {draft.opening_hook}")
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
