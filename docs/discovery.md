# Source Discovery

The monitor can grow its own source list over time instead of relying solely on
the hand-curated `config/sources.yaml`. Discovery mines the content already being
ingested for outbound links, ranks the domains behind them, finds RSS/Atom feeds
on the promising ones, and surfaces them for review — with the strongest ones
promoted automatically.

It is **opt-in** and fully local (only `requests` + `feedparser`, the existing
core dependencies). Nothing happens until you set `DISCOVERY_ENABLED=true`.

## How it works

1. **Harvest (every ingest, cheap).** After each run scores its stories, the
   pipeline extracts the outbound links from each story (its own URL plus the
   `<a href>` links embedded in the article body) and tallies the **domains**
   behind them. A domain's score is the sum of the relevance scores of the
   stories that link to it, so a domain repeatedly cited by relevant coverage
   rises to the top. The story's own domain, already-configured sources, and
   social/tracker/CDN hosts are ignored. Results accumulate in
   `candidate_domains`.

2. **Autodiscovery (throttled, network).** On a schedule (or via
   `ma-signal-discover`), the top-ranked domains that haven't been probed
   recently are fetched: the homepage is checked for declared feeds
   (`<link rel="alternate" type="application/rss+xml">`), falling back to common
   paths (`/feed`, `/rss`, …). Each candidate is validated by actually parsing
   it, so only real feeds are kept. Results land in `candidate_sources`.

3. **Hybrid promotion.** A discovered feed whose domain clears both
   `DISCOVERY_AUTOPROMOTE_SCORE` and `DISCOVERY_AUTOPROMOTE_MIN_SEEN` is marked
   `auto_promoted` and starts being fetched. Everything else stays `new` for you
   to review.

4. **Promotion = DB overlay.** Promoted feeds (auto or manual) are merged into
   the live source list at config-load time — `config/sources.yaml` is never
   rewritten. Use `ma-signal-candidates export-yaml <id>` if you'd rather paste a
   block into the YAML and keep it as the single source of truth.

## Reviewing candidates

- **Web:** the `/candidates` page lists candidate feeds ranked by promise, with
  status filters. The Daily Briefing also includes a "New candidate sources"
  section.
- **CLI:**
  ```bash
  ma-signal-discover                 # run autodiscovery now
  ma-signal-candidates               # list candidates (ranked)
  ma-signal-candidates list new      # filter by status
  ma-signal-candidates promote <id>  # make a candidate a live source
  ma-signal-candidates reject <id>
  ma-signal-candidates ignore-domain <domain>
  ma-signal-candidates export-yaml <id>   # print a sources.yaml block
  ```

## Configuration

All knobs are environment variables (see `.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `DISCOVERY_ENABLED` | `false` | Master switch |
| `DISCOVERY_MIN_STORY_SCORE` | `0.3` | Only harvest links from stories at/above this |
| `DISCOVERY_MAX_DOMAINS_PER_RUN` | `20` | Domains probed per autodiscovery run |
| `DISCOVERY_INTERVAL_HOURS` | `24` | Autodiscovery cadence (scheduler) |
| `DISCOVERY_RECHECK_DAYS` | `14` | Minimum gap before re-probing a domain |
| `DISCOVERY_MIN_TIMES_SEEN` | `2` | Ignore one-off domains |
| `DISCOVERY_AUTOPROMOTE_SCORE` | `3.0` | Auto-promote threshold (domain score) |
| `DISCOVERY_AUTOPROMOTE_MIN_SEEN` | `4` | Auto-promote threshold (sightings) |
| `CANDIDATE_RETENTION_DAYS` | `180` | Prune dormant candidates after this |

## Notes & limitations

- Domain parsing is stdlib-only (no `tldextract`), so compound TLDs like
  `.co.uk` keep their full host. This is fine for ranking.
- Discovery only follows links found in feeds we already ingest; it does not
  crawl linked article pages or use web search.
- A discovery failure never blocks ingestion — the harvest stage is wrapped in a
  guard, and per-domain autodiscovery errors are isolated.
