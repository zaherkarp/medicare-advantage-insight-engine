# Troubleshooting

## ntfy.sh Issues

### Notifications not appearing
- Verify you're subscribed to the correct topic: open `https://ntfy.sh/<your-topic>` in your browser
- Check that `WEBHOOK_MODE=ntfy` in your `.env`
- Check that `WEBHOOK_URL` matches the topic you subscribed to
- Run `python scripts/seed_test_data.py --deliver` and look for `status 200` in the output

### Notifications visible to others
ntfy.sh topics are **public by default**. Anyone who knows your topic name can read your alerts. Use a unique, hard-to-guess topic name (e.g., `ma-monitor-a8f3k2x9`) for production use. For private topics, consider [self-hosting ntfy](https://docs.ntfy.sh/install/) or using a different delivery mode.

### Markdown not rendering
Ensure `WEBHOOK_MODE=ntfy` (not `generic` or `test`). The ntfy renderer sets `"markdown": true` in the payload. Markdown rendering is supported in the ntfy web UI and mobile apps.

### Priority levels not working
The ntfy renderer maps alert confidence to priority: high → 5 (urgent), medium → 3 (default), low → 2. Verify in the ntfy web UI that the priority badge appears on each notification.

## Webhook Endpoint Errors

### "WEBHOOK_URL is not set"
Set `WEBHOOK_URL` in your `.env` file. For ntfy.sh: `https://ntfy.sh/your-topic-name`. For testing, use a webhook.site URL.

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

## Relevance & Noise

See the Operations guide, [Reduce noise](operations.md#tuning-scoring), for the
knobs referenced here.

### An off-topic story appears on the public site
- Confirm `ARCHIVE_MIN_SCORE` isn't set to `0.0` (that disables the display floor).
- If it comes from a broad, low-priority source, the MA-context gate only
  suppresses it when it lacks a Medicare anchor — a coincidental keyword *plus* a
  watched payer or `ma_context_terms` term will still score. Add the offending
  term to `exclusions.soft` (penalty) or `exclusions.hard` (veto) in
  `config/taxonomy.yaml`, and guard the change with a golden-set entry.
- Raising `ARCHIVE_MIN_SCORE` (e.g. to `0.15`) trims more aggressively.

### A relevant story isn't showing up
- Check its score and reasons:
  `sqlite3 data/state.db "SELECT title, relevance_score FROM stories WHERE title LIKE '%keyword%'"`.
  Below `ARCHIVE_MIN_SCORE` (default 0.1) → hidden from the site; below
  `MIN_RELEVANCE_SCORE` (default 0.3) → no alert.
- If it's from a low-priority source and names no payer or Medicare term, the
  **MA-context gate** suppressed its keyword matches. Add the missing anchor to
  `ma_context_terms`, add the payer to `watched_entities`, or lower
  `scoring.ma_context_min_priority`.
- If it near-duplicates another story, feed grouping shows only the
  representative — the rest appear on that story's page under "Also reported by".

### The public feed count differs from /status
Expected. `/status` and `/health` report the **full** archive (every scored item,
including sub-floor noise and near-duplicates); the feed, topics, states, and
search apply the display floor and duplicate grouping. The gap is exactly how
much a source contributes vs. how much reaches readers.

## Archive Restore Failures (deploy-pages.yml)

### "Aborting deliberately" / the restore step fails

This is intentional, not a bug. The published archive DB could not be
downloaded or failed validation (`scripts/archive_guard.py validate`), so the
job stopped instead of silently ingesting into an empty DB and overwriting
the real production archive on publish. See [Operations →
Archive-Restore Safety](operations.md#archive-restore-safety-deploy-pagesyml)
for the full behavior. Re-run the workflow once GitHub Pages/Actions is
healthy again — nothing was lost, since the failure happens before the
publish step ever runs.

### "CATASTROPHIC SHRINK" from `archive_guard.py compare`

The `stories` row count dropped far more than normal retention pruning
(`config/app.yaml`'s `story_retention_days`) could explain between restore
and publish. The build step refused to publish. Investigate before re-running
manually — this usually means the restored archive was already wrong, or
something upstream (a bad `--rescore`, a bug in ingestion) deleted rows.

## Silent Sources

### A source has zero archive rows and no error in the logs

Historically this was invisible: `main._fetch_one_source` isolates every
per-source failure (by design — one bad feed shouldn't stop the run), which
meant a source that raised on every request and a source that was simply
quiet both looked identical from the `stories` table alone. 16 enabled
sources went unnoticed this way for up to two months (see the iteration-11
investigation).

Check `/status` ("Silent sources") or `/sources` (each source's "silent"
badge) — these read `source_fetch_log`, a per-run record of what actually
happened on the fetch side, independent of whether anything reached the
archive. Or run `ma-signal-source-health` for a plain-text report (exits 1 if
anything is flagged). A source is flagged once `SOURCE_SILENT_DAYS` (default
7) pass with no persisted item; the reported "last status"/"last error"
tells you which of these it is:

- **`error`** — the fetch itself failed (raised, or a non-2xx response).
  Check `last_error` for the status code/message.
- **`empty`** — fetched fine (2xx), but 0 usable items. Either the feed is
  genuinely quiet right now, or its content isn't valid RSS/Atom (see
  "Feed has parsing issues and no entries" above).
- **`ok`** with a source that's still flagged — items are being fetched but
  not making it into the archive. Check the logs for "Failed to persist
  story" around that source's fetch.

### What the iteration-11 investigation found for the 16 silent sources

(13x SEC EDGAR feeds, Alliance of Community Health Plans, Congress.gov —
Bills Presented to the President — see `docs/loop.md` for the full record.)

- **The 13 SEC EDGAR feeds**: `error` — sec.gov rejects any User-Agent
  without a real contact email (403), independent of how descriptive it
  otherwise reads. Fixed by requiring `SEC_CONTACT_EMAIL` (see
  [Operations → SEC EDGAR Sources Require a Contact
  Email](operations.md#sec-edgar-sources-require-a-contact-email)).
- **Alliance of Community Health Plans**: `error` — confirmed via a real
  `scheduled-monitor.yml` run's logs: `403 Client Error: Forbidden for url:
  https://achp.org/feed/`. The same URL returns `200` with 10 parseable
  items from other networks (curl from a workstation, this repo's dev
  sandbox), and the full pipeline (fetch → normalize → score → persist)
  works end to end against those items when run locally — so this is not a
  bug in this app. It's most likely a WAF (the feed is fronted by
  Cloudflare) blocking or challenging GitHub Actions' runner IP ranges
  specifically, which is outside this codebase's control. Left enabled and
  now visible as silent, rather than fixed — there's nothing to fix here
  short of contacting ACHP or routing requests through a different network.
- **Congress.gov - Bills Presented to the President**: `empty` — the feed
  itself currently has zero `<item>` entries (verified with a plain
  `curl`). This is a legitimately bursty source (a bill only appears here
  between passing both chambers and being signed), not a bug. Left as-is.

### `ma-signal-source-health` is not wired into the deploy/scheduled workflows

Deliberate: a source silenced by an external, unfixable-from-here condition
(like ACHP above) would fail the shared deploy job on every single run
forever, training everyone to ignore red CI. The `/status` and `/sources`
pages are the primary surface — everyone already looks at those. Run
`ma-signal-source-health` by hand, or wire it into your own monitoring if
you're self-hosting and want a hard gate.

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
