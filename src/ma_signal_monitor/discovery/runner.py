"""Run feed autodiscovery over the top-ranked candidate domains.

This is the network-heavy half of discovery, run on a throttled schedule (or via
the ``ma-signal-discover`` CLI) — separate from the cheap per-ingest link
harvest in ``main._harvest_candidates``. Implements the hybrid promotion policy:
feeds on sufficiently strong domains are auto-promoted; the rest queue for
review.
"""

import logging

from ma_signal_monitor.config import AppConfig
from ma_signal_monitor.discovery.autodiscover import discover_feeds
from ma_signal_monitor.storage import StateStore

logger = logging.getLogger("ma_signal_monitor.discovery.runner")


def run_discovery(config: AppConfig, store: StateStore) -> dict:
    """Probe due candidate domains for feeds; queue or auto-promote results.

    Returns a summary dict (domains_checked, feeds_found, auto_promoted).
    """
    summary = {"domains_checked": 0, "feeds_found": 0, "auto_promoted": 0}
    if not config.discovery_enabled:
        logger.info("Discovery disabled; skipping autodiscovery run")
        return summary

    domains = store.domains_due_for_discovery(
        limit=config.discovery_max_domains_per_run,
        recheck_days=config.discovery_recheck_days,
        min_times_seen=config.discovery_min_times_seen,
    )
    logger.info("Autodiscovery: %d domain(s) due", len(domains))

    for row in domains:
        domain = row["domain"]
        summary["domains_checked"] += 1
        try:
            feeds = discover_feeds(
                domain,
                timeout=config.request_timeout,
                user_agent=config.user_agent,
            )
        except Exception as e:  # one bad domain shouldn't stop the run
            logger.warning("Discovery failed for %s: %s", domain, e)
            feeds = []

        promote = (
            row["relevance_score"] >= config.discovery_autopromote_score
            and row["times_seen"] >= config.discovery_autopromote_min_seen
        )
        for feed in feeds:
            store.upsert_candidate_source(
                feed_url=feed.feed_url,
                domain=domain,
                feed_title=feed.feed_title,
                discovery_method=feed.discovery_method,
                times_seen=row["times_seen"],
                relevance_score=row["relevance_score"],
                status="auto_promoted" if promote else "new",
            )
            summary["feeds_found"] += 1
            if promote:
                summary["auto_promoted"] += 1

        store.mark_domain_checked(domain)

    logger.info("Autodiscovery complete: %s", summary)
    return summary
