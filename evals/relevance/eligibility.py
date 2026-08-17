"""Compatibility shim: the gate now lives in production.

The two-tier MA-eligibility gate graduated into the package at
``src/ma_signal_monitor/eligibility.py`` (behind a default-off flag), and its
vocabulary moved into ``config/taxonomy.yaml``. This module keeps the eval
harness (``build_holdout.py``, ``scorecard.py``) and its regression tests
(``tests/test_relevance_eligibility.py``) working against that single source of
truth, so the held-out evaluation stays reproducible without a second copy of
the logic or the vocabulary.

The only difference from the production API is ergonomics: production
:func:`classify_eligibility` takes an explicit :class:`EligibilityVocab`
(built from a loaded config), whereas the harness has no config, so here the
vocabulary defaults to the shipped ``config/taxonomy.yaml`` loaded once.
"""

from __future__ import annotations

import functools
from pathlib import Path

from ma_signal_monitor.eligibility import (
    Eligibility,
    EligibilityVocab,
    entity_group_delta,
    eligible_for,
    load_eligibility_vocab,
)
from ma_signal_monitor.eligibility import classify_eligibility as _classify

__all__ = [
    "Eligibility",
    "EligibilityVocab",
    "classify_eligibility",
    "eligible_for",
    "entity_group_delta",
    "default_vocab",
]

_ROOT = Path(__file__).resolve().parents[2]


@functools.lru_cache(maxsize=1)
def default_vocab() -> EligibilityVocab:
    """The shipped eligibility vocabulary (config/taxonomy.yaml), loaded once."""
    return load_eligibility_vocab(_ROOT / "config" / "taxonomy.yaml")


def classify_eligibility(
    title: str,
    summary: str,
    matched_entities: list[str] | None = None,
    vocab: EligibilityVocab | None = None,
) -> Eligibility:
    """Classify one story, defaulting to the shipped vocabulary.

    Mirrors the production signature but makes ``vocab`` optional so the offline
    harness and its tests keep their ``classify_eligibility(title, summary,
    entities)`` call sites.
    """
    return _classify(title, summary, matched_entities, vocab or default_vocab())
