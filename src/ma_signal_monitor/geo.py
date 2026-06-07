"""Lightweight U.S. state detection for the State Intelligence section.

This is intentionally dependency-free: a static name->code map plus
word-boundary regex matching over a story's title and summary. It runs only on
already-MA-relevant items, so we accept the occasional false positive in
exchange for zero NLP/ML overhead.
"""

import logging
import re

from ma_signal_monitor.models import ScoredItem

logger = logging.getLogger("ma_signal_monitor.geo")

# USPS code -> full state name (50 states + DC).
STATE_NAMES: dict[str, str] = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "DC": "District of Columbia",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
}

# Full state name -> code, longest names first so "West Virginia" matches before
# "Virginia" and "North Carolina" before "Carolina"-style substrings.
_NAME_TO_CODE: list[tuple[re.Pattern, str]] = [
    (re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE), code)
    for code, name in sorted(
        STATE_NAMES.items(), key=lambda kv: len(kv[1]), reverse=True
    )
]


def state_name(code: str) -> str:
    """Return the full name for a USPS code (or the code itself if unknown)."""
    return STATE_NAMES.get(code, code)


def detect_states_in_text(text: str) -> list[str]:
    """Return USPS codes for any state names found in `text`.

    Matches longest names first and removes each match from the working text so
    a substring (e.g. "Virginia" inside "West Virginia") doesn't double-count.
    """
    if not text:
        return []
    work = text
    found: list[str] = []
    for pattern, code in _NAME_TO_CODE:
        if pattern.search(work):
            found.append(code)
            work = pattern.sub(" ", work)
    return found


def detect_states(scored: ScoredItem) -> list[str]:
    """Detect U.S. states referenced in a scored story's title and summary."""
    item = scored.item
    return detect_states_in_text(f"{item.title} {item.summary}")
