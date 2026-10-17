# Setup Guide

## Prerequisites

- Python 3.11 or later
- pip (included with Python) or [uv](https://github.com/astral-sh/uv)

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
# Required while any SEC EDGAR source is enabled (sec.gov rejects requests
# without a real contact email)
SEC_CONTACT_EMAIL=you@example.com

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

## Step 3: Try it with sample data

```bash
python scripts/seed_test_data.py
```

This prints each sample item's score and reasons, shows the two-section
analysis (Section A, internal analytic note; Section B, draft public insight)
for items above the threshold, and writes everything to the local archive.

## Step 4: Run against live feeds

```bash
python scripts/run_once.py
```

Then browse the result in the web frontend (see the README, "Web Frontend & Self-Hosting") or
build the static site. If few stories get an analysis draft, the feeds may not
have had items above the relevance threshold; try lowering
`MIN_RELEVANCE_SCORE` in `.env` to `0.1` temporarily.

## Step 5: Schedule Recurring Runs

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
