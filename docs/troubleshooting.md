# Troubleshooting

## Webhook Endpoint Errors

### "WEBHOOK_URL is not set"
Set `WEBHOOK_URL` in your `.env` file. For testing, use a webhook.site URL.

### HTTP 400/403 from webhook endpoint
- **400 Bad Request**: The payload format may not match what the endpoint expects. If using Teams, ensure `WEBHOOK_MODE=teams`. Try sending to webhook.site first with `WEBHOOK_MODE=test` to inspect the payload.
- **403 Forbidden**: The webhook URL may be expired or revoked. Generate a new one.

### HTTP 500 from webhook endpoint
Server-side issue. The tool will retry automatically (up to 3 times with backoff). If it persists, the endpoint may be down.

### Connection refused / timeout
- Check that `WEBHOOK_URL` is correct and accessible from your network
- Check for proxy/firewall restrictions
- Verify the endpoint is online (try opening it in a browser)

## Malformed Payloads

### Teams card doesn't render
- Verify `WEBHOOK_MODE=teams` in `.env`
- Send the same alert to webhook.site (`WEBHOOK_MODE=test`) and inspect the JSON structure
- Check that the Adaptive Card `version` is `"1.4"` and `$schema` is present
- Teams has a ~28KB payload limit — check payload size
- The Incoming Webhook connector may be disabled in your tenant's admin settings

### JSON parsing error at endpoint
Ensure the endpoint accepts `Content-Type: application/json`. The tool sets this header automatically.

### Payload missing fields
Check the log output. If normalization failed for an item, some fields may be empty. Look for `WARNING` entries in the log.

## Duplicate Posts

### Same alert posted twice
This shouldn't happen if the state database is intact. Check:
1. Is `data/state.db` present and not corrupted?
2. Did the database get deleted between runs?
3. Did the source URL change (causing a different item_id hash)?

### Items reappear after being marked seen
- Check if the `data/state.db` file was deleted or reset
- If older than 90 days, items are cleaned up — reduce `seen_item_retention_days` to keep them longer or increase to allow them to resurface

## Broken Feeds

### "Failed to fetch [source]: Connection error"
The feed URL may be down, changed, or blocked. Check the URL manually in a browser. If the feed has moved, update `config/sources.yaml`.

### "Feed has parsing issues and no entries"
The feed content is not valid RSS/Atom. This can happen if the server returns HTML (e.g., a paywall or error page) instead of XML. Check the URL in a browser.

### One feed fails but others work
This is expected behavior — the pipeline continues with remaining sources. Check the logs for the specific error and fix that source's configuration.

### All feeds return zero items
- Check network connectivity
- Verify feed URLs are still valid
- Check if a proxy or firewall is blocking outbound HTTP
- Try: `python -c "import requests; print(requests.get('https://www.cms.gov/newsroom/rss').status_code)"`

## Missing Config

### "Sources config not found: config/sources.yaml"
Ensure the `config/` directory exists with `sources.yaml`. If running from a different directory, set `CONFIG_DIR` in `.env` or pass `--project-root`.

### "No enabled sources found"
All sources in `sources.yaml` have `enabled: false`. Enable at least one.

### "No taxonomy categories found"
`config/taxonomy.yaml` is missing or has no `categories` section.

## Teams Rendering Weirdness

### Card renders on desktop but not mobile
Teams mobile has more limited Adaptive Card support. The card should still render, but some formatting may differ. This is a Teams limitation.

### Emojis don't show in confidence indicator
The Teams renderer uses Unicode emojis (🟢🟡🔴) for confidence. If these don't render, it's a font/encoding issue on the Teams client.

### Card is too tall / too much content
Lower `max_summary_length` in `config/app.yaml` or increase `MIN_RELEVANCE_SCORE` to reduce the number of alerts.

### "Action.OpenUrl" button doesn't work
Verify the source URL is a valid, publicly accessible URL. Some Teams environments restrict external URL access.

### Switching from Incoming Webhook to Workflow
Microsoft is migrating from Incoming Webhooks to Power Automate Workflows. The payload format differs. If you're using a Workflow webhook, the Teams renderer will need to be adapted. As a workaround, use `WEBHOOK_MODE=generic` and configure the Workflow to parse the generic JSON payload.

## General

### "ModuleNotFoundError: No module named 'ma_signal_monitor'"
Ensure you installed the package: `pip install -e ".[dev]"` from the project root.

### Logs not appearing
- Check `LOG_LEVEL` in `.env` (set to `DEBUG` for verbose output)
- Logs go to stderr and `logs/ma_signal_monitor.log`
- For cron, redirect both stdout and stderr: `>> logs/cron.log 2>&1`

### Database locked error
SQLite doesn't support concurrent writes. Ensure only one instance of the monitor runs at a time. If using cron, add a lock:
```bash
flock -n /tmp/ma_signal_monitor.lock python -m ma_signal_monitor.main
```
