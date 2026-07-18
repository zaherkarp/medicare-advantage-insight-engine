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
8. **Persists** every scored item into a browsable **story archive**, plus state, delivery logs, and run metadata locally

A bundled **web frontend** then renders that archive as a browsable, paginated
site with a streamlined navigation: the feed carries a tag-style **filter bar**
(topic verticals, states, and payers are filters over one archive, not separate
sections), alongside the Daily Briefing, an **Angles** page (ways of looking at
the week's signals — cards form where analytical lenses overlap), and a System
menu holding the Sources directory, discovery candidates, and status dashboard.
See [Web Frontend & Self-Hosting](#web-frontend--self-hosting).

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

## How This Works

This section explains the mechanics end to end — most importantly, **how an
item earns a relevance score and how noise is kept out of the public site**.

### The pipeline

Each run walks one linear pipeline (`main.run`), and one bad feed never stops
the run:

1. **Fetch** — every enabled source in `config/sources.yaml` is pulled (RSS,
   plus SEC EDGAR, CMS, and litigation-tracker fetchers), concurrently on a
   small thread pool (`FETCH_WORKERS`, default 8).
2. **Normalize** — raw entries become a uniform `NormalizedItem` (title,
   summary, link, published date, source + priority), HTML stripped.
3. **Deduplicate** — a stable `item_id` (hash of source + link) is checked
   against the SQLite `seen_items` table, so each item is processed only the
   first time it appears.
4. **Score** — each new item gets a `relevance_score` in `[0, 1]` plus a list
   of human-readable `reasons` (see below).
5. **Classify** — the highest-weighted matched category becomes the item's
   primary topic vertical (or `uncategorized`).
6. **Draft** — items above the alert threshold get a two-part alert: an
   internal analytic note and a clearly-marked *draft* public insight angle.
7. **Deliver** — those alerts are rendered for ntfy / Teams / generic JSON and
   POSTed to your webhook.
8. **Persist** — **every** scored item (not just alerted ones) is written to
   the `stories` archive that backs the web frontend.

### The scoring model

Scoring is deliberately **transparent and keyword-based** — no ML black box.
Every point added or removed is recorded as a `reason` you can read on the
story page. An item's raw score is the sum of:

| Factor | Contribution | Notes |
|---|---|---|
| **Category keyword** | `0.15 × category weight` | First match per category only; ×`1.5` when the keyword is in the title |
| **Source priority** | `(priority ÷ 5) × 0.10` | A small floor of trust for where it came from (`0.02`–`0.10`) |
| **Named entity** | `+0.20` each | Watched payers (UnitedHealthcare, Humana, …); capped at 2 |
| **Core MA term** | `+0.15` once | Strong MA vocabulary (`ma_boost_terms`: "Medicare Advantage", "D-SNP", "Part C", …) is relevance evidence even when no category keyword matches |
| **Multi-category** | `+0.10` per extra category | Rewards items touching several trigger types |
| **Soft exclusion** | `−0.25` each | Configurable "this term makes it less relevant" penalties |
| **Hard exclusion** | **score → 0** | Vetoes an unambiguously off-topic item (still archived, with the reason) |

The final score is clamped to `[0, 1]`. Keyword matching is **whole-token**, so
`MA` won't match "Massachusetts" and `bid` won't match "forbidden", with
singular/plural folding (`premium` matches "premiums").

### Two layers of noise control

The hard problem: broad news feeds (e.g. the ~50 statewide newsrooms) publish
mostly non-Medicare content that still brushes a keyword. Two independent layers
handle this — one at scoring time, one at read time.

**1. Source-aware Medicare-context gate (scoring).** A lone generic keyword —
`premium`, `network`, `earnings` — is strong evidence from CMS but weak from a
general-politics firehose. So for sources **below**
`scoring.ma_context_min_priority` (default `3`), keyword matches only count once
the item *also* carries real Medicare context: a watched payer, or a core anchor
from `ma_context_terms` (Medicare, Part C/D, D-SNP, CMS, …). Without an anchor,
the item collapses to its source-priority floor and reads as noise. Dedicated MA
sources (priority 3–5) are trusted on-topic and are never gated.

**2. Archive display floor (read time).** `ARCHIVE_MIN_SCORE` (default `0.1`)
keeps anything still scoring as pure source-priority noise out of the public
surfaces — the feed, topic/state pages, search, and the static site. With
default weights, anything with a genuine signal scores `≥ 0.12`, so the floor
removes noise without hiding real signals.

Nothing is ever deleted: the archive keeps **every** scored item, and the
`/status` dashboard and `/health` endpoint report the full, unfiltered counts —
that's where you gauge a source's yield and decide whether to prune it in
`sources.yaml`. The gate and floor only shape what the *public* views show.

### Near-duplicate grouping

The same story is often carried by several sources at once. At persist time,
each new story's headline is compared (title-token similarity) against the ones
already archived; a near-match is labelled a **duplicate** of the
highest-scoring representative (`processing.story_dedup` in `app.yaml`). The
browsable surfaces — feed, topic/state/payer pages, search, briefing — then
show **one** representative per story, and its story page lists the other
sources under *"Also reported by …"*. As with the floor, nothing is deleted:
every row stays in the archive and `/status` and `/health` count them all.

### Two thresholds, two audiences

- **`MIN_RELEVANCE_SCORE`** (default `0.3`) — the bar to fire an **alert** to
  your webhook. Higher = fewer, higher-confidence pushes.
- **`ARCHIVE_MIN_SCORE`** (default `0.1`) — the bar to appear on the **public
  browsable site**. Lower, because the archive is meant to be richer than the
  alert stream.

An item can be archived-and-browsable (`≥ 0.1`) without being alert-worthy
(`< 0.3`).

### Worked example

*"Humana flags rising medical loss ratio in its Medicare Advantage business"* —
Fierce Healthcare (priority 3):

- `medical loss ratio` in the title [Financial, weight 1.0] → `0.15 × 1.0 × 1.5` = **0.225**
- `Humana` entity → **0.20**
- source priority 3 → `(3 ÷ 5) × 0.10` = **0.06**
- **score ≈ 0.49** → above both thresholds → alerted *and* browsable.

Contrast *"County debates premium gas tax"* from a priority-2 state feed: it
brushes `premium`, but names no payer and no Medicare term, so the **gate**
suppresses the keyword → the score falls to the **0.04** priority floor → the
**display floor** hides it from the site.

### Closing the loop

Readers tune the filter over time. Story pages carry a 👍/👎 widget (giscus on
the static site), and ntfy alerts can carry feedback buttons. Those verdicts
feed three advisory tools — keyword mining, source-yield review, and a
scorer-vs-reader disagreement digest — that *suggest* edits to `sources.yaml` /
`taxonomy.yaml`. Owner verdicts are ground truth; crowd reactions are advisory
and never auto-change scoring on their own. See
[Reader feedback](#reader-feedback).

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

## Web Frontend & Self-Hosting

The pipeline persists every scored item into a `stories` table, and a FastAPI +
Jinja web app renders it as a browsable site. Pages:

| Route | Purpose |
|---|---|
| `/` | Paginated, reverse-chronological signal feed |
| `/topics/{category}` | One of the six trigger verticals (e.g. `policy_regulatory`) |
| `/payers` and `/payers/{slug}` | Payer Intelligence — signals grouped by watched payer, with a signal-volume trend sparkline, signal mix, state footprint, and SEC filings |
| `/briefing` and `/briefing/{date}` | Daily Briefing digest + archive |
| `/angles` | Ways of looking at the week's signals — cards at the intersections of analytical lenses (payer × topic, topic × topic, topic × state, payer × payer) over the last 7/14/30 days, with momentum and fact-derived summaries, weighted by the declared causal model (`/post-ideas` 301-redirects here) |
| `/search?q=` | Full-text search across the archive (SQLite FTS5) |
| `/sources` | Public Sources directory with coverage + ingestion cadence |
| `/states` and `/states/{code}` | State Intelligence — signals by U.S. state |
| `/story/{id}` | Story detail with the draft insight angle |
| `/status` | System status dashboard (last run, 12-week signal-volume trend, coverage by topic/source) |
| `/health` | JSON health/counts (stories, sources, last run, categories) |

#### Archive noise floor

Ingestion stores **every** scored item so the source-yield review and feedback
loops see the full picture, but broad, general-interest feeds (e.g. the
statewide newsrooms) inevitably produce items with no Medicare Advantage angle
— they match no taxonomy keyword and no watched payer, so their entire score is
just the source-priority floor (`0.04`–`0.10`).

`ARCHIVE_MIN_SCORE` (default `0.1`) keeps those items out of the browsable
surfaces — the feed, topic/state pages, search, and the static Pages site —
while leaving them in the archive. With default scoring weights, anything with
a real signal scores `≥ 0.12`, so the floor drops pure-priority noise without
hiding genuine signals. The `/status` dashboard and `/health` counts still
reflect the **full** archive (that's where you gauge how much a source
contributes and decide whether to prune it in `sources.yaml`). Set it to `0.0`
to surface everything, as before.

Noise reduction is two layers, one at scoring and one at display:

1. **Source-aware scoring gate** (`taxonomy.yaml` → `scoring.ma_context_min_priority`,
   default `3`). Broad, general-interest feeds constantly brush a taxonomy
   keyword ("premium", "network", "earnings") in stories with no Medicare angle.
   For sources **below** that priority, an item must carry real Medicare/MA
   context — a watched payer or one of the `ma_context_terms` (Medicare, Part C/D,
   D-SNP, CMS, …) — before its keyword matches count; otherwise it falls back to
   the source-priority floor and reads as noise. Dedicated MA sources (priority
   3–5: CMS, KFF, Fierce, …) are trusted on-topic and stay fully sensitive. Set
   to `0` to disable.
2. **Display floor** (`ARCHIVE_MIN_SCORE`, above) then hides whatever still
   scores as pure source-priority noise from the public surfaces.

### Run the web app locally

```bash
pip install -e ".[web]"          # FastAPI, Uvicorn, Jinja2, APScheduler
ma-signal-monitor                # populate data/state.db at least once
uvicorn ma_signal_monitor.web.app:app_factory --factory --port 8000
# open http://localhost:8000
```

### Docker (self-host)

One container serves the site **and** runs ingestion on an interval (APScheduler,
`INGEST_INTERVAL_HOURS`, default 6) against a shared SQLite volume:

```bash
docker compose up --build
# site on http://localhost:8000 ; archive persists in ./data
```

The archive fills going forward from newly-seen items — there is no historical
backfill, since prior runs never stored full content.

### Static hosting on GitHub Pages (free)

The site can also be published as static HTML to GitHub Pages — no server, no
infra. A scheduled GitHub Actions workflow (`.github/workflows/deploy-pages.yml`)
ingests feeds, renders every page to flat HTML, and deploys it:

```bash
ma-signal-build --base-path /<your-repo>   # build into ./site
# (or scripts/build_static.py --base-path /<your-repo> --out site)
```

The archive DB is persisted inside the published site (`/<repo>/data/state.db`)
and restored on the next run, so it accumulates over time. Full-text search runs
**client-side** on Pages via a generated `search-index.json`. To enable: in the
repo's **Settings → Pages**, set the source to **GitHub Actions**. Email digests
still send from the workflow if you add the `SMTP_*` / `DIGEST_*` secrets.

### Daily Briefing

The **Daily Briefing** is a curated digest of the day's top signals — the
fastest way to get insight in front of people. It's grouped into the six topic
verticals, scored, and always viewable at `/briefing` (with a dated archive).

Generate one on demand:

```bash
ma-signal-digest                 # build + save today's briefing (+ email if configured)
# then open http://localhost:8000/briefing
```

To **email** it daily, set `DIGEST_ENABLED=true`, `DIGEST_TO`, and the `SMTP_*`
vars (see `.env.example`). The web container's scheduler then sends it at
`DIGEST_HOUR` (UTC) each day; email is best-effort, so the briefing is never
blocked by SMTP being unset. Set `PUBLIC_BASE_URL` so email links resolve back
to your site.

### Angles

`/angles` reframes the recent archive as **ways of looking at the week's
signals**: cards form where two analytical lenses overlap. Every lens is data
already on each story — its payers, topic categories, and states — so there's no
new scoring or ingestion. Stories from the last 7 days (`?days=7|14|30` on the
live app, default 7) are bucketed into intersections across four angle types —
payer × topic, topic × topic, topic × state, and payer × payer — and each
intersection needs **at least two stories** to become a card. Cards are compared
against the previous window of the same length for momentum
(new / up / down / steady), and each carries a fact-derived summary line built
only from data on the stories (signal and source counts, the momentum shift, and
the strongest story) — no mad-libs and no borrowed drafts.

**Weighted by a declared causal model.** The six topic categories are stages in
a causal cascade — structural/policy **drivers** → economic **pressure** →
strategic **response** → market **outcomes** — declared in
`config/causal_model.yaml`. That file lists the four ordered layers and a set of
**downstream-only** weighted edges (e.g. `policy → financial` at weight 1.0),
each carrying a one-sentence, citable **evidence** rationale (MedPAC reports, KFF
enrollment/switching analyses, CMS rule-impact analyses, payer 10-K/8-K cycles).
It is a transparent, inspectable model — not an inferred one — and the shipped
file is **test-enforced** (every taxonomy category in exactly one layer; edges
known, non-self, strictly downstream, weight ∈ [0, 1], evidence non-empty).

Cards rank by `count × (1 + boost × edge_weight)`: an intersection lying *along*
a causal edge earns a boost (`CHAIN_BOOST = 0.5` for a two-topic chain,
`CASCADE_BOOST = 0.75` for a payer moving across an edge), so a genuine causal
chain out-ranks an incidental overlap of equal volume — yet a boost below 1
re-ranks without steamrolling raw volume. Non-edge overlaps (same-layer or
unrelated topics) still appear, **ranked by volume alone**. Causal cards are
grouped under *"Causal chains in motion"* with a JS-free *About this model*
panel; everything else falls under *"More angles"*. A greedy **subset
suppression** pass then drops any card whose stories are a subset of a
higher-ranked card (so a cascade absorbs its own constituent cards). When the
archive is too sparse to yield enough intersections, the page **falls back** to
single-lens topic cards. On the static Pages site the page is frozen at build
time; the scheduled deploy workflow keeps it fresh, and `/post-ideas` redirects
here.

### Source Discovery (opt-in)

The monitor can **grow its own source list over time**. With
`DISCOVERY_ENABLED=true`, each run harvests the outbound links from ingested
stories, ranks the domains behind them by relevance-weighted frequency,
autodiscovers RSS/Atom feeds on the promising ones, and surfaces them as
reviewable candidates. Strong candidates are promoted automatically (hybrid
policy); the rest queue for review at `/candidates` and in the Daily Briefing.

```bash
ma-signal-discover                 # run autodiscovery now
ma-signal-candidates               # list ranked candidate feeds
ma-signal-candidates promote <id>  # make a candidate a live source
```

Promoted feeds are merged in via a DB overlay (your curated `sources.yaml` is
never rewritten). Everything stays local — no paid APIs or external crawlers.
See [`docs/discovery.md`](docs/discovery.md) for the full design and all knobs.

## Reader feedback

Stories can be rated to tune what the filter surfaces. On the live web app,
each story page has a 👍 / 👎 widget (with an optional "wrong category"
correction) that records an owner verdict. On the static site, the same place
mounts a [giscus](https://giscus.app) thread so visitors can react with their
GitHub login — keyed on the story's stable `item_id`, so feedback stays bound
to the right story even if titles or URLs change.

If `WEBHOOK_MODE=ntfy`, alerts can also carry 👍/👎 buttons (set
`NTFY_FEEDBACK_TOPIC`) so you can rate a signal the moment you read it.

```bash
ma-signal-feedback mark <item_id> relevant   # record an owner verdict (weight 1.0)
ma-signal-feedback ingest-github             # pull giscus reactions into the DB
ma-signal-feedback ingest-ntfy               # pull ntfy 👍/👎 votes into the DB
ma-signal-feedback mine-keywords             # suggest taxonomy keywords from labels
ma-signal-feedback disagreements             # stories where your verdicts diverge from the scorer
ma-signal-feedback summary <item_id>         # show verdicts for one story
```

The `/status` page also flags low-yield sources for review, `mine-keywords`
proposes inclusion/exclusion keyword candidates from your verdicts, and
`disagreements` lists stories the scorer over- or under-valued relative to your
verdicts — all advisory, with you confirming changes to `sources.yaml` /
`taxonomy.yaml`.

Owner verdicts are ground truth (weight 1.0); crowd reactions are advisory
(weight < 1.0) and never auto-change scoring or sources on their own. See
[`docs/feedback.md`](docs/feedback.md) for the full design.

## Configuration

### `.env` — Environment settings

| Variable | Default | Description |
|---|---|---|
| `WEBHOOK_URL` | *(required)* | Webhook endpoint URL (e.g., `https://ntfy.sh/your-topic`) |
| `WEBHOOK_MODE` | `ntfy` | `ntfy`, `generic`, `teams`, or `test` |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `MIN_RELEVANCE_SCORE` | `0.3` | Threshold for alert generation (0.0-1.0) |
| `ARCHIVE_MIN_SCORE` | `0.1` | Public display floor — hides sub-floor "noise" from the site (0.0-1.0; 0 disables) |
| `FETCH_WORKERS` | `8` | Concurrent source fetches per run (1 = strictly sequential) |

### `config/sources.yaml` — Feed sources

Add/remove/disable sources. Each source has a name, URL, `type` (`rss`, `sec`,
`cms`, or `litigation`), priority (1-5), and tags. Litigation-tracker sources
also carry a `context` string that the fetcher injects into each item's summary
(their entries are case names with boilerplate summaries).

### `config/taxonomy.yaml` — Trigger categories

Configure the six trigger categories, their keywords, weights, and the list of watched payer entities. Also contains the core-MA-term boost list (`ma_boost_terms`), soft/hard exclusion keywords, and scoring tuning parameters.

### `config/app.yaml` — Application settings

Delivery retry behavior, processing limits, and storage retention settings.

## Scheduler Setup

### Linux/WSL (cron)

```bash
crontab -e
# Add (every 3 hours, matching the repo's scheduled-monitor workflow):
0 */3 * * * cd /path/to/project && /path/to/.venv/bin/python -m ma_signal_monitor.main >> logs/cron.log 2>&1
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

### Near-duplicate alert suppression

The same story is often carried by several sources (e.g. a payer's insulin
settlement covered by Healthcare Dive *and* Becker's). Because each item's ID is
`hash(source + link)`, those are distinct items that would each fire an alert.
Before delivery, alerts are de-duplicated by headline similarity (title-token
Jaccard): within a run, only the highest-scoring member of a near-duplicate
cluster is kept; across runs, an alert whose headline matches one delivered in
the last few days is suppressed. Only the **webhook stream** is trimmed — every
scored item is still written to the archive. Tune under `delivery.dedup` in
`config/app.yaml` (`enabled`, `similarity_threshold`, `lookback_days`).

## Teams Compatibility Notes

- Uses Adaptive Card schema v1.4 wrapped in the Teams message format
- The payload targets the "Incoming Webhook" connector
- Teams has a ~28KB payload size limit — alerts are designed to stay well under this
- Teams rendering can be inconsistent across clients (desktop vs. web vs. mobile)
- If cards don't render, check: webhook URL validity, payload size, and card schema version
- **Workflow webhooks** (Power Automate) use a different format than Incoming Webhooks — this tool targets Incoming Webhooks

## Limitations

- **No NLP/ML**: Scoring is keyword-based, not semantic. High-quality but not perfect.
- **Feed-based only**: All ingestion (RSS/Atom, SEC EDGAR, CMS, and litigation-tracker feeds) consumes public feeds — no scraping, APIs, or document parsing.
- **No live Teams validation**: Teams rendering is validated structurally, not against a live endpoint (unless you provide one).
- **English only**: Keywords and content processing assume English-language sources.
- **No authentication**: RSS fetching does not support authenticated feeds.
- **Thread-pool fetching**: Sources are fetched concurrently (`FETCH_WORKERS`, default 8; set 1 for strictly sequential).

## Roadmap

Phase 1 (done): browsable story archive + web frontend (feed, topic verticals,
Sources directory, State Intelligence), Docker self-host.

Phase 2 (done): Daily Briefing digest — web page + archive + optional daily email.

Phase 3 (done): full-text search over the archive (SQLite FTS5, LIKE fallback).

Phase 4 (done): SEC EDGAR + CMS feed fetchers activated, expanded sources
(research / state / SEC filings), and a `/status` observability dashboard.

Phase 5 (done): static-site export + GitHub Pages deploy workflow (free hosting,
client-side search).

Phase 6 (done): Payer Intelligence pages (`/payers`), concurrent source
fetching, and a litigation-tracker fetcher (Georgetown Health Care Litigation
Tracker).

- Other ideas: semantic/NLP scoring, Slack renderer,
  historical trend analysis, CMS enrollment/market-share data on payer pages

## Project Structure

```
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── config/
│   ├── sources.yaml        # Feed source configuration
│   ├── taxonomy.yaml       # Trigger categories and scoring
│   ├── app.yaml            # Application settings
│   └── causal_model.yaml   # Declared causal layer model (Angles weighting)
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
│   ├── trends.py           # Weekly-volume bucketing + inline-SVG sparklines
│   ├── payers.py           # Canonical payer grouping (Payer Intelligence pages)
│   ├── angles.py           # "Angles" lens-intersection view-model
│   ├── drafting.py         # Alert generation
│   ├── delivery.py         # Webhook delivery
│   ├── fetchers/
│   │   ├── rss.py          # RSS feed fetcher
│   │   ├── sec.py          # SEC EDGAR filings fetcher
│   │   ├── cms.py          # CMS feeds fetcher
│   │   └── litigation.py   # Litigation-tracker fetcher (context injection)
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
