"""Canonical grouping of watched entities into organization intelligence pages.

The scorer records every matched ``watched_entities`` alias on each story
(``stories.entities``). Aliases are intentionally granular so detection works
("UnitedHealthcare", "UnitedHealth", "UHC"), but readers think in terms of
organizations. This module folds aliases into canonical groups so the web
frontend can present one intelligence page per organization.

Every entry in ``config/taxonomy.yaml``'s ``watched_entities`` must belong to
exactly one group — ``tests/test_payers.py`` enforces this, so adding a new
watched entity prompts adding (or extending) its group here.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PayerGroup:
    """One organization page: a slug, display name, kind, and its aliases."""

    slug: str
    name: str
    kind: str  # "payer" | "distribution" | "people"
    aliases: tuple[str, ...]


KIND_LABELS = {
    "payer": "Payers",
    "distribution": "Brokerage & Distribution",
    "people": "People",
}

PAYER_GROUPS: tuple[PayerGroup, ...] = (
    PayerGroup(
        "unitedhealthcare",
        "UnitedHealthcare",
        "payer",
        ("UnitedHealthcare", "UnitedHealth", "UHC"),
    ),
    PayerGroup("humana", "Humana", "payer", ("Humana",)),
    PayerGroup("cvs-aetna", "CVS Health / Aetna", "payer", ("CVS Health", "Aetna")),
    PayerGroup("cigna", "Cigna", "payer", ("Cigna",)),
    PayerGroup("elevance", "Elevance Health", "payer", ("Elevance", "Anthem")),
    PayerGroup("centene", "Centene", "payer", ("Centene", "WellCare")),
    PayerGroup("molina", "Molina Healthcare", "payer", ("Molina",)),
    PayerGroup("kaiser", "Kaiser Permanente", "payer", ("Kaiser",)),
    PayerGroup("scan", "SCAN Health Plan", "payer", ("SCAN",)),
    PayerGroup("devoted-health", "Devoted Health", "payer", ("Devoted Health",)),
    PayerGroup("clover-health", "Clover Health", "payer", ("Clover Health",)),
    PayerGroup(
        "alignment-healthcare",
        "Alignment Healthcare",
        "payer",
        ("Alignment Healthcare",),
    ),
    PayerGroup("oscar-health", "Oscar Health", "payer", ("Oscar Health",)),
    PayerGroup(
        "bcbs",
        "Blue Cross Blue Shield plans",
        "payer",
        ("Blue Cross", "Blue Shield", "BCBS"),
    ),
    PayerGroup("caresource", "CareSource", "payer", ("CareSource",)),
    PayerGroup("bright-health", "Bright Health", "payer", ("Bright Health",)),
    PayerGroup("gohealth", "GoHealth", "distribution", ("GoHealth",)),
    PayerGroup("ehealth", "eHealth", "distribution", ("eHealth", "eHealthInsurance")),
    PayerGroup("selectquote", "SelectQuote", "distribution", ("SelectQuote",)),
    PayerGroup("gyde", "Gyde", "distribution", ("Gyde",)),
    PayerGroup("bailey-co", "Bailey & Co", "distribution", ("Bailey & Co",)),
    PayerGroup(
        "ezekiel-emanuel",
        "Ezekiel Emanuel",
        "people",
        ("Ezekiel Emanuel", "Zeke Emanuel"),
    ),
    PayerGroup("mark-bertolini", "Mark Bertolini", "people", ("Mark Bertolini",)),
)

_BY_SLUG = {g.slug: g for g in PAYER_GROUPS}
ALIAS_TO_GROUP = {alias: g for g in PAYER_GROUPS for alias in g.aliases}


def get_group(slug: str) -> PayerGroup | None:
    """Return the group for a slug, or None if unknown."""
    return _BY_SLUG.get(slug)
