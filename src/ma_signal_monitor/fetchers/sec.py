"""SEC EDGAR fetcher.

Consumes SEC EDGAR Atom feeds (e.g. a company's recent filings via
``browse-edgar?...&output=atom``) and normalizes them into RawFeedItems. EDGAR
serves standard Atom, so this delegates to the shared feed fetcher.

Note: unlike every other source, the SEC requires a User-Agent with a real
contact *email address* in it (set via SEC_CONTACT_EMAIL) — a merely
descriptive one, with no email, is rejected outright with a 403, independent
of ingestion rate. See compose_sec_user_agent() and
https://www.sec.gov/os/webmaster-faq#developers.
"""

import logging

from ma_signal_monitor.config import SourceConfig
from ma_signal_monitor.fetchers.rss import fetch_feed
from ma_signal_monitor.models import RawFeedItem

logger = logging.getLogger("ma_signal_monitor.fetchers.sec")


class MissingSecContactError(ValueError):
    """Raised when an SEC EDGAR fetch is attempted with no contact email.

    sec.gov 403s any User-Agent without one, so proceeding would just
    reproduce that silently (the fetch is wrapped in a broad except-and-log
    by main._fetch_one_source) — raise instead so the run fails loudly
    in config validation before any fetch is attempted.
    """


def compose_sec_user_agent(user_agent: str, contact_email: str) -> str:
    """Build a User-Agent sec.gov will accept: the base UA plus a contact email.

    Verified against the live endpoint: a UA with no email 403s no matter how
    descriptive it reads (e.g. "MA Signal Monitor Research Project"), while
    any UA ending in a real-looking email address (e.g. "someone@gmail.com")
    gets a 200 — including on the exact defaults this project shipped with
    for months. Note *.github.com noreply addresses are themselves rejected
    (SEC appears to block that specific domain), so use a normal inbox.
    """
    contact_email = (contact_email or "").strip()
    if "@" not in contact_email:
        raise MissingSecContactError(
            "SEC EDGAR fetch requires a contact email but none is "
            "configured. Set SEC_CONTACT_EMAIL (see .env.example) — sec.gov "
            "rejects any User-Agent without one (403)."
        )
    return f"{user_agent} {contact_email}"


def fetch_sec(
    source: SourceConfig,
    timeout: int = 30,
    user_agent: str = "MA-Signal-Monitor/1.0",
    max_items: int = 50,
    contact_email: str = "",
) -> list[RawFeedItem]:
    """Fetch filings from a SEC EDGAR Atom feed.

    Composes an SEC-compliant User-Agent from ``contact_email`` rather than
    sending ``user_agent`` as-is — see compose_sec_user_agent().
    """
    compliant_user_agent = compose_sec_user_agent(user_agent, contact_email)
    return fetch_feed(
        source, timeout=timeout, user_agent=compliant_user_agent, max_items=max_items
    )
