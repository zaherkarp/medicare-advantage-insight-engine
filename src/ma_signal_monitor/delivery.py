"""Webhook delivery with retry logic and format abstraction.

Supports four modes:
- "ntfy": Posts to ntfy.sh with markdown, priority, and click actions
- "generic": Posts plain JSON to any webhook endpoint
- "teams": Posts Teams-formatted Adaptive Card payload
- "test": Same as generic, but logs extra debug info
"""

import json
import logging
import time

import requests

from ma_signal_monitor.config import AppConfig
from ma_signal_monitor.models import Alert, DeliveryResult
from ma_signal_monitor.renderers.generic_webhook import render_generic
from ma_signal_monitor.renderers.ntfy import render_ntfy
from ma_signal_monitor.renderers.teams import render_teams

logger = logging.getLogger("ma_signal_monitor.delivery")


def _render_payload(alert: Alert, mode: str) -> dict:
    """Render an alert into the appropriate payload format.

    Args:
        alert: The alert to render.
        mode: Webhook mode ("generic", "teams", "test").

    Returns:
        Dictionary payload for JSON serialization.
    """
    if mode == "ntfy":
        return render_ntfy(alert)
    if mode == "teams":
        return render_teams(alert)
    # "generic" and "test" both use the generic renderer
    return render_generic(alert)


def deliver_alert(alert: Alert, config: AppConfig) -> DeliveryResult:
    """Deliver a single alert to the configured webhook endpoint.

    Implements retry with exponential backoff on transient failures.

    Args:
        alert: The alert to deliver.
        config: Application configuration.

    Returns:
        DeliveryResult indicating success or failure.
    """
    url = config.webhook_url
    mode = config.webhook_mode
    max_retries = config.delivery_max_retries
    backoff_base = config.delivery_retry_backoff_base
    timeout = config.delivery_timeout

    payload = _render_payload(alert, mode)

    if mode == "test":
        logger.info(
            "TEST MODE delivery to %s\nPayload preview:\n%s",
            url, json.dumps(payload, indent=2)[:500],
        )

    last_error = None
    last_status = None

    for attempt in range(max_retries + 1):
        try:
            response = requests.post(
                url,
                json=payload,
                timeout=timeout,
                headers={"Content-Type": "application/json"},
            )
            last_status = response.status_code

            if response.status_code in (200, 201, 202):
                logger.info(
                    "Delivered alert '%s' to %s (status %d)",
                    alert.internal.title[:60], url, response.status_code,
                )
                return DeliveryResult(
                    alert_title=alert.internal.title,
                    success=True,
                    status_code=response.status_code,
                )

            # Non-retryable client errors
            if 400 <= response.status_code < 500:
                error_msg = (
                    f"Client error {response.status_code}: {response.text[:200]}"
                )
                logger.error(
                    "Non-retryable delivery failure for '%s': %s",
                    alert.internal.title[:60], error_msg,
                )
                return DeliveryResult(
                    alert_title=alert.internal.title,
                    success=False,
                    status_code=response.status_code,
                    error=error_msg,
                )

            # Server error - retryable
            last_error = f"Server error {response.status_code}: {response.text[:200]}"

        except requests.Timeout:
            last_error = f"Request timed out after {timeout}s"
        except requests.ConnectionError as e:
            last_error = f"Connection error: {e}"
        except requests.RequestException as e:
            last_error = f"Request error: {e}"

        if attempt < max_retries:
            wait = backoff_base * (2 ** attempt)
            logger.warning(
                "Delivery attempt %d/%d failed for '%s': %s. Retrying in %ds...",
                attempt + 1, max_retries + 1,
                alert.internal.title[:60], last_error, wait,
            )
            time.sleep(wait)

    logger.error(
        "Delivery failed after %d attempts for '%s': %s",
        max_retries + 1, alert.internal.title[:60], last_error,
    )
    return DeliveryResult(
        alert_title=alert.internal.title,
        success=False,
        status_code=last_status,
        error=last_error,
    )


def deliver_alerts(alerts: list[Alert], config: AppConfig) -> list[DeliveryResult]:
    """Deliver multiple alerts to the configured webhook endpoint.

    Args:
        alerts: List of alerts to deliver.
        config: Application configuration.

    Returns:
        List of DeliveryResult objects.
    """
    results = []
    for alert in alerts:
        result = deliver_alert(alert, config)
        results.append(result)

    success_count = sum(1 for r in results if r.success)
    logger.info(
        "Delivery complete: %d/%d alerts delivered successfully",
        success_count, len(results),
    )
    return results
