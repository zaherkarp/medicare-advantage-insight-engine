"""Tests for webhook delivery."""

import responses

from ma_signal_monitor.delivery import deliver_alert, deliver_alerts


class TestDelivery:
    """Test webhook delivery with mocked HTTP."""

    @responses.activate
    def test_successful_delivery(self, sample_alert, sample_config):
        """Successful POST returns success result."""
        responses.add(
            responses.POST,
            sample_config.webhook_url,
            json={"ok": True},
            status=200,
        )
        result = deliver_alert(sample_alert, sample_config)
        assert result.success is True
        assert result.status_code == 200

    @responses.activate
    def test_client_error_no_retry(self, sample_alert, sample_config):
        """4xx errors are not retried."""
        responses.add(
            responses.POST,
            sample_config.webhook_url,
            json={"error": "bad request"},
            status=400,
        )
        result = deliver_alert(sample_alert, sample_config)
        assert result.success is False
        assert result.status_code == 400
        assert len(responses.calls) == 1  # No retries

    @responses.activate
    def test_server_error_retries(self, sample_alert, sample_config):
        """5xx errors trigger retries."""
        # Set fast retries for test
        sample_config.delivery_max_retries = 2
        sample_config.delivery_retry_backoff_base = 0  # No actual wait in tests

        responses.add(responses.POST, sample_config.webhook_url, status=500)
        responses.add(responses.POST, sample_config.webhook_url, status=500)
        responses.add(
            responses.POST, sample_config.webhook_url, json={"ok": True}, status=200
        )

        result = deliver_alert(sample_alert, sample_config)
        assert result.success is True
        assert len(responses.calls) == 3

    @responses.activate
    def test_all_retries_exhausted(self, sample_alert, sample_config):
        """Returns failure when all retries are exhausted."""
        sample_config.delivery_max_retries = 1
        sample_config.delivery_retry_backoff_base = 0

        responses.add(responses.POST, sample_config.webhook_url, status=500)
        responses.add(responses.POST, sample_config.webhook_url, status=500)

        result = deliver_alert(sample_alert, sample_config)
        assert result.success is False
        assert len(responses.calls) == 2

    @responses.activate
    def test_connection_error_retries(self, sample_alert, sample_config):
        """Connection errors trigger retries."""
        import requests as req_lib

        sample_config.delivery_max_retries = 1
        sample_config.delivery_retry_backoff_base = 0

        responses.add(
            responses.POST,
            sample_config.webhook_url,
            body=req_lib.ConnectionError("Connection refused"),
        )
        responses.add(
            responses.POST,
            sample_config.webhook_url,
            json={"ok": True},
            status=200,
        )

        result = deliver_alert(sample_alert, sample_config)
        assert result.success is True

    @responses.activate
    def test_deliver_multiple_alerts(self, sample_alert, sample_config):
        """Batch delivery handles multiple alerts."""
        responses.add(
            responses.POST, sample_config.webhook_url, json={"ok": True}, status=200
        )
        responses.add(
            responses.POST, sample_config.webhook_url, json={"ok": True}, status=200
        )

        results = deliver_alerts([sample_alert, sample_alert], sample_config)
        assert len(results) == 2
        assert all(r.success for r in results)

    @responses.activate
    def test_ntfy_mode_uses_ntfy_format(self, sample_alert, sample_config):
        """Ntfy mode sends ntfy.sh-compatible payload."""
        sample_config.webhook_mode = "ntfy"
        responses.add(responses.POST, sample_config.webhook_url, json={}, status=200)

        deliver_alert(sample_alert, sample_config)

        body = responses.calls[0].request.body
        import json

        payload = json.loads(body)
        assert "title" in payload
        assert "message" in payload
        assert "markdown" not in payload

    def test_test_mode_dry_run_no_url(self, sample_alert, sample_config):
        """Test mode with no URL returns success without HTTP call."""
        sample_config.webhook_mode = "test"
        sample_config.webhook_url = ""
        result = deliver_alert(sample_alert, sample_config)
        assert result.success is True
        assert result.status_code == 0

    @responses.activate
    def test_teams_mode_uses_teams_format(self, sample_alert, sample_config):
        """Teams mode sends Adaptive Card format."""
        sample_config.webhook_mode = "teams"
        responses.add(responses.POST, sample_config.webhook_url, json={}, status=200)

        deliver_alert(sample_alert, sample_config)

        body = responses.calls[0].request.body
        import json

        payload = json.loads(body)
        assert payload["type"] == "message"
        assert "attachments" in payload
