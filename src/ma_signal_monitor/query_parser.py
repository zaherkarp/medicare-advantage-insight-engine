"""Rule-based translator from a plain-language archive question into the
structured filters :class:`~ma_signal_monitor.storage.StateStore` already
accepts (category, score tier, date range, watched entity, state, free-text
keywords).

No embeddings, no NLP dependency. The archive's fields are already
categorical/structured (category, entities, states, score, date) plus short
free text already served by FTS5 — the questions this is built for ("show me
everything above alert grade related to measure set changes since March")
decompose cleanly into that shape without needing semantic similarity to
resolve. Pure function, no I/O, so the CLI (``scripts/ask_archive.py``) and
the ``/ask`` web route can share it and it's easy to test.
"""

import calendar
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ma_signal_monitor.config import AppConfig
from ma_signal_monitor.geo import detect_states_in_text
from ma_signal_monitor.geo import state_name as _state_name

_MONTH_NAMES = [m.lower() for m in calendar.month_name if m]
_MONTH_PATTERN = "|".join(_MONTH_NAMES)

_SINCE_MONTH_RE = re.compile(rf"since\s+({_MONTH_PATTERN})(?:\s+(\d{{4}}))?")
_LAST_N_DAYS_RE = re.compile(r"last\s+(\d+)\s+days?")
_THIS_WEEK_RE = re.compile(r"\bthis\s+week\b")
_THIS_MONTH_RE = re.compile(r"\bthis\s+month\b")
_IN_YEAR_RE = re.compile(r"\bin\s+(\d{4})\b")

_ALERT_GRADE_RE = re.compile(
    r"(?:above|over|at or above)\s+(?:the\s+)?alert[\s-]*(?:grade|threshold)"
    r"|alert[\s-]?grade(?:\s+or\s+above)?"
)
_ARCHIVE_FLOOR_RE = re.compile(r"(?:above|over)\s+(?:the\s+)?archive\s+floor")
_EXPLICIT_SCORE_RE = re.compile(
    r"(?:score\s*)?(?:above|over|>=?)\s*(0?\.\d+|1(?:\.0)?)\b"
)

# Filler words stripped from whatever's left over after structured filters are
# pulled out, so the remaining free text is a clean FTS keyword query rather
# than a full sentence.
_STOPWORDS = frozenset(
    {
        "show",
        "me",
        "everything",
        "anything",
        "all",
        "articles",
        "stories",
        "signals",
        "that",
        "are",
        "is",
        "related",
        "to",
        "about",
        "regarding",
        "the",
        "a",
        "an",
        "and",
        "or",
        "on",
        "in",
        "of",
        "for",
        "with",
    }
)
_PUNCT_RE = re.compile(r"[^\w\s-]")


def _word_pattern(term: str) -> re.Pattern:
    """Case-insensitive whole-phrase matcher with an optional trailing s/es.

    Mirrors scoring._keyword_pattern's plural tolerance (e.g. "star rating"
    matches "star ratings") without pulling in that module's private helper.
    """
    return re.compile(rf"\b{re.escape(term.lower())}(?:es|s)?\b")


@dataclass
class ParsedQuery:
    """The structured filter a plain-language question was translated into."""

    since: str | None = None
    category: str | None = None
    min_score: float = 0.0
    min_score_label: str = ""
    entity_aliases: list[str] = field(default_factory=list)
    state: str | None = None
    keywords: str = ""
    # Human-readable trace of what was parsed, echoed back so the CLI/web
    # form stay transparent about how the question was interpreted.
    notes: list[str] = field(default_factory=list)


def _extract_date(working: str, now: datetime) -> tuple[str, str | None, str | None]:
    m = _SINCE_MONTH_RE.search(working)
    if m:
        month = _MONTH_NAMES.index(m.group(1)) + 1
        year = (
            int(m.group(2))
            if m.group(2)
            else (now.year if month <= now.month else now.year - 1)
        )
        since = datetime(year, month, 1)
        working = working[: m.start()] + working[m.end() :]
        return working, since.date().isoformat(), f"since {m.group(1)}"

    m = _LAST_N_DAYS_RE.search(working)
    if m:
        n = int(m.group(1))
        since = now - timedelta(days=n)
        working = working[: m.start()] + working[m.end() :]
        return working, since.date().isoformat(), f"last {n} days"

    m = _THIS_WEEK_RE.search(working)
    if m:
        since = now - timedelta(days=now.weekday())
        working = working[: m.start()] + working[m.end() :]
        return working, since.date().isoformat(), "this week"

    m = _THIS_MONTH_RE.search(working)
    if m:
        since = now.replace(day=1)
        working = working[: m.start()] + working[m.end() :]
        return working, since.date().isoformat(), "this month"

    m = _IN_YEAR_RE.search(working)
    if m:
        since = datetime(int(m.group(1)), 1, 1)
        working = working[: m.start()] + working[m.end() :]
        return working, since.date().isoformat(), f"year {m.group(1)} (no upper bound)"

    return working, None, None


def _extract_score_tier(working: str, config: AppConfig) -> tuple[str, float, str]:
    m = _ALERT_GRADE_RE.search(working)
    if m:
        working = working[: m.start()] + working[m.end() :]
        return working, config.min_relevance_score, "alert grade"
    m = _ARCHIVE_FLOOR_RE.search(working)
    if m:
        working = working[: m.start()] + working[m.end() :]
        return working, config.archive_min_score, "archive floor"
    m = _EXPLICIT_SCORE_RE.search(working)
    if m:
        value = float(m.group(1))
        working = working[: m.start()] + working[m.end() :]
        return working, value, f"explicit score {value}"
    return working, config.archive_min_score, "archive floor (default)"


def _extract_state(working: str) -> tuple[str, str | None, str | None]:
    codes = detect_states_in_text(working)
    if not codes:
        return working, None, None
    code = codes[0]
    working = re.sub(
        rf"\b{re.escape(_state_name(code).lower())}\b", " ", working, count=1
    )
    return working, code, f"state {code}"


def _extract_entities(
    working: str, config: AppConfig
) -> tuple[str, list[str], str | None]:
    matched: list[str] = []
    for entity in config.watched_entities:
        pattern = _word_pattern(entity)
        m = pattern.search(working)
        if m:
            matched.append(entity)
            working = working[: m.start()] + working[m.end() :]
    if not matched:
        return working, [], None
    return working, matched, f"entities {matched}"


def _extract_category(
    working: str, config: AppConfig
) -> tuple[str, str | None, str | None]:
    best: tuple[int, str, str, int, int] | None = None
    for category in config.categories:
        for kw in [category.label, *category.keywords]:
            m = _word_pattern(kw).search(working)
            if m and (best is None or len(kw) > best[0]):
                best = (len(kw), category.key, kw, m.start(), m.end())
    if best is None:
        return working, None, None
    _, key, kw, start, end = best
    working = working[:start] + working[end:]
    return working, key, f"category {key} (matched {kw!r})"


def _remaining_keywords(working: str) -> str:
    working = _PUNCT_RE.sub(" ", working)
    tokens = [t for t in working.split() if t not in _STOPWORDS]
    return " ".join(tokens).strip()


def parse_query(
    text: str, config: AppConfig, *, now: datetime | None = None
) -> ParsedQuery:
    """Translate a free-text question into a :class:`ParsedQuery` filter.

    Order matters: each extractor consumes (removes) the text it matched
    before the next one runs, so a phrase like "Humana" isn't later mistaken
    for a leftover keyword, and whatever's left after every structured
    extractor has run becomes the free-text keyword search.
    """
    now = now or datetime.utcnow()
    working = text.lower()
    parsed = ParsedQuery()

    working, parsed.since, since_note = _extract_date(working, now)
    if since_note:
        parsed.notes.append(f"since={parsed.since} ({since_note})")

    working, parsed.min_score, parsed.min_score_label = _extract_score_tier(
        working, config
    )
    parsed.notes.append(f"min_score={parsed.min_score:.2f} ({parsed.min_score_label})")

    working, parsed.state, state_note = _extract_state(working)
    if state_note:
        parsed.notes.append(state_note)

    working, parsed.entity_aliases, entity_note = _extract_entities(working, config)
    if entity_note:
        parsed.notes.append(entity_note)

    working, parsed.category, category_note = _extract_category(working, config)
    if category_note:
        parsed.notes.append(category_note)

    parsed.keywords = _remaining_keywords(working)
    if parsed.keywords:
        parsed.notes.append(f"keywords={parsed.keywords!r}")

    return parsed
