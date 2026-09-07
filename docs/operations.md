# Operations Guide

## Daily Operation Model

The monitor is designed to run periodically (the repo workflows run alerts
every 3 hours and the Pages rebuild every 2 hours; self-hosters can pick any
cadence). Each run:

1. Fetches all enabled sources concurrently (`FETCH_WORKERS`, default 8)
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
0 */3 * * * cd /path/to/project && .venv/bin/python -m ma_signal_monitor.main >> logs/cron.log 2>&1
```

## State Database Location

Default: `data/state.db` (configurable via `DB_PATH` in `.env`).

The SQLite database contains:

| Table | Purpose |
|---|---|
| `seen_items` | Deduplication records |
| `delivery_log` | Webhook delivery attempts |
| `run_metadata` | Start/end times and counts per run |
| `stories` | Browsable archive of every scored item |
| `stories_fts` | FTS5 full-text index over `stories` |
| `digests` | Saved Daily Briefing digests |
| `candidate_domains` | Source-discovery domain harvest |
| `candidate_sources` | Ranked candidate feeds for review/promotion |
| `feedback` | Reader/owner verdicts (append-only, weighted) |

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

## SEC EDGAR Sources Require a Contact Email

The 13 "SEC EDGAR - `<payer>`" sources in `config/sources.yaml` hit
`sec.gov`, which **rejects any User-Agent that lacks a real contact email —
403, every time**, regardless of how descriptive the UA otherwise reads (this
is documented at
[sec.gov/os/webmaster-faq#developers](https://www.sec.gov/os/webmaster-faq#developers)
and was verified against the live endpoint: a UA like
`"MA Signal Monitor Research Project"` 403s, `"MA Signal Monitor
you@example.com"` gets a 200). The app fails config loading with a clear
`ValueError` if any SEC source is enabled and `SEC_CONTACT_EMAIL` isn't set —
better a loud startup failure than another silent, months-long 403.

Set it via `SEC_CONTACT_EMAIL` (see `.env.example`). Because this repo is
public, **do not commit a real address** — configure it as a **GitHub Actions
secret** (not a Variable, so it's masked in logs if ever echoed):

1. Repo → **Settings → Secrets and variables → Actions → Secrets → New
   repository secret**
2. Name: `SEC_CONTACT_EMAIL`, value: an email you control
3. Both `deploy-pages.yml` and `scheduled-monitor.yml` already read
   `secrets.SEC_CONTACT_EMAIL` into the environment for you

Note: `*.github.com` noreply addresses (e.g.
`123+you@users.noreply.github.com`) are themselves rejected by sec.gov — use
a normal inbox.

If you don't want to expose any contact address, disable the SEC sources
instead (`enabled: false` in `sources.yaml`) rather than leaving
`SEC_CONTACT_EMAIL` unset, since that now hard-fails the run.

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

### Reduce noise (the MA-context gate and display floor)

Two independent controls keep off-topic items off the public site without losing
genuine signals. Both are on by default.

**Source-aware MA-context gate.** For sources below
`scoring.ma_context_min_priority` (default `3`), a taxonomy-keyword match only
counts if the item *also* names a watched payer or one of the `ma_context_terms`
anchors (Medicare, Part C/D, D-SNP, CMS, …). This stops broad, low-priority
feeds (e.g. the statewide newsrooms) from scoring a generic keyword brush
(`premium`, `network`) as an MA signal. Dedicated MA sources (priority 3–5) are
never gated. Set the threshold to `0` to disable.

```yaml
# config/taxonomy.yaml
ma_context_terms:             # anchors that satisfy the gate
  - "Medicare"
  - "Medicare Advantage"
  - "D-SNP"
  # …
scoring:
  ma_context_min_priority: 3  # gate sources below this priority; 0 = off
```

**Core-MA-term boost.** Strong MA vocabulary earns a one-time
`scoring.ma_term_boost` (default `0.15`), so a clear MA story scores as a signal
even when no category keyword happens to match. Its list is `ma_boost_terms`,
deliberately narrower than `ma_context_terms` — bare "Medicare"/"CMS" establishes
context but isn't by itself an MA-market signal.

**Archive display floor.** `ARCHIVE_MIN_SCORE` (`.env`, or
`processing.archive_min_score` in `config/app.yaml`; default `0.1`) hides
anything still scoring as pure source-priority noise from the public surfaces
(feed, topics, states, search, static site) while keeping it in the archive.
`/status` and `/health` always report the full, unfiltered archive — that's where
you gauge a source's yield. Set to `0.0` to surface everything.

```ini
# .env
ARCHIVE_MIN_SCORE=0.1   # public display floor; 0.0 disables
```

## Monitoring Run Health

Check the last few runs:

```bash
sqlite3 data/state.db "SELECT * FROM run_metadata ORDER BY id DESC LIMIT 5"
```

Check delivery success rate:

```bash
sqlite3 data/state.db "SELECT success, COUNT(*) FROM delivery_log GROUP BY success"
```

Check for silently broken sources — an enabled source with no archived item
in `SOURCE_SILENT_DAYS` (default 7):

```bash
ma-signal-source-health   # plain-text report; exits 1 if anything is flagged
```

Same data is on `/status` ("Silent sources") and `/sources` (each source's
"silent" badge) — see [Troubleshooting → Silent
Sources](troubleshooting.md#silent-sources) for what each status means and
how to read the "last error".

## Storage Cleanup

Automatic cleanup runs at the end of each pipeline execution. Retention periods are configured in `config/app.yaml`:

```yaml
storage:
  seen_item_retention_days: 90
  delivery_log_retention_days: 30
```

## Archive-Restore Safety (`deploy-pages.yml`)

The production `stories` archive lives only inside a GitHub Actions cache
entry — each `deploy-pages.yml` run restores it, ingests into it, and saves
it back under a fresh cache key. **It is not published inside the site.**
That was tried first (`cp data/state.db site/data/state.db`, so the next
run's restore was a plain unauthenticated `curl` against the published URL)
and dropped as a security fix: it made the whole archive — including
`source_fetch_log` and `delivery_log`, an unreviewed operational dump, not
just the story content already shown on the site — a public download,
republished every two hours to anyone who found the URL, with no review step
in between. `actions/cache` gives the same across-run persistence, private to
this repo, with no publish step to accidentally widen later.

The restore step tells a genuine cold start apart from a failed restore
deliberately, the same distinction the old HTTP probe-then-download dance
existed to make, now for a different transport:

- **Genuine cold start** (`actions/cache/restore`'s `cache-matched-key`
  output comes back empty — no `state-db-*` entry exists at all, whether
  because this is the first run ever or every prior entry aged out): proceeds
  with no DB, as before. This is the only case that's allowed to be silent,
  since `compare` can't catch a false positive either way — a cold start has
  no prior row count to compare against.
- **A matched cache entry fails validation** (`scripts/archive_guard.py
  validate` — integrity check + core-table check): the job **fails on
  purpose** rather than continue. A transport-level failure (corrupt or
  truncated download) already fails the restore step itself before this
  check ever runs; this check is the second layer, catching a bad *save*
  from a prior run rather than a bad transfer. Proceeding either way is
  exactly what destroys the archive.
- As a second checkpoint, the "Build static site" step still runs
  `archive_guard.py compare` before the save step persists the DB forward,
  and fails the job — which also skips the save step — instead of persisting
  if the row count dropped catastrophically.

If a run fails at either checkpoint, no data was lost — the cache entry from
the last successful run is untouched (a failed run never reaches its own save
step). Just re-run the workflow (`workflow_dispatch` or wait for the next
schedule); the next successful restore picks up right where the archive left
off.

Need a copy of the production DB for local analysis (calibration, evals —
see `scripts/calibrate_threads.py` and `evals/relevance/README.md`)? It's no
longer a plain `curl`. Add a temporary `actions/upload-artifact` step to a
`deploy-pages.yml` run that uploads `data/state.db`, download the resulting
run artifact, then remove the step.
