# MA Signal Monitor

A free, local Medicare Advantage news insight monitor that fetches public sources, scores items for analytic relevance, and posts structured alerts to a webhook endpoint (ntfy.sh push notifications, Teams Adaptive Cards, or generic JSON).

## What It Does

On each run, the monitor:

1. **Fetches** RSS feeds from configurable healthcare/policy news sources
2. **Normalizes** items into a standard schema
3. **Deduplicates** against previously seen items (SQLite-backed)
4. **Scores** each item for Medicare Advantage relevance using keyword matching, entity detection, and source priority
5. **Classifies** relevant items into a configurable trigger taxonomy (membership movement, policy changes, financial pressure, etc.)
6. **Drafts** two-section alerts:
   - **Section A**: Internal analytic alert with signal type, entities, relevance score, and suggested follow-up checks
   - **Section B**: Draft public insight angle with opening hook, analytic angles, and a clearly-marked draft paragraph
7. **Delivers** alerts to a webhook endpoint (ntfy.sh push notifications, Teams Adaptive Card, generic JSON, or test mode)
8. **Persists** state, delivery logs, and run metadata locally

## Architecture Overview

```
RSS Feeds ──→ Fetcher ──→ Normalizer ──→ Deduplicator ──→ Scorer ──→ Classifier
                                              │                          │
                                         SQLite State              Taxonomy Config
                                              │                          │
                                              └──────── Drafter ─────────┘
                                                           │
                                                      Renderer
                                                  (ntfy/Generic/Teams)
                                                           │
                                                    Webhook Delivery
```

All processing is local. No cloud services, paid APIs, or Google dependencies.

## Quick Start

### 1. Prerequisites

- Python 3.11+
- `pip` or `uv`

### 2. Setup

```bash
# Clone the repo
git clone <repo-url> && cd ma-signal-monitor

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/macOS/WSL
# .venv\Scripts\activate   # Windows

# Install dependencies
pip install -e ".[dev]"

# Configure
cp .env.example .env
# Edit .env — at minimum set WEBHOOK_URL
```

### 3. Test with ntfy.sh (Recommended)

[ntfy.sh](https://ntfy.sh) is a free push notification service — no signup, no API keys.

1. Open `https://ntfy.sh/ma-signal-monitor` in your browser (or subscribe in the ntfy mobile app)
2. The default `.env` is already configured for ntfy.sh:
   ```ini
   WEBHOOK_URL=https://ntfy.sh/ma-signal-monitor
   WEBHOOK_MODE=ntfy
   ```
3. Run the seed script:
   ```bash
   python scripts/seed_test_data.py --deliver
   ```
4. Check the ntfy.sh topic page — you should see formatted alerts with priority levels and click-through links

> **Tip**: Use a unique, hard-to-guess topic name (e.g., `https://ntfy.sh/my-ma-monitor-abc123`) for privacy, since ntfy.sh topics are public by default.

### 4. Run Against Live Feeds

```bash
python scripts/run_once.py
# Or:
python -m ma_signal_monitor.main
```

### 5. Run Tests

```bash
pytest
pytest --cov=ma_signal_monitor
```

## Configuration

### `.env` — Environment settings

| Variable | Default | Description |
|---|---|---|
| `WEBHOOK_URL` | *(required)* | Webhook endpoint URL (e.g., `https://ntfy.sh/your-topic`) |
| `WEBHOOK_MODE` | `ntfy` | `ntfy`, `generic`, `teams`, or `test` |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `MIN_RELEVANCE_SCORE` | `0.3` | Threshold for alert generation (0.0-1.0) |

### `config/sources.yaml` — Feed sources

Add/remove/disable RSS sources. Each source has a name, URL, priority (1-5), and tags.

### `config/taxonomy.yaml` — Trigger categories

Configure the five trigger categories, their keywords, weights, and the list of watched payer entities. Also contains scoring tuning parameters.

### `config/app.yaml` — Application settings

Delivery retry behavior, processing limits, and storage retention settings.

## Scheduler Setup

### Linux/WSL (cron)

```bash
crontab -e
# Add:
0 */4 * * * cd /path/to/project && /path/to/.venv/bin/python -m ma_signal_monitor.main >> logs/cron.log 2>&1
```

### Windows (Task Scheduler)

1. Open Task Scheduler → Create Basic Task
2. Set trigger (e.g., Daily at 7 AM)
3. Action: Start `python.exe` with arguments `-m ma_signal_monitor.main`
4. Start in: project directory

See `src/ma_signal_monitor/scheduler_notes.py` for detailed examples.

## Webhook Delivery Modes

The delivery system supports four modes:

- **`ntfy`** *(recommended)*: Push notifications via [ntfy.sh](https://ntfy.sh) — free, no signup, supports mobile push, markdown, priority levels, and click-through actions.
- **`teams`**: Adaptive Card format for Microsoft Teams incoming webhooks.
- **`generic`**: Clean JSON payload for any webhook consumer.
- **`test`**: Generic JSON with extra debug logging. Use with webhook.site or RequestBin.

**Recommendation**: Start with `ntfy` mode for the fastest setup. Use `test` mode with a webhook inspector if you need to debug payloads before switching to `teams`.

## Teams Compatibility Notes

- Uses Adaptive Card schema v1.4 wrapped in the Teams message format
- The payload targets the "Incoming Webhook" connector
- Teams has a ~28KB payload size limit — alerts are designed to stay well under this
- Teams rendering can be inconsistent across clients (desktop vs. web vs. mobile)
- If cards don't render, check: webhook URL validity, payload size, and card schema version
- **Workflow webhooks** (Power Automate) use a different format than Incoming Webhooks — this tool targets Incoming Webhooks

## Limitations

- **No NLP/ML**: Scoring is keyword-based, not semantic. High-quality but not perfect.
- **RSS only (Phase 1)**: SEC EDGAR and CMS public file fetchers are stubbed for Phase 2.
- **No live Teams validation**: Teams rendering is validated structurally, not against a live endpoint (unless you provide one).
- **English only**: Keywords and content processing assume English-language sources.
- **No authentication**: RSS fetching does not support authenticated feeds.
- **Single-threaded**: Sources are fetched sequentially, not in parallel.

## Future Enhancements

- SEC EDGAR filing ingestion (Phase 2)
- CMS public data file monitoring (Phase 2)
- Semantic/NLP scoring (e.g., via local model)
- Parallel source fetching
- Web dashboard for alert review
- Configurable alert templates
- Slack webhook renderer
- Historical trend analysis

## Project Structure

```
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── config/
│   ├── sources.yaml        # Feed source configuration
│   ├── taxonomy.yaml       # Trigger categories and scoring
│   └── app.yaml            # Application settings
├── src/ma_signal_monitor/
│   ├── main.py             # Pipeline orchestrator
│   ├── config.py           # Configuration loading
│   ├── models.py           # Data models
│   ├── storage.py          # SQLite persistence
│   ├── logging_setup.py    # Logging configuration
│   ├── normalize.py        # Item normalization
│   ├── dedupe.py           # Deduplication
│   ├── scoring.py          # Relevance scoring
│   ├── classify.py         # Trigger classification
│   ├── drafting.py         # Alert generation
│   ├── delivery.py         # Webhook delivery
│   ├── fetchers/
│   │   ├── rss.py          # RSS feed fetcher
│   │   ├── sec.py          # SEC EDGAR (stub)
│   │   └── cms.py          # CMS files (stub)
│   └── renderers/
│       ├── ntfy.py             # ntfy.sh push notification renderer
│       ├── generic_webhook.py  # Generic JSON renderer
│       └── teams.py            # Teams Adaptive Card renderer
├── tests/                  # pytest test suite
├── scripts/
│   ├── run_once.py         # One-shot execution
│   └── seed_test_data.py   # Test data seeder
└── docs/                   # Documentation
```

## License

MIT
