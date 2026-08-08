"""Data models for MA Signal Monitor.

These are plain dataclasses representing the core data flowing through the pipeline.
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RawFeedItem:
    """An item as fetched from a source, before normalization."""

    source_name: str
    source_type: str
    source_url: str
    source_priority: int
    source_tags: list[str]
    title: str
    link: str
    published: str  # Raw date string from feed
    summary: str
    author: str = ""
    raw_content: str = ""
    # Un-stripped HTML of the entry (summary + content). Carries the embedded
    # <a href> links that source discovery harvests. Intentionally transient:
    # it is not copied onto NormalizedItem and is never persisted to the DB.
    content_html: str = ""


@dataclass
class NormalizedItem:
    """A feed item after normalization to a standard schema."""

    item_id: str  # Stable hash for deduplication
    source_name: str
    source_type: str
    source_priority: int
    source_tags: list[str]
    title: str
    link: str
    published_date: datetime | None
    summary: str
    author: str = ""
    fetched_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ScoringReason:
    """One contributing factor to a relevance score."""

    factor: str
    detail: str
    contribution: float


@dataclass
class ScoredItem:
    """A normalized item with relevance scoring attached."""

    item: NormalizedItem
    relevance_score: float  # 0.0 to 1.0
    reasons: list[ScoringReason] = field(default_factory=list)
    matched_categories: list[str] = field(default_factory=list)
    matched_entities: list[str] = field(default_factory=list)


@dataclass
class InternalAlert:
    """Section A: Internal analytic alert."""

    signal_type: str
    source: str
    title: str
    publication_date: str
    entities: list[str]
    trigger_category: str
    relevance_score: float
    summary: str
    why_it_matters: str
    suggested_checks: list[str]
    confidence: str  # "high", "medium", "low"
    source_url: str
    scoring_reasons: list[str]


@dataclass
class PublicInsightDraft:
    """Section B: Draft public insight angle."""

    opening_hook: str
    analytic_angles: list[str]
    uncertainty_caution: str
    suggested_hashtags: list[str]
    draft_paragraph: str


@dataclass
class Alert:
    """Complete alert combining internal analysis and public draft."""

    internal: InternalAlert
    public_draft: PublicInsightDraft
    scored_item: ScoredItem


@dataclass
class DeliveryResult:
    """Result of attempting to deliver an alert."""

    alert_title: str
    success: bool
    status_code: int | None = None
    error: str | None = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    # The archived story this delivery attempt posted (or tried to post).
    # Lets alert-outcome feedback confirm a story was actually delivered
    # before a human labels it correct/false_positive/missed.
    item_id: str | None = None


@dataclass
class SourceFetchOutcome:
    """One source's fetch result for a single run (see main._fetch_one_source).

    Exists because a source that raises/403s and a source that's simply quiet
    both collapse to "returns no items" at the RawFeedItem level, and both
    look identical to "zero archive rows" from the `stories` table alone —
    which is exactly how 16 sources went unnoticed for months. ``status``
    distinguishes them: "error" (the fetch itself failed — exception or
    non-200), "empty" (fetched fine, 0 usable items), or "ok" (>= 1 item).
    ``n_persisted`` is filled in after persistence, separately from fetch —
    it's what would catch a source whose items fetch fine but never archive
    (upstream persist failure); verified against the real production
    archive that no currently-silent source is actually in that state today
    — see docs/troubleshooting.md for what each of the 16 turned out to be.
    """

    source_name: str
    status: str  # "ok" | "empty" | "error"
    n_items: int = 0
    error: str = ""
