# Operations Guide

## Daily Operation Model

The monitor is designed to run periodically (e.g., every 4 hours or daily). Each run:

1. Fetches all enabled RSS sources
2. Normalizes and deduplicates items
3. Scores and classifies new items
4. Delivers alerts for items above the relevance threshold
5. Records state and logs

A typical run takes 10-60 seconds depending on the number of sources and network speed.

## Log Location

Logs are written to:

- **stderr** (always)
- **`logs/ma_signal_monitor.log`** (when run via the main entry point)

Log level is controlled by `LOG_LEVEL` in `.env` (default: `INFO`).

For cron, redirect output:
```bash
0 */4 * * * cd /path/to/project && .venv/bin/python -m ma_signal_monitor.main >> logs/cron.log 2>&1
```

## State Database Location

Default: `data/state.db` (configurable via `DB_PATH` in `.env`).

The SQLite database contains:

| Table | Purpose |
|---|---|
| `seen_items` | Deduplication records |
| `delivery_log` | Webhook delivery attempts |
| `run_metadata` | Start/end times and counts per run |

## Delivery Modes

The monitor supports four delivery modes via `WEBHOOK_MODE` in `.env`:

| Mode | Endpoint | Use Case |
|---|---|---|
| `ntfy` *(recommended)* | `https://ntfy.sh/your-topic` | Free push notifications — mobile + web, no signup |
| `teams` | Teams incoming webhook URL | Microsoft Teams Adaptive Cards |
| `generic` | Any HTTP endpoint | Raw JSON for custom integrations |
| `test` | Any HTTP endpoint or webhook.site | Debug logging + payload inspection |

## Retry Behavior

Webhook delivery retries on transient failures:

- **5xx errors**: Retried up to `max_retries` (default: 3) with exponential backoff
- **Connection errors/timeouts**: Same retry behavior
- **4xx errors**: Not retried (indicates a client-side problem)
- **Backoff**: `base * 2^attempt` seconds (default base: 2, so waits are 2s, 4s, 8s)

## How Deduplication Works

Each item gets a stable ID from `SHA-256(source_name + link)`. Before processing, items are checked against the `seen_items` table. Only items not previously seen proceed through scoring/delivery.

Items are marked seen **after** processing, so a failed run won't permanently skip items.

Seen records are retained for 90 days (configurable in `config/app.yaml`), then cleaned up automatically.

## Safely Rerunning

Rerunning is safe:

- Items already seen will be skipped (deduplication)
- The same alert won't be delivered twice
- State is only updated after successful processing

To force reprocessing of previously seen items:

```bash
# Option 1: Delete the database (loses all history)
rm data/state.db

# Option 2: Delete specific items from seen_items table
sqlite3 data/state.db "DELETE FROM seen_items WHERE source_name = 'Some Feed'"
```

## Adding a Source

1. Edit `config/sources.yaml`
2. Add a new entry:
   ```yaml
   - name: "New Source Name"
     type: rss
     url: "https://example.com/feed.xml"
     priority: 3  # 1-5, higher = more important
     enabled: true
     tags: ["custom"]
   ```
3. Run once to verify: `python scripts/run_once.py`

## Removing/Disabling a Source

Set `enabled: false` in `config/sources.yaml`. The source will be skipped on the next run. Previously seen items from that source remain in the database.

## Tuning Scoring

### Adjust the relevance threshold

In `.env`:
```ini
MIN_RELEVANCE_SCORE=0.2  # Lower = more alerts, higher = fewer alerts
```

### Adjust keyword weights

In `config/taxonomy.yaml` under `scoring:`:
```yaml
scoring:
  keyword_match_base: 0.15      # Base score per keyword match
  entity_match_boost: 0.20      # Bonus for named entity detection
  source_priority_weight: 0.10  # How much source priority matters
  multi_category_boost: 0.10    # Bonus for cross-category matches
  title_keyword_multiplier: 1.5 # Title matches weighted this much more
```

### Add keywords to categories

Add keywords to any category in `config/taxonomy.yaml`:
```yaml
categories:
  membership_movement:
    keywords:
      - "enrollment"
      - "your_new_keyword"
```

### Add watched entities

Add payer/organization names to `watched_entities` in `config/taxonomy.yaml`.

## Monitoring Run Health

Check the last few runs:

```bash
sqlite3 data/state.db "SELECT * FROM run_metadata ORDER BY id DESC LIMIT 5"
```

Check delivery success rate:

```bash
sqlite3 data/state.db "SELECT success, COUNT(*) FROM delivery_log GROUP BY success"
```

## Storage Cleanup

Automatic cleanup runs at the end of each pipeline execution. Retention periods are configured in `config/app.yaml`:

```yaml
storage:
  seen_item_retention_days: 90
  delivery_log_retention_days: 30
```
