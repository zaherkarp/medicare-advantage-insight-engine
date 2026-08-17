"""Two-tier Medicare-Advantage eligibility gate.

Promoted from the research subsystem (``evals/relevance/eligibility.py``) after a
held-out evaluation justified wiring it in (see ``evals/relevance/RESULTS_2026-08
.md``). It is consulted by the production scorer only when the
``ma_eligibility_gate`` flag is enabled (default off); with the flag off nothing
here runs and the pipeline is byte-identical to before.

The gate is deliberately separate from the additive relevance score, so the
owner's dispositions are expressed directly rather than by nudging numbers:

    BRIEF   >  ALERT  >  DISPLAY  >  EXCLUDE

- ``BRIEF``   the item carries MA-specific vocabulary (Tier A/B) — a real
              Medicare Advantage signal. Eligible for the Daily Briefing.
- ``ALERT``   a watched payer plus Medicare context but no MA-specific term
              (Tier C, "defensible MA implication"). Eligible for the push
              alert stream but not the stricter briefing.
- ``DISPLAY`` Medicare-adjacent (bare "Medicare"/"CMS"/"Part D") with no MA
              line of business — traditional Medicare, the Medicare GLP-1
              Bridge. Kept in the archive/feed, kept OUT of briefing+alerts
              ("display-only, not briefed").
- ``EXCLUDE`` an owner-designated noise class — Medicaid-only, general-CMS
              non-MA rulemaking, payer criminal/reputational, payer
              commercial/ACA — and not rescued by an MA-specific term. Kept in
              the archive for audit, hidden from the public display floor.

A separate, purely mechanical score fix (:func:`entity_group_delta`) removes the
double-count where one payer matches under two aliases (``UnitedHealthcare`` +
``UnitedHealth`` = +0.40 for one company).

The vocabulary lives in ``config/taxonomy.yaml`` (``eligibility:`` section),
loaded into :class:`~ma_signal_monitor.config.AppConfig` by ``config.py`` and
turned into an :class:`EligibilityVocab` by :func:`vocab_from_config`. The eval
harness loads the same file via :func:`load_eligibility_vocab`, so the shipped
gate and the reproducible evaluation share exactly one vocabulary. Matching
reuses the production whole-token matcher so "MA" never matches "Massachusetts"
and "SNP" never matches "snippet".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ma_signal_monitor.scoring import _keyword_in_text

if TYPE_CHECKING:
    from ma_signal_monitor.config import AppConfig

ENTITY_MATCH_BOOST = 0.20  # mirrors config.scoring.entity_match_boost default


@dataclass(frozen=True)
class EligibilityVocab:
    """The gate's tunable vocabulary (from ``taxonomy.yaml``'s ``eligibility:``).

    ``ma_specific`` / ``medicare_adjacent`` are whole-token term lists;
    ``negative_context`` maps an owner noise-class name to its term list.
    """

    ma_specific: tuple[str, ...] = ()
    medicare_adjacent: tuple[str, ...] = ()
    negative_context: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass
class Eligibility:
    tier: str  # "brief" | "alert" | "display" | "exclude"
    ma_specific: bool
    medicare_adjacent: bool
    payer_present: bool
    negative_hits: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


_TIER_RANK = {"exclude": 0, "display": 1, "alert": 2, "brief": 3}


def _any(text: str, terms) -> list[str]:
    return [t for t in terms if _keyword_in_text(t, text)]


def classify_eligibility(
    title: str,
    summary: str,
    matched_entities: list[str] | None,
    vocab: EligibilityVocab,
) -> Eligibility:
    """Assign an MA-eligibility tier to one story. Pure and deterministic."""
    text = f"{title or ''} {summary or ''}"
    ma = _any(text, vocab.ma_specific)
    adj = _any(text, vocab.medicare_adjacent)
    payer = bool(matched_entities)
    neg: list[str] = []
    for cls, terms in vocab.negative_context.items():
        hits = _any(text, terms)
        if hits:
            neg.append(f"{cls}:{hits[0]}")

    reasons: list[str] = []
    # MA-specific vocabulary rescues an item from every negative class.
    if ma:
        reasons.append(f"MA-specific: {ma[0]}")
        return Eligibility("brief", True, bool(adj), payer, neg, reasons)
    if neg:
        reasons.append(f"excluded: {neg[0]}")
        return Eligibility("exclude", False, bool(adj), payer, neg, reasons)
    if adj and payer:
        reasons.append(f"payer + Medicare context ({adj[0]}, defensible implication)")
        return Eligibility("alert", False, True, payer, neg, reasons)
    if adj:
        reasons.append(f"Medicare-adjacent only ({adj[0]}); display-only")
        return Eligibility("display", False, True, payer, neg, reasons)
    reasons.append("no Medicare context")
    return Eligibility("exclude", False, False, payer, neg, reasons)


def entity_group_delta(
    matched_entities: list[str],
    alias_to_group,
    boost: float = ENTITY_MATCH_BOOST,
) -> float:
    """Score correction (<=0) that collapses payer aliases to distinct groups.

    ``alias_to_group`` maps a watched-entity alias to a payer group (the
    production ``payers.ALIAS_TO_GROUP``). Production caps entity boosts at 2
    aliases (+0.40); this recomputes the boost over distinct *groups* so
    "UnitedHealthcare" + "UnitedHealth" counts once (+0.20), not twice. ``boost``
    is ``scoring.entity_match_boost`` (defaults to its 0.20 default so the eval
    and unit tests can call it without a config).
    """
    ents = list(matched_entities or [])
    old = min(len(ents), 2) * boost
    groups = set()
    for alias in ents:
        g = alias_to_group.get(alias)
        groups.add(g.slug if g is not None else alias.lower())
    new = min(len(groups), 2) * boost
    return round(new - old, 3)


def eligible_for(tier: str, gate: str) -> bool:
    """True if a story's ``tier`` clears the requested ``gate`` (brief/alert/display)."""
    return _TIER_RANK.get(tier, 0) >= _TIER_RANK.get(gate, 99)


def vocab_from_config(config: AppConfig) -> EligibilityVocab:
    """Build an :class:`EligibilityVocab` from a loaded :class:`AppConfig`."""
    return EligibilityVocab(
        ma_specific=tuple(config.eligibility_ma_specific),
        medicare_adjacent=tuple(config.eligibility_medicare_adjacent),
        negative_context={
            cls: tuple(terms)
            for cls, terms in config.eligibility_negative_context.items()
        },
    )


def load_eligibility_vocab(taxonomy_path: str | Path) -> EligibilityVocab:
    """Read the ``eligibility:`` vocabulary straight from a taxonomy.yaml file.

    A lightweight loader that bypasses full :func:`config.load_config` validation
    (which requires SEC_CONTACT_EMAIL and a sources file), so the offline eval
    harness and the unit tests can obtain the shipped vocabulary from one source
    of truth without standing up a whole config.
    """
    import yaml

    with open(taxonomy_path) as f:
        data = yaml.safe_load(f) or {}
    eligibility = data.get("eligibility", {}) or {}
    return EligibilityVocab(
        ma_specific=tuple(eligibility.get("ma_specific", []) or []),
        medicare_adjacent=tuple(eligibility.get("medicare_adjacent", []) or []),
        negative_context={
            cls: tuple(terms or [])
            for cls, terms in (eligibility.get("negative_context", {}) or {}).items()
        },
    )
