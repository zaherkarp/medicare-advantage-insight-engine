"""Microsoft Teams webhook renderer.

Produces an Adaptive Card payload compatible with Teams Incoming Webhooks.
Uses the Adaptive Card schema (version 1.4) which is well-supported
by Teams connectors.

Note: Teams webhook compatibility can be fragile. This renderer targets
the "Incoming Webhook" connector format using Adaptive Cards wrapped
in a message payload. If Teams changes their webhook format, this
renderer may need updates.
"""

from ma_signal_monitor.models import Alert


def _truncate(text: str, max_len: int = 300) -> str:
    """Truncate text for Teams card readability."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def render_teams(alert: Alert) -> dict:
    """Render an alert as a Teams-compatible Adaptive Card payload.

    Args:
        alert: The alert to render.

    Returns:
        A dictionary representing a Teams webhook message with Adaptive Card.
    """
    internal = alert.internal
    draft = alert.public_draft

    # Confidence indicator
    confidence_emoji = {
        "high": "🟢", "medium": "🟡", "low": "🔴"
    }.get(internal.confidence, "⚪")

    # Build Adaptive Card body
    card_body = [
        # Header
        {
            "type": "TextBlock",
            "text": f"MA Signal: {internal.title}",
            "wrap": True,
            "weight": "Bolder",
            "size": "Medium",
        },
        # Metadata row
        {
            "type": "ColumnSet",
            "columns": [
                {
                    "type": "Column",
                    "width": "auto",
                    "items": [{
                        "type": "TextBlock",
                        "text": f"**Source:** {internal.source}",
                        "wrap": True,
                        "size": "Small",
                    }],
                },
                {
                    "type": "Column",
                    "width": "auto",
                    "items": [{
                        "type": "TextBlock",
                        "text": f"**Score:** {internal.relevance_score:.2f} {confidence_emoji}",
                        "wrap": True,
                        "size": "Small",
                    }],
                },
            ],
        },
        # Category and date
        {
            "type": "TextBlock",
            "text": (
                f"**Category:** {internal.trigger_category}  \n"
                f"**Date:** {internal.publication_date or 'N/A'}  \n"
                f"**Entities:** {', '.join(internal.entities) if internal.entities else 'None detected'}"
            ),
            "wrap": True,
            "size": "Small",
        },
        # Separator
        {"type": "TextBlock", "text": "---", "separator": True},
        # Section A: Internal Alert
        {
            "type": "TextBlock",
            "text": "**INTERNAL ALERT**",
            "wrap": True,
            "weight": "Bolder",
            "size": "Small",
            "color": "Accent",
        },
        {
            "type": "TextBlock",
            "text": _truncate(internal.summary),
            "wrap": True,
            "size": "Small",
        },
        {
            "type": "TextBlock",
            "text": f"**Why it matters:** {_truncate(internal.why_it_matters)}",
            "wrap": True,
            "size": "Small",
        },
        {
            "type": "TextBlock",
            "text": "**Suggested checks:**\n" + "\n".join(
                f"- {check}" for check in internal.suggested_checks[:3]
            ),
            "wrap": True,
            "size": "Small",
        },
        # Separator
        {"type": "TextBlock", "text": "---", "separator": True},
        # Section B: Public Draft
        {
            "type": "TextBlock",
            "text": "**DRAFT PUBLIC INSIGHT**",
            "wrap": True,
            "weight": "Bolder",
            "size": "Small",
            "color": "Good",
        },
        {
            "type": "TextBlock",
            "text": f"**Hook:** {_truncate(draft.opening_hook, 200)}",
            "wrap": True,
            "size": "Small",
        },
        {
            "type": "TextBlock",
            "text": "**Angles:**\n" + "\n".join(
                f"- {angle}" for angle in draft.analytic_angles[:4]
            ),
            "wrap": True,
            "size": "Small",
        },
        {
            "type": "TextBlock",
            "text": f"**Caution:** {_truncate(draft.uncertainty_caution, 200)}",
            "wrap": True,
            "size": "Small",
            "isSubtle": True,
        },
        {
            "type": "TextBlock",
            "text": f"**Tags:** {' '.join(draft.suggested_hashtags)}",
            "wrap": True,
            "size": "Small",
        },
    ]

    # Add source link action if available
    actions = []
    if internal.source_url:
        actions.append({
            "type": "Action.OpenUrl",
            "title": "View Source",
            "url": internal.source_url,
        })

    # Adaptive Card wrapped in Teams message format
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": card_body,
                    "actions": actions,
                },
            }
        ],
    }
