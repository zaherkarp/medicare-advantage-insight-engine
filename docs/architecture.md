# Architecture

## Component Flow

```
┌─────────────┐     ┌────────────┐     ┌───────────┐     ┌─────────┐
│   Sources    │────→│  Fetchers  │────→│ Normalizer│────→│  Dedup  │
│ (RSS feeds)  │     │ (rss.py)   │     │           │     │         │
└─────────────┘     └────────────┘     └───────────┘     └────┬────┘
                                                               │
                                                          ┌────▼────┐
                                                          │  Scorer │
                                                          │         │
                                                          └────┬────┘
                                                               │
                                                          ┌────▼──────┐
                                                          │ Classifier│
                                                          │           │
                                                          └────┬──────┘
                                                               │
                                                          ┌────▼────┐
                                                          │ Drafter │
                                                          │         │
                                                          └────┬────┘
                                                               │
                                                          ┌────▼─────┐
                                                          │ Renderer │
                                                          │(generic/ │
                                                          │  teams)  │
                                                          └────┬─────┘
                                                               │
                                                          ┌────▼─────┐
                                                          │ Delivery │
                                                          │(webhook) │
                                                          └────┬─────┘
                                                               │
                                                          ┌────▼─────┐
                                                          │  Storage │
                                                          │ (SQLite) │
                                                          └──────────┘
```

## Source Ingestion

**Current (Phase 1)**: RSS feeds via `feedparser`. Each source is configured in `config/sources.yaml` with a name, URL, priority (1-5), and tags. The RSS fetcher uses `requests` for HTTP and `feedparser` for parsing, with HTML stripping for summaries.

**Phase 2 (stubbed)**: SEC EDGAR and CMS public file fetchers. The fetcher interface is standardized: `fetch_*(source, timeout, user_agent, max_items) -> list[RawFeedItem]`, so adding new source types requires only implementing this function and registering it in the dispatcher.

**Error handling**: Each source is fetched independently. A failure in one source (network error, parse error) is logged and skipped — the pipeline continues with the remaining sources.

## Normalization

`normalize.py` converts `RawFeedItem` → `NormalizedItem`:

- Generates a stable `item_id` from a SHA-256 hash of `source_name + link`
- Parses dates from multiple formats (RFC 2822, ISO 8601, common variants)
- Strips HTML from summaries
- Collapses whitespace
- Truncates summaries to configurable length

## Scoring

`scoring.py` implements a transparent, explainable relevance model:

1. **Keyword matching**: For each taxonomy category, checks if category keywords appear in the title or summary. Title matches are weighted higher (1.5x by default).
2. **Source priority**: Higher-priority sources (e.g., CMS newsroom = 5) contribute more.
3. **Entity detection**: Named payer entities from the watch list boost the score.
4. **Multi-category bonus**: Items matching 2+ categories get an additional boost.

The score is clamped to [0.0, 1.0] and returned with a list of `ScoringReason` objects explaining each contribution. This makes the scoring explainable and auditable.

**Design choice**: Keyword-based scoring was chosen over NLP/ML for simplicity, transparency, and zero external dependencies. It's effective for domain-specific monitoring where the vocabulary is well-defined. A future phase could layer semantic scoring on top.

## Classification

`classify.py` selects the primary trigger category from matched categories, preferring the one with the highest taxonomy weight. The taxonomy has five categories:

1. Membership Movement
2. Demographic Shifts
3. Policy / Regulatory Changes
4. Financial / Operating Pressure
5. Competitive / Operational Strategy

## Rendering and Delivery

Delivery is abstracted behind a mode selector:

- `delivery.py` dispatches to the appropriate renderer based on `WEBHOOK_MODE`
- `renderers/generic_webhook.py` produces clean JSON (for webhook inspectors and generic consumers)
- `renderers/teams.py` produces a Teams Adaptive Card (v1.4) wrapped in the Teams message format

**Why the abstraction**: Teams compatibility is fragile and may change. By keeping rendering separate from delivery, we can swap formats without touching the delivery retry logic, and test with generic webhooks before switching to Teams.

**Retry logic**: Exponential backoff for transient (5xx/connection) errors. 4xx errors fail immediately without retry.

## State Management

SQLite (`storage.py`) provides three tables:

- `seen_items`: Deduplication records (item_id, source, title, link, timestamp)
- `delivery_log`: Record of every delivery attempt (success/failure, status code, error)
- `run_metadata`: Start/end times and counts for each pipeline run

**Why SQLite**: Durable, zero-config, single-file, works on all platforms. No server needed. Retention-based cleanup prevents unbounded growth.

## Key Design Choices

| Choice | Rationale |
|---|---|
| SQLite for state | Zero-config, durable, portable, no server |
| Keyword scoring (not ML) | Transparent, explainable, no dependencies, effective for domain vocabulary |
| feedparser + requests | Mature, well-tested, minimal footprint |
| Adaptive Cards (not MessageCard) | Adaptive Cards are the current Teams standard; MessageCard is legacy |
| Dataclasses (not Pydantic) | Sufficient for this use case; avoids extra dependency |
| Sequential fetching | Simpler to reason about, debug, and log; parallelism is a future enhancement |
| Per-source error isolation | One bad feed shouldn't block the entire run |
