"""Silent-source detection.

Turns per-run fetch outcomes (``source_fetch_log``, see
``storage.get_source_fetch_health``) into a flagged review list — the same
"detect, never auto-mutate" shape as ``source_review.py``'s low-yield
flagging. A human confirms any action (e.g. disabling a source in
``sources.yaml``); this module only decides what deserves a look.

Distinct from ``source_review.py``: that flags a source that's *working* but
low-quality, computed from the ``stories`` table. This flags a source that
may not be working at all — computed from ``source_fetch_log`` precisely
because a broken source has no ``stories`` rows to compute anything from.
"""

from datetime import datetime

from ma_signal_monitor.config import AppConfig


def flag_silent_sources(
    health: dict[str, dict], config: AppConfig, now: datetime | None = None
) -> list[dict]:
    """Return enabled sources silent for >= ``config.source_silent_days``.

    A source is flagged when it has been attempted (present in ``health``,
    from ``storage.get_source_fetch_health``) and the time since its last
    persisted item — or, if it has never persisted one, since its first
    logged attempt — is at least the threshold. This catches both a source
    that has never worked (e.g. a permanent 403) and one that broke after
    working (e.g. a feed URL that started 404ing).

    A source with no entry in ``health`` at all (never attempted within the
    lookback window passed to ``get_source_fetch_health`` — freshly enabled,
    most likely) is not flagged; it hasn't had a chance to run yet.
    """
    now = now or datetime.utcnow()
    flagged = []
    for s in config.sources:
        if not s.enabled:
            continue
        entry = health.get(s.name)
        if entry is None:
            continue
        reference = entry["last_persisted_at"] or entry["first_attempt_at"]
        age_days = (now - datetime.fromisoformat(reference)).days
        if age_days < config.source_silent_days:
            continue

        error_suffix = f": {entry['last_error']}" if entry.get("last_error") else ""
        if entry["last_persisted_at"] is None:
            reason = (
                f"never persisted an item in {age_days}d "
                f"({entry['attempts']} attempt(s), last status "
                f"'{entry['last_status']}'{error_suffix})"
            )
        else:
            reason = (
                f"last persisted an item {age_days}d ago "
                f"(last status '{entry['last_status']}'{error_suffix})"
            )
        flagged.append(
            {
                "source_name": s.name,
                "priority": s.priority,
                "reason": reason,
                **entry,
            }
        )
    return flagged
