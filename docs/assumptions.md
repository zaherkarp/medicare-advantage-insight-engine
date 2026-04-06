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

### Taxonomy
- **Five categories cover the primary MA signal space.** The categories (membership, demographic, policy, financial, competitive) are based on common MA industry analysis frameworks. Additional categories can be added in `taxonomy.yaml`.
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

3. **Is there a preferred schedule frequency?** The current recommendation is every 4 hours. More frequent runs are safe (dedup handles it) but increase network requests.

4. **Should alerts be batched or sent individually?** Currently each alert is sent as a separate webhook POST. Batching into a single message would reduce noise but make individual signals harder to track.

5. **Is there a need for alert suppression rules?** For example, "don't alert on UnitedHealthcare more than once per day" or "suppress membership movement signals during open enrollment." Not implemented but could be added.

6. **What is the expected volume?** With 5-6 RSS sources checking every 4 hours, expect 0-10 alerts per day depending on news volume and threshold settings. High-volume periods (CMS rulemaking, earnings season) will produce more.
