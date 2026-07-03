# Assumptions and Open Questions

## Assumptions Made

### Sources
- **RSS is the primary input format.** All configured default sources publish RSS/Atom feeds. If a source stops publishing RSS, it must be replaced or a new fetcher type added.
- **Public, unauthenticated feeds only.** No support for feeds requiring login, API keys, or OAuth. This keeps the tool free and simple.
- **English-language content.** All keyword matching, scoring, and drafting assume English text.
- **Default sources are reasonable starting points.** The pre-configured feeds (CMS Newsroom, Federal Register, Healthcare Dive, etc.) cover major MA-relevant sources but are not exhaustive.

### Scoring
- **Keyword-based scoring is sufficient for Phase 1.** It will catch most relevant signals but may miss nuanced or novel topics not covered by the keyword lists. False positives are preferred over false negatives at this stage.
- **First keyword match per category is sufficient.** To avoid over-scoring articles dense with one topic's keywords, only the first keyword match per taxonomy category contributes to the score. This is a conservative choice.
- **Entity detection is case-insensitive substring matching.** This will occasionally match partial words (e.g., "Oscar" in "Oscar Health" might match non-health contexts). For the MA domain, this is an acceptable tradeoff.
- **Score of 0.3 is a reasonable default threshold.** This was calibrated against sample test data. Real-world tuning may be needed.
- **Relevance is source-aware for broad feeds.** A lone generic keyword ("premium", "network", "earnings") is strong evidence from a dedicated MA source (CMS, KFF, Fierce) but weak from a statewide-news firehose that covers everything. So for sources below `scoring.ma_context_min_priority` (default 3), keyword matches only count once the item also carries real Medicare/MA context — a watched payer or an `ma_context_terms` anchor (Medicare, Part C/D, D-SNP, CMS, …). Without an anchor the item falls back to the source-priority floor and is treated as noise. This keeps recall high (genuine state-level MA stories almost always name Medicare or a payer) while cutting the keyword false-positives a pure score floor can't catch. Set the threshold to 0 to disable.
- **The archive keeps everything; the public site does not.** Every scored item is persisted (so source-yield review and the feedback loops see the full picture and nothing is silently dropped), but the browsable surfaces — feed, topic/state pages, search, and the static Pages export — apply a display floor (`ARCHIVE_MIN_SCORE`, default `0.1`). This hides pure source-priority "noise": items that matched no taxonomy keyword and no watched entity, whose entire score is the source-priority floor. With default weights any real signal scores `≥ 0.12` while pure-priority items top out at `0.10`, so the floor trades no recall for a much cleaner public feed. Preferring false positives over false negatives still governs *relevance detection* (the scorer stays sensitive); the floor only suppresses items with *zero* detected relevance. The `/status` dashboard and `/health` counts intentionally report the full archive so low-yield sources remain visible for pruning.

### Taxonomy
- **Six categories cover the primary MA signal space.** The categories (membership, demographic, policy, financial, competitive, brokerage/distribution) are based on common MA industry analysis frameworks. Additional categories can be added in `taxonomy.yaml`.
- **Category weights reflect relative analytic importance.** Policy/regulatory is weighted slightly higher (1.2) because regulatory signals tend to have outsized market impact. This is adjustable.

### Delivery
- **ntfy.sh is the recommended default delivery mode.** It's free, requires no signup or API keys, supports mobile push notifications, markdown rendering, priority levels, and click-through actions. Topics are public by default — users should choose a unique topic name for privacy.
- **Teams Incoming Webhook connector is supported as an alternative.** The Teams renderer produces Adaptive Card v1.4 payloads for the Incoming Webhook connector, not for Power Automate Workflow webhooks (which use a different format).
- **Adaptive Card v1.4 is well-supported.** This is the current recommended version for Teams connectors as of 2024. If Microsoft deprecates this version, the renderer will need updates.
- **Payload size stays under 28KB.** Teams has a payload size limit. The alert format is designed to be concise, but extremely long article summaries could approach the limit.

### State
- **SQLite is suitable for local single-user operation.** No concurrent access concerns. If the tool were to run in a multi-process or distributed setup, a different store would be needed.
- **90-day dedup retention is reasonable.** Old items are cleaned up after 90 days. If a source republishes an old article after 90 days, it would be treated as new.
- **Hash-based dedup using source+link is stable.** If a source changes its URL scheme, previously seen items would appear as new. This is an acceptable edge case.

### Environment
- **Local execution only.** No cloud deployment, no container orchestration. Runs on a developer's machine or a small server.
- **Python 3.11+ is available.** Uses features like `str | None` type syntax.
- **Network access is available for RSS fetching.** Firewall or proxy configurations are the user's responsibility.

## Open Questions

1. **Which Teams webhook type is in use?** The tool targets "Incoming Webhook" connectors. Microsoft has been migrating to "Workflow" webhooks (Power Automate) which use a different payload format. If your tenant only supports Workflow webhooks, the Teams renderer will need modification.

2. **Are there specific sources you want prioritized?** The defaults are general MA-relevant feeds. If there are specific payer investor relations pages, state DOI feeds, or niche sources you want included, they can be added.

3. **Is there a preferred schedule frequency?** The repo workflows run alerts every 3 hours and rebuild the Pages site every 2 hours. More frequent runs are safe (dedup handles it) but increase network requests.

4. **Should alerts be batched or sent individually?** Currently each alert is sent as a separate webhook POST. Batching into a single message would reduce noise but make individual signals harder to track.

5. **Is there a need for alert suppression rules?** *(Partly addressed.)* Near-duplicate suppression is now built in: the same story republished by multiple sources fires a single alert (headline-similarity dedup, within-run and against recently-delivered alerts — see `delivery.dedup` in `app.yaml`). Entity- or category-scoped rate limits ("don't alert on UnitedHealthcare more than once per day", "suppress membership movement during open enrollment") are still not implemented but could be added.

6. **What is the expected volume?** With ~90 sources checking every few hours, expect a handful to a few dozen alerts per day depending on news volume and threshold settings. High-volume periods (CMS rulemaking, earnings season) will produce more.
