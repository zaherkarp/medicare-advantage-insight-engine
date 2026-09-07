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
                                                          │(ntfy/gen/│
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

RSS/Atom feeds via `feedparser`. Each source is configured in `config/sources.yaml` with a name, URL, type (`rss`, `sec`, `cms`, or `litigation`), priority (1-5), and tags. The fetcher uses `requests` for HTTP and `feedparser` for parsing, with HTML stripping for summaries.

**SEC EDGAR, CMS, and litigation fetchers are live**: `fetchers/sec.py` consumes SEC EDGAR Atom feeds (8-K filings for watched payers/brokerages), `fetchers/cms.py` consumes CMS newsroom/bulletin feeds, and `fetchers/litigation.py` consumes the Georgetown Health Care Litigation Tracker's per-issue feeds. All delegate to the shared feed fetcher. The litigation fetcher additionally injects the source's `context` field into each item's summary — those feeds carry the topic only in the feed title, so this makes the guaranteed Medicare/MA context visible to scoring. The fetcher interface is standardized: `fetch_*(source, timeout, user_agent, max_items) -> list[RawFeedItem]`, so adding new source types requires only implementing this function and registering it in the dispatcher.

**Error handling**: Each source is fetched independently. A failure in one source (network error, parse error) is logged and skipped — the pipeline continues with the remaining sources.

## Normalization

`normalize.py` converts `RawFeedItem` → `NormalizedItem`:

- Generates a stable `item_id` from a SHA-256 hash of `source_name + link`
- Parses dates from multiple formats (RFC 2822, ISO 8601, common variants)
- Strips HTML from summaries
- Collapses whitespace
- Truncates summaries to configurable length
- Drops items with no title and no summary (a malformed feed can emit empty entries — there is nothing to score or display)

## Scoring

`scoring.py` implements a transparent, explainable relevance model:

1. **Keyword matching**: For each taxonomy category, checks if category keywords appear in the title or summary. Title matches are weighted higher (1.5x by default).
2. **Source priority**: Higher-priority sources (e.g., CMS newsroom = 5) contribute more.
3. **Entity detection**: Named payer entities from the watch list boost the score.
4. **Core-MA-term boost**: Strong MA vocabulary (`ma_boost_terms` — "Medicare Advantage", "D-SNP", "Part C", …) adds a one-time boost, since it is direct relevance evidence even when no category keyword matches.
5. **Multi-category bonus**: Items matching 2+ categories get an additional boost.
6. **Exclusions**: soft-exclusion terms each subtract a penalty; a hard-exclusion term vetoes the item to 0 (still archived, with the reason).
7. **Source-aware Medicare-context gate**: for sources below `scoring.ma_context_min_priority`, keyword matches only count once the item carries real MA context (a watched payer or an `ma_context_terms` anchor) — this keeps broad, low-priority feeds from scoring generic keyword brushes as MA signal.

The score is clamped to [0.0, 1.0] and returned with a list of `ScoringReason` objects explaining each contribution. This makes the scoring explainable and auditable.

**Design choice**: Keyword-based scoring was chosen over NLP/ML for simplicity, transparency, and zero external dependencies. It's effective for domain-specific monitoring where the vocabulary is well-defined. A future phase could layer semantic scoring on top.

## Classification

`classify.py` selects the primary trigger category from matched categories, preferring the one with the highest taxonomy weight. The taxonomy has six categories:

1. Membership Movement
2. Demographic Shifts
3. Policy / Regulatory Changes
4. Financial / Operating Pressure
5. Competitive / Operational Strategy
6. Brokerage / Distribution

## Drafting

`drafting.py` turns each scored + classified item into the two-section `Alert`: an **internal analytic alert** (signal type, source, entities, a factual "why it matters", suggested checks, confidence, scoring reasons) and a **draft public insight** (opening hook, analytic angles, a `[DRAFT — verify before any external use]` paragraph, an uncertainty caution, and hashtags). All text is deterministic string assembly grounded in the item's own facts — the named payer(s), the primary category, the source, and the top scoring reasons — modeled on the fact-derived voice of `angles.py`: no speculative "this may signal…" framing and no significance inflation. Analytic angles are concrete follow-ups (a specific dataset or filing to check per category), not conjecture. The `[DRAFT]` marker and the uncertainty caution are retained by design — the public draft is a starting point for an analyst to edit, never finished copy.

## Angles (lens intersections weighted by a causal model)

`angles.py` builds the `/angles` view ("ways of looking at the week's signals") from two adjacent story windows. Cards form at the intersections of lenses already present on every story — payer × topic, topic × topic (the full `categories` list, not just the primary), topic × state, and payer × payer co-mentions — with a minimum of 2 stories per card and fact-derived text (count, distinct sources, momentum vs. the prior window, strongest headline).

Ranking is differential along a **declared causal model** (`config/causal_model.yaml`): four ordered layers (Structural & Policy Drivers → Economic Pressure → Strategic Response → Market Outcomes) and downstream-only weighted edges, each carrying a one-sentence citable `evidence` rationale. A topic-pair on an edge becomes a *causal chain* card; a payer with current signals on both sides of an edge becomes a *payer cascade*. `rank_score = count × (1 + boost × edge_weight)` with boosts 0.5 (chain) / 0.75 (cascade), so causality re-ranks without steamrolling volume; non-edge overlaps still appear, ranked by volume alone. Config load validates soundness (unknown/self/upstream/duplicate edges, weight range, blank evidence); full taxonomy coverage is test-enforced (`tests/test_causal_model.py`). Greedy subset suppression removes cards whose story set adds nothing over a higher-ranked card, and a single-lens topic fallback keeps sparse archives readable. The window fetch is an uncapped lean facet query (`get_recent_story_facets`), so counts and momentum are exact.

## Daily Briefing and the synthesis lede

`digest.py` assembles the Daily Briefing — the top windowed stories grouped into topic sections — for the `/briefing` page and the optional email. Above that per-story list sits a **synthesis lede** (`synthesis.py`): a deterministic, higher-altitude "what's happening" read of the same window. Like `angles.py` it is a pure function over two adjacent facet windows (`get_recent_story_facets`, the current window and the same-length prior one), reporting the window's volume and momentum, the leading topic, the most-named payers, and a topic breakdown.

The lede is **calendar-aware**. `ma_calendar.py` models the approximate Medicare cycle windows (AEP, OEP, the Advance Notice / Final Rate / bid cycle, Star Ratings) and, for each, the taxonomy categories whose elevated volume is *seasonal*. When the window falls inside a cycle the lede frames those categories as seasonal ("AEP is underway … treat it as expected, not a step-change") and flags only genuinely off-cycle categories — so a routine enrollment-season flurry reads as routine, not as a step-change. Off-season it points at the next milestone instead. The dates are approximate framing aids, not compliance dates. The lede introduces no schema change and no scoring change, and is disabled with `DIGEST_LEDE_ENABLED=false`.

## Rendering and Delivery

Delivery is abstracted behind a mode selector:

- `delivery.py` dispatches to the appropriate renderer based on `WEBHOOK_MODE`
- `renderers/ntfy.py` produces ntfy.sh-compatible JSON with markdown formatting, priority levels, emoji tags, and click-through actions (recommended — free, no signup)
- `renderers/generic_webhook.py` produces clean JSON (for webhook inspectors and generic consumers)
- `renderers/teams.py` produces a Teams Adaptive Card (v1.4) wrapped in the Teams message format

**Why the abstraction**: Endpoint compatibility varies (Teams is particularly fragile). By keeping rendering separate from delivery, we can swap formats without touching the delivery retry logic, and test with generic webhooks before switching to Teams.

**ntfy.sh payload features**:
- Markdown-formatted message body with both alert sections
- Priority mapping: high confidence → priority 5 (urgent), medium → 3 (default), low → 2
- Emoji tags for visual indicators (rotating_light, warning, chart_with_upwards_trend)
- Click-through URL to original source article
- "View Source" action button

**Retry logic**: Exponential backoff for transient (5xx/connection) errors. 4xx errors fail immediately without retry.

## State Management

SQLite (`storage.py`) provides eight tables plus a full-text index:

- `seen_items`: Deduplication records (item_id, source, title, link, timestamp)
- `delivery_log`: Record of every delivery attempt (success/failure, status code, error)
- `run_metadata`: Start/end times and counts for each pipeline run
- `stories`: The browsable archive — every scored item, with score, reasons, category, entities, states, and `duplicate_of` (the representative it near-duplicates, for feed grouping)
- `stories_fts`: FTS5 full-text index over the archive (powers `/search`)
- `digests`: Saved Daily Briefing digests
- `candidate_domains` / `candidate_sources`: Source-discovery harvest and ranked feed candidates
- `feedback`: Reader/owner verdicts (append-only, weighted)

**Why SQLite**: Durable, zero-config, single-file, works on all platforms. No server needed. Retention-based cleanup prevents unbounded growth.

**Schema evolution**: new tables are `CREATE TABLE IF NOT EXISTS` in `SCHEMA_SQL`. Adding a *column* to an existing table (the production DB is carried forward across CI runs — see [operations.md](operations.md#archive-restore-safety-deploy-pagesyml)) needs a guarded, idempotent `ALTER TABLE` — `_ensure_column` checks `PRAGMA table_info` before adding, run from `_migrate` in the store constructor. `duplicate_of` was the first such migration.

## Key Design Choices

| Choice | Rationale |
|---|---|
| SQLite for state | Zero-config, durable, portable, no server |
| ntfy.sh as default delivery | Free, no signup, mobile push, markdown — lowest friction for setup |
| Keyword scoring (not ML) | Transparent, explainable, no dependencies, effective for domain vocabulary |
| feedparser + requests | Mature, well-tested, minimal footprint |
| Adaptive Cards (not MessageCard) | Adaptive Cards are the current Teams standard; MessageCard is legacy |
| Dataclasses (not Pydantic) | Sufficient for this use case; avoids extra dependency |
| Delivery abstraction | Endpoint compatibility varies; abstraction allows easy format swaps |
| Thread-pool fetching | Sources are fetched concurrently (`FETCH_WORKERS`, default 8) with per-source error isolation and config-order results; set `FETCH_WORKERS=1` for strictly sequential fetching |
| Per-source error isolation | One bad feed shouldn't block the entire run |
| Declared causal model (not inference) | Layers/edges with citable evidence in `config/causal_model.yaml`; transparent differential ranking, soundness validated at load, coverage test-enforced |
