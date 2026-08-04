"""Quality gates for the emergent story-thread lane (threads.py) at a
realistic window size.

``tests/test_threads.py`` exercises ``build_threads()`` correctness with 5
hand-built near-duplicate stories. That is too small a corpus to exhibit the
failure modes that make the real ``/timeline/threads`` page unusable: with no
domain-boilerplate mass and no background pool, a single "Ungrouped signals"
row can swallow most of a realistic window, and multiple threads can collide
on the same label. ``tests/fixtures/thread_corpus.yaml`` is an ~80-headline
synthetic 30-day window (arcs of genuinely related coverage plus a long tail
of one-off stories with little lexical overlap to anything else) built to
reproduce those failure modes honestly.

Floor convention (mirrors the precision/recall floors in
``tests/test_golden_set.py``): the two numeric gates below (ungrouped
fraction, largest-thread fraction) are ``<=`` ceilings pinned at -- or just
above -- what is measured today against this fixture. They may only ever be
tightened (lowered) as later steps improve the clusterer, never loosened to
paper over a regression. If a change to ``threads.py`` needs a *higher*
ceiling to pass, that is a real regression, not a fixture problem.

The two label-quality gates (distinct labels; no label built solely from
window-common terms) are plain assertions, same as the numeric floors above --
step 2 (``keyword_mining.distinctive_terms`` + ``threads._dedupe_labels``)
earned both outright against this fixture (see the measured-numbers comment
below), so there is no floor to loosen here, only a bar to keep clearing.

KNOWN LIMITATION -- this fixture UNDERSTATES any entity-related improvement.
``sample_config.watched_entities`` carries only four aliases
(UnitedHealthcare, Humana, Aetna, CMS), and only one of them belongs to a
payer group with siblings. Real ``config/taxonomy.yaml`` watches 33 aliases
folding into 23 groups, several of them multi-alias (UnitedHealthcare /
UnitedHealth / UHC / Optum; CVS Health / Aetna; Elevance / Anthem). Alias
*fragmentation* is therefore barely exercised here, so a change that fixes it
shows a small delta against this fixture and a much larger one in production.
Measured for step 1: this fixture moved 0.605 -> 0.556 ungrouped, while an
83-story window scored through the real taxonomy moved 0.53 -> 0.41 with
thread count 14 -> 18. Read the fixture deltas as a REGRESSION FLOOR, not as
the effect size; measure effect size against the real archive.
"""

from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

from ma_signal_monitor.keyword_mining import _terms
from ma_signal_monitor.threads import build_threads

_FIXTURE = Path(__file__).parent / "fixtures" / "thread_corpus.yaml"
# Arbitrary fixed anchor date; day_offset in the fixture counts forward from
# here. build_threads() itself never reads event_date, but later steps will,
# so the loader produces it now to keep the shape stable.
_BASE_DATE = date(2026, 7, 1)

# Measured against tests/fixtures/thread_corpus.yaml at the production
# defaults (sample_config.thread_similarity_threshold /
# .thread_min_stories) when this test was written: 81 stories -> 11 threads,
# 49 ungrouped (0.605 of the window), largest thread 9 stories (0.111 of the
# window), and 6 distinct labels across 11 threads -- 6 of those 11 threads
# collide on the single fallback-ish label "medicare · advantage", whose two
# terms sit at window document-frequency 0.531 / 0.506 (well over the 0.4
# blocking fraction _DF_BLOCK_FRACTION uses for candidate generation). That
# reproduces both diagnosed failure modes at once. Ceilings below are set
# just above those measured values -- tight enough to catch a regression,
# loose enough not to flake. Floors only ever tighten from here.
#
# Step 1 (threads._story_terms folding each watched-entity alias to one
# opaque per-payer-group token instead of a raw lowercased alias string --
# see threads.py) re-measures this same fixture at: 81 stories -> 11
# threads, 45 ungrouped (0.556 of the window), largest thread still 9
# stories (0.111 of the window), and still 6 distinct labels across 11
# threads (label quality is step 2's job, untouched here). The ungrouped
# floor tightens accordingly; the other floor is unchanged since largest-
# thread share didn't move.
#
# Step 2 (keyword_mining.distinctive_terms -- log-odds ranking gated by an
# in-thread-share floor, so ranking measures distinctiveness rather than
# raw frequency -- plus threads._dedupe_labels' collision-escalation ladder)
# re-measures this same fixture at: 81 stories -> 11 threads (unchanged),
# 45 ungrouped, 0.556 of the window (unchanged), largest thread still 9
# stories, 0.111 of the window (unchanged) -- clustering is untouched by a
# labeling-only step, as expected -- and now **11 distinct labels across 11
# threads** (up from 6/11), with zero labels built solely from window-common
# (DF > 40%) terms. The resulting labels: "broker · groups", "bonus ·
# threatens", "court · cms star", "cost trend · earnings guidance",
# "medical loss · ratio", "network · supplemental benefits",
# "unitedhealthcare medicare · advantage contract", "value-based primary ·
# expand", "growth concentrated · advantage enrollment", "aetna · exits",
# "health raises · cvs". Both label-quality gates below are now plain
# assertions instead of xfail. The two numeric ceilings are unchanged since
# this step doesn't touch clustering.
#
# Step 3 (similarity.idf_weights/weighted_cosine replacing plain Jaccard, and
# threads._cluster switching from single-linkage to average-linkage with a
# merge guard -- see threads.py's module docstring) re-measures this same
# fixture, now at the recalibrated ``sample_config.thread_similarity_threshold
# == 0.13`` (a different scale than the old 0.28 -- see config.py's comment):
# 81 stories -> 20 threads (up from 11), 21 ungrouped, 0.259 of the window
# (down from 0.556), largest thread 6 stories, 0.074 of the window (down from
# 0.111 -- the chaining-regression canary moving the *right* direction, not
# just flat), and 20 distinct labels across 20 threads (no collisions), still
# zero labels built solely from window-common terms. Both numeric ceilings
# tighten accordingly. (Single-category purity, the anti-chaining metric the
# plan measures directly, isn't gated here since this fixture's 4-alias
# watch list barely exercises the failure mode being fixed -- see the sweep
# in scripts/calibrate_threads.py / config/app.yaml's comment for that
# number against both this fixture and a richer real-taxonomy corpus.)
_UNGROUPED_FRACTION_CEILING = 0.27
_LARGEST_THREAD_FRACTION_CEILING = 0.08


def _load_corpus() -> list[dict]:
    """Turn the YAML fixture into ``_story_view``-shaped dicts for build_threads."""
    data = yaml.safe_load(_FIXTURE.read_text())
    stories = []
    for i, entry in enumerate(data["stories"]):
        event_date = _BASE_DATE + timedelta(days=entry["day_offset"])
        category = entry["primary_category"]
        stories.append(
            {
                "item_id": f"corpus-{i}",
                "title": entry["title"],
                "summary": entry["title"],
                "source_name": entry.get("source_name", "Test Feed"),
                "relevance_score": 0.5,
                "primary_category": category,
                "categories": [category] if category != "uncategorized" else [],
                "entities": list(entry.get("entities") or []),
                "states": [],
                "event_date": event_date.isoformat(),
            }
        )
    return stories


def _window_document_frequency(stories: list[dict]) -> Counter:
    """How many story titles each headline n-gram term appears in.

    Mirrors ``threads.build_threads``' own ``global_terms`` bookkeeping
    (``Counter(_terms(title))`` per doc, summed): since ``_terms`` returns a
    *set* of a document's terms, summing one such Counter per doc is a
    document-frequency count, not a raw term-frequency count. Shared here so
    the DF>40% label-purity gate (this step's xfail, and step 2's earned
    version of it) has one place to compute it from.
    """
    df: Counter = Counter()
    for s in stories:
        df.update(_terms(s.get("title") or ""))
    return df


@pytest.fixture
def corpus() -> list[dict]:
    return _load_corpus()


@pytest.fixture
def threaded(corpus, sample_config):
    return build_threads(
        corpus,
        sample_config,
        threshold=sample_config.thread_similarity_threshold,
        min_stories=sample_config.thread_min_stories,
    )


def test_corpus_is_realistically_sized(corpus):
    # Guards against someone shrinking the fixture back toward the 5-story
    # regime where these failure modes don't show up.
    assert 60 <= len(corpus) <= 120


def test_ungrouped_fraction_within_floor(threaded, corpus):
    _, ungrouped = threaded
    fraction = len(ungrouped) / len(corpus)
    assert fraction <= _UNGROUPED_FRACTION_CEILING, (
        f"{len(ungrouped)}/{len(corpus)} = {fraction:.2f} of the window is "
        f"ungrouped, exceeds the {_UNGROUPED_FRACTION_CEILING} floor"
    )


def test_largest_thread_within_floor(threaded, corpus):
    threads, _ = threaded
    assert threads, "expected at least one thread on a realistic window"
    largest = max(t.total for t in threads)
    fraction = largest / len(corpus)
    assert fraction <= _LARGEST_THREAD_FRACTION_CEILING, (
        f"largest thread holds {largest}/{len(corpus)} = {fraction:.2f} of "
        f"the window, exceeds the {_LARGEST_THREAD_FRACTION_CEILING} floor"
    )


def test_thread_labels_are_distinct(threaded):
    threads, _ = threaded
    labels = [t.label for t in threads]
    dupes = sorted({label for label in labels if labels.count(label) > 1})
    assert not dupes, f"duplicate thread labels: {dupes}"


def test_thread_labels_avoid_window_common_terms(threaded, corpus):
    threads, _ = threaded
    df = _window_document_frequency(corpus)
    n = len(corpus)
    offending = []
    for t in threads:
        terms = [term for term in t.label.lower().split(" · ") if term]
        if terms and all(df.get(term, 0) / n > 0.4 for term in terms):
            offending.append(t.label)
    assert not offending, f"labels built solely from window-common terms: {offending}"
