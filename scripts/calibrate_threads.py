#!/usr/bin/env python3
"""Sweep candidate ``thread_similarity_threshold`` values and print clustering
quality per threshold, for choosing the new production default.

DEV SCRIPT -- not run in CI. ``testpaths`` in ``pyproject.toml`` is scoped to
``tests/`` and this file isn't named ``test_*.py``, so pytest never collects
it; ``ruff format``/``ruff check`` still run over ``scripts/`` (see
``.github/workflows/ci.yml``), so it has to stay lint-clean, but it never
executes as part of the test suite. Run it by hand:

    python scripts/calibrate_threads.py
    python scripts/calibrate_threads.py --db path/to/state.db --days 30

Step 3 of the emergent-story-thread plan (see ``threads.py``, ``similarity.py``)
replaced plain Jaccard with IDF-weighted cosine and single-linkage with
average-linkage. The similarity *scale* changed completely as a result, so the
old ``thread_similarity_threshold: 0.28`` (calibrated for Jaccard) is
meaningless here -- this script re-measures the tradeoff on the new scale so
the new default is picked from data, not guessed.

For each candidate threshold it prints, per corpus:

* thread count
* ungrouped % of the window
* largest-thread % of the window (the chaining-regression canary -- if this
  balloons as the threshold drops, single-cluster collapse is back)
* single-category purity: fraction of threads whose members all share one
  ``primary_category`` (a proxy for "did clustering group unrelated stories
  together")

Two corpora, both required by the plan:

* ``tests/fixtures/thread_corpus.yaml`` -- via the same loader
  ``tests/test_thread_quality.py`` uses, scored with the narrow 4-alias
  ``sample_config`` test fixture (``tests/conftest.py``).
* the richer scratchpad ``corpus.py`` -- 83 stories scored through the real
  taxonomy (33 watched entities, 6 categories) via ``config/app.yaml`` +
  ``config/taxonomy.yaml``. Much closer to production than the test fixture;
  read as the primary signal for picking the shipped threshold.

Optionally a third, real corpus: ``--db state.db`` reads actual archived
stories (``--days`` back from now, default 30, matching the timeline's own
default window) instead of either synthetic corpus.

The shipped threshold has been validated this way against the real archive
(6,701 stories, 382 in the default 30-day window) -- see the sweep table in
the ``config/app.yaml`` comment next to ``similarity_threshold``.

Getting a real archive to sweep is a one-liner, because the production DB
round-trips through the published Pages site: ``deploy-pages.yml`` restores it
with a plain unauthenticated GET at the start of every run, so the same URL
serves the current archive to anyone::

    curl -fsSL https://zaherkarp.github.io/medicare-advantage-insight-engine/data/state.db \\
        -o /tmp/prod.db
    python scripts/calibrate_threads.py --db /tmp/prod.db --days 30

Write it somewhere outside the repo: ``.gitignore`` covers ``data/`` and
``*.db``, but a 14 MB archive has no business near a commit either way.
"""

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))
sys.path.insert(0, str(_PROJECT_ROOT / "tests"))

from ma_signal_monitor.config import AppConfig, load_config  # noqa: E402
from ma_signal_monitor.threads import build_threads  # noqa: E402

# Swept on the cosine scale (0-1, but boilerplate-suppressed so realistic
# thread-level scores sit much lower than legacy Jaccard's did -- see the
# module docstring). Dense (0.01 steps) through 0.02-0.20, where the
# ungrouped/largest-thread/purity tradeoff actually moves; coarser above that.
_THRESHOLDS: tuple[float, ...] = tuple(
    round(t, 2) for t in [x / 100 for x in range(2, 21)]
) + (0.22, 0.25, 0.30)


def _purity(stories: list[dict]) -> bool:
    """True if every story in a thread shares one ``primary_category``."""
    cats = {s.get("primary_category") or "uncategorized" for s in stories}
    return len(cats) == 1


def measure(stories: list[dict], config: AppConfig, threshold: float) -> dict:
    """Cluster ``stories`` at ``threshold`` and summarize the four quality numbers."""
    threads, ungrouped = build_threads(
        stories, config, threshold=threshold, min_stories=config.thread_min_stories
    )
    n = len(stories)
    largest = max((t.total for t in threads), default=0)
    pure = sum(1 for t in threads if _purity(t.stories))
    return {
        "threshold": threshold,
        "threads": len(threads),
        "ungrouped_frac": (len(ungrouped) / n) if n else 0.0,
        "largest_frac": (largest / n) if n else 0.0,
        "purity": (pure / len(threads)) if threads else float("nan"),
    }


def sweep(
    stories: list[dict], config: AppConfig, thresholds: tuple[float, ...] = _THRESHOLDS
) -> list[dict]:
    return [measure(stories, config, t) for t in thresholds]


def print_table(name: str, n_stories: int, rows: list[dict]) -> None:
    print(f"\n=== {name} ({n_stories} stories) ===")
    header = (
        f"{'threshold':>9}  {'threads':>7}  {'ungrouped%':>10}  "
        f"{'largest%':>8}  {'purity':>6}"
    )
    print(header)
    print("-" * len(header))
    for r in rows:
        purity_s = "n/a" if r["purity"] != r["purity"] else f"{r['purity']:.2f}"
        print(
            f"{r['threshold']:>9.2f}  {r['threads']:>7}  "
            f"{r['ungrouped_frac'] * 100:>9.1f}%  {r['largest_frac'] * 100:>7.1f}%  "
            f"{purity_s:>6}"
        )


def _fixture_corpus_and_config() -> tuple[list[dict], AppConfig]:
    """The ~80-headline synthetic fixture, scored with the narrow test config."""
    from conftest import sample_config as sample_config_fixture
    from test_thread_quality import _load_corpus

    # sample_config is a plain, dependency-free @pytest.fixture function (no
    # fixture args of its own) -- __wrapped__ is the underlying function,
    # callable directly outside a pytest run.
    config = sample_config_fixture.__wrapped__()
    return _load_corpus(), config


def _production_corpus_and_config(scratch_dir: Path) -> tuple[list[dict], AppConfig]:
    """The richer 83-story corpus, scored through the real taxonomy."""
    sys.path.insert(0, str(scratch_dir))
    import corpus as production_corpus

    config = load_config(_PROJECT_ROOT)
    return production_corpus.build(config), config


def _db_corpus_and_config(db_path: Path, days: int) -> tuple[list[dict], AppConfig]:
    """Real archived stories from a ``state.db``, most recent ``days`` back."""
    config = load_config(_PROJECT_ROOT)
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM stories WHERE COALESCE(published_date, fetched_at) >= ? "
        "AND relevance_score >= ? AND duplicate_of IS NULL "
        "ORDER BY COALESCE(published_date, fetched_at) DESC",
        (cutoff, config.archive_min_score),
    ).fetchall()
    conn.close()

    import json

    stories = [
        {
            "item_id": r["item_id"],
            "title": r["title"],
            "summary": r["summary"] or "",
            "source_name": r["source_name"],
            "relevance_score": r["relevance_score"] or 0.0,
            "primary_category": r["primary_category"] or "uncategorized",
            "categories": json.loads(r["categories"] or "[]"),
            "entities": json.loads(r["entities"] or "[]"),
            "states": json.loads(r["states"] or "[]"),
        }
        for r in rows
    ]
    return stories, config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        help="Real state.db archive to sweep instead of/besides the synthetic corpora",
    )
    parser.add_argument(
        "--days", type=int, default=30, help="--db window size in days (default 30)"
    )
    parser.add_argument(
        "--scratch",
        type=Path,
        default=Path(
            "/tmp/claude-0/-home-user-medicare-advantage-insight-engine/"
            "b6dbc164-65af-5f9d-aad4-bb38c5c692ed/scratchpad"
        ),
        help="Directory containing the production-config corpus.py helper",
    )
    args = parser.parse_args()

    stories, config = _fixture_corpus_and_config()
    print_table(
        "tests/fixtures/thread_corpus.yaml (sample_config)",
        len(stories),
        sweep(stories, config),
    )

    if (args.scratch / "corpus.py").exists():
        stories, config = _production_corpus_and_config(args.scratch)
        print_table(
            "scratchpad corpus.py (real taxonomy, production config)",
            len(stories),
            sweep(stories, config),
        )
    else:
        print(
            f"\n(skipping production-config corpus: {args.scratch}/corpus.py not found)"
        )

    if args.db:
        stories, config = _db_corpus_and_config(args.db, args.days)
        print_table(
            f"{args.db} (last {args.days}d, real archive)",
            len(stories),
            sweep(stories, config),
        )


if __name__ == "__main__":
    main()
