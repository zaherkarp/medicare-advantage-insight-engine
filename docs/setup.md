# Setup Guide

## Prerequisites

- Python 3.11 or later
- pip (included with Python) or [uv](https://github.com/astral-sh/uv)
- A webhook endpoint for testing (e.g., [webhook.site](https://webhook.site))

## Step 1: Python Environment

### Option A: pip + venv

```bash
python3 -m venv .venv
source .venv/bin/activate  # Linux/macOS/WSL
# .venv\Scripts\activate   # Windows PowerShell

pip install -e ".[dev]"
```

### Option B: uv

```bash
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

## Step 2: Configuration

### 2a: Environment file

```bash
cp .env.example .env
```

Edit `.env`:

```ini
# Required: your webhook endpoint
WEBHOOK_URL=https://webhook.site/your-uuid-here

# Start with test mode
WEBHOOK_MODE=test

# Optional tuning
LOG_LEVEL=INFO
MIN_RELEVANCE_SCORE=0.3
```

### 2b: Source configuration

Edit `config/sources.yaml` to enable/disable feeds or add your own:

```yaml
sources:
  - name: "My Custom Feed"
    type: rss
    url: "https://example.com/rss"
    priority: 3
    enabled: true
    tags: ["custom"]
```

### 2c: Taxonomy tuning

Edit `config/taxonomy.yaml` to adjust keywords, weights, or watched entities.

## Step 3: Test with Webhook.site

1. Go to [webhook.site](https://webhook.site)
2. Copy the unique URL shown (e.g., `https://webhook.site/abc-123-...`)
3. Paste it into `.env` as `WEBHOOK_URL`
4. Run the seed test:

```bash
python scripts/seed_test_data.py --deliver
```

5. Check webhook.site — you should see JSON payloads arrive
6. Review the payload structure: Section A (internal alert) and Section B (public draft)

## Step 4: Run Against Live Feeds

```bash
python scripts/run_once.py
```

Check your webhook endpoint for any alerts. If no alerts appear, the feeds may not have had items matching the relevance threshold — try lowering `MIN_RELEVANCE_SCORE` in `.env` to `0.1` temporarily.

## Step 5: Switch to Teams (Optional)

1. In your Teams channel, add an "Incoming Webhook" connector
2. Copy the webhook URL
3. Update `.env`:
   ```ini
   WEBHOOK_URL=https://your-org.webhook.office.com/webhookb2/...
   WEBHOOK_MODE=teams
   ```
4. Test with the seed script first:
   ```bash
   python scripts/seed_test_data.py --deliver
   ```
5. If the card renders in Teams, switch to live feeds

**Troubleshooting Teams**: If cards don't render, switch back to `WEBHOOK_MODE=test` and send to webhook.site to inspect the payload structure. See `docs/troubleshooting.md`.

## Step 6: Schedule Recurring Runs

See the [operations guide](operations.md) for cron/Task Scheduler setup.

## Verify Installation

```bash
# Run tests
pytest

# Run with coverage
pytest --cov=ma_signal_monitor

# Check config loads
python -c "from ma_signal_monitor.config import load_config; c = load_config(); print(f'{len(c.sources)} sources, {len(c.categories)} categories')"
```
