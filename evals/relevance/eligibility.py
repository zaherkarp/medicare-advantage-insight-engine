"""Candidate Option A: a two-tier Medicare-Advantage eligibility gate.

This module is the *experiment*, not production. Nothing in the shipped
ingestion/scoring/briefing path imports it. It is exercised only by the
relevance evaluation harness (``evals/relevance/scorecard.py``) and its unit
tests (``tests/test_relevance_eligibility.py``), so the production scorer and
the golden-set gate are unchanged until a held-out evaluation justifies wiring
it in behind a default-off flag.

Design (from the owner rubric decisions, 2026-08-15):

* The current pipeline has *no* MA-eligibility gate — the "MA-context gate"
  is satisfied by the bare word "Medicare"/"CMS" or any watched-payer name,
  so Medicaid rules, general CMS rules, and a payer CEO's criminal case all
  clear it. This module adds the missing gate.
* It is a *tier gate*, deliberately separate from the additive score, so the
  rubric's dispositions are expressed directly rather than by nudging numbers:

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
                commercial/ACA — and not rescued by an MA-specific term.

* A separate, purely mechanical score fix (:func:`entity_group_delta`) removes
  the double-count where one payer matches under two aliases
  (``UnitedHealthcare`` + ``UnitedHealth`` = +0.40 for one company).

Vocabulary lives here as tunable defaults; a production port would move it to
``config/taxonomy.yaml``. Matching reuses the production whole-token matcher so
"MA" never matches "Massachusetts" and "SNP" never matches "snippet".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ma_signal_monitor.scoring import _keyword_in_text

# --- Vocabulary (tunable; a production port moves these to taxonomy.yaml) ----

# Tier A/B: a genuine Medicare Advantage signal. Any one match => BRIEF-eligible.
MA_SPECIFIC: tuple[str, ...] = (
    "Medicare Advantage",
    "MA plan",
    "MA plans",
    "MA business",
    "MA member",
    "MA members",
    "MA bonus",
    "MA market",
    "Part C",
    "D-SNP",
    "DSNP",
    "C-SNP",
    "I-SNP",
    "special needs plan",
    "dual eligible",
    "dual-eligible",
    "Medigap",
    "star rating",
    "star ratings",
    "risk adjustment",
    "RADV",
)

# Broad Medicare context (NOT MA-specific). Establishes DISPLAY eligibility.
MEDICARE_ADJACENT: tuple[str, ...] = (
    "Medicare",
    "CMS",
    "Centers for Medicare",
    "Part D",
    "Part B",
    "Medicare Part",
)

# Owner-designated noise classes (2026-08-15). A match EXCLUDES the item unless
# an MA-specific term is also present (a real MA story that merely mentions
# Medicaid is not noise). Kept precise and multi-word to avoid over-exclusion.
NEGATIVE_CONTEXT: dict[str, tuple[str, ...]] = {
    # Medicaid-only policy (owner: Medicaid-only is out).
    "medicaid": (
        "Medicaid expansion",
        "Medicaid managed care",
        "Medicaid waiver",
        "1115 waiver",
        "CHIP funding",
        "work requirements",
    ),
    # General CMS / non-MA rulemaking with no MA line (owner: exclude).
    "general_cms": (
        "gender-affirming",
        "transgender",
        "No Surprises Act",
    ),
    # Payer commercial / ACA lines (owner: exclude).
    "commercial_aca": (
        "ACA exchange",
        "ACA exchanges",
        "Affordable Care Act",
        "insurance exchange",
        "insurance marketplace",
        "individual market",
        "level-funded",
        "employer plan",
        "commercial plan",
        "self-funded",
    ),
    # Payer criminal / reputational (owner: exclude).
    "criminal": (
        "pleads guilty",
        "pleaded guilty",
        "stalking",
        "indicted",
        "murder",
        "killing",
        "insider stock",
        "insider trading",
    ),
}

# NB: AI-vendor "Stars" product claims are deliberately NOT a negative class —
# the owner chose not to exclude them (2026-08-15).

ENTITY_MATCH_BOOST = 0.20  # mirrors config.scoring.entity_match_boost default


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
    title: str, summary: str, matched_entities: list[str] | None = None
) -> Eligibility:
    """Assign an MA-eligibility tier to one story. Pure and deterministic."""
    text = f"{title or ''} {summary or ''}"
    ma = _any(text, MA_SPECIFIC)
    adj = _any(text, MEDICARE_ADJACENT)
    payer = bool(matched_entities)
    neg: list[str] = []
    for cls, terms in NEGATIVE_CONTEXT.items():
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


def entity_group_delta(matched_entities: list[str], alias_to_group) -> float:
    """Score correction (<=0) that collapses payer aliases to distinct groups.

    ``alias_to_group`` maps a watched-entity alias to a payer group (the
    production ``payers.ALIAS_TO_GROUP``). Production caps entity boosts at 2
    aliases (+0.40); this recomputes the boost over distinct *groups* so
    "UnitedHealthcare" + "UnitedHealth" counts once (+0.20), not twice.
    """
    ents = list(matched_entities or [])
    old = min(len(ents), 2) * ENTITY_MATCH_BOOST
    groups = set()
    for alias in ents:
        g = alias_to_group.get(alias)
        groups.add(g.slug if g is not None else alias.lower())
    new = min(len(groups), 2) * ENTITY_MATCH_BOOST
    return round(new - old, 3)


def eligible_for(tier: str, gate: str) -> bool:
    """True if a story's ``tier`` clears the requested ``gate`` (brief/alert/display)."""
    return _TIER_RANK.get(tier, 0) >= _TIER_RANK.get(gate, 99)
