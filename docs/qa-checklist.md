# QA Checklist

## Automated Tests

- [ ] `pytest` passes with no failures
- [ ] `pytest --cov=ma_signal_monitor` shows reasonable coverage

## Configuration

- [ ] Config loads successfully with default `.env.example` values (after setting WEBHOOK_URL)
- [ ] Missing `WEBHOOK_URL` produces a clear error message
- [ ] Invalid `WEBHOOK_MODE` produces a clear error message
- [ ] Missing `sources.yaml` produces a clear error
- [ ] Missing `taxonomy.yaml` produces a clear error
- [ ] Disabling all sources produces a clear error
- [ ] Adding a new source to `sources.yaml` works on next run
- [ ] Removing a source from `sources.yaml` works (no crash)

## Feed Fetching

- [ ] Run against at least one real public RSS feed
- [ ] A malformed/unreachable feed URL logs an error but doesn't crash the run
- [ ] Other sources still process when one feed fails
- [ ] Feed items have titles, links, and summaries after normalization

## Normalization

- [ ] HTML is stripped from summaries
- [ ] Dates are parsed from standard RSS formats
- [ ] Unparseable dates don't crash normalization
- [ ] Long summaries are truncated

## Deduplication

- [ ] First run processes items normally
- [ ] Second run with same feeds produces zero new items
- [ ] Deleting `data/state.db` allows reprocessing

## Scoring

- [ ] Relevant MA articles score above threshold
- [ ] Irrelevant articles (e.g., "hospital cafeteria") score below threshold
- [ ] Named entity detection boosts scores
- [ ] Score reasons are populated and readable
- [ ] Scores are between 0.0 and 1.0

## Classification

- [ ] Items are assigned to the correct primary category
- [ ] Multi-category items pick the highest-weight category

## Alert Drafting

- [ ] Internal alert has all required fields populated
- [ ] Public insight draft has all required fields populated
- [ ] Suggested checks are relevant to the category
- [ ] Draft paragraph is clearly marked as draft

## Webhook Delivery

- [ ] ntfy.sh alerts arrive with markdown formatting and priority levels
- [ ] ntfy.sh alerts have click-through links to source articles
- [ ] Payload arrives at webhook.site in test mode
- [ ] Payload is valid JSON
- [ ] Generic payload contains both Section A and Section B
- [ ] Teams payload has valid Adaptive Card structure
- [ ] ntfy payload has required fields (title, message, priority, markdown)
- [ ] Delivery logs are recorded in SQLite
- [ ] Failed delivery records the error
- [ ] Retry occurs on server errors (5xx)
- [ ] No retry on client errors (4xx)

## Payload Readability

- [ ] Alert title is clear and informative
- [ ] Summary is concise (not a wall of text)
- [ ] Scoring reasons are human-readable
- [ ] Suggested checks are actionable
- [ ] Public draft is clearly marked as draft material

## State and Persistence

- [ ] `data/state.db` is created on first run
- [ ] `seen_items` table grows with each run
- [ ] `delivery_log` records success/failure
- [ ] `run_metadata` tracks each run
- [ ] Database survives process restart
- [ ] Cleanup removes old records

## Error Handling

- [ ] Missing webhook URL fails with clear message at startup
- [ ] Network timeout on feed fetch is handled gracefully
- [ ] Invalid RSS content doesn't crash the pipeline
- [ ] Webhook delivery failure doesn't crash the pipeline

## Logs

- [ ] Logs show run start and completion
- [ ] Logs show item counts (fetched, new, relevant, sent)
- [ ] Error logs include enough context to diagnose issues
- [ ] Debug mode provides detailed scoring info
