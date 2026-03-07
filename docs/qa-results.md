# QA Results

## Automated Tests

**59 tests, all passing.**

```
tests/test_config.py      — 10 tests (config loading, validation)
tests/test_normalize.py   — 11 tests (date parsing, truncation, ID stability, whitespace)
tests/test_dedupe.py      —  5 tests (new/seen filtering, idempotency)
tests/test_scoring.py     —  9 tests (keyword, entity, priority, multi-category, clamping)
tests/test_renderers.py   — 10 tests (generic JSON + Teams Adaptive Card structure)
tests/test_delivery.py    —  7 tests (success, 4xx/5xx, retries, connection errors, Teams format)
tests/test_storage.py     —  7 tests (seen items, delivery log, run metadata, cleanup, persistence)
```

**Coverage: 58% overall.** Core pipeline modules (scoring, storage, dedupe, normalization, renderers) have 93-100% coverage. Lower coverage in orchestration/integration modules (main.py, drafting.py, fetchers) is expected — these are better tested via integration/manual testing.

## Manual QA

### Run against sample data
- **PASS**: `scripts/seed_test_data.py` processes 5 sample items correctly
- **PASS**: Relevant items (UHC expansion, CMS Stars, Humana MLR, Aetna partnership) score above threshold
- **PASS**: Irrelevant item ("hospital parking garage") scores below threshold
- **PASS**: Scoring reasons are clear and explainable

### Deduplication verification
- **PASS**: First run processes all items as new
- **PASS**: Second run correctly identifies all items as duplicates
- **PASS**: After deleting state.db, items are reprocessed

### Configuration changes
- **PASS**: Adding a source to sources.yaml is picked up on next run
- **PASS**: Disabling a source skips it without errors
- **PASS**: Changing `MIN_RELEVANCE_SCORE` adjusts alert count correctly

### Error handling
- **PASS**: Missing WEBHOOK_URL fails with clear message
- **PASS**: Invalid WEBHOOK_MODE fails with clear message
- **PASS**: Missing config files fail with clear messages
- **PASS**: Pipeline continues when one source fails (tested via invalid URL in sources)

### Payload structure
- **PASS**: Generic webhook payload is valid JSON with both Section A and Section B
- **PASS**: Teams payload is valid Adaptive Card v1.4 structure
- **PASS**: All required fields populated in both formats
- **PASS**: Payload size well under Teams 28KB limit (typical: 2-4KB)

### State persistence
- **PASS**: SQLite database created on first run
- **PASS**: seen_items, delivery_log, and run_metadata tables populated
- **PASS**: Database survives process restart
- **PASS**: Cleanup removes old records based on retention settings

### Logs
- **PASS**: Logs show run start/end with item counts
- **PASS**: DEBUG mode shows scoring details
- **PASS**: Errors include enough context to diagnose

## Partial / Not Fully Verified

### Live RSS feeds
- **NOT TESTED IN CI**: Live RSS fetching depends on network access and feed availability. The RSS fetcher is structurally sound and uses well-tested libraries (requests + feedparser). Manual testing against live feeds is recommended on first setup.

### Teams rendering
- **STRUCTURAL ONLY**: The Adaptive Card payload structure is verified against the schema, but rendering in an actual Teams client was not tested (requires a Teams tenant with an active Incoming Webhook). Use webhook.site to inspect the payload before switching to Teams.

### Webhook delivery to real endpoint
- **MOCKED IN TESTS**: Delivery tests use the `responses` library to mock HTTP. Actual delivery to webhook.site is validated via `seed_test_data.py --deliver` (requires setting WEBHOOK_URL).

## Known Issues

1. **Environment variable pollution in tests**: Resolved by adding autouse fixture to clean env vars between test cases.
2. **Sequential source fetching**: Sources are fetched one at a time. Not a problem for 5-10 sources but could be slow with many feeds.
3. **No semantic understanding**: Keyword-based scoring catches domain vocabulary well but will miss signals expressed in novel language.
