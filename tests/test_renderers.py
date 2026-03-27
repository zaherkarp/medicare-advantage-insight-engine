"""Tests for alert renderers (generic webhook, ntfy, and Teams)."""

import json

from ma_signal_monitor.renderers.generic_webhook import render_generic
from ma_signal_monitor.renderers.ntfy import render_ntfy
from ma_signal_monitor.renderers.teams import render_teams


class TestGenericWebhookRenderer:
    """Test the generic JSON webhook renderer."""

    def test_renders_valid_json(self, sample_alert):
        """Output is JSON-serializable."""
        payload = render_generic(sample_alert)
        json_str = json.dumps(payload)
        assert json_str  # No serialization error

    def test_contains_both_sections(self, sample_alert):
        """Payload contains both internal alert and public draft sections."""
        payload = render_generic(sample_alert)
        assert "section_a_internal_alert" in payload
        assert "section_b_public_insight_draft" in payload

    def test_internal_alert_fields(self, sample_alert):
        """Internal alert section has all required fields."""
        payload = render_generic(sample_alert)
        alert = payload["section_a_internal_alert"]
        required_fields = [
            "signal_type", "source", "title", "publication_date",
            "entities", "trigger_category", "relevance_score",
            "summary", "why_it_matters", "suggested_checks",
            "confidence", "source_url", "scoring_reasons",
        ]
        for field in required_fields:
            assert field in alert, f"Missing field: {field}"

    def test_public_draft_fields(self, sample_alert):
        """Public draft section has all required fields."""
        payload = render_generic(sample_alert)
        draft = payload["section_b_public_insight_draft"]
        required_fields = [
            "opening_hook", "analytic_angles",
            "uncertainty_caution", "suggested_hashtags",
            "draft_paragraph",
        ]
        for field in required_fields:
            assert field in draft, f"Missing field: {field}"

    def test_source_metadata(self, sample_alert):
        """Payload includes source metadata."""
        payload = render_generic(sample_alert)
        assert payload["source"] == "MA Signal Monitor"
        assert payload["alert_type"] == "medicare_advantage_signal"


class TestNtfyRenderer:
    """Test the ntfy.sh renderer."""

    def test_renders_valid_json(self, sample_alert):
        """Output is JSON-serializable."""
        payload = render_ntfy(sample_alert)
        json_str = json.dumps(payload)
        assert json_str

    def test_has_required_fields(self, sample_alert):
        """Payload has title, message, priority, and tags."""
        payload = render_ntfy(sample_alert)
        assert "title" in payload
        assert "message" in payload
        assert "priority" in payload
        assert "tags" in payload
        assert payload["markdown"] is True

    def test_title_contains_signal_prefix(self, sample_alert):
        """Title starts with MA Signal prefix."""
        payload = render_ntfy(sample_alert)
        assert payload["title"].startswith("MA Signal:")

    def test_priority_maps_from_confidence(self, sample_alert):
        """Confidence level maps to ntfy priority."""
        payload = render_ntfy(sample_alert)
        assert payload["priority"] in (2, 3, 5)

    def test_high_confidence_gets_max_priority(self, sample_alert):
        """High-confidence alerts get urgent priority."""
        sample_alert.internal.confidence = "high"
        payload = render_ntfy(sample_alert)
        assert payload["priority"] == 5

    def test_click_url_set_from_source(self, sample_alert):
        """Click action links to the source URL."""
        payload = render_ntfy(sample_alert)
        assert payload["click"] == sample_alert.internal.source_url

    def test_has_view_action(self, sample_alert):
        """Payload includes a view action button."""
        payload = render_ntfy(sample_alert)
        assert len(payload["actions"]) == 1
        assert payload["actions"][0]["action"] == "view"
        assert payload["actions"][0]["url"] == sample_alert.internal.source_url

    def test_no_click_when_no_source_url(self, sample_alert):
        """No click or actions when source URL is empty."""
        sample_alert.internal.source_url = ""
        payload = render_ntfy(sample_alert)
        assert "click" not in payload
        assert "actions" not in payload

    def test_message_contains_key_sections(self, sample_alert):
        """Message body includes summary, why-it-matters, and draft."""
        payload = render_ntfy(sample_alert)
        msg = payload["message"]
        assert "**Summary:**" in msg
        assert "**Why it matters:**" in msg
        assert "**Draft insight:**" in msg

    def test_topic_included_when_provided(self, sample_alert):
        """Topic field is set when explicitly provided."""
        payload = render_ntfy(sample_alert, topic="my-alerts")
        assert payload["topic"] == "my-alerts"

    def test_topic_absent_when_not_provided(self, sample_alert):
        """Topic field is absent when not provided."""
        payload = render_ntfy(sample_alert)
        assert "topic" not in payload


class TestTeamsRenderer:
    """Test the Teams Adaptive Card renderer."""

    def test_renders_valid_json(self, sample_alert):
        """Output is JSON-serializable."""
        payload = render_teams(sample_alert)
        json_str = json.dumps(payload)
        assert json_str

    def test_has_adaptive_card_structure(self, sample_alert):
        """Output follows Teams Adaptive Card structure."""
        payload = render_teams(sample_alert)
        assert payload["type"] == "message"
        assert "attachments" in payload
        assert len(payload["attachments"]) == 1

        attachment = payload["attachments"][0]
        assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"

        card = attachment["content"]
        assert card["type"] == "AdaptiveCard"
        assert card["version"] == "1.4"
        assert "body" in card

    def test_card_body_has_content(self, sample_alert):
        """Card body contains text blocks with content."""
        payload = render_teams(sample_alert)
        card = payload["attachments"][0]["content"]
        body = card["body"]
        assert len(body) > 5  # Should have multiple sections

        # First block should be the title
        assert body[0]["type"] == "TextBlock"
        assert "MA Signal" in body[0]["text"]

    def test_card_has_action_for_source_url(self, sample_alert):
        """Card includes an OpenUrl action when source URL exists."""
        payload = render_teams(sample_alert)
        card = payload["attachments"][0]["content"]
        actions = card.get("actions", [])
        assert len(actions) >= 1
        assert actions[0]["type"] == "Action.OpenUrl"

    def test_payload_size_reasonable(self, sample_alert):
        """Teams payload is not unreasonably large."""
        payload = render_teams(sample_alert)
        json_str = json.dumps(payload)
        # Teams has a ~28KB limit for incoming webhooks
        assert len(json_str) < 20000
