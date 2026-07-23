"""Daily Briefing digest — the marquee "help people" feature.

Builds a curated digest of the day's top Medicare Advantage signals from the
story archive, renders it to email-safe HTML and plain text, persists it (so the
``/briefing`` web page can show it and email sends stay idempotent), and
optionally delivers it over SMTP.

It is useful even without SMTP configured: the digest is always saved and shown
on the web. Email is a bonus when ``DIGEST_*``/``SMTP_*`` env vars are set.
"""

import logging
import smtplib
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ma_signal_monitor.classify import get_category_label
from ma_signal_monitor.config import AppConfig, load_config
from ma_signal_monitor.geo import state_name
from ma_signal_monitor.storage import StateStore
from ma_signal_monitor.synthesis import DigestLede, build_lede

logger = logging.getLogger("ma_signal_monitor.digest")

_TEMPLATES_DIR = Path(__file__).parent / "web" / "templates"

# Fixed display order for the topic sections in a digest.
_CATEGORY_ORDER = [
    "policy_regulatory",
    "membership_movement",
    "financial_pressure",
    "competitive_strategy",
    "brokerage_distribution",
    "demographic_shifts",
    "uncategorized",
]


@dataclass
class DigestStory:
    """A single story as presented in a digest."""

    item_id: str
    title: str
    link: str
    source_name: str
    summary: str
    category_key: str
    category_label: str
    score: float
    states: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)


@dataclass
class CandidateSummary:
    """A discovered candidate source surfaced in the digest."""

    feed_title: str
    feed_url: str
    domain: str
    relevance_score: float
    times_seen: int
    auto_promoted: bool


@dataclass
class Digest:
    """A rendered Daily Briefing for one UTC day."""

    digest_date: str  # YYYY-MM-DD (UTC)
    generated_at: datetime
    subject: str
    lookback_hours: int
    sections: list[tuple[str, list[DigestStory]]]  # (category_label, stories)
    story_count: int
    candidates: list[CandidateSummary] = field(default_factory=list)
    lede: DigestLede | None = None  # "what's happening" synthesis block


def _build_candidates(store: StateStore, limit: int = 6) -> list[CandidateSummary]:
    """Top newly-discovered / auto-promoted candidate sources for the digest."""
    rows = store.list_candidate_sources(status="new", limit=limit)
    rows = list(rows) + list(
        store.list_candidate_sources(status="auto_promoted", limit=limit)
    )
    rows.sort(key=lambda r: r["relevance_score"] or 0.0, reverse=True)
    return [
        CandidateSummary(
            feed_title=r["feed_title"] or r["domain"],
            feed_url=r["feed_url"],
            domain=r["domain"],
            relevance_score=r["relevance_score"] or 0.0,
            times_seen=r["times_seen"],
            auto_promoted=(r["status"] == "auto_promoted"),
        )
        for r in rows[:limit]
    ]


def _row_to_story(row, config: AppConfig) -> DigestStory:
    import json

    cat = row["primary_category"] or "uncategorized"
    return DigestStory(
        item_id=row["item_id"],
        title=row["title"],
        link=row["link"],
        source_name=row["source_name"],
        summary=row["summary"] or "",
        category_key=cat,
        category_label=get_category_label(cat, config),
        score=row["relevance_score"] or 0.0,
        states=json.loads(row["states"] or "[]"),
        entities=json.loads(row["entities"] or "[]"),
    )


def _facet_dict(row) -> dict:
    """A ``get_recent_story_facets`` row → the facet dict ``build_lede`` folds.

    Same lens shape as ``routes._facet_view`` (JSON lenses parsed); the web
    layer isn't imported here, so the small conversion is repeated locally.
    """
    import json

    return {
        "primary_category": row["primary_category"] or "uncategorized",
        "categories": json.loads(row["categories"] or "[]"),
        "entities": json.loads(row["entities"] or "[]"),
        "states": json.loads(row["states"] or "[]"),
        "title": row["title"],
        "relevance_score": row["relevance_score"] or 0.0,
    }


def _build_lede(store: StateStore, config: AppConfig, now: datetime, since: datetime):
    """Fetch the current + prior facet windows and synthesize the lede.

    Mirrors the ``/angles`` route: two uncapped, time-bounded facet reads (the
    windows are small) feeding a pure builder. Returns ``None`` when the lede is
    disabled or the window is empty.
    """
    if not config.digest_lede_enabled:
        return None
    prev_since = since - timedelta(hours=config.digest_lookback_hours)
    current = [
        _facet_dict(r)
        for r in store.get_recent_story_facets(
            since=since, min_score=config.digest_min_score, until=now
        )
    ]
    previous = [
        _facet_dict(r)
        for r in store.get_recent_story_facets(
            since=prev_since, min_score=config.digest_min_score, until=since
        )
    ]
    return build_lede(current, previous, now, config)


def build_digest(
    store: StateStore,
    config: AppConfig,
    now: datetime | None = None,
) -> Digest:
    """Assemble (but do not persist) the digest for the current window."""
    now = now or datetime.utcnow()
    since = now - timedelta(hours=config.digest_lookback_hours)
    rows = store.get_recent_top_stories(
        since=since,
        limit=config.digest_max_items,
        min_score=config.digest_min_score,
    )
    stories = [_row_to_story(r, config) for r in rows]

    # Group into topic sections, preserving the fixed category order.
    by_cat: dict[str, list[DigestStory]] = {}
    for s in stories:
        by_cat.setdefault(s.category_key, []).append(s)
    ordered_keys = [k for k in _CATEGORY_ORDER if k in by_cat]
    ordered_keys += [k for k in by_cat if k not in _CATEGORY_ORDER]
    sections = [(get_category_label(k, config), by_cat[k]) for k in ordered_keys]

    digest_date = now.strftime("%Y-%m-%d")
    subject = (
        f"{config.digest_subject_prefix} — {now.strftime('%b %-d, %Y')} "
        f"({len(stories)} signal{'' if len(stories) == 1 else 's'})"
    )
    candidates = _build_candidates(store) if config.discovery_enabled else []
    return Digest(
        digest_date=digest_date,
        generated_at=now,
        subject=subject,
        lookback_hours=config.digest_lookback_hours,
        sections=sections,
        story_count=len(stories),
        candidates=candidates,
        lede=_build_lede(store, config, now, since),
    )


def _jinja_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    env.globals["state_name"] = state_name
    return env


def render_html(digest: Digest, config: AppConfig) -> str:
    """Render the digest to email-safe, self-contained HTML."""
    env = _jinja_env()
    template = env.get_template("digest_email.html")
    return template.render(digest=digest, base_url=config.public_base_url)


def render_text(digest: Digest) -> str:
    """Render a plain-text fallback for email clients without HTML."""
    lines = [
        digest.subject,
        "=" * len(digest.subject),
        "",
        f"Top {digest.story_count} Medicare Advantage signals from the last "
        f"{digest.lookback_hours} hours.",
        "",
    ]
    if digest.lede:
        lines.append("WHAT'S HAPPENING")
        lines.append(digest.lede.summary)
        if digest.lede.season_note:
            lines.append(digest.lede.season_note)
        if digest.lede.offcycle_note:
            lines.append(digest.lede.offcycle_note)
        lines.append(digest.lede.breakdown)
        lines.append("")
    if not digest.story_count:
        lines.append("No qualifying signals in this window.")
    for label, stories in digest.sections:
        lines.append(f"## {label}")
        for s in stories:
            tags = ""
            if s.states:
                tags = "  [" + ", ".join(state_name(c) for c in s.states) + "]"
            lines.append(f"- {s.title}{tags}")
            lines.append(f"  {s.source_name} · score {s.score:.2f}")
            if s.summary:
                lines.append(f"  {s.summary}")
            lines.append(f"  {s.link}")
        lines.append("")
    if digest.candidates:
        lines.append("## New candidate sources")
        for c in digest.candidates:
            flag = " (auto-promoted)" if c.auto_promoted else ""
            lines.append(f"- {c.feed_title}{flag} — score {c.relevance_score:.2f}")
            lines.append(f"  {c.feed_url}")
        lines.append("")
    lines.append(
        "Curated for analytic relevance — not legal, compliance, or investment advice."
    )
    return "\n".join(lines)


def send_digest(digest: Digest, html: str, config: AppConfig) -> bool:
    """Email the digest over SMTP. Returns True on success.

    Returns False (without raising) when SMTP is not configured, so callers can
    treat email as best-effort.
    """
    recipients = [a.strip() for a in config.digest_to.split(",") if a.strip()]
    sender = config.digest_from or config.smtp_user
    if not (config.smtp_host and recipients and sender):
        logger.info(
            "SMTP not fully configured (host/recipients/sender); "
            "skipping email delivery"
        )
        return False

    msg = EmailMessage()
    msg["Subject"] = digest.subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(render_text(digest))
    msg.add_alternative(html, subtype="html")

    try:
        with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30) as smtp:
            if config.smtp_use_tls:
                smtp.starttls()
            if config.smtp_user:
                smtp.login(config.smtp_user, config.smtp_password)
            smtp.send_message(msg)
        logger.info("Digest emailed to %d recipient(s)", len(recipients))
        return True
    except Exception as e:
        logger.error("Failed to send digest email: %s", e)
        return False


def generate_digest(
    config: AppConfig,
    store: StateStore,
    *,
    now: datetime | None = None,
    send: bool = True,
) -> Digest:
    """Build, persist, and (optionally) email the digest. Idempotent per day."""
    digest = build_digest(store, config, now=now)
    html = render_html(digest, config)

    sent_at = None
    if send:
        if send_digest(digest, html, config):
            sent_at = datetime.utcnow().isoformat()

    store.save_digest(
        digest_date=digest.digest_date,
        generated_at=digest.generated_at.isoformat(),
        story_count=digest.story_count,
        subject=digest.subject,
        html=html,
        sent_at=sent_at,
    )
    logger.info(
        "Generated digest for %s with %d stories (emailed=%s)",
        digest.digest_date,
        digest.story_count,
        bool(sent_at),
    )
    return digest


def main() -> None:
    """CLI entry point: build + save + send today's digest."""
    root = Path.cwd()
    if len(sys.argv) > 1:
        root = Path(sys.argv[1])
    config = load_config(root)
    store = StateStore(root / config.db_path)
    try:
        digest = generate_digest(config, store, send=True)
        print(
            f"Digest {digest.digest_date}: {digest.story_count} stories. "
            f"View at /briefing."
        )
    finally:
        store.close()


if __name__ == "__main__":
    main()
